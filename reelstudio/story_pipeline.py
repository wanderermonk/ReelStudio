"""Runs a story: keyframes (image edit) -> one video per shot -> cuts + narration + subtitles + sound effects + music."""
import hashlib
import os
import shutil
import subprocess
import threading
import time
import traceback

from . import credits, imagegen, music, sfx, story, subs, tts, video
from .comfy import ComfyCloud, ComfyError, build_flf, build_i2v
from .pipeline import CLOUD, CONTROL, RUNNING, ROOT, load_wf

CHAR_DIR = os.path.join(ROOT, "data", "characters")


def _cloud(p, cfg):
    return ComfyCloud(cfg.get("comfy_api_key"), cfg.get("comfy_base_url") or "https://cloud.comfy.org", log=p.log)


def _launch(p, target, *args):
    if not CLOUD["connected"]:
        raise RuntimeError("Comfy Cloud is disconnected. Click Connect first.")
    if p.id in RUNNING and RUNNING[p.id].is_alive():
        raise RuntimeError("This story is already being made.")
    CONTROL[p.id] = {"stop": False, "now": False}
    p.data["status"] = "running"
    p.data["error"] = None
    p.save()

    def run():
        try:
            target(p, *args)
        except Exception as e:
            p.data["status"] = "error"
            p.data["error"] = str(e)
            p.log(f"Stopped: {e}")
            if not isinstance(e, (ComfyError, RuntimeError)):
                p.log(traceback.format_exc()[-1500:])
            p.save()

    t = threading.Thread(target=run, daemon=True)
    RUNNING[p.id] = t
    t.start()


def _stopped(p):
    c = CONTROL.get(p.id, {})
    return c.get("stop") or c.get("now") or not CLOUD["connected"]


def _should_stop(p):
    return lambda: not CLOUD["connected"] or bool(CONTROL.get(p.id, {}).get("now"))


def cast_image(p, name):
    for c in p.data.get("cast", []):
        if c.get("name") == name:
            f = p.file(f"cast_{c['id']}.png")
            return f if os.path.exists(f) else None
    return None


# ---------------- keyframes ----------------
def start_keyframes(p, cfg, only=None, which="both"):
    _launch(p, _keyframes, cfg, only, which)


def _keyframes(p, cfg, only, which):
    d = p.data
    cast = d["cast"]
    cloud = _cloud(p, cfg)
    edit = imagegen.detect_edit(cloud, log=p.log)
    t2i = None
    uploads = {}

    def up(path):
        key = (path, os.path.getmtime(path))
        if key not in uploads:
            uploads[key] = cloud.upload_image(path)
        return uploads[key]

    d["stage"] = "keyframes"
    for sh in d["clips"]:
        if only and sh["id"] not in only:
            continue
        jobs = []
        if sh["mode"] in ("i2v", "flf") and which in ("both", "start"):
            jobs.append("start")
        if sh["mode"] == "flf" and which in ("both", "end"):
            jobs.append("end")
        for kind in jobs:
            if _stopped(p):
                raise RuntimeError("Stopped by you." if CLOUD["connected"] else "Disconnected from Comfy Cloud.")
            names = sh.get("characters") or [cast[0]["name"]]
            refs_files = [f for f in (cast_image(p, n) for n in names) if f]
            if kind == "end":
                if not sh.get("kf_start"):
                    p.log(f"{sh['id']}: make the start keyframe first.")
                    continue
                refs_files = [p.file(sh["kf_start"])] + refs_files[:2]
            sh["state"] = f"kf_{kind}"
            p.log(f"Shot {sh['id'][1:]}: making the {kind} keyframe…")
            p.save()
            dest = p.file(f"kf_{sh['id']}_{kind}_{int(time.time())}.png")
            refs = [up(f) for f in refs_files]
            eng, secs, edit, t2i = imagegen.edit_or_draw(
                cloud, edit, t2i, story.keyframe_prompt(sh, cast, kind, edit=True),
                story.keyframe_prompt(sh, cast, kind, edit=False), refs, dest, should_stop=_should_stop(p), log=p.log)
            old = sh.get(f"kf_{kind}")
            sh[f"kf_{kind}"] = os.path.basename(dest)
            sh[f"kf_{kind}_engine"] = eng
            sh[f"kf_{kind}_ok"] = False
            if old and os.path.exists(p.file(old)):
                os.replace(p.file(old), p.file("old_" + old))
            cr = credits.record("image", secs, note=p.id)
            p.log(f"Shot {sh['id'][1:]}: {kind} keyframe done with {eng} ({secs:.0f} s, ~{cr:.0f} credits).")
        sh["state"] = "keyframes" if (sh.get("kf_start") or sh["mode"] == "continue") else "waiting"
        p.save()
    d["status"] = "keyframes_ready"
    p.log("Keyframes ready. Check them, redo any you don't like, then make the video.")
    p.save()


