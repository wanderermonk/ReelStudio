"""Reel Studio — local app. Start it with ./run.sh, then use the page that opens in your browser."""
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid

from flask import Flask, abort, jsonify, request, send_from_directory

from reelstudio import audio, credits, gallery, library, lyrics, photochar, pipeline, planner, sfx, story, story_pipeline, tts
from reelstudio.comfy import ComfyCloud, ComfyError
from reelstudio.pipeline import Project

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CHAR_DIR = os.path.join(DATA, "characters")
CHAR_FILE = os.path.join(DATA, "characters.json")
CFG_FILE = os.path.join(ROOT, "config.json")
PROJ_DIR = os.path.join(ROOT, "projects")
for d in (DATA, CHAR_DIR, PROJ_DIR, os.path.join(ROOT, "output")):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")
_last_open = {"t": 0.0}


def _stored_cfg():
    stored = dict(DEFAULT_CFG)
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE) as f:
            stored.update(json.load(f))
    return stored


def open_dashboard(force=False):
    """Open Comfy Cloud in the default browser (at most once every 10 minutes unless forced)."""
    cfg = load_cfg()
    if not force and (not cfg.get("auto_open_cloud") or time.time() - _last_open["t"] < 600):
        return False
    _last_open["t"] = time.time()
    url = cfg.get("cloud_dashboard_url") or "https://cloud.comfy.org"
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", url])
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        return False
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
_lock = threading.Lock()

DEFAULT_CFG = {"comfy_api_key": "", "comfy_base_url": "https://cloud.comfy.org", "anthropic_api_key": "",
               "anthropic_model": "claude-sonnet-4-5", "use_ollama": True, "ollama_url": "http://127.0.0.1:11434",
               "ollama_model": "", "auto_open_cloud": True, "cloud_connected": True,
               "cloud_dashboard_url": "https://cloud.comfy.org", "default_turbo": True, "default_dynamic": True, "whisper_model": "base"}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE) as f:
            cfg.update(json.load(f))
    cfg["comfy_api_key"] = os.environ.get("COMFY_API_KEY") or cfg["comfy_api_key"]
    cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY") or cfg["anthropic_api_key"]
    return cfg


def save_cfg(cfg):
    with open(CFG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(CFG_FILE, 0o600)
    except OSError:
        pass


def load_chars():
    if not os.path.exists(CHAR_FILE):
        return []
    with open(CHAR_FILE) as f:
        return json.load(f)


def save_chars(chars):
    with open(CHAR_FILE, "w") as f:
        json.dump(chars, f, indent=2)


def get_char(cid):
    for c in load_chars():
        if c["id"] == cid:
            return c
    abort(404, "Character not found")


def resolve_char(ch):
    """Attach the chosen LoRA entry so prompts get its trigger word and workflows load its files."""
    ch = dict(ch)
    lo = library.get_lora(ch.get("lora_id") or "none")
    if lo and not lo.get("info_only") and lo["id"] != "none" and (lo.get("file_high") or lo.get("file_low")):
        ch["lora"] = lo
        ch["lora_trigger"] = lo.get("trigger", "")
    else:
        ch.pop("lora", None)
        ch["lora_trigger"] = ""
    return ch


def mask(k):
    return (k[:6] + "…" + k[-4:]) if k and len(k) > 12 else ("set" if k else "")


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:60]


@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "static"), "index.html")


@app.get("/help")
def help_page():
    return send_from_directory(os.path.join(ROOT, "static"), "help.html")


# ---------- Comfy Cloud connection ----------
def _set_connected(v):
    pipeline.CLOUD["connected"] = bool(v)
    st = _stored_cfg()
    st["cloud_connected"] = bool(v)
    save_cfg(st)


@app.get("/api/cloud")
def cloud_state():
    cfg = load_cfg()
    jobs = pipeline.active_jobs()
    out = {"connected": pipeline.CLOUD["connected"], "key": bool(cfg["comfy_api_key"]),
           "active_jobs": len(jobs) + (1 if gallery.JOB["state"] == "running" else 0) + (1 if photochar.JOB["state"] == "running" else 0), "dashboard": cfg.get("cloud_dashboard_url") or "https://cloud.comfy.org",
           "auto_open": bool(cfg.get("auto_open_cloud"))}
    if request.args.get("queue") and cfg["comfy_api_key"] and pipeline.CLOUD["connected"]:
        try:
            out["queue"] = ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]).queue()
        except Exception as e:
            out["queue_error"] = str(e)
    return jsonify(out)


@app.post("/api/cloud/connect")
def cloud_connect():
    cfg = load_cfg()
    try:
        ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]).check_key()
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    _set_connected(True)
    opened = open_dashboard(force=True)
    return jsonify({"ok": True, "opened": opened, "message": "Connected to Comfy Cloud."})


@app.post("/api/cloud/disconnect")
def cloud_disconnect():
    """Stop every reel, cancel its Comfy Cloud jobs and block new ones until Connect is pressed."""
    _set_connected(False)
    jobs = pipeline.active_jobs()
    for pid, _ in jobs:
        pipeline.CONTROL.setdefault(pid, {})["now"] = True
    for pid in list(pipeline.RUNNING):
        pipeline.CONTROL.setdefault(pid, {})["now"] = True
    cfg = load_cfg()
    cancelled = 0
    if cfg["comfy_api_key"]:
        try:
            ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]).cancel([j for _, j in jobs])
            cancelled = len(jobs)
        except Exception:
            pass
    return jsonify({"ok": True, "cancelled": cancelled,
                    "message": f"Disconnected. Cancelled {cancelled} running Comfy Cloud job(s). No new jobs will start until you click Connect."})


