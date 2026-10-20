"""Story mode: idea -> short story -> shot list -> keyframe and video prompts.

Each shot is ~5 seconds and does ONE action. The prompts follow rules that help Wan 2.2 with hands and limbs:
eyes lead the hand, the hand and the object are named, actions are split into timed beats, props are big and simple,
close-ups for hand moments, and first+last keyframes for anything the hands must get exactly right.
"""
import json
import random
import re

import requests

from .planner import CHILD_KINDS, NEGATIVE, CHILD_NEGATIVE, character_line, ollama_models

MAX_SHOTS = 30  # 2.5 minutes
SHOT_SIZES = ["wide", "medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "low angle", "high angle"]
MODES = {"i2v": "New shot from a start keyframe",
         "flf": "Start + end keyframes (best for hands and props)",
         "continue": "Continue from the previous shot (no cut)"}
HAND_NEGATIVE = (", extra fingers, missing fingers, fused fingers, six fingers, deformed hands, twisted wrist, "
                 "broken arm, extra arms, extra legs, bent limbs, object morphing, object melting, object floating, "
                 "object passing through hand, object changing size, duplicated object, sudden cut, scene change")

# Props that video models handle badly, and what to use instead
THIN_PROPS = {
    "pen": "a big crayon", "pencil": "a big crayon", "phone": "a big toy phone held in both hands", "mobile": "a toy phone",
    "spoon": "a bowl held in both hands", "fork": "a bowl held in both hands", "chopstick": "a bowl",
    "needle": "a ball of wool", "thread": "a ball of wool", "lace": "a big bow", "shoelace": "slipping on a shoe",
    "scissors": "a paper cut-out already made", "knife": "(avoid: unsafe; use a cake already cut)",
    "key": "a big toy key", "coin": "a big gold medal", "ring": "a flower", "cup": "a big mug held in both hands",
    "card": "a big greeting card held in both hands", "string": "a big ribbon", "wire": "a big ribbon",
    "book page": "a big picture book held open", "cigarette": "(not allowed)", "gun": "(not allowed)",
}
GOOD_PROPS = ["ball", "balloon", "kite", "flower", "puppy", "kitten", "teddy bear", "gift box", "umbrella", "big book",
              "drum", "lantern", "diya", "cake", "fruit basket", "watering can", "paper boat", "toy car", "scarf"]


def prop_warnings(shot):
    text = " ".join([shot.get("props", "") if isinstance(shot.get("props"), str) else " ".join(shot.get("props") or []),
                     shot.get("hands", ""), " ".join(b.get("action", "") for b in shot.get("beats", []))]).lower()
    out = []
    for word, better in THIN_PROPS.items():
        if re.search(rf"\b{re.escape(word)}s?\b", text):
            out.append(f"'{word}' is hard for video AI (small or thin). Try {better}.")
    acts = len([b for b in shot.get("beats", []) if b.get("action")])
    verbs = len(re.findall(r"\b(then|and then|after that|while)\b", " ".join(b.get("action", "") for b in shot.get("beats", []))))
    if acts > 4 or verbs > 3:
        out.append("This shot has many actions. Split it into two shots (one action per shot works best).")
    if shot.get("mode") == "i2v" and (shot.get("props") or "").strip() and re.search(
            r"\b(pick|grab|catch|hold|give|gave|take|took|hug|open|throw|threw|pour|tie|lift|pass|hand over|hands over)\w*", text):
        out.append("Hands touch an object here: 'Start + end keyframes' will look much better.")
    words = sum(len((ln.get("text") or "").split()) for ln in (shot.get("lines") or []) if isinstance(ln, dict))
    if words > 14:
        out.append(f"About {words / 2.5:.0f} s of speech in a 5 s shot: the shot will be slowed or held so the words fit. Shorter lines feel better.")
    if shot.get("mode") == "flf" and not (shot.get("end_frame") or "").strip():
        out.append("Describe the end keyframe (where the hands and the object are at the end).")
    return out


# ---------------- prompts ----------------

def cast_lines(chars):
    return " ".join(f"[{c.get('name')}] {character_line(c)}." for c in chars)


def _who(shot, chars):
    names = shot.get("characters") or [c.get("name") for c in chars[:1]]
    sel = [c for c in chars if c.get("name") in names] or chars[:1]
    return sel


def style_of(chars):
    return (chars[0].get("style") if chars else None) or "3D animated cartoon"


def keyframe_prompt(shot, chars, which="start", edit=True):
    who = _who(shot, chars)
    frame = (shot.get("end_frame") if which == "end" else shot.get("start_frame")) or ""
    size = shot.get("shot", "medium")
    if edit and which == "end":
        return (f"Same scene, same camera angle, same lighting and the same character(s) as the reference image. "
                f"Change only the pose: {frame}. Keep faces, hair, outfits and props exactly the same. "
                f"Clear, natural hands with five fingers.")
    if edit:
        refs = ", ".join(f"{c.get('name')} (reference image {i + 1})" for i, c in enumerate(who[:3]))
        return (f"{style_of(chars)} still frame, vertical 9:16. Show {refs}, keeping each face, hair and outfit exactly "
                f"the same as the reference. {size.capitalize()} shot in {shot.get('setting', '')}. {frame}. "
                f"Clear, natural hands with five fingers. Cinematic soft lighting, clean composition, no text.")
    return (f"{style_of(chars)} still frame, vertical 9:16. {cast_lines(who)} {size.capitalize()} shot in "
            f"{shot.get('setting', '')}. {frame}. Clear, natural hands with five fingers. Cinematic soft lighting, no text.")


def video_prompt(shot, chars):
    who = _who(shot, chars)
    beats = [b for b in shot.get("beats", []) if (b.get("action") or "").strip()]
    timeline = " ".join(f"({b.get('t', '').strip() or '…'}) {b['action'].strip().rstrip('.')}." for b in beats)
    props = shot.get("props") or ""
    props = ", ".join(props) if isinstance(props, list) else props
    keep = f" Props ({props}) keep the same size, colour and shape and stay in the hands that hold them." if props else ""
    lines = [
        f"{style_of(chars)}. {cast_lines(who)}",
        f"{shot.get('shot', 'medium').capitalize()} shot in {shot.get('setting', '')}.",
        timeline,
        f"Eyes: {shot['eyes'].rstrip('.')}." if shot.get("eyes") else "",
        f"Hands: {shot['hands'].rstrip('.')}." if shot.get("hands") else "",
        f"Expression: {shot['emotion']}." if shot.get("emotion") else "",
        f"Slow, deliberate, physically natural movement; the eyes look at an object before the hand reaches it; "
        f"fingers wrap clearly around objects; elbows and knees bend naturally.{keep}",
        "Faces, hair and outfits stay exactly the same. One continuous shot, no cuts.",
        f"{(shot.get('camera') or 'the camera holds steady').capitalize()}. Vertical video.",
    ]
    return " ".join(x for x in lines if x)


def negative_for_story(chars):
    child = any(c.get("kind") in CHILD_KINDS for c in chars)
    return NEGATIVE + HAND_NEGATIVE + (CHILD_NEGATIVE if child else "")


# ---------------- planning ----------------

def _llm_prompt(chars, idea, n, setting, tone, language, context=""):
    child = any(c.get("kind") in CHILD_KINDS for c in chars)
    safety = ("\nCHILD SAFETY (mandatory): children are in this story. Keep it wholesome and age-appropriate, cartoon "
              "style, modest clothing, no danger, no scary violence, no romance.") if child else ""
    cast = "\n".join(f"- {c.get('name')}: {character_line(c)} (type: {c.get('kind')})" for c in chars)
    narr = {"hi": "Hindi (Devanagari script)", "en": "English", "none": ""}.get(language, "English")
    names = ", ".join(c.get("name") for c in chars)
    narr_rule = (f'- "lines": 0 to 2 spoken lines in {narr}, each max 12 words (about 3 seconds): '
                 f'[{{"speaker":"Narrator","text":"..."}}] — speaker is "Narrator" or one of: {names}. Use the Narrator to '
                 f'tell the story and let characters say short, natural dialogue in their own voice. Animals only make '
                 f'animal sounds ("Woof!") unless the story is magical. Total speech in a shot must fit in 4 seconds.'
                 if narr else '- "lines": always [].')
    return f"""You are a film director for {n * 5}-second vertical shorts made with an AI video model (Wan 2.2, 5 seconds per shot).
Write a short story and a shot list with exactly {n} shots.{context}

Cast (use these names; the first is the main character):
{cast}
Idea: {idea or 'a sweet everyday adventure'}
Setting: {setting or 'choose one simple, bright setting'}. Tone: {tone or 'sweet'}.{safety}

RULES THAT MAKE AI VIDEO WORK (follow strictly):
- ONE clear action per shot. Split anything longer into more shots.
- Every shot lists beats with times "0-2s", "2-4s", "4-5s". Each beat = one simple movement.
- The EYES lead: the character looks at an object before reaching for it.
- Name the hand ("right hand", "both hands"), the object, and where it is ("on the bench at her left").
- Props must be big and simple: {', '.join(GOOD_PROPS[:12])}. Never pens, phones, spoons, needles, laces, keys, coins.
- Use close-up or medium close-up for hand moments, wide for whole-body movement (walking, jumping, dancing).
- "mode": "flf" when hands touch, give, hold, catch or hug (then also write "end_frame"); "continue" when the shot
  directly continues the previous one with no cut; otherwise "i2v".
- "start_frame": the exact still image the shot starts on (pose, where hands and props are, where eyes look).
- "end_frame": the exact final pose (only for flf).
- Keep the same setting for neighbouring shots unless the story needs a change. At most 3 characters per shot.
- Give EVERY cast member at least one moment of their own (an action or a line), matching their role.
- Build a clear arc: setup -> problem or wish -> try -> warm happy ending. Vary shot sizes and keep it moving.
{narr_rule}
- "sfx": one of chime, sparkle, pop, whoosh, boing, footsteps, clap, birds, rain, wind, drumroll, heartbeat, none.

Reply with JSON only:
{{"title":"...","logline":"one sentence","shots":[{{"title":"short name","characters":["Name"],"shot":"wide|medium|medium close-up|close-up|extreme close-up|over-the-shoulder|low angle|high angle",
"camera":"one simple camera move","setting":"...","props":"...","mode":"i2v|flf|continue","start_frame":"...","end_frame":"",
"beats":[{{"t":"0-2s","action":"..."}},{{"t":"2-4s","action":"..."}},{{"t":"4-5s","action":"..."}}],
"eyes":"...","hands":"...","emotion":"...","lines":[{{"speaker":"Narrator","text":"..."}}],"sfx":"..."}}]}}"""


def _parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def clean_lines(s, names):
    """Spoken lines of a shot: [{speaker, text}] (also accepts the old single 'narration' text)."""
    raw = s.get("lines")
    if not isinstance(raw, list):
        raw = [{"speaker": "Narrator", "text": s.get("narration")}] if (s.get("narration") or "").strip() else []
    out = []
    for ln in raw[:3]:
        if isinstance(ln, str):
            ln = {"speaker": "Narrator", "text": ln}
        text = (ln.get("text") or "").strip()
        if text:
            sp = ln.get("speaker") if ln.get("speaker") in names else "Narrator"
            out.append({"speaker": sp, "text": text})
    return out


def _clean(story, chars, n, language):
    names = [c.get("name") for c in chars]
    shots = (story.get("shots") or [])[:n]
    out = []
    for i, s in enumerate(shots):
        sh = {
            "id": f"s{i + 1}", "title": s.get("title") or f"Shot {i + 1}",
            "characters": [x for x in (s.get("characters") or names[:1]) if x in names] or names[:1],
            "shot": s.get("shot") if s.get("shot") in SHOT_SIZES else "medium",
            "camera": s.get("camera") or "the camera holds steady", "setting": s.get("setting") or "",
            "props": s.get("props") if isinstance(s.get("props"), str) else ", ".join(s.get("props") or []),
            "mode": s.get("mode") if s.get("mode") in MODES else "i2v",
            "start_frame": s.get("start_frame") or "", "end_frame": s.get("end_frame") or "",
            "beats": [{"t": b.get("t", ""), "action": b.get("action", "")} for b in (s.get("beats") or [])][:4]
                     or [{"t": "0-5s", "action": ""}],
            "eyes": s.get("eyes") or "", "hands": s.get("hands") or "", "emotion": s.get("emotion") or "",
            "lines": [] if language == "none" else clean_lines(s, names),
            "sfx": s.get("sfx") if s.get("sfx") else "none", "state": "waiting", "scene": s.get("scene") or "",
        }
        if i == 0 and sh["mode"] == "continue":
            sh["mode"] = "i2v"
        out.append(sh)
    return {"title": story.get("title") or "My short", "logline": story.get("logline") or "", "shots": out}


# ---- built-in stories (no AI key needed) ----
def _b(t, a):
    return {"t": t, "action": a}


def _template_puppy(m, o, setting, hi):
    M, O = m, o or None
    return {"title": f"{M} and the Lost Puppy", "logline": f"{M} finds a little lost puppy and helps it home.", "shots": [
        {"title": "A sound", "characters": [M], "shot": "wide", "camera": "the camera slowly pushes in", "setting": setting,
         "props": "", "mode": "i2v", "start_frame": f"{M} walking along a path, arms swinging, smiling",
         "beats": [_b("0-2s", f"{M} walks forward happily"), _b("2-4s", f"{M} stops and tilts the head, listening"),
                   _b("4-5s", f"{M} turns the head to the left, curious")],
         "eyes": "look ahead, then to the left toward the sound", "hands": "swing naturally, then rest at the sides",
         "emotion": "happy, then curious", "narration": "एक दिन, पार्क में…" if hi else "One sunny day, in the park…", "sfx": "birds"},
        {"title": "The puppy", "characters": [M], "shot": "medium close-up", "camera": "the camera holds steady", "setting": setting,
         "props": "a small fluffy brown puppy", "mode": "i2v",
         "start_frame": f"{M} kneeling on the grass looking down at a small fluffy brown puppy sitting near a bush",
         "beats": [_b("0-2s", "the puppy looks up and wags its tail"), _b("2-4s", f"{M}'s eyes go wide and a big smile appears"),
                   _b("4-5s", f"{M} leans forward a little toward the puppy")],
         "eyes": "fixed on the puppy", "hands": "both hands resting on the knees", "emotion": "surprised and delighted",
         "narration": "एक छोटा सा पिल्ला!" if hi else "A tiny puppy, all alone!", "sfx": "sparkle"},
        {"title": "Gentle hands", "characters": [M], "shot": "close-up", "camera": "the camera slowly pushes in", "setting": setting,
         "props": "the small fluffy brown puppy", "mode": "flf",
         "start_frame": f"close-up of {M}'s two open hands just in front of the sitting puppy, {M} looking at it",
         "end_frame": f"{M} holding the puppy against the chest with both arms, the puppy's head under the chin",
         "beats": [_b("0-2s", f"{M} looks at the puppy and slowly reaches out with both hands"),
                   _b("2-4s", "both hands slide gently under the puppy's belly and lift it"),
                   _b("4-5s", f"{M} hugs the puppy to the chest")],
         "eyes": "on the puppy the whole time", "hands": "both hands open, palms up, then cradle the puppy",
         "emotion": "gentle and caring", "narration": "डरो मत, मैं हूँ ना।" if hi else "Don't worry, I've got you.", "sfx": "none"},
        {"title": "Which way?", "characters": [M], "shot": "medium", "camera": "the camera slowly arcs to the side", "setting": setting,
         "props": "the puppy in her arms", "mode": "i2v",
         "start_frame": f"{M} standing, holding the puppy in both arms, looking around",
         "beats": [_b("0-2s", f"{M} looks left"), _b("2-4s", f"{M} looks right"),
                   _b("4-5s", f"{M} spots something in the distance and smiles")],
         "eyes": "look left, then right, then far ahead", "hands": "both arms keep holding the puppy",
         "emotion": "thoughtful, then hopeful", "narration": "तुम्हारा घर कहाँ है?" if hi else "Where is your home, little one?", "sfx": "none"},
        {"title": "Found!", "characters": [M] + ([O] if O else []), "shot": "wide", "camera": "the camera stays wide", "setting": setting,
         "props": "the puppy", "mode": "flf",
         "start_frame": f"{M} holding the puppy, facing {O or 'a worried owner'} a few steps away",
         "end_frame": f"{O or 'the owner'} holding the puppy and smiling, {M} clapping happily",
         "beats": [_b("0-2s", f"{M} walks forward holding the puppy"),
                   _b("2-4s", f"{M} gently passes the puppy with both hands into {O or 'the owner'}'s arms"),
                   _b("4-5s", f"{M} claps happily")],
         "eyes": "on the puppy, then on the other face", "hands": "both hands pass the puppy, then clap",
         "emotion": "joyful", "narration": "मिल गया!" if hi else "Home at last!", "sfx": "chime"},
        {"title": "Friends", "characters": [M], "shot": "medium close-up", "camera": "the camera slowly pulls back", "setting": setting,
         "props": "", "mode": "i2v", "start_frame": f"{M} facing the camera with a proud smile",
         "beats": [_b("0-2s", f"{M} giggles"), _b("2-4s", f"{M} waves at the camera with the right hand"),
                   _b("4-5s", f"{M} puts both hands on the hips")],
         "eyes": "look at the camera", "hands": "right hand waves, then both on hips", "emotion": "proud and happy",
         "narration": "दोस्ती सबसे प्यारी है।" if hi else "Kindness is the best adventure.", "sfx": "sparkle"},
    ]}


def _template_generic(m, o, setting, idea, hi):
    wish = idea or "a little adventure"
    shots = [
        ("Hello", "wide", "i2v", f"{m} standing in {setting}, smiling", [
            _b("0-2s", f"{m} looks around happily"), _b("2-4s", f"{m} waves at the camera with the right hand"),
            _b("4-5s", f"{m} lowers the hand and smiles")], "around, then at the camera", "right hand waves", "cheerful",
         "आज कुछ ख़ास होने वाला है…" if hi else "Today something special happens…", "birds"),
        ("The wish", "medium close-up", "i2v", f"{m} looking up, thinking, one finger on the chin", [
            _b("0-2s", f"{m} taps the chin with the right index finger"), _b("2-4s", f"{m}'s eyes light up with an idea"),
            _b("4-5s", f"{m} raises the right hand up, excited")], "up, then straight ahead", "right hand at the chin, then raised",
         "thinking, then excited", "मुझे पता है!" if hi else "I know what to do!", "pop"),
        ("Getting ready", "medium", "flf", f"{m} standing next to a big gift box on a table at the right", [
            _b("0-2s", f"{m} looks at the gift box"), _b("2-4s", f"{m} lifts the gift box with both hands"),
            _b("4-5s", f"{m} hugs the box to the chest")], "on the gift box", "both hands grip the sides of the box",
         "determined", "" , "whoosh"),
        ("The try", "wide", "i2v", f"{m} holding the gift box, walking", [
            _b("0-2s", f"{m} walks forward carefully"), _b("2-4s", f"{m} almost trips, wobbling"),
            _b("4-5s", f"{m} steadies and laughs")], "ahead, then down at the feet", "both arms hold the box",
         "careful, then relieved", "अरे, संभल के!" if hi else "Oops! Careful…", "boing"),
        ("The surprise", "medium", "flf", f"{m} holding out the gift box toward {o or 'a smiling friend'}", [
            _b("0-2s", f"{m} holds the box out with both hands"), _b("2-4s", f"{o or 'the friend'} takes it with both hands"),
            _b("4-5s", "both smile and laugh")], "at the friend's face", "both hands pass the box",
         "joyful", "सरप्राइज़!" if hi else "Surprise!", "chime"),
        ("Happy ending", "medium close-up", "i2v", f"{m} facing the camera with a big smile", [
            _b("0-2s", f"{m} giggles"), _b("2-4s", f"{m} gives a thumbs up with the right hand"),
            _b("4-5s", f"{m} puts both hands on the hips")], "at the camera", "thumbs up, then hands on hips",
         "proud", "छोटी खुशियाँ, बड़ी बातें।" if hi else "Small wishes, big smiles.", "sparkle"),
    ]
    out = []
    for t, size, mode, start, beats, eyes, hands, emo, narr, sfx in shots:
        ch = [m] + ([o] if o and mode == "flf" and "friend" not in start else [])
        out.append({"title": t, "characters": ch, "shot": size, "camera": "the camera holds steady", "setting": setting,
                    "props": "a big gift box" if "box" in start else "", "mode": mode, "start_frame": start,
                    "end_frame": (f"{m} hugging the gift box to the chest" if t == "Getting ready" else
                                  f"{o or 'the friend'} holding the box, {m} clapping" if t == "The surprise" else ""),
                    "beats": beats, "eyes": eyes, "hands": hands, "emotion": emo, "narration": narr, "sfx": sfx})
    return {"title": f"{m}'s {wish[:30]}".strip(), "logline": f"{m} and {wish}.", "shots": out}


def _shot(title, chars, size, mode, start, beats, eyes, hands, emo, lines, sfx, setting, props="", end="", cam="the camera holds steady", scene=""):
    return {"title": title, "characters": chars, "shot": size, "camera": cam, "setting": setting, "props": props,
            "mode": mode, "start_frame": start, "end_frame": end, "beats": [_b(t, a) for t, a in beats], "eyes": eyes,
            "hands": hands, "emotion": emo, "lines": lines, "sfx": sfx, "scene": scene}


def _block_journey(m, o, setting, hi):
    N = lambda t_hi, t_en: [{"speaker": "Narrator", "text": t_hi if hi else t_en}]
    S = lambda who, t_hi, t_en: [{"speaker": who, "text": t_hi if hi else t_en}]
    sc = "The walk"
    who = [m] + ([o] if o else [])
    return [
        _shot("Off we go", who, "wide", "i2v", f"{m}{' and ' + o if o else ''} walking side by side on a garden path",
              [("0-2s", "they walk forward happily"), ("2-4s", f"{m} swings both arms"), ("4-5s", f"{m} skips one step")],
              "ahead", "arms swing naturally", "cheerful", N("दोनों सैर पर निकले।", "Off they went for a walk."), "footsteps", setting,
              cam="the camera slowly tracks backwards", scene=sc),
        _shot("Birds", [m], "medium", "i2v", f"{m} standing, looking up at the sky",
              [("0-2s", f"{m} looks up and points with the right index finger"), ("2-4s", f"{m}'s eyes follow something across the sky"),
               ("4-5s", f"{m} smiles in wonder")], "up at the birds", "right index finger points up", "amazed",
              S(m, "देखो, चिड़िया!", "Look, birds!"), "birds", setting, scene=sc),
        _shot("A flower", [m], "medium close-up", "flf", f"{m} kneeling next to a big red flower growing by the path",
              [("0-2s", f"{m} looks at the flower"), ("2-4s", f"{m} gently picks it with the right hand"),
               ("4-5s", f"{m} holds it up and smells it")], "on the flower", "right hand closes around the stem",
              "gentle", N("एक सुंदर फूल…", "A beautiful flower…"), "sparkle", setting, props="a big red flower",
              end=f"{m} holding the red flower near the nose with the right hand, eyes closed, smiling", scene=sc),
        _shot("For you", who, "medium", "flf", f"{m} holding a big red flower out toward {o or 'the camera'}",
              [("0-2s", f"{m} holds the flower out with the right hand"), ("2-4s", f"{o + ' takes' if o else m + ' offers'} the flower with one hand"),
               ("4-5s", "both smile")], f"at {o + chr(39) + 's face' if o else 'the camera'}", "the flower passes from one hand to the other",
              "sweet", S(m, "यह तुम्हारे लिए!", "This is for you!"), "chime", setting, props="a big red flower",
              end=f"{o or m} holding the red flower and smiling", scene=sc),
    ]


def _block_celebrate(m, o, setting, hi):
    N = lambda t_hi, t_en: [{"speaker": "Narrator", "text": t_hi if hi else t_en}]
    S = lambda who, t_hi, t_en: [{"speaker": who, "text": t_hi if hi else t_en}]
    sc = "Celebration"
    who = [m] + ([o] if o else [])
    return [
        _shot("Happy dance", who, "wide", "i2v", f"{m}{' and ' + o if o else ''} standing, ready to dance",
              [("0-2s", "they clap twice"), ("2-4s", "they sway side to side"), ("4-5s", "they jump with arms up")],
              "at each other", "clap, then arms up", "joyful", N("और फिर… खुशी का नाच!", "And then… a happy dance!"), "drumroll", setting,
              cam="the camera stays wide", scene=sc),
        _shot("Spin", [m], "medium", "i2v", f"{m} with arms stretched out wide",
              [("0-2s", f"{m} starts to turn"), ("2-4s", f"{m} spins around once, outfit flowing"), ("4-5s", f"{m} stops facing the camera")],
              "ahead", "arms out wide", "delighted", [], "whoosh", setting, scene=sc),
        _shot("High five", who, "medium close-up", "flf", f"{m}{' and ' + o if o else ''} facing each other, right hands raised",
              [("0-2s", "they look at each other's raised hand"), ("2-4s", "their right palms meet in a high five"), ("4-5s", "they laugh")],
              "at the raised hands", "right palms meet flat", "excited", S(m, "हाई फाइव!", "High five!"), "clap", setting,
              end=f"{m}{' and ' + o if o else ''} laughing, hands lowered", scene=sc),
    ]


def _block_ball(m, o, setting, hi):
    N = lambda t_hi, t_en: [{"speaker": "Narrator", "text": t_hi if hi else t_en}]
    S = lambda who, t_hi, t_en: [{"speaker": who, "text": t_hi if hi else t_en}]
    sc, p = "Playtime", "a big red ball"
    other = o or "a friend"
    return [
        _shot("A ball!", [m], "medium", "flf", f"{m} looking at a big red ball lying on the grass in front",
              [("0-2s", f"{m} looks down at the ball"), ("2-4s", f"{m} bends and picks it up with both hands"), ("4-5s", f"{m} holds it up proudly")],
              "on the ball", "both hands hold the sides of the ball", "excited", S(m, "चलो खेलें!", "Let's play!"), "pop", setting, props=p,
              end=f"{m} holding the big red ball up at chest height with both hands", scene=sc),
        _shot("Throw", [m], "wide", "i2v", f"{m} holding the big red ball with both hands at chest height",
              [("0-2s", f"{m} pulls the ball back"), ("2-4s", f"{m} throws the ball forward with both hands"), ("4-5s", f"{m} watches it fly")],
              "follow the ball", "both hands push the ball forward", "playful", N("गेंद उड़ चली!", "Up goes the ball!"), "whoosh", setting, props=p, scene=sc),
        _shot("Catch", [o] if o else [m], "medium", "flf", f"{other} with both hands open, looking up at a red ball coming down",
              [("0-2s", "eyes follow the falling ball"), ("2-4s", "both hands close around the ball"), ("4-5s", "hugs the ball and laughs")],
              "on the ball", "both hands catch the ball", "happy", N("पकड़ लिया!", "Caught it!"), "clap", setting, props=p,
              end=f"{other} hugging the big red ball, laughing", scene=sc),
        _shot("Chase", [m] + ([o] if o else []), "wide", "i2v", "the ball rolling away on the grass, the characters running after it",
              [("0-2s", "the ball rolls away"), ("2-4s", "they run after it, laughing"), ("4-5s", "they slow down and giggle")],
              "on the rolling ball", "arms pumping while running", "giggly", [], "footsteps", setting, props=p, scene=sc),
    ]


def _block_rain(m, o, setting, hi):
    N = lambda t_hi, t_en: [{"speaker": "Narrator", "text": t_hi if hi else t_en}]
    S = lambda who, t_hi, t_en: [{"speaker": who, "text": t_hi if hi else t_en}]
    sc, p = "Rain", "a big yellow umbrella"
    who = [m] + ([o] if o else [])
    return [
        _shot("Clouds", [m], "medium", "i2v", f"{m} looking up at grey clouds",
              [("0-2s", f"{m} looks up"), ("2-4s", "a raindrop lands on the nose"), ("4-5s", f"{m} giggles and wipes the nose")],
              "up at the clouds", "right hand wipes the nose", "surprised", N("अचानक बारिश आ गई!", "Suddenly, it started to rain!"), "rain", setting, scene=sc),
        _shot("Umbrella", [m], "medium close-up", "flf", f"{m} holding a closed big yellow umbrella with both hands",
              [("0-2s", f"{m} looks at the umbrella"), ("2-4s", f"{m} pushes it open with both hands"), ("4-5s", f"{m} lifts it overhead")],
              "on the umbrella", "both hands push the umbrella open", "proud", [], "pop", setting, props=p,
              end=f"{m} holding the open yellow umbrella above the head with the right hand", scene=sc),
        _shot("Share", who, "medium", "i2v", f"{m} holding the open yellow umbrella, {o or 'a friend'} standing in the rain",
              [("0-2s", f"{m} sees {o or 'the friend'} getting wet"), ("2-4s", f"{m} steps closer and holds the umbrella over both"),
               ("4-5s", "both smile under the umbrella")], f"at {o or 'the friend'}", "right hand holds the umbrella handle",
              "kind", S(m, "आओ, साथ चलें!", "Come, let's share!"), "rain", setting, props=p, scene=sc),
        _shot("Puddles", who, "wide", "i2v", "two characters at the edge of a shallow puddle",
              [("0-2s", "they bend their knees"), ("2-4s", "they jump into the puddle with a splash"), ("4-5s", "they laugh")],
              "down at the puddle", "arms up while jumping", "joyful", N("छप-छप!", "Splish, splash!"), "boing", setting, scene=sc),
        _shot("Rainbow", who, "wide", "i2v", "the characters standing, looking at a rainbow in the sky",
              [("0-2s", "they look up"), ("2-4s", f"{m} points at the rainbow"), ("4-5s", "they smile in wonder")],
              "up at the rainbow", "right hand points up", "amazed", N("और फिर निकला इंद्रधनुष।", "And then, a rainbow."), "sparkle", setting,
              cam="the camera slowly tilts up", scene=sc),
    ]


DIALOGUE_TITLES = {"Gentle hands", "Which way?", "The wish", "The surprise"}


def builtin_story(chars, idea, n, setting, language):
    m = chars[0].get("name", "Pari")
    o = chars[1].get("name") if len(chars) > 1 else None
    hi = language == "hi"
    setting = setting or "a sunny park with flowers"
    if re.search(r"pupp|dog|kutt|pilla|पिल्ल|कुत्त", idea or "", re.I):
        st = _template_puppy(m, o, setting, hi)
    else:
        st = _template_generic(m, o, setting, idea, hi)
    for sh in st["shots"]:  # old templates use 'narration': give lines a speaker
        t = sh.pop("narration", "")
        sh["lines"] = [{"speaker": m if sh["title"] in DIALOGUE_TITLES else "Narrator", "text": t}] if t else []
        sh.setdefault("scene", "Main story")
    main, ending = st["shots"][:-1], st["shots"][-1]
    extra = []
    blocks = [_block_journey, _block_ball, _block_rain]
    i = 0
    while len(main) + len(extra) + 1 < n - 3:
        extra += blocks[i % len(blocks)](m, o, setting, hi)
        i += 1
    if len(main) + len(extra) + 1 < n:
        extra += _block_celebrate(m, o, setting, hi)
    cel = [x for x in extra if x.get("scene") == "Celebration"]
    rest = [x for x in extra if x.get("scene") != "Celebration"]
    body = main[:max(1, (len(main) + 1) // 2)] + rest + main[(len(main) + 1) // 2:] + cel
    shots = (body[: n - 1] if len(body) >= n - 1 else body) + [ending]
    if n < len(shots):
        shots = shots[: n - 1] + [ending]
    st["shots"] = shots
    return st


def _outline_prompt(chars, idea, n, setting, tone):
    child = any(c.get("kind") in CHILD_KINDS for c in chars)
    cast = "\n".join(f"- {c.get('name')}: {character_line(c)}" for c in chars)
    k = max(3, min(8, round(n / 4)))
    return f"""Plan a {n * 5}-second vertical short film ({n} shots of 5 seconds) made with an AI video model.
Cast:\n{cast}\nIdea: {idea or 'a sweet everyday adventure'}. Setting: {setting or 'simple, bright places'}. Tone: {tone or 'sweet'}.
{'Children are in this story: keep it wholesome, safe and age-appropriate.' if child else ''}
Split it into {k} scenes with a clear beginning, middle (a problem or wish, attempts, a turning point) and a warm ending.
The scene shot counts must add up to exactly {n}. Each scene has one setting.
Reply with JSON only: {{"title":"...","logline":"one sentence","scenes":[{{"name":"...","summary":"2-3 sentences of what happens","setting":"...","shots":4}}]}}"""


# JSON shapes for Ollama "structured outputs" (keeps small local models on track)
_LINE = {"type": "object", "properties": {"speaker": {"type": "string"}, "text": {"type": "string"}}, "required": ["speaker", "text"]}
_BEAT = {"type": "object", "properties": {"t": {"type": "string"}, "action": {"type": "string"}}, "required": ["t", "action"]}
SHOT_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "characters": {"type": "array", "items": {"type": "string"}}, "shot": {"type": "string"},
    "camera": {"type": "string"}, "setting": {"type": "string"}, "props": {"type": "string"},
    "mode": {"type": "string", "enum": ["i2v", "flf", "continue"]}, "start_frame": {"type": "string"},
    "end_frame": {"type": "string"}, "beats": {"type": "array", "items": _BEAT}, "eyes": {"type": "string"},
    "hands": {"type": "string"}, "emotion": {"type": "string"}, "lines": {"type": "array", "items": _LINE},
    "sfx": {"type": "string"}},
    "required": ["title", "characters", "shot", "mode", "start_frame", "beats", "lines"]}
STORY_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "logline": {"type": "string"},
                                                 "shots": {"type": "array", "items": SHOT_SCHEMA}},
                "required": ["title", "logline", "shots"]}