# ---------------- shot videos ----------------
def start_video(p, cfg, only=None, from_shot=0):
    shots = p.data["clips"]
    for i, sh in enumerate(shots):
        if (only and sh["id"] not in only) or i < from_shot:
            continue
        if sh["mode"] in ("i2v", "flf") and not sh.get("kf_start"):
            raise RuntimeError(f"Shot {i + 1} has no start keyframe yet. Make keyframes first.")
        if sh["mode"] == "flf" and not sh.get("kf_end"):
            raise RuntimeError(f"Shot {i + 1} needs an end keyframe (or change it to a normal shot).")
        if sh["mode"] == "continue" and i == 0:
            raise RuntimeError("The first shot cannot continue from a previous shot.")
    _launch(p, _videos, cfg, only, from_shot)


def _videos(p, cfg, only, from_shot):
    d = p.data
    s = d["settings"]
    cast = d["cast"]
    shots = d["clips"]
    W, H, fps = int(s.get("width", 720)), int(s.get("height", 1280)), int(s.get("fps", 16))
    frames = int(5 * fps) + 1
    turbo = bool(s.get("turbo", True))
    dynamic = turbo and bool(s.get("dynamic", True))
    neg = story.negative_for_story(cast)
    cloud = _cloud(p, cfg)
    i2v_t, flf_t = load_wf("wan22_i2v_api.json"), load_wf("wan22_flf2v_api.json")
    uploads = {}

    def up(path):
        key = (path, os.path.getmtime(path))
        if key not in uploads:
            uploads[key] = cloud.upload_image(path)
        return uploads[key]

    d["stage"] = "video"
    for i, sh in enumerate(shots):
        todo = (sh["id"] in only) if only else (i >= from_shot or not (sh.get("video") and os.path.exists(p.file(sh["video"]))))
        if not todo:
            continue
        if _stopped(p):
            raise RuntimeError("Stopped by you." if CLOUD["connected"] else "Disconnected from Comfy Cloud.")
        if sh["mode"] == "continue":
            prev = shots[i - 1]
            if not prev.get("last_frame"):
                raise RuntimeError(f"Shot {i + 1} continues shot {i}, which has no video yet.")
            start = p.file(prev["last_frame"])
        else:
            start = p.file(sh["kf_start"])
        sh["state"] = "uploading"
        p.save()
        prompt = story.video_prompt(sh, cast)
        sh["video_prompt"] = prompt
        prefix = f"video/rs_{p.id}_{sh['id']}"
        if sh["mode"] == "flf":
            wf = build_flf(flf_t, up(start), up(p.file(sh["kf_end"])), prompt, neg, W, H, frames, fps, turbo=turbo,
                           prefix=prefix, dynamic=dynamic)
        else:
            wf = build_i2v(i2v_t, up(start), prompt, neg, W, H, frames, fps, turbo=turbo, prefix=prefix, dynamic=dynamic)
        sh["state"] = "generating"
        sh["started"] = time.time()
        p.save()
        job = cloud.submit(wf)
        sh["job"] = job
        p.log(f"Shot {i + 1}/{len(shots)} ({sh.get('title', '')}): generating ({ {'flf': 'start+end keyframes', 'i2v': 'from keyframe', 'continue': 'continuing'}[sh['mode']] }).")

        def tick(st, el, sh=sh):
            sh["cloud_status"], sh["elapsed"] = st, round(el)
            p.save()

        det = cloud.wait(job, on_tick=tick, should_stop=_should_stop(p))
        name = f"{sh['id']}.mp4"
        cloud.download_video(det, p.file(name))
        video.last_frame(p.file(name), p.file(f"{sh['id']}_last.png"))
        video.first_frame(p.file(name), p.file(f"{sh['id']}_first.png"))
        cr = credits.record("clip", sh.get("elapsed", 0), "slow" if not turbo else ("dynamic" if dynamic else "turbo"), p.id)
        sh.update(video=name, last_frame=f"{sh['id']}_last.png", thumb=f"{sh['id']}_first.png", state="done",
                  credits=cr, seconds=round(video.duration(p.file(name)), 2))
        p.log(f"Shot {i + 1}: done in {sh.get('elapsed', 0)} s (~{cr:.0f} credits).")
        p.save()
    if all(sh.get("video") for sh in shots):
        assemble(p)
        d["status"] = "done"
        p.log("Finished. Your short is ready.")
    else:
        d["status"] = "keyframes_ready"
    p.save()