# ---------- settings ----------
@app.get("/api/settings")
def get_settings():
    cfg = load_cfg()
    return jsonify({"comfy_api_key": mask(cfg["comfy_api_key"]), "anthropic_api_key": mask(cfg["anthropic_api_key"]),
                    "anthropic_model": cfg["anthropic_model"], "use_ollama": cfg["use_ollama"],
                    "ollama_model": cfg["ollama_model"], "ollama_models": planner.ollama_models(cfg["ollama_url"]),
                    "comfy_ready": bool(cfg["comfy_api_key"]), "auto_open_cloud": bool(cfg.get("auto_open_cloud")),
                    "default_turbo": bool(cfg.get("default_turbo", True)),
                    "default_dynamic": bool(cfg.get("default_dynamic", True)), "whisper_model": cfg.get("whisper_model", "base")})


@app.post("/api/settings")
def set_settings():
    body = request.get_json(force=True)
    cfg = load_cfg()
    stored = dict(DEFAULT_CFG)
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE) as f:
            stored.update(json.load(f))
    for k in ("comfy_api_key", "anthropic_api_key"):
        v = (body.get(k) or "").strip()
        if v and "…" not in v:
            stored[k] = v
        if body.get(f"clear_{k}"):
            stored[k] = ""
    for k in ("anthropic_model", "ollama_model"):
        if k in body:
            stored[k] = (body[k] or "").strip()
    if "use_ollama" in body:
        stored["use_ollama"] = bool(body["use_ollama"])
    if "auto_open_cloud" in body:
        stored["auto_open_cloud"] = bool(body["auto_open_cloud"])
    if "default_turbo" in body:
        stored["default_turbo"] = bool(body["default_turbo"])
    if "default_dynamic" in body:
        stored["default_dynamic"] = bool(body["default_dynamic"])
    if body.get("whisper_model") in ("tiny", "base", "small", "medium"):
        stored["whisper_model"] = body["whisper_model"]
    save_cfg(stored)
    return get_settings()


@app.post("/api/settings/test")
def test_settings():
    cfg = load_cfg()
    try:
        ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]).check_key()
        return jsonify({"ok": True, "message": "Comfy Cloud key works."})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


# ---------- characters ----------
@app.get("/api/characters")
def list_chars():
    return jsonify(load_chars())


# ---------- character from a photo (consent required) ----------
@app.post("/api/characters/from-photo")
def char_from_photo():
    """multipart: photo, consent=yes, name, kind, style, outfit, notes, variations (1-3)."""
    if request.form.get("consent") not in ("yes", "true", "1", "on"):
        return jsonify({"error": "Please tick the box to confirm you are this person or have their permission "
                                 "(a parent or guardian for a child)."}), 400
    f = request.files.get("photo")
    if not f:
        return jsonify({"error": "Choose a photo first."}), 400
    cfg = load_cfg()
    if not cfg["comfy_api_key"]:
        return jsonify({"error": "Add your Comfy Cloud API key in Settings first."}), 400
    if not pipeline.CLOUD["connected"]:
        return jsonify({"error": "Comfy Cloud is disconnected. Click Connect first."}), 409
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".heic", ""):
        return jsonify({"error": "Use a PNG, JPG or WEBP photo."}), 400
    data = f.read()
    if len(data) > 25 * 1024 * 1024:
        return jsonify({"error": "That photo is too big (max 25 MB)."}), 400
    try:
        st = photochar.start(cfg, data, ext or ".jpg", request.form.get("name", "").strip()[:40],
                             request.form.get("kind", "girl-child"), request.form.get("style", "3D animated cartoon"),
                             request.form.get("outfit", "").strip()[:160], request.form.get("notes", "").strip()[:200],
                             int(request.form.get("variations", 2)), connected=lambda: pipeline.CLOUD["connected"])
    except (RuntimeError, ComfyError) as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": f"Could not read that photo ({str(e)[:120]}). iPhone HEIC photos: share or export them as JPG first."}), 400
    return jsonify(st)


@app.get("/api/characters/from-photo/status")
def char_from_photo_status():
    return jsonify(photochar.JOB)


@app.post("/api/characters")
def save_char():
    f = request.form
    with _lock:
        chars = load_chars()
        cid = f.get("id") or uuid.uuid4().hex[:8]
        ch = next((c for c in chars if c["id"] == cid), None)
        if ch is None:
            ch = {"id": cid}
            chars.append(ch)
        for k in ("name", "kind", "age", "style", "look", "outfit", "lora_id"):
            if k in f:
                ch[k] = f.get(k, "").strip()
        if ch.get("kind") in planner.CHILD_KINDS and ch.get("style", "").lower().startswith("photo"):
            ch["style"] = "3D animated cartoon"   # child characters stay cartoon
        img = request.files.get("image")
        lib_id = f.get("library_id")
        if lib_id and not (img and img.filename):
            item = library.get_image(lib_id)
            if item:
                ext = os.path.splitext(item["file"])[1].lower()
                shutil.copyfile(library.image_path(item), os.path.join(CHAR_DIR, f"{cid}{ext}"))
                ch["image"] = f"{cid}{ext}"
                ch["library_id"] = lib_id
        if img and img.filename:
            ext = os.path.splitext(img.filename)[1].lower() or ".png"
            name = f"{cid}{ext}"
            img.save(os.path.join(CHAR_DIR, name))
            ch["image"] = name
            try:  # keep a copy in the image library too
                with open(os.path.join(CHAR_DIR, name), "rb") as fh:
                    it = library.add_image(fh.read(), img.filename, title=f"{ch.get('name', '')} start image",
                                           tags=ch.get("name", ""))
                ch["library_id"] = it["id"] if it else None
            except Exception:
                pass
        save_chars(chars)
    return jsonify(ch)


@app.delete("/api/characters/<cid>")
def del_char(cid):
    with _lock:
        save_chars([c for c in load_chars() if c["id"] != cid])
    return jsonify({"ok": True})


