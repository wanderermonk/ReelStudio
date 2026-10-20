"""Song lyrics with timings: typed/pasted by you, or transcribed on this Mac with Whisper (faster-whisper).

Lyrics never go into the video prompt as text (the model would try to draw letters). They are only used to
choose gestures, e.g. a line about clapping -> the character claps.
"""
import os
import re
import subprocess
import sys

TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
PLAIN_TIME = re.compile(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*[-–—|]?\s*(.*)$")


def parse(text, start=0.0, length=None):
    """Returns [{start, end, text}] in song time.

    Accepts LRC lines like "[0:12.5] line", plain "0:12 line", or lines with no times
    (then they are spread evenly over start..start+length).
    """
    timed, untimed = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\[[a-z]+:.*\]", line, re.I):  # LRC tags like [ar:...]
            continue
        stamps = TIME.findall(line)
        if stamps:
            words = TIME.sub("", line).strip()
            for m, s in stamps:
                timed.append([int(m) * 60 + float(s), words])
            continue
        m = PLAIN_TIME.match(line)
        if m and m.group(3):
            timed.append([int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()])
        else:
            untimed.append(line)
    out = []
    if timed:
        timed.sort()
        for i, (t, w) in enumerate(timed):
            end = timed[i + 1][0] if i + 1 < len(timed) else t + 4
            if w:
                out.append({"start": round(t, 2), "end": round(min(end, t + 8), 2), "text": w})
    elif untimed:
        length = length or 4.0 * len(untimed)
        step = length / len(untimed)
        for i, w in enumerate(untimed):
            out.append({"start": round(start + i * step, 2), "end": round(start + (i + 1) * step, 2), "text": w})
    return out


def to_text(segments):
    out = []
    for s in segments:
        m, sec = divmod(float(s["start"]), 60)
        out.append(f"[{int(m)}:{sec:05.2f}] {s['text']}")
    return "\n".join(out)


def lines_between(segments, t0, t1):
    """Lines sung in this clip: the middle of the line falls inside it, or most of the clip is covered by it."""
    out = []
    for s in segments or []:
        mid = (s["start"] + s["end"]) / 2
        overlap = min(s["end"], t1) - max(s["start"], t0)
        if t0 <= mid < t1 or overlap >= 0.6 * (t1 - t0):
            out.append(s["text"])
    return out


# ---------- Whisper ----------
def whisper_ready():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def install_whisper(log=print):
    log("Installing faster-whisper into the app (one time, about 1 minute)…")
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--index-url",
           "https://pypi.org/simple", "faster-whisper"]
    env = dict(os.environ, PIP_CONFIG_FILE=os.devnull)
    for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS"):
        env.pop(k, None)
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError("Could not install faster-whisper: " + (p.stderr or p.stdout)[-600:])
    import importlib
    importlib.invalidate_caches()


def transcribe(path, language=None, model="base", log=print):
    """Timed lyric lines from the song. The model (~150 MB for 'base') downloads once on first use."""
    if not whisper_ready():
        install_whisper(log)
    from faster_whisper import WhisperModel
    log(f"Loading Whisper '{model}' (first time downloads it)…")
    wm = WhisperModel(model, device="cpu", compute_type="int8")
    log("Listening to the song for lyrics…")
    segs, info = wm.transcribe(path, language=(language or None) if language != "auto" else None,
                               vad_filter=True, beam_size=5, condition_on_previous_text=False)
    out = []
    for s in segs:
        t = s.text.strip()
        if t and not re.fullmatch(r"[\W_]*(music|♪|instrumental)?[\W_]*", t, re.I):
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": t})
    log(f"Found {len(out)} lyric lines (language: {getattr(info, 'language', '?')}).")
    return out, getattr(info, "language", None)
