"""Narration voices on your Mac: Microsoft Edge neural voices (edge-tts, free, needs internet) with the macOS
`say` voices as an offline fallback."""
import asyncio
import os
import platform
import shutil
import subprocess
import sys

from .video import FFMPEG

def _v(id_, label, lang, say, group):
    return {"id": id_, "label": label, "lang": lang, "say": say, "group": group}


VOICES = [
    # Hindi
    _v("hi-IN-SwaraNeural", "Swara (female)", "hi", "Lekha", "Hindi"),
    _v("hi-IN-MadhurNeural", "Madhur (male)", "hi", "Lekha", "Hindi"),
    _v("mr-IN-AarohiNeural", "Aarohi (female, Marathi accent)", "hi", "Lekha", "Hindi"),
    _v("mr-IN-ManoharNeural", "Manohar (male, Marathi accent)", "hi", "Lekha", "Hindi"),
    # speak any language, including Hindi
    _v("en-US-AvaMultilingualNeural", "Ava (female, any language)", "multi", "Samantha", "Multilingual (Hindi or English)"),
    _v("en-US-EmmaMultilingualNeural", "Emma (female, any language)", "multi", "Samantha", "Multilingual (Hindi or English)"),
    _v("en-US-AndrewMultilingualNeural", "Andrew (male, any language)", "multi", "Alex", "Multilingual (Hindi or English)"),
    _v("en-US-BrianMultilingualNeural", "Brian (male, any language)", "multi", "Alex", "Multilingual (Hindi or English)"),
    # English (India)
    _v("en-IN-NeerjaNeural", "Neerja (female)", "en", "Veena", "English (India)"),
    _v("en-IN-NeerjaExpressiveNeural", "Neerja expressive (female)", "en", "Veena", "English (India)"),
    _v("en-IN-PrabhatNeural", "Prabhat (male)", "en", "Rishi", "English (India)"),
    # English (child-like and storytellers)
    _v("en-US-AnaNeural", "Ana (child)", "en", "Samantha", "English · children"),
    _v("en-GB-MaisieNeural", "Maisie (child, UK)", "en", "Daniel", "English · children"),
    _v("en-US-JennyNeural", "Jenny (storyteller)", "en", "Samantha", "English · storytellers"),
    _v("en-US-AriaNeural", "Aria (bright)", "en", "Samantha", "English · storytellers"),
    _v("en-US-MichelleNeural", "Michelle (warm)", "en", "Samantha", "English · storytellers"),
    _v("en-US-GuyNeural", "Guy (storyteller)", "en", "Alex", "English · storytellers"),
    _v("en-US-ChristopherNeural", "Christopher (deep)", "en", "Alex", "English · storytellers"),
    _v("en-US-RogerNeural", "Roger (older man)", "en", "Alex", "English · storytellers"),
    _v("en-GB-SoniaNeural", "Sonia (UK)", "en", "Daniel", "English · UK & Australia"),
    _v("en-GB-LibbyNeural", "Libby (UK)", "en", "Daniel", "English · UK & Australia"),
    _v("en-GB-RyanNeural", "Ryan (UK)", "en", "Daniel", "English · UK & Australia"),
    _v("en-GB-ThomasNeural", "Thomas (UK)", "en", "Daniel", "English · UK & Australia"),
    _v("en-AU-NatashaNeural", "Natasha (Australia)", "en", "Karen", "English · UK & Australia"),
    _v("en-AU-WilliamNeural", "William (Australia)", "en", "Karen", "English · UK & Australia"),
]
_ALL = {"list": None}


def all_voices(log=print):
    """Every Edge voice that can read Hindi or English (about 50+), fetched once from Microsoft's voice list."""
    if _ALL["list"] is not None:
        return _ALL["list"]
    if not edge_ready():
        install_edge(log)
    import edge_tts
    raw = asyncio.run(edge_tts.list_voices())
    known = {v["id"] for v in VOICES}
    out = list(VOICES)
    for v in sorted(raw, key=lambda x: (x.get("Locale", ""), x.get("ShortName", ""))):
        sid, loc = v.get("ShortName", ""), v.get("Locale", "")
        multi = "Multilingual" in sid
        if sid in known or not (loc.startswith("en-") or loc in ("hi-IN", "mr-IN") or multi):
            continue
        name = sid.split("-")[-1].replace("Neural", "")
        lang = "multi" if multi else ("hi" if loc in ("hi-IN", "mr-IN") else "en")
        group = "Multilingual (Hindi or English)" if multi else (f"English ({loc[3:]})" if lang == "en" else "Hindi")
        out.append(_v(sid, f"{name} ({v.get('Gender', '').lower()}, {loc})", lang, "Samantha", group))
    _ALL["list"] = out
    return out


