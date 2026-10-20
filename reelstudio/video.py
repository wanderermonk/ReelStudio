"""ffmpeg helpers: last frame, joining clips, mixing music."""
import re
import subprocess

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    FFMPEG = "ffmpeg"


def run(args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-y", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + p.stderr[-800:])
    return p.stderr


def duration(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def last_frame(video, png):
    run(["-sseof", "-0.25", "-i", video, "-frames:v", "1", "-update", "1", png])
    return png


def first_frame(video, png):
    run(["-i", video, "-frames:v", "1", png])
    return png


def join_clips(clips, out, trim_first_frame, width=1080, height=1920, fps=30, speeds=None):
    """clips: list of paths. trim_first_frame: list of bools (drop the repeated shared frame).
    speeds: optional play-speed per clip (e.g. 1.05 = 5% faster) used for beat sync."""
    speeds = speeds or [1.0] * len(clips)
    args = []
    for c in clips:
        args += ["-i", c]
    parts = []
    for i, trim in enumerate(trim_first_frame):
        t = "trim=start_frame=1," if trim else ""
        sp = f"/{speeds[i]:.4f}" if abs(speeds[i] - 1) > 1e-3 else ""
        parts.append(f"[{i}:v]{t}setpts=(PTS-STARTPTS){sp},scale={width}:{height}:flags=lanczos,setsar=1[v{i}]")
    chain = "".join(f"[v{i}]" for i in range(len(clips)))
    fc = ";".join(parts) + f";{chain}concat=n={len(clips)}:v=1:a=0,fps={fps},format=yuv420p[v]"
    run(args + ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-movflags", "+faststart", out])
    return out


def add_audio(video, audio, out, start=0.0, volume=0.9, fade_in=0.5, fade_out=1.5):
    d = duration(video)
    fo_start = max(0.0, d - fade_out)
    af = (f"atrim=start={start}:duration={d},asetpts=PTS-STARTPTS,volume={volume},"
          f"afade=t=in:st=0:d={fade_in},afade=t=out:st={fo_start}:d={fade_out}")
    run(["-i", video, "-i", audio, "-filter_complex", f"[1:a]{af}[a]", "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])
    return out


def add_silence(video, out):
    run(["-i", video, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out])
    return out


def motion_curve(path, size=96):
    """How much the picture changes from each frame to the next (a 'movement' signal), plus the clip fps."""
    import numpy as np
    p = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-i", path, "-vf", f"scale={size}:{size},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    fr = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(fr) // (size * size)
    if n < 3:
        return np.zeros(0), 16.0
    fr = fr[: n * size * size].reshape(n, size, size).astype(np.float32)
    diff = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    fps = n / max(duration(path), 1e-3)
    return diff, fps


def motion_peaks(curve):
    """Frames where a move 'hits': local maxima of change, followed by a slow-down."""
    import numpy as np
    if len(curve) < 5:
        return [], []
    c = np.convolve(curve, [0.25, 0.5, 0.25], mode="same")
    # an accent is where movement suddenly drops (a hit / stop) or peaks strongly
    drop = np.maximum(0, c[:-1] - c[1:])
    drop = np.concatenate([drop, [0]])
    sig = 0.6 * drop / (drop.max() or 1) + 0.4 * c / (c.max() or 1)
    thr = sig.mean() + 0.5 * sig.std()
    idx, wts = [], []
    for i in range(1, len(sig) - 1):
        if sig[i] >= thr and sig[i] >= sig[i - 1] and sig[i] > sig[i + 1]:
            if idx and i - idx[-1] < 4:
                if sig[i] > wts[-1]:
                    idx[-1], wts[-1] = i, float(sig[i])
                continue
            idx.append(i)
            wts.append(float(sig[i]))
    return idx, wts


def beat_speeds(clips, trims, beats, max_change=0.1, steps=21):
    """Pick a play speed per clip (within +-10%) so its movement accents land on the music beats.

    Returns (speeds, score_before, score_after) where score = share of accents within 60 ms of a beat.
    """
    import numpy as np
    if not beats:
        return [1.0] * len(clips), 0.0, 0.0
    bt = np.array(beats, dtype=float)

    def near(t):
        j = np.searchsorted(bt, t)
        cand = bt[max(0, j - 1): j + 1]
        return np.min(np.abs(cand - t)) if len(cand) else 1.0

    speeds, offset = [], 0.0
    before = after = total = 0.0
    off_plain = 0.0
    for path, trim in zip(clips, trims):
        curve, fps = motion_curve(path)
        n_frames = len(curve) + 1 - (1 if trim else 0)
        pk, wt = motion_peaks(curve)
        pk = [(p + 1 - (1 if trim else 0)) / fps for p in pk]  # time of the frame after the change
        best, best_s = 1.0, -1
        for s in np.linspace(1 - max_change, 1 + max_change, steps):
            sc = sum(w * np.exp(-(near(offset + t / s) / 0.05) ** 2 / 2) for t, w in zip(pk, wt))
            sc -= 0.02 * abs(s - 1) * sum(wt)  # small preference for natural speed
            if sc > best_s + 1e-9:
                best, best_s = float(s), sc
        for t, w in zip(pk, wt):
            total += w
            before += w * (near(off_plain + t) < 0.06)
            after += w * (near(offset + t / best) < 0.06)
        speeds.append(round(best, 3))
        offset += n_frames / fps / best
        off_plain += n_frames / fps
    return speeds, round(before / (total or 1), 2), round(after / (total or 1), 2)