# ---------------- assembly ----------------
def _run(args):
    r = subprocess.run([video.FFMPEG, "-hide_banner", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-900:])


def shot_lines(sh):
    lines = sh.get("lines")
    if isinstance(lines, list):
        return [ln for ln in lines if (ln.get("text") or "").strip()]
    t = (sh.get("narration") or "").strip()
    return [{"speaker": "Narrator", "text": t}] if t else []


def narration_files(p):
    """Make (or reuse) the voice files. Returns {shot id: [(path, seconds, text, speaker), ...]} in speaking order."""
    st = p.data.get("story_settings", {})
    if st.get("language") == "none":
        return {}
    narrator = st.get("voice") or "hi-IN-SwaraNeural"
    cast_voices = tts.voice_cast(p.data.get("cast", []), st.get("language", "en"), narrator, st.get("voices"))
    rate = int(st.get("voice_rate", 0))
    out = {}
    for sh in p.data["clips"]:
        for j, ln in enumerate(shot_lines(sh)):
            vc = cast_voices.get(ln.get("speaker")) or cast_voices["Narrator"]
            text = ln["text"].strip()
            r = rate + int(vc.get("rate", 0))
            h = hashlib.sha1(f"{vc['voice']}|{vc.get('pitch', 0)}|{r}|{text}".encode()).hexdigest()[:10]
            f = p.file(f"voice_{sh['id']}_{j}_{h}.wav")
            if not os.path.exists(f):
                tts.synth(text, vc["voice"], f, r, log=p.log, pitch_hz=int(vc.get("pitch", 0)))
            out.setdefault(sh["id"], []).append((f, video.duration(f), text, ln.get("speaker", "Narrator")))
    return out


GAP = 0.25  # pause between two lines in one shot


def _speech_len(items):
    return sum(x[1] for x in items) + GAP * max(0, len(items) - 1)


