"""Listens to a song: tempo (BPM), beats, downbeats, loudness/energy over time, and the best part to use.

Pure numpy (no librosa), so it installs everywhere. Results are cached next to the song as <song>.analysis.json.
"""
import json
import os
import subprocess

import numpy as np

from .video import FFMPEG

SR = 22050
HOP = 512
NFFT = 2048
FPS = SR / HOP          # onset frames per second (~43)
VERSION = 3
LAG = 0.75 * NFFT / SR  # measured on test tracks: ~0.07 s


def load_mono(path, sr=SR):
    p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("Could not read this audio file: " + p.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)


def _stft_mag(y):
    if len(y) < NFFT:
        y = np.pad(y, (0, NFFT - len(y)))
    n = 1 + (len(y) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(NFFT)
    out = np.empty((n, NFFT // 2 + 1))
    for s in range(0, n, 2000):  # chunks keep memory low on long songs
        out[s:s + 2000] = np.abs(np.fft.rfft(y[idx[s:s + 2000]] * win, axis=1))
    return out


def _onset(mag, lo=0, hi=None):
    S = np.log1p(100 * mag[:, lo:hi])
    flux = np.maximum(0, np.diff(S, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    k = int(FPS)  # remove slow trend
    trend = np.convolve(flux, np.ones(k) / k, mode="same")
    o = np.maximum(0, flux - trend)
    return o / (o.std() or 1)


def _tempo(onset, lo_bpm=70, hi_bpm=190):
    o = onset - onset.mean()
    n = len(o)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    f = np.fft.rfft(o, size)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / np.maximum(n - np.arange(n), 1)          # unbiased
    ac = ac / (ac[0] or 1)

    def at(x):
        return np.interp(x, np.arange(n), ac, right=0)

    lags = np.arange(int(60 * FPS / hi_bpm), int(60 * FPS / lo_bpm) + 1)
    # a real beat also repeats every 2 and 4 beats (half bar, bar); syncopated hits do not
    score = at(lags) + 0.6 * at(2 * lags) + 0.6 * at(4 * lags) + 0.3 * at(lags / 2)
    bpm = 60 * FPS / lags
    score = score * (0.85 + 0.15 * np.exp(-0.5 * (np.log2(bpm / 120) / 0.8) ** 2))
    lag = int(lags[int(np.argmax(score))])
    if 1 <= lag < n - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        d = (a - c) / (2 * (a - 2 * b + c) or 1)
        lag_f = lag + float(np.clip(d, -0.5, 0.5))
    else:
        lag_f = float(lag)
    return 60 * FPS / lag_f, lag_f


def _track_beats(onset, period, tight=100.0):
    """Dynamic-programming beat tracker (Ellis 2007)."""
    n = len(onset)
    local = np.convolve(onset, np.exp(-0.5 * ((np.arange(-period, period + 1)) / (period / 32)) ** 2), mode="same")
    score = local.copy()
    back = -np.ones(n, dtype=int)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    prange = np.arange(-hi, -lo + 1)
    pen = -tight * np.log(-prange / period) ** 2
    for i in range(hi, n):
        cand = score[i + prange] + pen
        j = int(np.argmax(cand))
        score[i] = local[i] + cand[j]
        back[i] = i + prange[j]
    # start from the best score in the last period
    tail = max(0, n - int(period))
    i = tail + int(np.argmax(score[tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    beats = np.array(beats[::-1])
    # drop weak beats at the very start/end (silence)
    thr = 0.1 * np.median(local[beats]) if len(beats) else 0
    return beats[local[beats] > thr] if len(beats) else beats


def analyze(path):
    y = load_mono(path)
    dur = len(y) / SR
    mag = _stft_mag(y)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    onset = _onset(mag)
    bass = _onset(mag, 0, int(np.searchsorted(freqs, 180)))
    bpm, period = _tempo(onset)
    beats_f = _track_beats(onset, period)
    beats = beats_f / FPS + LAG  # flux peaks a little before the hit; shift onto it

    # downbeats: which of the 4 beat phases has the strongest bass hits
    if len(beats_f) >= 8:
        strength = [bass[beats_f[k::4]].mean() + 0.5 * onset[beats_f[k::4]].mean() for k in range(4)]
        phase = int(np.argmax(strength))
    else:
        phase = 0
    downbeats = beats[phase::4]

    # loudness/energy curve, 2 values per second, scaled 0..1 within this song
    rms = np.sqrt(np.convolve(y ** 2, np.ones(SR // 2) / (SR // 2), mode="same"))[:: SR // 2]
    dens = np.array([onset[int(t * FPS): int((t + 1) * FPS)].mean() if int(t * FPS) < len(onset) else 0
                     for t in np.arange(len(rms)) * 0.5])
    lo, hi = np.percentile(rms, 10), np.percentile(rms, 97)
    loud = np.clip((rms - lo) / ((hi - lo) or 1), 0, 1)
    dlo, dhi = np.percentile(dens, 10), np.percentile(dens, 97)
    busy = np.clip((dens - dlo) / ((dhi - dlo) or 1), 0, 1)
    energy = np.convolve(0.65 * loud + 0.35 * busy, np.ones(3) / 3, mode="same")

    return {"version": VERSION, "duration": round(dur, 2), "bpm": round(float(bpm), 1),
            "beats": [round(float(b), 3) for b in beats], "downbeats": [round(float(b), 3) for b in downbeats],
            "energy": [round(float(e), 3) for e in energy], "energy_step": 0.5}


def analyze_cached(path):
    cache = path + ".analysis.json"
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
        with open(cache) as f:
            a = json.load(f)
        if a.get("version") == VERSION:
            return a
    a = analyze(path)
    with open(cache, "w") as f:
        json.dump(a, f)
    return a


def level(v):
    return "high" if v >= 0.62 else ("medium" if v >= 0.33 else "calm")


def energy_at(a, t0, t1):
    e, step = a["energy"], a.get("energy_step", 0.5)
    i, j = int(t0 / step), max(int(t0 / step) + 1, int(t1 / step))
    seg = e[i:j] or e[-1:] or [0.5]
    return float(np.mean(seg))


def snap_to_downbeat(a, t):
    d = a.get("downbeats") or a.get("beats") or []
    if not d:
        return round(float(t), 2)
    return round(float(min(d, key=lambda x: abs(x - t))), 2)


def best_start(a, seconds):
    """Start (on a downbeat) of the liveliest stretch of `seconds` that still fits in the song."""
    cands = [t for t in (a.get("downbeats") or a.get("beats") or [0.0]) if t + seconds <= a["duration"] + 0.1] or [0.0]
    best, score = cands[0], -1
    for t in cands:
        s = energy_at(a, t, t + seconds)
        s -= 0.15 * energy_at(a, t, t + 1.5) < 0.25  # avoid starting in a silent gap
        if s > score + 1e-6:
            best, score = t, s
    return round(float(best), 2)


def windows(a, start, n, seconds=5):
    """Per-clip music facts for the planner."""
    out = []
    for i in range(n):
        t0, t1 = start + i * seconds, start + (i + 1) * seconds
        v = energy_at(a, t0, t1)
        nb = sum(1 for b in a.get("beats", []) if t0 <= b < t1)
        out.append({"t0": round(t0, 2), "t1": round(t1, 2), "energy": level(v), "energy_value": round(v, 2),
                    "beats": nb, "bpm": a.get("bpm")})
    return out


def beats_in_reel(a, start, length):
    return [round(b - start, 3) for b in a.get("beats", []) if start - 0.01 <= b <= start + length + 0.5]


def synthetic(bpm, seconds, arc=None):
    """Analysis for the app's own generated track (beats are exact)."""
    beat = 60.0 / bpm
    beats = [round(k * beat, 3) for k in range(int(seconds / beat) + 1)]
    arc = arc or [0.55, 0.8, 0.85, 0.7]
    n = int(seconds / 0.5) + 1
    energy = [arc[min(len(arc) - 1, int(i / n * len(arc)))] for i in range(n)]
    return {"version": VERSION, "duration": seconds, "bpm": bpm, "beats": beats, "downbeats": beats[::4],
            "energy": energy, "energy_step": 0.5, "synthetic": True}
