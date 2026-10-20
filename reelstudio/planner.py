"""Writes one motion prompt per 5-second clip. Uses Claude (API key), local Ollama, or built-in templates."""
import json
import random
import re

import requests

from . import moves as M

CHILD_KINDS = {"girl-child", "boy-child"}

NEGATIVE = ("blurry, low quality, distorted face, deformed hands, extra fingers, extra limbs, flicker, "
            "text, watermark, photorealistic, static, frozen")
CHILD_NEGATIVE = ", adult, makeup, revealing clothes, suggestive pose"
NEGATIVE += ", slow motion, standing still, stiff body, idle pose, minimal movement, repetitive looping motion"


def _pronoun(kind):
    return M.pronouns(kind)["he"]


def character_line(ch):
    s = ch.get("name", "the character")
    if ch.get("age"):
        s += f", {ch['age']}"
    if ch.get("look"):
        s += f", {ch['look']}"
    if ch.get("outfit"):
        s += f", wearing {ch['outfit']}"
    return s


def _wrap(ch, setting, body, end_pose, camera, energy=2, bpm=None):
    style = ch.get("style") or "3D animated cartoon"
    trig = (ch.get("lora_trigger") or "").strip()
    head = f"{trig} " if trig else ""
    He = M.pronouns(ch.get("kind"))["He"]
    tempo = M.tempo_words(bpm)
    return (f"{head}{style}. {character_line(ch)}, in {setting}. {body}, and ends {end_pose}. "
            f"{He} keeps dancing the whole time and never stands still. {M.ENERGY_WORDS[energy]}"
            f"{', ' + tempo if tempo else ''}. The face, hair and outfit stay exactly the same. "
            f"Soft warm lighting, {camera}. Vertical video.")


def plan_flags(n):
    """Which clips restart from the original image, and which lock their end to it."""
    clips = []
    for i in range(n):
        reanchor = n >= 4 and i > 0 and i % 3 == 0
        lock_end = (i == n - 1) or (n >= 4 and (i + 1) % 3 == 0 and i + 1 < n)
        clips.append({"reanchor": reanchor, "lock_end": lock_end})
    return clips