def assemble(p):
    d = p.data
    st = d.setdefault("story_settings", {})
    s = d["settings"]
    shots = d["clips"]
    up = s.get("upscale", True)
    W, H = (1080, 1920) if up else (int(s.get("width", 720)), int(s.get("height", 1280)))
    p.log("Assembling the short: cuts, voice, subtitles, sound effects and music.")
    voices = {}
    try:
        voices = narration_files(p)
    except Exception as e:
        p.log(f"Narration skipped: {e}")

    # 1) video: cuts, each shot slowed (max 1.25x) or held so its narration fits
    parts, args, offsets, t = [], [], [], 0.0
    for i, sh in enumerate(shots):
        args += ["-i", p.file(sh["video"])]
        trim = sh["mode"] == "continue"
        dur = video.duration(p.file(sh["video"])) - (1 / 16 if trim else 0)
        need = (_speech_len(voices[sh["id"]]) + 0.7) if sh["id"] in voices else 0
        slow = min(1.25, max(1.0, need / dur)) if need else 1.0
        hold = max(0.0, need - dur * slow)
        sh["timing"] = {"start": round(t, 2), "slow": round(slow, 3), "hold": round(hold, 2)}
        tf = "trim=start_frame=1," if trim else ""
        hf = f",tpad=stop_mode=clone:stop_duration={hold:.2f}" if hold > 0.05 else ""
        parts.append(f"[{i}:v]{tf}setpts=(PTS-STARTPTS)*{slow:.4f}{hf},scale={W}:{H}:flags=lanczos,setsar=1,fps=30[v{i}]")
        offsets.append(t)
        t += dur * slow + hold
    total = t
    chain = "".join(f"[v{i}]" for i in range(len(shots)))
    fc = ";".join(parts) + f";{chain}concat=n={len(shots)}:v=1:a=0,fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, total - 0.6):.2f}:d=0.6,format=yuv420p[v]"
    base = p.file("story_video.mp4")
    _run(args + ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18", base])

    # 2) subtitles
    cues = []
    for i, sh in enumerate(shots):
        a = offsets[i] + 0.3
        for (f, dur, text, spk) in voices.get(sh["id"], []):
            cues.append((a, a + dur + 0.2, text))
            a += dur + GAP
    subs.srt(cues, p.file("subtitles.srt"))
    subbed = None
    if cues:
        try:
            subbed = _burn_subs(p, base, cues, W, H)
        except Exception as e:
            p.log(f"Could not draw subtitles on the video ({str(e)[:200]}); subtitles.srt is saved.")

    # 3) audio: voice + sound effects + music (ducked under the voice)
    ins, flt, labels_v, labels_fx = [], [], [], []
    k = 0
    for i, sh in enumerate(shots):
        a = offsets[i] + 0.3
        for (f, dur, text, spk) in voices.get(sh["id"], []):
            ins += ["-i", f]
            ms = int(a * 1000)
            flt.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[n{k}]")
            labels_v.append(f"[n{k}]")
            k += 1
            a += dur + GAP
        f = sfx.path(sh.get("sfx"))
        if f:
            ins += ["-i", f]
            ms = int((offsets[i] + 0.15) * 1000)
            flt.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={float(st.get('sfx_volume', 0.6)):.2f},adelay={ms}|{ms}[x{k}]")
            labels_fx.append(f"[x{k}]")
            k += 1
    mus = None
    mode = st.get("music", "original")
    if mode == "original":
        mus = p.file("story_music.wav")
        music.make_track(mus, total + 1, bpm=int(st.get("bpm", 96)), style="sweet")
    elif mode == "song" and st.get("song_file") and os.path.exists(p.file(st["song_file"])):
        mus = p.file(st["song_file"])
    if mus:
        ins += ["-i", mus]
        ss = float(st.get("song_start", 0)) if mode == "song" else 0
        flt.append(f"[{k}:a]atrim=start={ss}:duration={total:.2f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
                   f"volume={float(st.get('music_volume', 0.35)):.2f},afade=t=in:d=0.6,afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[m]")
        k += 1
    mix = []
    if labels_v:
        flt.append(f"{''.join(labels_v)}amix=inputs={len(labels_v)}:normalize=0:duration=longest[voice]")
        if mus:
            flt.append("[voice]asplit=2[vo1][vo2]")
            flt.append("[m][vo1]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[md]")
            mix += ["[md]", "[vo2]"]
        else:
            mix.append("[voice]")
    elif mus:
        mix.append("[m]")
    mix += labels_fx
    sound = None
    if mix:
        sound = p.file("story_sound.m4a")
        flt.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=longest,apad,atrim=duration={total:.2f},alimiter=limit=0.95[a]")
        _run([*ins, "-filter_complex", ";".join(flt), "-map", "[a]", "-c:a", "aac", "-b:a", "192k", sound])

    def mux(v, out):
        if sound:
            _run(["-i", v, "-i", sound, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-shortest",
                  "-movflags", "+faststart", out])
        else:
            video.add_silence(v, out)
        return out

    final = mux(base, p.file("story_final.mp4"))
    d["final_subtitled"] = os.path.basename(mux(subbed, p.file("story_final_subtitled.mp4"))) if subbed else None
    d["final"] = "story_final.mp4"
    d["silent"] = "story_video.mp4"
    out_dir = os.path.join(ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)
    nice = f"{d.get('title', 'story')}_{p.id}.mp4".replace(" ", "_").replace("/", "-")
    shutil.copyfile(final, os.path.join(out_dir, nice))
    d["output_copy"] = os.path.join("output", nice)
    if d.get("final_subtitled"):
        shutil.copyfile(p.file(d["final_subtitled"]), os.path.join(out_dir, nice[:-4] + "_subtitled.mp4"))
    if cues:
        shutil.copyfile(p.file("subtitles.srt"), os.path.join(out_dir, nice[:-4] + ".srt"))
    p.log(f"Saved to ReelStudio/output/{nice} ({total:.1f} s)" + (", plus a subtitled copy and the .srt file." if cues else "."))
    p.save()


def _burn_subs(p, base, cues, W, H):
    if all(subs.can_render(c[2]) for c in cues):
        ins, ov, last = [], [], "[0:v]"
        for j, (a, b, text) in enumerate(cues):
            png = p.file(f"sub_{j}.png")
            _, h = subs.render(text, png, W, size=int(W / 20))
            ins += ["-i", png]
            y = int(H * 0.70)
            ov.append(f"{last}[{j + 1}:v]overlay=x=0:y={y}:enable='between(t,{a:.2f},{b:.2f})'[o{j}]")
            last = f"[o{j}]"
        out = p.file("story_subbed.mp4")
        _run(["-i", base, *ins, "-filter_complex", ";".join(ov), "-map", last, "-c:v", "libx264", "-preset", "medium",
              "-crf", "18", out])
        return out
    # Hindi without the RAQM shaper: let ffmpeg/libass draw the .srt
    out = p.file("story_subbed.mp4")
    srt = p.file("subtitles.srt").replace("\\", "/").replace(":", "\\:")
    _run(["-i", base, "-vf", f"subtitles='{srt}':force_style='Fontsize=16,Outline=2,MarginV=70,Alignment=2'",
          "-c:v", "libx264", "-preset", "medium", "-crf", "18", out])
    return out
