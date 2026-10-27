"""Get a song from a link: a direct audio file (…/song.mp3) or a page yt-dlp understands (YouTube, SoundCloud, …).

Everything is converted to MP3 with the app's own ffmpeg, so the rest of the app sees a normal song file.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse

import requests

from .video import FFMPEG

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm", ".mp4")
MAX_BYTES = 200 * 1024 * 1024
MAX_SECONDS = 20 * 60


def _clean_name(s):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s or "").strip()
    return (s[:120] or "song")


def _ytdlp_cmd():
    return [sys.executable, "-m", "yt_dlp"]


def ytdlp_ready():
    """yt-dlp runs as its own process, so installing or upgrading it never disturbs the running app."""
    try:
        return subprocess.run(_ytdlp_cmd() + ["--version"], capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def install_ytdlp(log=print):
    log("Installing yt-dlp into the app (one time, a few seconds)…")
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--index-url",
           "https://pypi.org/simple", "yt-dlp[default]"]
    env = dict(os.environ, PIP_CONFIG_FILE=os.devnull)
    for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS"):
        env.pop(k, None)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Installing yt-dlp took too long. Check the internet connection and try again.") from None
    if p.returncode != 0:
        raise RuntimeError("Could not install yt-dlp: " + (p.stderr or p.stdout)[-600:])
    import importlib
    importlib.invalidate_caches()


def _mb(n):
    return f"{(n or 0) / 1048576:.1f} MB"


def js_runtimes():
    """JavaScript programs yt-dlp can use to pass YouTube's checks. Without one, YouTube downloads are very slow."""
    found = {}
    extra = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.deno/bin"), os.path.expanduser("~/.bun/bin")]
    for name in ("deno", "node", "bun"):
        path = shutil.which(name) or next((os.path.join(d, name) for d in extra
                                           if os.access(os.path.join(d, name), os.X_OK)), None)
        if path:
            found[name] = {"path": path}
    return found


def _direct(url, tmp, log):
    """Download a plain audio file. Returns (path, name) or None if the link is not an audio file."""
    path_ext = os.path.splitext(urlparse(url).path)[1].lower()
    r = requests.get(url, stream=True, timeout=30, headers={"User-Agent": "Mozilla/5.0 ReelStudio"})
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not (ctype.startswith("audio/") or path_ext in AUDIO_EXT and not ctype.startswith("text/")):
        r.close()
        return None
    name = unquote(os.path.basename(urlparse(url).path)) or "song"
    cd = r.headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    if m:
        name = unquote(m.group(1))
    ext = path_ext if path_ext in AUDIO_EXT else ".audio"
    out = os.path.join(tmp, "download" + ext)
    log("Downloading the audio file…")
    got, total = 0, int(r.headers.get("content-length") or 0)
    with open(out, "wb") as fh:
        for chunk in r.iter_content(1 << 16):
            got += len(chunk)
            if got > MAX_BYTES:
                raise RuntimeError("That file is bigger than 200 MB.")
            fh.write(chunk)
            log(f"Downloading… {got * 100 // total}% of {_mb(total)}" if total else f"Downloading… {_mb(got)}")
    return out, os.path.splitext(name)[0]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


STALL_SECONDS = 60