OUTLINE_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "logline": {"type": "string"},
    "scenes": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "summary": {"type": "string"}, "setting": {"type": "string"}, "shots": {"type": "integer"}},
        "required": ["name", "summary", "setting", "shots"]}}}, "required": ["title", "logline", "scenes"]}
IDEAS_SCHEMA = {"type": "object", "properties": {"ideas": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "idea": {"type": "string"}}, "required": ["title", "idea"]}}}, "required": ["ideas"]}
BRIEF_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "logline": {"type": "string"}, "idea": {"type": "string"},
    "roles": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                                          "required": ["name", "role"]}},
    "wish": {"type": "string"}, "problem": {"type": "string"}, "attempts": {"type": "string"}, "turning_point": {"type": "string"},
    "ending": {"type": "string"}, "lesson": {"type": "string"},
    "beats": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "logline", "idea", "roles", "beats"]}


def writers(cfg):
    """Who can write stories: Claude (API key), each installed Ollama model, the built-in templates."""
    url = cfg.get("ollama_url") or "http://127.0.0.1:11434"
    models = ollama_models(url) if cfg.get("use_ollama", True) else []
    out = [{"id": "auto", "label": "Automatic (best available)"}]
    if cfg.get("anthropic_api_key"):
        out.append({"id": "claude", "label": f"Claude ({cfg.get('anthropic_model') or 'claude-sonnet-4-5'})"})
    out += [{"id": f"ollama:{m}", "label": f"Ollama · {m} (on this Mac)"} for m in models]
    out.append({"id": "builtin", "label": "Built-in templates (no AI)"})
    return out