def find_voice(voice_id):
    for v in (_ALL["list"] or []) + VOICES:
        if v["id"] == voice_id:
            return v
    if voice_id and voice_id.endswith("Neural"):  # any Edge voice id still works
        return _v(voice_id, voice_id, "en", "Samantha", "Other")
    return VOICES[0]



def edge_ready():
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def install_edge(log=print):
    log("Installing edge-tts (one time)…")
    env = dict(os.environ, PIP_CONFIG_FILE=os.devnull)
    for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--index-url",
                        "https://pypi.org/simple", "edge-tts"], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError("Could not install edge-tts: " + (p.stderr or p.stdout)[-400:])
    import importlib
    importlib.invalidate_caches()


def say_ready():
    return platform.system() == "Darwin" and shutil.which("say") is not None


def voices():
    return {"voices": VOICES, "edge": edge_ready(), "say": say_ready()}


def _to_wav(src, dst, pitch_hz=0):
    af = []
    if pitch_hz:  # used for the offline Mac voices (edge voices change pitch themselves)
        f = max(0.6, min(1.6, 1 + pitch_hz / 200))
        af = ["-af", f"asetrate={int(48000 * f)},aresample=48000,atempo={1 / f:.4f}"]
    p = subprocess.run([FFMPEG, "-hide_banner", "-y", "-i", src, "-ac", "1", "-ar", "48000", *af, dst], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("Could not convert the voice audio: " + p.stderr[-300:])


PITCHES = {"much lower": -40, "lower": -20, "normal": 0, "higher": 25, "much higher": 50}


def default_voice(char, language, narrator_voice):
    """A sensible voice + pitch for a character, by type and narration language."""
    kind = (char or {}).get("kind", "")
    hi = language == "hi"
    table = {
        "girl-child": ("hi-IN-SwaraNeural", 25) if hi else ("en-US-AnaNeural", 0),
        "boy-child": ("hi-IN-MadhurNeural", 50) if hi else ("en-IN-PrabhatNeural", 50),
        "woman": ("hi-IN-SwaraNeural", 0) if hi else ("en-IN-NeerjaNeural", 0),
        "man": ("hi-IN-MadhurNeural", 0) if hi else ("en-IN-PrabhatNeural", 0),
        "animal": ("hi-IN-SwaraNeural", 50) if hi else ("en-US-AnaNeural", 25),
    }
    v, p = table.get(kind, (narrator_voice, 0))
    return {"voice": v, "pitch": p, "rate": 0}


def voice_cast(cast, language, narrator_voice, saved=None):
    """{speaker: {voice, pitch}} for the Narrator and every character, keeping any saved choices."""
    out = {"Narrator": {"voice": narrator_voice, "pitch": 0, "rate": 0}}
    for c in cast:
        out[c.get("name")] = default_voice(c, language, narrator_voice)
    for k, v in (saved or {}).items():
        if k in out and isinstance(v, dict) and v.get("voice"):
            out[k] = {"voice": v["voice"], "pitch": int(v.get("pitch", 0)), "rate": int(v.get("rate", 0))}
    return out


def synth(text, voice_id, out_wav, rate_pct=0, log=print, pitch_hz=0):
    """Speak `text` into out_wav (48 kHz mono). rate_pct: +10 = 10% faster. pitch_hz: +25 = higher voice."""
    v = find_voice(voice_id)
    tmp = out_wav + ".mp3"
    err = None
    try:
        if not edge_ready():
            install_edge(log)
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, v["id"], rate=f"{int(rate_pct):+d}%", pitch=f"{int(pitch_hz):+d}Hz").save(tmp)
        asyncio.run(go())
        _to_wav(tmp, out_wav)
        os.remove(tmp)
        return out_wav, v["id"]
    except Exception as e:
        err = e
    if say_ready():
        aiff = out_wav + ".aiff"
        rate = int(180 * (1 + rate_pct / 100))
        p = subprocess.run(["say", "-v", v["say"], "-r", str(rate), "-o", aiff, text], capture_output=True, text=True)
        if p.returncode != 0:  # the chosen system voice may not be installed
            p = subprocess.run(["say", "-r", str(rate), "-o", aiff, text], capture_output=True, text=True)
        if p.returncode == 0:
            _to_wav(aiff, out_wav, pitch_hz)
            os.remove(aiff)
            log(f"Used the Mac voice '{v['say']}' (online voice unavailable: {str(err)[:120]}).")
            return out_wav, "say:" + v["say"]
    raise RuntimeError(f"No narration voice available ({err}). Check your internet connection.")