@app.get("/api/characters/<cid>/image")
def char_image(cid):
    c = get_char(cid)
    if not c.get("image"):
        abort(404)
    return send_from_directory(CHAR_DIR, c["image"])


# ---------- LoRA library ----------
@app.get("/api/loras")
def list_loras():
    return jsonify(library.loras())


@app.post("/api/loras")
def save_lora():
    try:
        return jsonify(library.save_lora(request.get_json(force=True)))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/loras/<lid>")
def delete_lora(lid):
    library.delete_lora(lid)
    return jsonify({"ok": True})


# ---------- image library ----------
@app.get("/api/library")
def list_library():
    return jsonify(library.images())


@app.post("/api/library")
def add_library():
    added, errors = [], []
    for f in request.files.getlist("images"):
        try:
            added.append(library.add_image(f, f.filename, request.form.get("title", ""), request.form.get("tags", "")))
        except ValueError as e:
            errors.append(f"{f.filename}: {e}")
    return jsonify({"added": added, "errors": errors})


@app.patch("/api/library/<iid>")
def edit_library(iid):
    b = request.get_json(force=True)
    return jsonify(library.update_image(iid, b.get("title"), b.get("tags")))


@app.delete("/api/library/<iid>")
def del_library(iid):
    library.delete_image(iid)
    return jsonify({"ok": True})


@app.get("/api/library/<iid>/image")
def library_image(iid):
    it = library.get_image(iid)
    if not it:
        abort(404)
    return send_from_directory(library.LIB_DIR, it["file"])


@app.post("/api/library/from-project")
def library_from_project():
    b = request.get_json(force=True)
    p = _proj(b["project"])
    name = os.path.basename(b["file"])
    path = p.file(name)
    if not os.path.exists(path):
        abort(404)
    with open(path, "rb") as fh:
        it = library.add_image(fh.read(), name, title=f"{p.data.get('title', '')} · {name}",
                               tags=p.data.get("character", {}).get("name", ""))
    return jsonify(it)


# ---------- songs: beats, energy and lyrics ----------
SONG_DIR = os.path.join(DATA, "songs")
os.makedirs(SONG_DIR, exist_ok=True)
TRANSCRIBE = {}  # song id -> {"state": "running|done|error", "msg": "..."}


def song_meta(sid):
    d = os.path.join(SONG_DIR, re.sub(r"[^a-z0-9]", "", sid or ""))
    f = os.path.join(d, "meta.json")
    if not sid or not os.path.exists(f):
        return None
    with open(f) as fh:
        m = json.load(fh)
    m["path"] = os.path.join(d, m["file"])
    return m


def save_song_meta(m):
    m = {k: v for k, v in m.items() if k != "path"}
    with open(os.path.join(SONG_DIR, m["id"], "meta.json"), "w") as f:
        json.dump(m, f, indent=2)


def song_view(m, seconds=None):
    a = audio.analyze_cached(m["path"])
    out = {k: v for k, v in m.items() if k != "path"}
    out.update(bpm=a["bpm"], duration=a["duration"], energy=a["energy"], energy_step=a["energy_step"],
               downbeats=a["downbeats"], transcribe=TRANSCRIBE.get(m["id"]), whisper_ready=lyrics.whisper_ready())
    if seconds:
        out["best_start"] = audio.best_start(a, seconds)
    return out


@app.get("/api/songs")
def list_songs():
    out = []
    for sid in sorted(os.listdir(SONG_DIR), reverse=True):
        m = song_meta(sid)
        if m:
            out.append({"id": m["id"], "name": m["name"], "bpm": m.get("bpm"), "duration": m.get("duration"),
                        "has_lyrics": bool(m.get("lyrics"))})
    return jsonify(out)


@app.post("/api/songs")
def add_song():
    """Upload a song once; the app finds its tempo, beats and loud/quiet parts."""
    f = request.files.get("song")
    if not f:
        return jsonify({"error": "Choose a song file."}), 400
    data = f.read()
    import hashlib
    sid = hashlib.sha1(data).hexdigest()[:12]
    d = os.path.join(SONG_DIR, sid)
    os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(f.filename or "")[1].lower() or ".mp3"
    m = song_meta(sid)
    if not m:
        with open(os.path.join(d, "song" + ext), "wb") as fh:
            fh.write(data)
        m = {"id": sid, "name": f.filename or "song", "file": "song" + ext, "lyrics": [], "lyrics_text": "",
             "added": time.time()}
        save_song_meta(m)
        m = song_meta(sid)
    try:
        a = audio.analyze_cached(m["path"])
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    m.update(bpm=a["bpm"], duration=a["duration"])
    save_song_meta(m)
    return jsonify(song_view(m, request.args.get("seconds", type=float)))


@app.get("/api/songs/<sid>")
def get_song(sid):
    m = song_meta(sid) or abort(404)
    return jsonify(song_view(m, request.args.get("seconds", type=float)))


@app.get("/api/songs/<sid>/file")
def song_file(sid):
    m = song_meta(sid) or abort(404)
    return send_from_directory(os.path.dirname(m["path"]), m["file"])


@app.post("/api/songs/<sid>/lyrics")
def set_lyrics(sid):
    """Save typed/pasted lyrics. Lines may start with times like [0:12] or 0:12; otherwise they are spread evenly."""
    m = song_meta(sid) or abort(404)
    b = request.get_json(force=True)
    text = b.get("text", "")
    start, length = float(b.get("start", 0)), b.get("length")
    m["lyrics_text"] = text
    m["lyrics"] = lyrics.parse(text, start, float(length) if length else None)
    save_song_meta(m)
    return jsonify({"lines": len(m["lyrics"]), "lyrics": m["lyrics"]})