def _ytdlp(url, tmp, log):
    if not ytdlp_ready():
        install_ytdlp(log)
    runtimes = js_runtimes()
    yt = re.search(r"(youtube\.com|youtu\.be)", url, re.I)
    slow_hint = "" if runtimes or not yt else " (slow: install Deno with 'brew install deno' for fast YouTube downloads)"
    log("Opening the link…" + slow_hint)
    info_file = os.path.join(tmp, "info.json")
    cmd = _ytdlp_cmd() + [
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "-o", os.path.join(tmp, "download.%(ext)s"),
        "--force-ipv4",  # some home networks advertise IPv6 that doesn't work; Python then hangs for minutes
        "--no-playlist", "--no-simulate", "--newline", "--progress", "--no-colors", "--no-warnings",
        "--socket-timeout", "15", "--retries", "3", "--fragment-retries", "3", "--extractor-retries", "1",
        "-N", "4", "--http-chunk-size", "10M", "--max-filesize", "200M",
        "--match-filters", f"duration <? {MAX_SECONDS}",
        "--progress-template", "download:PROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
                               "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s",
        "--print-to-file", "after_move:%(.{title,track,artist,uploader})j", info_file,
    ]
    for name, cfg in runtimes.items():
        cmd += ["--js-runtimes", f"{name}:{cfg['path']}"]
    cmd += ["--", url]

    import queue
    import threading
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    lines = queue.Queue()
    threading.Thread(target=lambda: ([lines.put(x) for x in p.stdout], lines.put(None)), daemon=True).start()
    errors, tail = [], []
    while True:
        try:
            line = lines.get(timeout=STALL_SECONDS)
        except queue.Empty:
            p.kill()
            last = (tail or ["(no output from yt-dlp)"])[-1]
            raise _LinkError(f"Nothing happened for {STALL_SECONDS} seconds, so the download was cancelled. "
                             f"Last step: {last[:200]}" + slow_hint, final=True)
        if line is None:
            break
        line = line.strip()
        if line.startswith("PROG "):
            parts = (line.split() + [None] * 6)[1:6]
            done, total, est, speed, eta = (_num(x) for x in parts)
            total = total or est
            pct = f"{int(done * 100 // total)}% of {_mb(total)}" if done and total else _mb(done)
            sp = f" · {_mb(speed)}/s" if speed else ""
            et = f" · {int(eta) // 60}:{int(eta) % 60:02d} left" if eta is not None else ""
            log(f"Downloading… {pct}{sp}{et}{slow_hint}")
        elif line.startswith("ERROR:"):
            errors.append(line[6:].strip())
        elif line:
            tail = (tail + [line])[-5:]
            if line.startswith("[") and not re.search(r"Destination|Writing|Deleting|\[info\]|\[download\]", line):
                log(re.sub(r"^\[[^\]]+\]\s*", "", line)[:120] + slow_hint)
    p.wait()
    files = [f for f in glob.glob(os.path.join(tmp, "download.*")) if not f.endswith((".part", ".ytdl"))]
    if p.returncode != 0 or not files:
        msg = errors[-1] if errors else (" ".join(tail) or "no audio was found")
        if not files and not errors and "does not pass filter" in " ".join(tail):
            msg = "the song is longer than 20 minutes"
        raise _LinkError(f"Could not get audio from that link: {msg[:400]}", _final(msg))
    info = {}
    try:
        with open(info_file, encoding="utf-8") as fh:
            info = json.loads(fh.read().strip().splitlines()[-1])
    except Exception:
        pass
    title = info.get("track") or info.get("title") or "song"
    artist = info.get("artist") or info.get("uploader") or ""
    if artist and info.get("track") and artist.lower() not in title.lower():
        title = f"{artist} - {title}"
    return files[0], title


class _LinkError(RuntimeError):
    def __init__(self, msg, final=False):
        super().__init__(msg)
        self.final = final


def _final(msg):
    """Errors a newer yt-dlp would not fix: no point upgrading and trying again."""
    return bool(re.search(r"private video|video unavailable|been removed|copyright|confirm your age|age-restricted|"
                           r"members-only|not available in your country|does not pass filter|larger than|"
                           r"Unsupported URL|HTTP Error 404|Failed to establish|Connection refused|timed out|"
                           r"Name or service not known|nodename nor servname|Temporary failure in name", msg, re.I))


def fetch(url, log=print):
    """Return (mp3_bytes, display_name) for a song link."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Paste a full link that starts with http:// or https://")
    with tempfile.TemporaryDirectory(prefix="reelsong_") as tmp:
        log("Checking the link…")
        # Everything (direct MP3 links too) goes through yt-dlp in its own process: it forces IPv4, reports
        # progress and can be cancelled if it stalls, so a bad network can never freeze the app.
        had = ytdlp_ready()
        try:
            src, name = _ytdlp(url, tmp, log)
        except _LinkError as e:
            if e.final or not had:
                raise
            # Sites like YouTube change often; a newer yt-dlp usually fixes it. Upgrade once and retry.
            for f in glob.glob(os.path.join(tmp, "download.*")):
                os.remove(f)
            log("Updating yt-dlp and trying again…")
            try:
                install_ytdlp(log)
            except RuntimeError:
                raise e from None
            src, name = _ytdlp(url, tmp, log)
        log("Converting to MP3…")
        out = os.path.join(tmp, "song.mp3")
        p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", src, "-vn", "-ac", "2", "-ar", "44100",
                            "-b:a", "192k", "-t", str(MAX_SECONDS), out], capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
            raise RuntimeError("That link did not contain playable audio. " + (p.stderr or "")[-300:])
        with open(out, "rb") as fh:
            return fh.read(), _clean_name(name) + ".mp3"
