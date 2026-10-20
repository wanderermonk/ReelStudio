"""Original, copyright-free festive background track (dhol beat + simple tune)."""
import wave

import numpy as np

SR = 48000
SCALES = {
    "festive": [293.66, 330.0, 367.0, 440.0, 489.4, 587.3, 660.6],   # D major-ish pentatonic
    "sweet":   [261.6, 293.7, 329.6, 392.0, 440.0, 523.3, 587.3],    # C major pentatonic
}


def _env(n, a=0.005, d=0.3):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.exp(-t / d)


def _dhol_low():
    n = int(0.35 * SR); t = np.arange(n) / SR
    f = 90 * np.exp(-t * 6) + 55
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.12)


def _dhol_high(rng):
    n = int(0.12 * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 420 * t) * 0.5 + rng.standard_normal(n) * 0.35) * _env(n, 0.001, 0.03)


def _clap(rng):
    n = int(0.15 * SR)
    return rng.standard_normal(n) * _env(n, 0.001, 0.04)


def _tone(freq, length):
    n = int(length * SR); t = np.arange(n) / SR
    vib = 1 + 0.003 * np.sin(2 * np.pi * 5 * t)
    body = np.sin(2 * np.pi * freq * np.cumsum(vib) / SR) * 0.6
    harm = (np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(4 * np.pi * freq * t) + 0.2 * np.sin(6 * np.pi * freq * t))
    return (body + harm * 0.25) * _env(n, 0.01, 0.25)


def make_track(path, seconds, bpm=120, style="festive", seed=7):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    out = np.zeros(int(SR * seconds))

    def add(sig, start, gain):
        i = int(start * SR); j = min(len(out), i + len(sig))
        if i < len(out):
            out[i:j] += sig[: j - i] * gain

    bars = int(seconds / (4 * beat)) + 1
    pattern = [(0, "L"), (0.75, "H"), (1, "H"), (1.5, "L"), (2, "L"), (2.75, "H"), (3, "H"), (3.5, "H")]
    for b in range(bars):
        s = b * 4 * beat
        for off, kind in pattern:
            add(_dhol_low() if kind == "L" else _dhol_high(rng), s + off * beat, 0.9 if kind == "L" else 0.5)
        if b >= 1:
            add(_clap(rng), s + beat, 0.25); add(_clap(rng), s + 3 * beat, 0.25)

    sc = SCALES.get(style, SCALES["festive"])
    phrase = [(0, .5), (2, .5), (3, .5), (4, .5), (3, 1), (2, .5), (1, .5),
              (0, .5), (1, .5), (2, .5), (3, .5), (4, .5), (5, .5), (4, 1)]
    pos = 2 * 4 * beat
    while pos < seconds - 1:
        for idx, ln in phrase:
            if pos >= seconds - 0.5:
                break
            add(_tone(sc[idx], ln * beat * 0.95), pos, 0.18)
            pos += ln * beat
    t = np.arange(len(out)) / SR
    out += np.sin(2 * np.pi * sc[0] / 2 * t) * 0.05 + np.sin(2 * np.pi * sc[3] / 2 * t) * 0.03
    fi, fo = int(0.5 * SR), int(2 * SR)
    out[:fi] *= np.linspace(0, 1, fi); out[-fo:] *= np.linspace(1, 0, fo)
    out = out / (np.max(np.abs(out)) or 1) * 0.85
    st = (np.stack([out, out], 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(st.tobytes())
    return path