@app.post("/api/songs/<sid>/transcribe")
def transcribe_song(sid):
    """Write the lyrics with Whisper on this Mac (installs faster-whisper the first time)."""
    m = song_meta(sid) or abort(404)
    if (TRANSCRIBE.get(sid) or {}).get("state") == "running":
        return jsonify(TRANSCRIBE[sid])
    lang = (request.get_json(silent=True) or {}).get("language") or None
    st = TRANSCRIBE[sid] = {"state": "running", "msg": "Starting…"}

    def work():
        try:
            segs, found = lyrics.transcribe(m["path"], lang, load_cfg().get("whisper_model") or "base",
                                            log=lambda x: st.update(msg=x))
            mm = song_meta(sid)
            mm.update(lyrics=segs, lyrics_text=lyrics.to_text(segs), language=found)
            save_song_meta(mm)
            st.update(state="done", msg=f"Found {len(segs)} lyric lines. Check and fix them below.")
        except Exception as e:
            st.update(state="error", msg=f"Transcription failed: {e}")

    threading.Thread(target=work, daemon=True).start()
    return jsonify(st)


def music_for_plan(b, n):
    """Per-clip energy, beats and lyric lines for the planner."""
    mode = (b.get("music") or {}).get("mode") or b.get("music_mode")
    if mode == "song" and b.get("song_id"):
        m = song_meta(b["song_id"])
        if not m:
            return None, None
        a = audio.analyze_cached(m["path"])
        start = float(b.get("song_start") or 0)
        if b.get("snap", True):
            start = audio.snap_to_downbeat(a, start)
        wins = audio.windows(a, start, n)
        for w in wins:
            w["lyrics"] = lyrics.lines_between(m.get("lyrics"), w["t0"], w["t1"])
        return {"bpm": a["bpm"], "windows": wins, "start": start, "song": m["name"]}, start
    if mode == "original":
        a = audio.synthetic(120, n * 5)
        return {"bpm": 120, "windows": audio.windows(a, 0, n)}, 0
    return None, None


# ---------- credits ----------
@app.get("/api/credits")
def get_credits():
    """Credits left (live from Comfy Cloud billing when available) and what this app used."""
    cfg = load_cfg()
    out = {"rates": {"credits_per_usd": credits.CREDITS_PER_USD, "credits_per_gpu_second": credits.CREDITS_PER_GPU_SECOND},
           "app": credits.app_usage(), "balance": {"available": False}}
    if cfg["comfy_api_key"]:
        try:
            out["balance"] = credits.balance(ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]),
                                             max_age=0 if request.args.get("refresh") else 60)
        except Exception as e:
            out["balance"] = {"available": False, "error": str(e)[:200]}
    else:
        out["balance"]["error"] = "Add your Comfy Cloud API key in Settings."
    return jsonify(out)


@app.get("/api/credits/estimate")
def estimate_credits():
    a = request.args
    est = credits.estimate(int(a.get("clips", 3)), a.get("turbo", "1") != "0", a.get("dynamic", "1") != "0")
    bal = credits._cache.get("data") or {}
    if bal.get("available"):
        est["balance"] = bal["credits"]
        est["enough"] = bal["credits"] >= est["credits"]
    return jsonify(est)


# ---------- gallery (ready-made characters) ----------
@app.get("/api/gallery/presets")
def gallery_presets():
    have = gallery.existing_presets()
    items = [dict(p, have=p["id"] in have) for p in gallery.presets()]
    groups = {}
    for p in items:
        g = groups.setdefault(p["kind"], {"kind": p["kind"], "label": p["group"], "total": 0, "have": 0})
        g["total"] += 1
        g["have"] += p["have"]
    per = credits.CREDITS_PER_GPU_SECOND * 25
    return jsonify({"presets": items, "groups": list(groups.values()), "credits_per_image": round(per, 1)})


@app.post("/api/gallery/build")
def gallery_build():
    """Generate gallery images on Comfy Cloud: {"kinds": ["girl-child", ...]} or {"ids": [...]}, "again": false."""
    cfg = load_cfg()
    if not cfg["comfy_api_key"]:
        return jsonify({"error": "Add your Comfy Cloud API key in Settings first."}), 400
    if not pipeline.CLOUD["connected"]:
        return jsonify({"error": "Comfy Cloud is disconnected. Click Connect first."}), 409
    b = request.get_json(silent=True) or {}
    try:
        st = gallery.start(cfg, ids=b.get("ids"), kinds=b.get("kinds"), again=bool(b.get("again")),
                           connected=lambda: pipeline.CLOUD["connected"])
    except (RuntimeError, ComfyError) as e:
        return jsonify({"error": str(e)}), 409
    open_dashboard()
    return jsonify(st)


@app.get("/api/gallery/status")
def gallery_status():
    return jsonify(gallery.JOB)


@app.post("/api/gallery/stop")
def gallery_stop():
    gallery.CONTROL["stop"] = True
    return jsonify({"ok": True, "message": "Stopping after the current image."})


# ---------- story mode ----------
def _cast(ids):
    cast = [resolve_char(get_char(i)) for i in (ids or [])][:6]
    if not cast:
        abort(400, "Pick at least one character.")
    return cast


def _story_view(st):
    for sh in st.get("shots", st.get("clips", [])):
        if "lines" not in sh:
            sh["lines"] = story.clean_lines(sh, [c.get("name") for c in st.get("cast", [])])
        sh["warnings"] = story.prop_warnings(sh)
    if st.get("cast") and st.get("story_settings"):
        ss = st["story_settings"]
        st["voice_cast"] = tts.voice_cast(st["cast"], ss.get("language", "en"), ss.get("voice") or "en-US-AnaNeural", ss.get("voices"))
    return st