def _claude(cfg, prompt, max_tokens):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300, headers={
        "x-api-key": cfg["anthropic_api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": cfg.get("anthropic_model") or "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return _parse("".join(b.get("text", "") for b in r.json().get("content", [])))


def _ollama(cfg, model, prompt, schema, log):
    url = cfg.get("ollama_url") or "http://127.0.0.1:11434"
    body = {"model": model, "prompt": prompt, "stream": False, "format": schema or "json",
            "options": {"num_ctx": 16384, "temperature": 0.8},
            "system": "You are a careful children's film writer. Reply with valid JSON only, no comments."}
    r = requests.post(f"{url}/api/generate", timeout=1200, json=body)
    if r.status_code >= 400 and schema:  # older Ollama: no JSON-schema support, use plain JSON mode
        body["format"] = "json"
        r = requests.post(f"{url}/api/generate", timeout=1200, json=body)
    r.raise_for_status()
    return _parse(r.json().get("response", ""))


def _ask(cfg, prompt, log, max_tokens=8000, writer="auto", schema=None):
    """Ask the chosen writer. Returns (parsed json, writer name) or raises."""
    writer = writer or "auto"
    if writer == "builtin":
        raise RuntimeError("built-in templates chosen")
    errs = []
    if writer in ("auto", "claude") and cfg.get("anthropic_api_key"):
        try:
            return _claude(cfg, prompt, max_tokens), "Claude"
        except Exception as e:
            errs.append(f"Claude: {e}")
    if writer.startswith("ollama:") or writer == "auto":
        url = cfg.get("ollama_url") or "http://127.0.0.1:11434"
        models = ollama_models(url) if (cfg.get("use_ollama", True) or writer.startswith("ollama:")) else []
        model = writer.split(":", 1)[1] if writer.startswith("ollama:") else (cfg.get("ollama_model") or (models[0] if models else None))
        if model and (model in models or writer.startswith("ollama:")):
            try:
                log(f"Asking Ollama ({model}) — local models can take a minute or two…")
                return _ollama(cfg, model, prompt, schema, log), f"Ollama · {model}"
            except Exception as e:
                errs.append(f"Ollama {model}: {e}")
        elif writer.startswith("ollama:"):
            errs.append("Ollama is not running on this Mac (start the Ollama app).")
    raise RuntimeError("; ".join(errs) or "no AI writer available (add a Claude key or start Ollama)")


# ---------------- idea builder ----------------
ROLE_DEFAULTS = {"girl-child": "the brave, curious hero", "boy-child": "the loyal best friend", "woman": "the kind, wise helper",
                 "man": "the funny, clumsy uncle", "animal": "the playful pet sidekick"}


def brief_text(brief):
    """The idea-builder answers, as text for the story prompts."""
    if not brief:
        return ""
    parts = []
    roles = brief.get("roles") or []
    if isinstance(roles, dict):
        roles = [{"name": k, "role": v} for k, v in roles.items()]
    if roles:
        parts.append("Roles: " + "; ".join(f"{r.get('name')} = {r.get('role')}" for r in roles if r.get("role")))
    for k, label in (("wish", "What the hero wants"), ("problem", "What gets in the way"), ("attempts", "What they try"),
                     ("turning_point", "Turning point"), ("ending", "Ending"), ("lesson", "Message"), ("twist", "Fun twist")):
        if (brief.get(k) or "").strip():
            parts.append(f"{label}: {brief[k].strip()}")
    beats = [b for b in (brief.get("beats") or []) if str(b).strip()]
    if beats:
        parts.append("Plot beats in order: " + " | ".join(f"{i + 1}. {b}" for i, b in enumerate(beats)))
    return "\n".join(parts)


def _cast_text(chars):
    return "\n".join(f"- {c.get('name')}: {character_line(c)} (type: {c.get('kind')})" for c in chars)


IDEA_TEMPLATES = [
    ("The Lost Kite", "{m}'s favourite kite gets stuck high in a mango tree. {o} tries to climb, {p} finds a long stick, and {q} has the clever idea that finally brings it down — then they fly it together."),
    ("The Secret Birthday", "{m} wants to surprise {o} on their birthday. {p} keeps almost spilling the secret, a sudden rain ruins the decorations, but {q} helps turn the rainy evening into the best party ever."),
    ("The Missing Diya", "On Diwali night, {m}'s special diya goes missing. Following clues of flour footprints, {m} and {o} discover that {p} borrowed it to light the way for {q}."),
    ("The Talent Show", "{m} is too shy to dance at the school talent show. {o} teaches a silly move, {p} makes a costume, and when the music stops {q} starts clapping the beat so {m} dances anyway."),
    ("The Garden Seed", "{m} plants a tiny seed and waits impatiently. {o} waters it, {p} chases away the birds, and after a storm {q} helps them find the very first flower blooming."),
    ("Monsoon Boats", "{m} and {o} race paper boats in the monsoon puddles. {p}'s boat sinks, everyone is sad, until {q} shows them how to build one big boat together."),
    ("The Lost Puppy", "{m} finds a lost puppy in the park. {o} helps search for its home, {p} spots a poster, and {q} — the puppy's owner — cries happy tears."),
    ("Grandma's Magic Recipe", "{m} wants to make grandma's famous laddoos alone. Flour flies everywhere, {o} and {p} make it messier, but {q} shows them the secret ingredient: doing it together."),
]


def _fill(t, names):
    pad = names + ["a new friend", "a kind neighbour", "grandma", "a little puppy"]
    return t.format(m=pad[0], o=pad[1], p=pad[2], q=pad[3])


def builtin_ideas(chars, k=3):
    names = [c.get("name") for c in chars]
    picks = random.sample(IDEA_TEMPLATES, k)
    return {"ideas": [{"title": t, "idea": _fill(i, names)} for t, i in picks]}


def builtin_brief(chars, idea, brief):
    b = dict(brief or {})
    names = [c.get("name") for c in chars]
    m = names[0] if names else "Pari"
    roles = b.get("roles") or []
    have = {r.get("name") for r in roles if isinstance(r, dict) and r.get("role")}
    for c in chars:
        if c.get("name") not in have:
            roles.append({"name": c.get("name"), "role": "the hero" if c is chars[0] else ROLE_DEFAULTS.get(c.get("kind"), "a good friend")})
    b["roles"] = roles
    b.setdefault("wish", "")
    b["wish"] = b["wish"] or f"something special ({idea or 'a little adventure'})"
    b["problem"] = b.get("problem") or "an unexpected problem makes it hard"
    b["attempts"] = b.get("attempts") or f"{m} tries once and fails, then tries a new way"
    b["turning_point"] = b.get("turning_point") or (f"{names[1]} helps" if len(names) > 1 else "a clever idea")
    b["ending"] = b.get("ending") or "everyone celebrates together"
    b["lesson"] = b.get("lesson") or "kindness and teamwork make everything better"
    b["beats"] = b.get("beats") or [f"Meet {m} and friends", f"{m} wants {b['wish']}", f"Problem: {b['problem']}",
                                    f"Try: {b['attempts']}", f"Turning point: {b['turning_point']}", f"Ending: {b['ending']}"]
    b["title"] = b.get("title") or (idea[:40] if idea else f"{m}'s Adventure")
    b["logline"] = b.get("logline") or f"{m} wants {b['wish']}, but {b['problem']}."
    b["idea"] = (idea or "").strip() or b["logline"]
    return b


def brainstorm(cfg, chars, idea="", brief=None, language="en", writer="auto", mode="expand", log=print):
    """mode='ideas': 3 story ideas for this cast. mode='expand': turn a short idea into a full story brief."""
    child = any(c.get("kind") in CHILD_KINDS for c in chars)
    safe = "Children are in the cast: keep it wholesome, safe, age-appropriate, no danger or scary violence." if child else ""
    lang = {"hi": "Write the idea texts in simple English (dialogue will be in Hindi later).", "en": ""}.get(language, "")
    if mode == "ideas":
        prompt = f"""Suggest 3 different, charming short-film story ideas (about 1 minute each) for this cast:
{_cast_text(chars)}
Each idea uses EVERY cast member with a clear role, has a wish, a problem, a funny or touching middle, and a warm ending.
Ideas must be easy to show with simple visible actions (no reading, no phones, no tiny objects). {safe} {lang}
Reply with JSON only: {{"ideas":[{{"title":"...","idea":"3-4 sentences"}}]}}"""
        try:
            out, by = _ask(cfg, prompt, log, 1500, writer, IDEAS_SCHEMA)
            if out.get("ideas"):
                return out, by
        except Exception as e:
            log(f"AI ideas not used ({str(e)[:140]}).")
        return builtin_ideas(chars), "built-in"
    have = brief_text(brief or {})
    prompt = f"""Turn this short-film idea into a complete, well-structured story brief.
Cast (use every one, each with a clear role and a personality trait):
{_cast_text(chars)}
Idea: {idea or 'a sweet everyday adventure'}
{('Keep these choices the user already made:' + chr(10) + have) if have else ''}
Story shape: a hook in the first seconds; the hero WANTS something; a PROBLEM gets in the way; 2 attempts (one fails, often funny);
a TURNING POINT where another character helps or a clever idea appears; a warm ENDING; a one-line message.
Everything must be visual and simple to animate: big gestures, simple props (ball, kite, balloon, flower, gift box, umbrella), few locations.
{safe} {lang}
Reply with JSON only: {{"title":"...","logline":"one sentence","idea":"a 5-7 sentence story summary","roles":[{{"name":"...","role":"..."}}],
"wish":"...","problem":"...","attempts":"...","turning_point":"...","ending":"...","lesson":"...","beats":["6 to 10 short plot beats in order"]}}"""
    try:
        out, by = _ask(cfg, prompt, log, 2500, writer, BRIEF_SCHEMA)
        if out.get("idea"):
            return out, by
    except Exception as e:
        log(f"AI brief not used ({str(e)[:140]}).")
    return builtin_brief(chars, idea, brief), "built-in"


def plan_story(cfg, chars, idea, n, setting="", tone="", language="en", log=print, writer="auto", brief=None):
    n = max(2, min(MAX_SHOTS, int(n)))
    bt = brief_text(brief)
    if bt:
        idea = f"{idea}\nSTORY BRIEF (follow it):\n{bt}"
    try:
        if n <= 10:
            st, by = _ask(cfg, _llm_prompt(chars, idea, n, setting, tone, language), log, 8000, writer, STORY_SCHEMA)
            st = _clean(st, chars, n, language)
            if len(st["shots"]) < max(2, n - 1):
                raise RuntimeError("too few shots")
            log(f"Story written by {by}.")
            return st, by
        # long story: outline first, then the shots of each scene with the story so far as context
        ol, by = _ask(cfg, _outline_prompt(chars, idea, n, setting, tone), log, 3000, writer, OUTLINE_SCHEMA)
        scenes = ol.get("scenes") or []
        counts = [max(1, int(sc.get("shots", 3))) for sc in scenes]
        while sum(counts) > n:
            counts[counts.index(max(counts))] -= 1
        while sum(counts) < n:
            counts[counts.index(min(counts))] += 1
        shots, so_far = [], []
        for k, (sc, m) in enumerate(zip(scenes, counts)):
            ctx = (f"\nThis is SCENE {k + 1} of {len(scenes)} of the film \"{ol.get('title', '')}\" ({ol.get('logline', '')}).\n"
                   f"Scene {k + 1}: {sc.get('name', '')}: {sc.get('summary', '')}\n"
                   f"Story so far: {' '.join(so_far)[-1200:] or 'this is the opening scene'}\n"
                   + (f"The previous shot ended like this: {shots[-1].get('end_frame') or shots[-1].get('start_frame', '')}" if shots else "")
                   + ("\nThis is the FINAL scene: end warmly." if k == len(scenes) - 1 else "\nDo NOT end the story in this scene."))
            part, _ = _ask(cfg, _llm_prompt(chars, idea, m, sc.get("setting") or setting, tone, language, ctx), log, 7000, writer, STORY_SCHEMA)
            got = (part.get("shots") or [])[:m]
            for g in got:
                g["scene"] = sc.get("name", f"Scene {k + 1}")
            shots += got
            so_far.append(f"Scene {k + 1}: {sc.get('summary', '')}")
            log(f"Scene {k + 1}/{len(scenes)} written ({len(got)} shots).")
        st = _clean({"title": ol.get("title"), "logline": ol.get("logline"), "shots": shots}, chars, n, language)
        if len(st["shots"]) < n - 2:
            raise RuntimeError("too few shots")
        log(f"Story written by {by} ({len(scenes)} scenes).")
        return st, by
    except Exception as e:
        log(f"AI story writer not used ({str(e)[:160]}).")
    log("Story written from the built-in templates.")
    return _clean(builtin_story(chars, idea, n, setting, language), chars, n, language), "built-in"
