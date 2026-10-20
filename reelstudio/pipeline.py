"""Runs a whole reel: clips on Comfy Cloud -> chain last frames -> join -> music."""
import json
import os
import shutil
import threading
import time
import traceback

from . import audio, credits, music, video
from .comfy import ComfyCloud, ComfyError, build_flf, build_i2v
from .planner import negative_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, "workflows")


def load_wf(name):
    with open(os.path.join(WF_DIR, name)) as f:
        return json.load(f)


class Project:
    """Everything about one reel, saved as projects/<id>/project.json."""

    def __init__(self, pid):
        self.id = pid
        self.dir = os.path.join(ROOT, "projects", pid)
        self.path = os.path.join(self.dir, "project.json")
        self.lock = threading.Lock()
        self.data = {}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.data = json.load(f)

    def save(self):
        with self.lock:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(f"[{self.id}] {line}", flush=True)
        self.data.setdefault("log", []).append(line)
        self.data["log"] = self.data["log"][-300:]
        self.save()

    def file(self, name):
        return os.path.join(self.dir, name)


RUNNING = {}
CLOUD = {"connected": True}   # flipped by the Connect / Disconnect button
CONTROL = {}                  # project id -> {"stop": finish current clip then stop, "now": cancel at once}


def active_jobs():
    """(project, job id) for every clip currently on Comfy Cloud."""
    out = []
    for pid, t in list(RUNNING.items()):
        if t.is_alive():
            p = Project(pid)
            for c in p.data.get("clips", []):
                if c.get("state") in ("generating", "uploading") and c.get("job"):
                    out.append((pid, c["job"]))
    return out


def start(project, cfg, from_clip=0):
    if not CLOUD["connected"]:
        raise RuntimeError("Comfy Cloud is disconnected. Click Connect first.")
    if project.id in RUNNING and RUNNING[project.id].is_alive():
        raise RuntimeError("This reel is already being made.")
    project.data["status"] = "running"
    project.data["error"] = None
    CONTROL[project.id] = {"stop": False, "now": False}
    project.save()
    t = threading.Thread(target=_run, args=(project, cfg, from_clip), daemon=True)
    RUNNING[project.id] = t
    t.start()


def _run(p, cfg, from_clip):
    try:
        _make(p, cfg, from_clip)
        p.data["status"] = "done"
        p.log("Finished. Your reel is ready.")
    except Exception as e:
        p.data["status"] = "error"
        p.data["error"] = str(e)
        p.log(f"Stopped: {e}")
        if not isinstance(e, ComfyError):
            p.log(traceback.format_exc()[-1500:])
    p.save()


def _make(p, cfg, from_clip):
    d = p.data
    ch = d["character"]
    s = d["settings"]
    clips = d["clips"]
    W, H = int(s.get("width", 720)), int(s.get("height", 1280))
    fps = int(s.get("fps", 16))
    frames = int(s.get("seconds_per_clip", 5) * fps) + 1
    turbo = bool(s.get("turbo", True))
    dynamic = turbo and bool(s.get("dynamic", True))
    if dynamic:
        p.log("Dynamic Turbo: the first steps run without the speed LoRA for bigger moves.")
    neg = negative_for(ch)
    char_lora = ch.get("lora") if (ch.get("lora") or {}).get("file_high") or (ch.get("lora") or {}).get("file_low") else None
    if char_lora:
        p.log(f"Using character LoRA '{char_lora.get('name')}' (trigger: {char_lora.get('trigger') or 'none'}).")
    original = p.file("start.png")
    if not os.path.exists(original):
        raise RuntimeError("The character start image is missing.")

    cloud = ComfyCloud(cfg.get("comfy_api_key"), cfg.get("comfy_base_url") or "https://cloud.comfy.org", log=p.log)
    i2v_t, flf_t = load_wf("wan22_i2v_api.json"), load_wf("wan22_flf2v_api.json")
    uploads = {}

    def up(path):
        key = (path, os.path.getmtime(path))
        if key not in uploads:
            uploads[key] = cloud.upload_image(path)
        return uploads[key]

    for i, c in enumerate(clips):
        if i < from_clip and c.get("video") and os.path.exists(p.file(c["video"])):
            continue
        ctl = CONTROL.get(p.id, {})
        if ctl.get("stop") or ctl.get("now") or not CLOUD["connected"]:
            raise RuntimeError("Stopped by you." if CLOUD["connected"] else "Disconnected from Comfy Cloud.")
        start_img = original if (i == 0 or c.get("reanchor")) else p.file(clips[i - 1]["last_frame"])
        mode = "first+last frame" if c.get("lock_end") else "image-to-video"
        p.log(f"Clip {i + 1}/{len(clips)}: uploading start image ({mode}).")
        c["state"] = "uploading"; p.save()
        start_name = up(start_img)
        prefix = f"video/rs_{p.id}_c{i + 1}"
        if c.get("lock_end"):
            wf = build_flf(flf_t, start_name, up(original), c["motion"], neg, W, H, frames, fps, turbo=turbo, prefix=prefix,
                           char_lora=char_lora, dynamic=dynamic)
        else:
            wf = build_i2v(i2v_t, start_name, c["motion"], neg, W, H, frames, fps, turbo=turbo, prefix=prefix,
                           char_lora=char_lora, dynamic=dynamic)
        c["state"] = "generating"; c["started"] = time.time(); p.save()
        job = cloud.submit(wf)
        c["job"] = job
        p.log(f"Clip {i + 1}: generating on Comfy Cloud (job {job[:8]}).")

        def tick(st, el, c=c):
            c["cloud_status"], c["elapsed"] = st, round(el)
            p.save()

        def should_stop():
            return not CLOUD["connected"] or bool(CONTROL.get(p.id, {}).get("now"))

        det = cloud.wait(job, on_tick=tick, should_stop=should_stop)
        name = f"clip{i + 1}.mp4"
        cloud.download_video(det, p.file(name))
        video.last_frame(p.file(name), p.file(f"clip{i + 1}_last.png"))
        video.first_frame(p.file(name), p.file(f"clip{i + 1}_first.png"))
        c.update(video=name, last_frame=f"clip{i + 1}_last.png", thumb=f"clip{i + 1}_first.png",
                 state="done", seconds=round(video.duration(p.file(name)), 2))
        cr = credits.record("clip", c.get("elapsed", 0), "slow" if not turbo else ("dynamic" if dynamic else "turbo"), p.id)
        c["credits"] = cr
        p.log(f"Clip {i + 1}: done in {c.get('elapsed', 0)} s (about {cr:.0f} credits).")
        p.save()

    finish(p)