def story_estimate(shots, turbo=True, dynamic=True):
    n = len(shots)
    kf = sum(1 for s in shots if s.get("mode") in ("i2v", "flf")) + sum(1 for s in shots if s.get("mode") == "flf")
    img = round(kf * credits.CREDITS_PER_GPU_SECOND * 35)
    vid = credits.estimate(n, turbo, dynamic)
    return {"keyframes": kf, "keyframe_credits": img, "video_credits": vid["credits"], "credits": img + vid["credits"],
            "usd": round((img + vid["credits"]) / credits.CREDITS_PER_USD, 2), "minutes": vid["minutes"] + round(kf * 0.6, 1)}


@app.get("/api/story/options")
def story_options():
    return jsonify({"voices": tts.voices(), "sfx": ["none"] + sfx.names(), "shot_sizes": story.SHOT_SIZES,
                    "modes": story.MODES, "good_props": story.GOOD_PROPS, "pitches": tts.PITCHES, "max_shots": story.MAX_SHOTS})


@app.get("/api/story/voices")
def story_all_voices():
    """?all=1 fetches every Hindi/English/multilingual Edge voice (needs internet the first time)."""
    if request.args.get("all"):
        try:
            return jsonify({"voices": tts.all_voices()})
        except Exception as e:
            return jsonify({"voices": tts.VOICES, "error": f"Could not load the full voice list ({str(e)[:120]})."})
    return jsonify({"voices": tts.VOICES})


PLAN_LOG = []


@app.get("/api/story/writers")
def story_writers():
    return jsonify({"writers": story.writers(load_cfg())})


@app.get("/api/story/progress")
def story_progress():
    return jsonify({"log": PLAN_LOG[-12:]})


@app.post("/api/story/brainstorm")
def story_brainstorm():
    """{character_ids, idea, brief, language, writer, mode: ideas|expand}"""
    b = request.get_json(force=True)
    cast = _cast(b.get("character_ids"))
    PLAN_LOG.clear()
    out, by = story.brainstorm(load_cfg(), cast, b.get("idea", ""), b.get("brief"), b.get("language", "en"),
                               b.get("writer", "auto"), b.get("mode", "expand"), log=PLAN_LOG.append)
    return jsonify({"result": out, "writer": by, "log": PLAN_LOG[-6:]})


@app.post("/api/story/voice-cast")
def story_voice_cast():
    """Default voices for a cast: {character_ids, language, voice (narrator), voices (saved choices)}."""
    b = request.get_json(force=True)
    cast = _cast(b.get("character_ids"))
    return jsonify(tts.voice_cast(cast, b.get("language", "en"), b.get("voice") or "en-US-AnaNeural", b.get("voices")))


VOICE_TEST = {"hi": "नमस्ते! मेरी आवाज़ ऐसी सुनाई देती है।", "en": "Hello! This is how my voice sounds."}


@app.post("/api/story/voice-test")
def story_voice_test():
    """Speak a short sample with a voice and pitch. Returns the audio file."""
    b = request.get_json(force=True)
    v = tts.find_voice(b.get("voice"))
    lang = b.get("language") if b.get("language") in VOICE_TEST else ("hi" if v["lang"] == "hi" else "en")
    text = (b.get("text") or VOICE_TEST[lang])[:200]
    d = os.path.join(DATA, "voice_tests")
    os.makedirs(d, exist_ok=True)
    import hashlib
    f = os.path.join(d, hashlib.sha1(f"{v['id']}|{b.get('pitch', 0)}|{b.get('rate', 0)}|{text}".encode()).hexdigest()[:12] + ".wav")
    if not os.path.exists(f):
        try:
            tts.synth(text, v["id"], f, int(b.get("rate", 0)), pitch_hz=int(b.get("pitch", 0)))
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return send_from_directory(d, os.path.basename(f), mimetype="audio/wav")


@app.get("/api/story/sfx/<name>")
def story_sfx(name):
    f = sfx.path(name) or abort(404)
    return send_from_directory(os.path.dirname(f), os.path.basename(f))


@app.post("/api/story/plan")
def story_plan():
    """{character_ids:[...], idea, shots, setting, tone, language: hi|en|none} -> a story with a shot list."""
    b = request.get_json(force=True)
    cast = _cast(b.get("character_ids"))
    PLAN_LOG.clear()
    logs = PLAN_LOG
    st, by = story.plan_story(load_cfg(), cast, b.get("idea", ""), int(b.get("shots", 6)), b.get("setting", ""),
                              b.get("tone", ""), b.get("language", "en"), log=logs.append,
                              writer=b.get("writer", "auto"), brief=b.get("brief"))
    _story_view(st)
    return jsonify({"story": st, "planner": by, "log": logs, "estimate": story_estimate(st["shots"])})


@app.post("/api/story/estimate")
def story_est():
    b = request.get_json(force=True)
    return jsonify(story_estimate(b.get("shots", []), b.get("turbo", True), b.get("dynamic", True)))


SHOT_FIELDS = ("title", "characters", "shot", "camera", "setting", "props", "mode", "start_frame", "end_frame", "beats",
               "eyes", "hands", "emotion", "narration", "lines", "sfx", "scene")


