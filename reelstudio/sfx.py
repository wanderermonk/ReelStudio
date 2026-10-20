"""Small built-in sound effects, generated with numpy (no downloads, free to use).
Put your own WAV/MP3 files in ReelStudio/data/sfx/ and they appear in the list by file name."""
import os
import wave

import numpy as np

SR = 48000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DIR = os.path.join(ROOT, "data", "sfx")
CACHE = os.path.join(ROOT, "data", "sfx_cache")


def _t(sec):
    return np.arange(int(sec * SR)) / SR


def _env(n, a=0.005, d=0.3):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.exp(-t / d)


def chime():
    out = np.zeros(int(1.6 * SR))
    for i, f in enumerate([1318.5, 1568, 2093]):
        s = int(i * 0.12 * SR)
        t = _t(1.6 - i * 0.12)
        out[s:s + len(t)] += np.sin(2 * np.pi * f * t) * np.exp(-t * 3) * 0.4
    return out


def sparkle():
    rng = np.random.default_rng(3)
    out = np.zeros(int(1.2 * SR))
    for k in range(14):
        s = int(rng.uniform(0, 0.8) * SR)
        f = rng.uniform(2500, 5000)
        t = _t(0.25)
        out[s:s + len(t)] += np.sin(2 * np.pi * f * t) * np.exp(-t * 18) * 0.18
    return out


def pop():
    t = _t(0.12)
    return np.sin(2 * np.pi * (300 + 900 * np.exp(-t * 40)) * t) * np.exp(-t * 35) * 0.8


def whoosh():
    rng = np.random.default_rng(5)
    n = int(0.9 * SR)
    noise = rng.standard_normal(n)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    k = np.linspace(60, 6, n).astype(int)
    sm = np.array([noise[max(0, i - k[i]):i + 1].mean() for i in range(0, n, 1)])
    return sm * env * 2.2


def boing():
    t = _t(0.7)
    f = 180 + 120 * np.sin(2 * np.pi * 9 * t) * np.exp(-t * 4)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5) * 0.7


def footsteps():
    rng = np.random.default_rng(7)
    out = np.zeros(int(2.0 * SR))
    for k in range(4):
        s = int((0.1 + k * 0.45) * SR)
        n = int(0.09 * SR)
        out[s:s + n] += rng.standard_normal(n) * _env(n, 0.002, 0.02) * 0.5
        t = _t(0.09)
        out[s:s + n] += np.sin(2 * np.pi * 90 * t) * np.exp(-t * 40) * 0.5
    return out


def clap():
    rng = np.random.default_rng(9)
    out = np.zeros(int(1.6 * SR))
    for k in range(4):
        s = int(k * 0.35 * SR)
        n = int(0.12 * SR)
        out[s:s + n] += rng.standard_normal(n) * _env(n, 0.001, 0.025) * 0.7
    return out


def birds():
    rng = np.random.default_rng(11)
    out = np.zeros(int(3.0 * SR))
    for k in range(9):
        s = int(rng.uniform(0, 2.6) * SR)
        t = _t(0.18)
        f = 3000 + 1500 * np.sin(2 * np.pi * rng.uniform(15, 30) * t)
        out[s:s + len(t)] += np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / 0.18) * 0.15
    return out


def rain():
    rng = np.random.default_rng(13)
    n = int(4.0 * SR)
    x = rng.standard_normal(n)
    x = np.convolve(x, np.ones(6) / 6, mode="same") * 0.25
    for k in range(120):
        s = int(rng.uniform(0, 3.9) * SR)
        m = int(0.02 * SR)
        x[s:s + m] += rng.standard_normal(m) * _env(m, 0.001, 0.004) * 0.4
    fade = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.4 * SR))
    return x * fade


def wind():
    rng = np.random.default_rng(15)
    n = int(4.0 * SR)
    x = np.cumsum(rng.standard_normal(n))
    x = x - np.convolve(x, np.ones(4000) / 4000, mode="same")
    x = x / (np.abs(x).max() or 1) * 0.5
    return x * (0.6 + 0.4 * np.sin(np.linspace(0, 3 * np.pi, n)) ** 2) * np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.5 * SR))


def drumroll():
    rng = np.random.default_rng(17)
    out = np.zeros(int(2.4 * SR))
    k = 0.0
    while k < 1.8:
        s = int(k * SR)
        n = int(0.05 * SR)
        out[s:s + n] += rng.standard_normal(n) * _env(n, 0.001, 0.01) * (0.2 + 0.4 * k / 1.8)
        k += 0.045
    t = _t(0.4)
    s = int(1.85 * SR)
    out[s:s + len(t)] += np.sin(2 * np.pi * 70 * t) * np.exp(-t * 8) * 0.9
    return out


def heartbeat():
    out = np.zeros(int(2.4 * SR))
    for beat in (0.1, 1.1):
        for off, g in ((0, 0.9), (0.22, 0.6)):
            t = _t(0.15)
            s = int((beat + off) * SR)
            out[s:s + len(t)] += np.sin(2 * np.pi * 55 * t) * np.exp(-t * 25) * g
    return out


BUILTIN = {"chime": chime, "sparkle": sparkle, "pop": pop, "whoosh": whoosh, "boing": boing, "footsteps": footsteps,
           "clap": clap, "birds": birds, "rain": rain, "wind": wind, "drumroll": drumroll, "heartbeat": heartbeat}


def names():
    user = []
    if os.path.isdir(USER_DIR):
        user = sorted(os.path.splitext(f)[0] for f in os.listdir(USER_DIR) if f.lower().endswith((".wav", ".mp3", ".m4a")))
    return list(BUILTIN) + [u for u in user if u not in BUILTIN]


def path(name):
    """A WAV/MP3 file for this effect, or None."""
    if not name or name == "none":
        return None
    if os.path.isdir(USER_DIR):
        for f in os.listdir(USER_DIR):
            if os.path.splitext(f)[0] == name:
                return os.path.join(USER_DIR, f)
    fn = BUILTIN.get(name)
    if not fn:
        return None
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{name}.wav")
    if not os.path.exists(out):
        x = fn()
        x = x / (np.abs(x).max() or 1) * 0.8
        st = (np.stack([x, x], 1) * 32767).astype(np.int16)
        with wave.open(out, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(st.tobytes())
    return out