def finish(p):
    """Join the clips and add music. Can be re-run alone to change the music."""
    d = p.data
    s = d["settings"]
    clips = d["clips"]
    paths = [p.file(c["video"]) for c in clips]
    trims = [i > 0 and not c.get("reanchor") for i, c in enumerate(clips)]
    up = s.get("upscale", True)
    W, H = (1080, 1920) if up else (int(s.get("width", 720)), int(s.get("height", 1280)))
    m = d.get("music") or {"mode": "none"}
    beats, an = [], None
    raw_len = sum(video.duration(x) for x in paths)
    if m.get("mode") == "original":
        bpm = int(m.get("bpm", 120))
        beats = [k * 60.0 / bpm for k in range(int((raw_len + 2) * bpm / 60) + 1)]
    elif m.get("mode") == "song" and m.get("file") and os.path.exists(p.file(m["file"])):
        try:
            an = audio.analyze_cached(p.file(m["file"]))
            start = float(m.get("start", 0))
            if m.get("snap", True):
                start = audio.snap_to_downbeat(an, start)
            m["start_used"] = start
            m["bpm"] = an.get("bpm")
            beats = audio.beats_in_reel(an, start, raw_len * 1.15)
            p.log(f"Song: {an.get('bpm')} BPM, starting on the beat at {start:.2f} s.")
        except Exception as e:
            p.log(f"Could not analyse the song for beat sync ({e}).")
    speeds = [1.0] * len(paths)
    if beats and s.get("beat_sync", True):
        speeds, before, after = video.beat_speeds(paths, trims, beats)
        d["beat_sync"] = {"speeds": speeds, "before": before, "after": after}
        p.log(f"Beat sync: {int(after * 100)}% of the dance accents land on a beat (was {int(before * 100)}%). "
              f"Clip speeds: {', '.join(f'{x:.2f}x' for x in speeds)}.")
    p.log(f"Joining {len(paths)} clips into one {W}x{H} reel.")
    joined = p.file("reel_silent_raw.mp4")
    video.join_clips(paths, joined, trims, W, H, speeds=speeds)
    silent = p.file("reel_silent.mp4")
    video.add_silence(joined, silent)
    final = p.file("reel_final.mp4")
    if m.get("mode") == "original":
        p.log("Making an original background track.")
        track = p.file("track.wav")
        music.make_track(track, video.duration(silent) + 0.5, bpm=int(m.get("bpm", 120)), style=m.get("style", "festive"))
        video.add_audio(joined, track, final, 0, float(m.get("volume", 0.9)))
    elif m.get("mode") == "song" and m.get("file") and os.path.exists(p.file(m["file"])):
        p.log(f"Mixing in your song from {float(m.get('start_used', m.get('start', 0))):.2f} s.")
        video.add_audio(joined, p.file(m["file"]), final, float(m.get("start_used", m.get("start", 0))), float(m.get("volume", 0.9)),
                        float(m.get("fade_in", 0.5)), float(m.get("fade_out", 1.5)))
    else:
        shutil.copyfile(silent, final)
    d["final"] = "reel_final.mp4"
    d["silent"] = "reel_silent.mp4"
    out_dir = os.path.join(ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)
    nice = f"{d['character'].get('name', 'reel')}_{p.id}.mp4".replace(" ", "_")
    shutil.copyfile(final, os.path.join(out_dir, nice))
    d["output_copy"] = os.path.join("output", nice)
    os.remove(joined)
    p.log(f"Saved to ReelStudio/output/{nice}")
    p.save()