@app.post("/api/story")
def create_story():
    b = request.get_json(force=True)
    cast = _cast(b.get("character_ids"))
    st = b.get("story") or {}
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    p = Project(pid)
    os.makedirs(p.dir, exist_ok=True)
    from PIL import Image
    for c in cast:
        if c.get("image") and os.path.exists(os.path.join(CHAR_DIR, c["image"])):
            Image.open(os.path.join(CHAR_DIR, c["image"])).convert("RGB").save(p.file(f"cast_{c['id']}.png"))
    shots = []
    for i, sh in enumerate(st.get("shots", [])):
        x = {k: sh.get(k) for k in SHOT_FIELDS}
        x.update(id=f"s{i + 1}", state="waiting")
        if i == 0 and x.get("mode") == "continue":
            x["mode"] = "i2v"
        shots.append(x)
    cfg = load_cfg()
    ss = dict(b.get("story_settings") or {})
    if ss.get("music") == "song" and ss.get("song_id"):
        sm = song_meta(ss["song_id"])
        if sm:
            shutil.copyfile(sm["path"], p.file(sm["file"]))
            ss["song_file"] = sm["file"]
    p.data = {"id": pid, "type": "story", "title": st.get("title") or "My short", "logline": st.get("logline", ""),
              "created": time.time(), "status": "ready", "stage": "plan", "cast": cast,
              "character": cast[0], "clips": shots, "log": [],
              "settings": {"width": 720, "height": 1280, "fps": 16, "seconds_per_clip": 5,
                           "turbo": bool(b.get("turbo", cfg.get("default_turbo", True))),
                           "dynamic": bool(b.get("dynamic", cfg.get("default_dynamic", True))),
                           "upscale": bool(b.get("upscale", True))},
              "story_settings": {"language": ss.get("language", "en"), "voice": ss.get("voice", "en-US-AnaNeural"),
                                 "subtitles": bool(ss.get("subtitles", True)), "music": ss.get("music", "original"),
                                 "song_file": ss.get("song_file"), "song_start": float(ss.get("song_start", 0) or 0),
                                 "music_volume": float(ss.get("music_volume", 0.35)), "sfx_volume": float(ss.get("sfx_volume", 0.6)),
                                 "voice_rate": int(ss.get("voice_rate", 0)), "voices": ss.get("voices") or {}}}
    p.save()
    return jsonify(_story_view(p.data))


def _story(pid):
    p = _proj(pid)
    if p.data.get("type") != "story":
        abort(400, "Not a story project.")
    return p


def _busy(p):
    return p.id in pipeline.RUNNING and pipeline.RUNNING[p.id].is_alive()


@app.post("/api/story/<pid>/shots")
def story_shots(pid):
    """Save edits to the shot list (and title/settings)."""
    p = _story(pid)
    if _busy(p):
        return jsonify({"error": "The story is being made right now. Stop it first."}), 409
    b = request.get_json(force=True)
    for i, sh in enumerate(b.get("shots", [])):
        if i < len(p.data["clips"]):
            for k in SHOT_FIELDS:
                if k in sh:
                    p.data["clips"][i][k] = sh[k]
    if p.data["clips"] and p.data["clips"][0].get("mode") == "continue":
        p.data["clips"][0]["mode"] = "i2v"
    for k in ("title", "logline"):
        if k in b:
            p.data[k] = b[k]
    if "settings" in b:
        p.data["settings"].update({k: bool(v) for k, v in b["settings"].items() if k in ("turbo", "dynamic", "upscale")})
    if "story_settings" in b:
        p.data["story_settings"].update({k: v for k, v in b["story_settings"].items()
                                         if k in ("language", "voice", "subtitles", "music", "song_start", "music_volume", "sfx_volume", "voice_rate", "voices")})
    p.save()
    return jsonify(_story_view(p.data))


@app.post("/api/story/<pid>/keyframes")
def story_keyframes(pid):
    """{only:[shot ids], which: both|start|end} -> make keyframes on Comfy Cloud."""
    p = _story(pid)
    cfg = load_cfg()
    if not cfg["comfy_api_key"]:
        return jsonify({"error": "Add your Comfy Cloud API key in Settings first."}), 400
    b = request.get_json(silent=True) or {}
    try:
        story_pipeline.start_keyframes(p, cfg, b.get("only"), b.get("which", "both"))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    open_dashboard()
    return jsonify(p.data)


@app.post("/api/story/<pid>/keyframe/<sid>/<kind>")
def story_keyframe_upload(pid, sid, kind):
    """Use your own picture as a keyframe (multipart 'image'), or {"approve": true}."""
    p = _story(pid)
    if kind not in ("start", "end"):
        abort(400)
    sh = next((s for s in p.data["clips"] if s["id"] == sid), None) or abort(404)
    f = request.files.get("image")
    if f:
        from PIL import Image
        name = f"kf_{sid}_{kind}_{int(time.time())}.png"
        Image.open(f.stream).convert("RGB").save(p.file(name))
        sh[f"kf_{kind}"], sh[f"kf_{kind}_engine"] = name, "your upload"
    if (request.get_json(silent=True) or {}).get("approve") or request.form.get("approve"):
        sh[f"kf_{kind}_ok"] = True
    p.save()
    return jsonify(p.data)


@app.post("/api/story/<pid>/video")
def story_video(pid):
    """{only:[ids]} or {from_shot: n}; turbo check like reels (allow_slow)."""
    p = _story(pid)
    cfg = load_cfg()
    if not cfg["comfy_api_key"]:
        return jsonify({"error": "Add your Comfy Cloud API key in Settings first."}), 400
    b = request.get_json(silent=True) or {}
    if not p.data["settings"].get("turbo", True) and not b.get("allow_slow"):
        return jsonify({"error": "Turbo mode is off for this story.", "turbo_off": True, "turbo": turbo_status(p)}), 409
    try:
        story_pipeline.start_video(p, cfg, b.get("only"), int(b.get("from_shot", 0)))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    open_dashboard()
    return jsonify(p.data)