def default_arc(n):
    """Energy per clip when there is no song to listen to: hook, build, peak, cute ending."""
    if n == 1:
        return [3]
    arc = [2] + [3 if i % 2 == 1 else 2 for i in range(1, n - 1)] + [2]
    if n >= 3:
        arc[n // 2] = 3
    return arc


def clip_music(music, i):
    w = ((music or {}).get("windows") or [])
    return w[i] if i < len(w) else {}


def builtin_plan(ch, n, setting, mood, music=None, rng=random):
    kind = ch.get("kind")
    p = M.pronouns(kind)
    He = p["He"]
    flags = plan_flags(n)
    arc = default_arc(n)
    used, used_end = set(), set()
    last_part = None
    prev_end = "standing in the pose of the start image"
    out = []
    for i in range(n):
        info = clip_music(music, i)
        e = M.energy_num(info["energy"]) if info.get("energy") else arc[i]
        if i == 0:
            e = max(e, 2)  # the first 2 seconds must hook the viewer
        lyr = info.get("lyrics") or []
        gestures = M.lyric_gestures(lyr, kind)
        a = M.pick(e, kind, used, last_part, rng)
        body = f"{M.fill(a[0], kind)}"
        if gestures:
            body += f", then {gestures[0]}" + (f" and {gestures[1]}" if len(gestures) > 1 else "")
        else:
            b = M.pick(max(1, e - (1 if e == 3 and rng.random() < 0.4 else 0)), kind, used, a[2], rng)
            body += f", then {M.fill(b[0], kind)}"
        if i == n - 1 or flags[i]["lock_end"]:
            end = M.HOME_POSE
        else:
            choices = [x for x in M.END_POSES[e] if x not in used_end] or M.END_POSES[e]
            end = rng.choice(choices)
            used_end.add(end)
        if i == n - 1:
            body += f", and finishes with a happy wave and a tiny bow"
        start = "standing in the pose of the start image" if (i == 0 or flags[i]["reanchor"]) else prev_end
        text = f"{He} starts {start}. {He} {body}"
        cam = rng.choice(M.CAMERAS[e])
        out.append({"motion": _wrap(ch, setting, text, end, cam, e, info.get("bpm")), "end_pose": end,
                    "energy": ["", "calm", "medium", "high"][e], "lyrics": lyr, "gestures": gestures, **flags[i]})
        prev_end = end
        last_part = a[2]
    return out


def _music_lines(music, n, kind):
    if not music or not music.get("windows"):
        return "No song analysis: use the energy arc hook -> build -> peak -> cute ending."
    rows = []
    for i in range(n):
        w = clip_music(music, i)
        lyr = " / ".join(w.get("lyrics") or []) or "(no words)"
        g = M.lyric_gestures(w.get("lyrics"), kind)
        rows.append(f"Clip {i + 1} ({i * 5}-{i * 5 + 5} s): energy {w.get('energy', 'medium').upper()}, "
                    f"{w.get('beats', '?')} beats, lyrics sung: {lyr}" + (f" -> suggested gestures: {'; '.join(g)}" if g else ""))
    return f"Song tempo: {music.get('bpm')} BPM.\n" + "\n".join(rows)


def _llm_prompt(ch, n, setting, mood, music=None):
    kind = ch.get("kind")
    child = kind in CHILD_KINDS
    rules = ("\nCHILD SAFETY (mandatory): the character is a young child; keep the cartoon style, modest clothing, "
             "only playful kid moves (claps, hops, spins, jumps, marching, swaying, waving, namaste, giggling); "
             "nothing sensual or adult, no hip or chest isolations.") if child else ""
    flags = plan_flags(n)
    ex = "\n".join(f"- {m}" for e in (1, 2, 3) for m in M.examples(e, kind, 4))
    return f"""You are a choreographer. Plan a {n * 5}-second vertical dance reel made of {n} chained 5-second AI video clips (Wan 2.2 image-to-video).
Each clip starts from the LAST FRAME of the previous clip, so each clip must begin in the exact pose where the previous one ended and end in a clear pose. No camera cuts, no scene change, one character only.
Character: {character_line(ch)}. Style: {ch.get('style') or '3D animated cartoon'}. Setting: {setting}. Mood / song notes: {mood or 'happy festive'}.{rules}

MUSIC (follow it closely):
{_music_lines(music, n, kind)}

CHOREOGRAPHY RULES:
- Every clip uses DIFFERENT moves and a different main body part (arms, feet, spins, jumps, shoulders, travelling). Never repeat a move.
- Match energy: CALM = slow flowing arms and sways; MEDIUM = bouncy steps, claps, twists; HIGH = jumps, fast spins, big arm throws, shuffles.
- Act out the meaning of the lyrics with clear gestures (e.g. moon/sky -> reach up, heart/love -> heart hands, come -> beckon, clap -> clap). Do not write the lyrics themselves.
- 2 or 3 moves per clip, described with strong action verbs and big, readable motion. Never "gently" for MEDIUM or HIGH clips. The character never stands still.
- Clip 1 must hook the viewer in the first second. The last clip ends with a wave or tiny bow and finishes standing facing the camera with a big proud smile, hands on hips.
- Clips that restart from the original start image instead of the previous last frame: {[i + 1 for i, f in enumerate(flags) if f['reanchor']] or 'none'}.
Move ideas (use or adapt, invent others in the same spirit):
{ex}

Reply with JSON only: {{"clips":[{{"body":"1-3 sentences, starting pose -> moves","end_pose":"...","camera":"one short camera move","energy":"calm|medium|high"}}]}} with exactly {n} items."""


def _parse(text, n):
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    clips = data.get("clips", data if isinstance(data, list) else [])
    if len(clips) < n:
        raise ValueError("too few clips")
    return clips[:n]


def _from_llm(ch, items, setting, music=None):
    flags = plan_flags(len(items))
    out = []
    for i, it in enumerate(items):
        info = clip_music(music, i)
        body = it.get("body", "").strip().rstrip(".")
        end = it.get("end_pose", "facing the camera with a smile").strip().rstrip(".")
        lvl = (it.get("energy") or info.get("energy") or "medium").lower()
        e = M.energy_num(lvl if lvl in ("calm", "medium", "high") else "medium")
        out.append({"motion": _wrap(ch, setting, body, end, it.get("camera") or "the camera holds steady", e, info.get("bpm")),
                    "end_pose": end, "energy": ["", "calm", "medium", "high"][e], "lyrics": info.get("lyrics") or [],
                    "gestures": M.lyric_gestures(info.get("lyrics"), ch.get("kind")), **flags[i]})
    return out


def claude_plan(cfg, ch, n, setting, mood, music=None):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": cfg["anthropic_api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": cfg.get("anthropic_model") or "claude-sonnet-4-5", "max_tokens": 2000,
              "messages": [{"role": "user", "content": _llm_prompt(ch, n, setting, mood, music)}]})
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    return _from_llm(ch, _parse(text, n), setting, music)


def ollama_models(url="http://127.0.0.1:11434"):
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def ollama_plan(cfg, ch, n, setting, mood, music=None):
    url = cfg.get("ollama_url") or "http://127.0.0.1:11434"
    model = cfg.get("ollama_model") or (ollama_models(url) or [None])[0]
    if not model:
        raise RuntimeError("no Ollama model installed")
    r = requests.post(f"{url}/api/generate", timeout=300,
                      json={"model": model, "prompt": _llm_prompt(ch, n, setting, mood, music), "format": "json", "stream": False})
    r.raise_for_status()
    return _from_llm(ch, _parse(r.json().get("response", ""), n), setting, music)


def make_plan(cfg, ch, seconds, setting, mood, log=print, music=None):
    n = max(1, int(round(seconds / 5)))
    setting = setting or "a warm festive courtyard with marigold garlands"
    tries = []
    if cfg.get("anthropic_api_key"):
        tries.append(("Claude", claude_plan))
    if cfg.get("use_ollama", True) and ollama_models(cfg.get("ollama_url") or "http://127.0.0.1:11434"):
        tries.append(("Ollama", ollama_plan))
    for name, fn in tries:
        try:
            plan = fn(cfg, ch, n, setting, mood, music)
            log(f"Planned {n} clips with {name}.")
            return plan, name
        except Exception as e:  # fall through to next planner
            log(f"{name} planning failed ({e}); trying the next option.")
    log(f"Planned {n} clips with the built-in templates.")
    return builtin_plan(ch, n, setting, mood, music), "built-in"


def negative_for(ch):
    return NEGATIVE + (CHILD_NEGATIVE if ch.get("kind") in CHILD_KINDS else "")