@app.post("/api/story/<pid>/assemble")
def story_assemble(pid):
    """Re-make the final video with new voice / subtitles / music / sound settings. No cloud credits."""
    p = _story(pid)
    if _busy(p):
        return jsonify({"error": "The story is being made right now."}), 409
    if not all(s.get("video") for s in p.data["clips"]):
        return jsonify({"error": "Make all the shot videos first."}), 409
    b = request.get_json(silent=True) or {}
    p.data["story_settings"].update({k: v for k, v in b.items()
                                     if k in ("language", "voice", "subtitles", "music", "song_start", "music_volume", "sfx_volume", "voice_rate", "voices")})
    if b.get("music") == "song" and b.get("song_id"):
        sm = song_meta(b["song_id"])
        if sm:
            shutil.copyfile(sm["path"], p.file(sm["file"]))
            p.data["story_settings"]["song_file"] = sm["file"]
    p.data["status"] = "running"
    p.save()

    def work():
        try:
            story_pipeline.assemble(p)
            p.data["status"] = "done"
        except Exception as e:
            p.data["status"] = "error"
            p.data["error"] = str(e)
            p.log(f"Assembly failed: {e}")
        p.save()

    t = threading.Thread(target=work, daemon=True)
    pipeline.RUNNING[p.id] = t
    t.start()
    return jsonify(p.data)


# ---------- planning ----------
@app.post("/api/plan")
def plan():
    b = request.get_json(force=True)
    ch = resolve_char(get_char(b["character_id"]))
    logs = []
    seconds = int(b.get("seconds", 15))
    music, start = music_for_plan(b, max(1, int(round(seconds / 5))))
    if music:
        logs.append(f"Music: {music['bpm']} BPM; energy per clip: {', '.join(w['energy'] for w in music['windows'])}.")
    clips, by = planner.make_plan(load_cfg(), ch, seconds, b.get("setting", ""), b.get("mood", ""),
                                  log=logs.append, music=music)
    return jsonify({"clips": clips, "planner": by, "log": logs, "music": music, "song_start": start})


# ---------- projects ----------
def _proj(pid):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        abort(400)
    p = Project(pid)
    if not p.data:
        abort(404, "Reel not found")
    return p


@app.get("/api/projects")
def list_projects():
    out = []
    for pid in sorted(os.listdir(PROJ_DIR), reverse=True):
        p = Project(pid)
        if p.data:
            out.append({"id": pid, "title": p.data.get("title"), "status": p.data.get("status"),
                        "created": p.data.get("created"), "final": p.data.get("final"),
                        "character": (p.data.get("character") or {}).get("name"), "type": p.data.get("type", "dance")})
    return jsonify(out[:50])


# ---------- Turbo mode (lightx2v 4-step LoRA) ----------
TURBO_LORAS = ("wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
               "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors")


def turbo_check():
    """Can the workflows actually run in Turbo mode? Looks for the switch and the LoRA files."""
    problems = []
    try:
        i2v = pipeline.load_wf("wan22_i2v_api.json")
        if not any(n.get("class_type") == "PrimitiveBoolean" for n in i2v.values()):
            problems.append("The image-to-video workflow has no Turbo on/off switch (PrimitiveBoolean node).")
        names = {n.get("inputs", {}).get("lora_name") for n in i2v.values()}
        for lora in TURBO_LORAS:
            if lora not in names:
                problems.append(f"The image-to-video workflow does not load {lora}.")
    except Exception as e:
        problems.append(f"Could not read workflows/wan22_i2v_api.json: {e}")
    if not os.path.exists(os.path.join(pipeline.WF_DIR, "wan22_flf2v_api.json")):
        problems.append("workflows/wan22_flf2v_api.json is missing.")
    return {"available": not problems, "problems": problems}


def turbo_status(p=None):
    cfg = load_cfg()
    out = {"default": bool(cfg.get("default_turbo", True)), **turbo_check()}
    if p is not None:
        out["project"] = bool(p.data.get("settings", {}).get("turbo", True))
    out["enabled"] = out.get("project", out["default"])
    out["message"] = ("Turbo is ON: about 1–2 min per clip, fewer credits." if out["enabled"] else
                      "Turbo is OFF: about 8–12 min per clip and several times the credits.")
    return out


@app.get("/api/turbo")
def get_turbo():
    """Is Turbo on? ?project=<id> also reports that reel's own setting."""
    pid = request.args.get("project")
    return jsonify(turbo_status(_proj(pid) if pid else None))


@app.post("/api/turbo")
def set_turbo():
    """Turn Turbo on/off. {"enabled": true, "project": "<id>"} sets that reel too; the default is always saved."""
    b = request.get_json(force=True)
    on = bool(b.get("enabled", True))
    stored = dict(DEFAULT_CFG)
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE) as f:
            stored.update(json.load(f))
    stored["default_turbo"] = on
    save_cfg(stored)
    p = None
    if b.get("project"):
        p = _proj(b["project"])
        if p.data.get("status") == "running":
            return jsonify({"error": "This reel is being made right now. Stop it first, then change Turbo."}), 409
        p.data.setdefault("settings", {})["turbo"] = on
        p.log(f"Turbo turned {'ON' if on else 'OFF'}.")
    return jsonify(turbo_status(p))


@app.post("/api/projects")
def create_project():
    b = request.get_json(force=True)
    ch = resolve_char(get_char(b["character_id"]))
    if not ch.get("image"):
        return jsonify({"error": "This character has no start image yet. Add one first."}), 400
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    p = Project(pid)
    os.makedirs(p.dir, exist_ok=True)
    src = os.path.join(CHAR_DIR, ch["image"])
    if b.get("start_library_id"):
        item = library.get_image(b["start_library_id"])
        if item:
            src = library.image_path(item)
    if src.lower().endswith(".png"):
        shutil.copyfile(src, p.file("start.png"))
    else:
        from PIL import Image
        Image.open(src).convert("RGB").save(p.file("start.png"))
    clips = [{"motion": c["motion"], "end_pose": c.get("end_pose", ""), "reanchor": bool(c.get("reanchor")),
              "lock_end": bool(c.get("lock_end")), "state": "waiting"} for c in b["clips"]]
    p.data = {"id": pid, "title": b.get("title") or f"{ch['name']} · {len(clips) * 5}s",
              "created": time.time(), "status": "ready", "character": ch, "clips": clips,
              "setting": b.get("setting"), "mood": b.get("mood"),
              "settings": {"width": 720, "height": 1280, "fps": 16, "seconds_per_clip": 5,
                           "turbo": bool(b.get("turbo", load_cfg().get("default_turbo", True))), "upscale": bool(b.get("upscale", True)),
                           "dynamic": bool(b.get("dynamic", load_cfg().get("default_dynamic", True))),
                           "beat_sync": bool(b.get("beat_sync", True))},
              "music": b.get("music") or {"mode": "none"}, "log": []}
    for i, c in enumerate(clips):
        src_c = b["clips"][i]
        for k in ("energy", "lyrics", "gestures"):
            if k in src_c:
                c[k] = src_c[k]
    mu = p.data["music"]
    if mu.get("mode") == "song" and mu.get("song_id"):
        sm = song_meta(mu["song_id"])
        if sm:
            shutil.copyfile(sm["path"], p.file(sm["file"]))
            cache = sm["path"] + ".analysis.json"
            if os.path.exists(cache):
                shutil.copyfile(cache, p.file(sm["file"]) + ".analysis.json")
            mu.update(file=sm["file"], original_name=sm["name"])
    p.save()
    return jsonify(p.data)


@app.get("/api/projects/<pid>")
def get_project(pid):
    d = _proj(pid).data
    return jsonify(_story_view(d) if d.get("type") == "story" else d)


@app.post("/api/projects/<pid>/song")
def upload_song(pid):
    p = _proj(pid)
    f = request.files.get("song")
    if not f:
        abort(400)
    name = "song" + (os.path.splitext(f.filename)[1].lower() or ".mp3")
    f.save(p.file(name))
    m = p.data.setdefault("music", {})
    m.update(mode="song", file=name, original_name=f.filename)
    p.save()
    return jsonify(p.data)


@app.post("/api/projects/<pid>/music")
def set_music(pid):
    p = _proj(pid)
    b = request.get_json(force=True)
    m = p.data.setdefault("music", {})
    m.update({k: b[k] for k in ("mode", "start", "volume", "fade_in", "fade_out", "bpm", "style", "snap") if k in b})
    p.save()
    return jsonify(p.data)


@app.post("/api/projects/<pid>/clips")
def edit_clips(pid):
    p = _proj(pid)
    b = request.get_json(force=True)
    for i, c in enumerate(b.get("clips", [])):
        if i < len(p.data["clips"]):
            for k in ("motion", "reanchor", "lock_end"):
                if k in c:
                    p.data["clips"][i][k] = c[k]
    p.save()
    return jsonify(p.data)


@app.post("/api/projects/<pid>/start")
def start_project(pid):
    p = _proj(pid)
    b = request.get_json(silent=True) or {}
    cfg = load_cfg()
    if not cfg["comfy_api_key"]:
        return jsonify({"error": "Add your Comfy Cloud API key in Settings first."}), 400
    if not p.data.get("settings", {}).get("turbo", True) and not b.get("allow_slow"):
        st = turbo_status(p)
        return jsonify({"error": "Turbo mode is off for this reel. Turn it on, or confirm you want the slow mode.",
                        "turbo_off": True, "turbo": st}), 409
    frm = int(b.get("from_clip", 0))
    for i, c in enumerate(p.data["clips"]):
        if i >= frm:
            c["state"] = "waiting"
    try:
        pipeline.start(p, cfg, from_clip=frm)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    open_dashboard()
    return jsonify(p.data)


@app.post("/api/projects/<pid>/stop")
def stop_project(pid):
    p = _proj(pid)
    now = bool((request.get_json(silent=True) or {}).get("now"))
    ctl = pipeline.CONTROL.setdefault(pid, {})
    ctl["stop"] = True
    if now:
        ctl["now"] = True
        cfg = load_cfg()
        jobs = [j for q, j in pipeline.active_jobs() if q == pid]
        if jobs and cfg["comfy_api_key"]:
            try:
                ComfyCloud(cfg["comfy_api_key"], cfg["comfy_base_url"]).cancel(jobs)
            except Exception:
                pass
        return jsonify({"ok": True, "message": "Cancelling the current Comfy Cloud job now."})
    return jsonify({"ok": True, "message": "Stopping after the current clip finishes."})


@app.post("/api/projects/<pid>/remix")
def remix(pid):
    p = _proj(pid)
    if not all(c.get("video") for c in p.data["clips"]):
        return jsonify({"error": "Make all the clips first."}), 400

    def job():
        try:
            p.data["status"] = "running"; p.save()
            pipeline.finish(p)
            p.data["status"] = "done"
        except Exception as e:
            p.data["status"], p.data["error"] = "error", str(e)
            p.log(f"Stopped: {e}")
        p.save()
    threading.Thread(target=job, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/projects/<pid>/file/<path:name>")
def project_file(pid, name):
    p = _proj(pid)
    return send_from_directory(p.dir, name)


@app.post("/api/projects/<pid>/reveal")
def reveal(pid):
    p = _proj(pid)
    target = os.path.join(ROOT, p.data.get("output_copy") or "output")
    if platform.system() == "Darwin":
        subprocess.Popen(["open", "-R", target])
    return jsonify({"ok": True, "path": target})


@app.get("/api/status")
def status():
    cfg = load_cfg()
    return jsonify({"comfy": bool(cfg["comfy_api_key"]), "claude": bool(cfg["anthropic_api_key"]),
                    "ollama": planner.ollama_models(cfg["ollama_url"]) if cfg["use_ollama"] else [],
                    "workflows": sorted(os.listdir(os.path.join(ROOT, "workflows")))})


pipeline.CLOUD["connected"] = bool(load_cfg().get("cloud_connected", True))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    print(f"\n  Reel Studio is running →  http://127.0.0.1:{port}\n  (Press Ctrl+C here to stop it.)\n", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
