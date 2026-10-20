"""Choreography library: dance moves by energy and body part, end poses, and lyric -> gesture rules.

{he}/{his}/{him} are filled in for the character. Energy: 1 calm, 2 medium, 3 high.
"who": "all" = any character, "human" = people only (not animals), "grown" = adults only, "animal" = animals only.
"""
import random
import re

# (text, energy, part, who)
MOVES = [
    # ---- calm / flowing (1)
    ("sways {his} whole body slowly side to side with both arms floating up and down like waves", 1, "arms", "human"),
    ("draws big slow circles in the air with both hands, following them with {his} eyes", 1, "arms", "human"),
    ("rocks gently from heel to toe while rolling {his} shoulders in time with the music", 1, "shoulders", "human"),
    ("turns slowly in a full circle with arms open wide, outfit flowing", 1, "spin", "all"),
    ("steps to the side and back with soft knees, hands making graceful flower shapes", 1, "feet", "human"),
    ("tilts {his} head side to side with a sweet smile while hands sway like leaves", 1, "head", "human"),
    ("lifts one arm high and lowers the other in a slow wave, then switches arms", 1, "arms", "human"),
    ("does a slow graceful bow forward then rises up with arms opening like a blooming flower", 1, "body", "human"),
    ("sways {his} head and body gently from side to side, tail swishing slowly", 1, "body", "animal"),
    # ---- medium / lively (2)
    ("claps on every beat while stepping side to side, bouncing at the knees", 2, "hands", "human"),
    ("does a cheerful step-touch to the left and right, swinging both arms forward and back", 2, "feet", "human"),
    ("rolls {his} hands in front of the chest like a little wheel, then flicks them open to the sides", 2, "hands", "human"),
    ("shrugs {his} shoulders up and down to the beat while bobbing {his} head", 2, "shoulders", "human"),
    ("does a playful heel-toe tap with one foot, then the other, hands on hips", 2, "feet", "human"),
    ("twists side to side at the waist with elbows bent, like a happy twist dance", 2, "body", "human"),
    ("points up to the sky with one hand then down to the floor with the other, switching on the beat", 2, "arms", "human"),
    ("marches on the spot lifting {his} knees high while swinging arms", 2, "feet", "human"),
    ("does a quick half turn to the left, then a half turn back to the right, arms out", 2, "spin", "all"),
    ("shimmies {his} shoulders forward then back while giggling", 2, "shoulders", "human"),
    ("does the Bollywood light-bulb move: one hand twists up high while the other twists down low, then swaps", 2, "hands", "human"),
    ("does a bhangra step: one hand raised high, the other on the hip, hopping lightly from foot to foot", 2, "feet", "human"),
    ("waves both hands above {his} head from side to side like a happy crowd", 2, "arms", "human"),
    ("steps forward toward the camera with a big smile, then steps back again", 2, "travel", "all"),
    ("slides one foot out to the side and back while snapping fingers", 2, "feet", "grown"),
    ("bobs {his} head and pats {his} knees, then claps twice", 2, "hands", "human"),
    ("bounces on {his} paws and wiggles {his} tail happily", 2, "body", "animal"),
    ("lifts one paw and waves it in rhythm, then the other paw", 2, "arms", "animal"),
    # ---- high / energetic (3)
    ("does a fast full spin on the spot, arms out wide, outfit flaring, then lands facing the camera", 3, "spin", "all"),
    ("jumps up with both arms thrown high in the air, lands and jumps again", 3, "jump", "all"),
    ("does a quick jumping jack, arms up and down twice with big energy", 3, "jump", "human"),
    ("does fast bhangra shoulder shrugs with both arms raised high, bouncing on the knees", 3, "shoulders", "human"),
    ("hops in a small circle on one foot, then the other, clapping above {his} head", 3, "travel", "human"),
    ("kicks one leg forward, then the other, with arms punching the air on every beat", 3, "feet", "human"),
    ("does a big dramatic arm sweep from the floor to the sky and finishes with a star jump", 3, "jump", "human"),
    ("does a double spin with one hand raised, then strikes a confident pose", 3, "spin", "human"),
    ("does an energetic side shuffle to the left and right with fast stepping feet and pumping arms", 3, "feet", "human"),
    ("leans forward and back while clapping fast, then throws both hands up with a laugh", 3, "hands", "human"),
    ("does a fast Bollywood jhatka: quick shoulder pop with one hand on the waist and the other pointing forward", 3, "shoulders", "grown"),
    ("does a quick cross-step and spins out of it, arms stretching wide", 3, "spin", "grown"),
    ("runs in a tiny happy circle, then jumps and lands facing the camera", 3, "travel", "all"),
    ("hops up and down with all paws, then spins in a quick circle chasing {his} tail", 3, "jump", "animal"),
]

# Clear, stable end poses (each clip ends in one so the next clip can continue from it)
END_POSES = {
    1: ["standing with arms open wide and a soft smile", "with one hand on the heart and the other reaching out",
        "standing with palms pressed together in a namaste"],
    2: ["with one hand on the hip and the other waving at the camera", "pointing at the camera with a big grin",
        "with both arms raised in a V shape", "standing with both thumbs up and a cheeky smile",
        "with hands framing the face and a big smile"],
    3: ["in a star pose, arms and legs spread wide, laughing", "landing in a confident pose, one arm raised high",
        "with both fists pumped in the air", "in a proud superhero pose with hands on hips"],
}
HOME_POSE = "facing the camera with a big proud smile, hands on hips"

# lyric word -> gesture. Words: English, Hindi in Latin letters, and Devanagari.
LYRIC_GESTURES = [
    (["clap", "claps", "clapping", "taali", "tali", "taaliyan", "ताली", "तालियाँ"], "claps hands right on the beat"),
    (["dance", "dancing", "nach", "naach", "nache", "nachle", "naacho", "nacho", "thumka", "नाच", "नाचे", "नाचो", "ठुमका"],
     "breaks into a bigger, faster dance step"),
    (["moon", "chand", "chanda", "chaand", "sky", "aasman", "asmaan", "aasmaan", "star", "stars", "tara", "taare",
      "चाँद", "चंदा", "आसमान", "तारे", "तारा"], "reaches both arms high up toward the sky"),
    (["heart", "love", "dil", "pyaar", "pyar", "mohabbat", "दिल", "प्यार"], "makes a heart shape with both hands"),
    (["come", "aaja", "aaj", "aao", "aa", "आजा", "आओ"], "beckons the viewer with both hands"),
    (["spin", "round", "around", "ghoom", "ghoomar", "gol", "घूम", "घूमर"], "spins around once"),
    (["jump", "jumping", "kood", "uchhal", "उछल", "कूद"], "jumps up high"),
    (["fly", "flying", "bird", "birds", "udd", "ud", "chidiya", "उड़", "चिड़िया"], "flaps both arms like wings"),
    (["rain", "barish", "baarish", "barsaat", "बारिश", "बरसात"], "flutters fingers down like falling raindrops"),
    (["sun", "suraj", "sunshine", "धूप", "सूरज"], "spreads arms out like sunshine rays"),
    (["hello", "hi", "namaste", "namaskar", "नमस्ते"], "folds hands in a namaste"),
    (["bye", "goodbye", "tata", "alvida", "अलविदा"], "waves goodbye"),
    (["smile", "laugh", "hasi", "haso", "hasna", "हँसी", "हँसो"], "laughs with a huge smile"),
    (["eyes", "aankh", "aankhen", "आँख", "आँखें"], "frames the eyes with fingers"),
    (["flower", "flowers", "phool", "फूल"], "opens both hands like a blooming flower"),
    (["happy", "joy", "khush", "khushi", "खुश", "खुशी"], "jumps up with joy"),
    (["run", "running", "daud", "bhaag", "दौड़", "भाग"], "runs on the spot"),
    (["up", "high", "upar", "ऊपर"], "points up high"),
    (["down", "low", "neeche", "niche", "नीचे"], "crouches down low and pops back up"),
    (["peacock", "mor", "मोर"], "makes a peacock beak with one hand above the head"),
    (["drum", "dhol", "dhak", "ढोल"], "pretends to beat a big drum"),
    (["shake", "hila", "hilaa", "हिला"], "shakes both hands fast"),
    (["walk", "chal", "chalo", "चल", "चलो"], "walks forward in rhythm"),
    (["you", "tu", "tum", "tujhe", "तू", "तुम"], "points playfully at the camera"),
    (["me", "main", "mai", "mujhe", "मैं", "मुझे"], "points to {him}self with both thumbs"),
]

CAMERAS = {
    1: ["the camera slowly pushes in", "the camera holds steady at a medium shot", "the camera gently floats to the side"],
    2: ["the camera bounces slightly with the beat", "the camera slowly arcs around a little",
        "the camera holds steady, full body in frame", "the camera slowly pulls back to show the full body"],
    3: ["the camera quickly pushes in then settles", "the camera swings around a little with the spin",
        "the camera stays wide to show the full body moving", "the camera does a quick low-angle push in"],
}
ENERGY_WORDS = {
    1: "Graceful, flowing dancing with soft continuous movement",
    2: "Lively, playful dancing with clear, bouncy movement on every beat",
    3: "Big, energetic full-body dancing with fast, sharp, joyful movement",
}


def pronouns(kind):
    if kind in ("boy-child", "man"):
        return {"he": "he", "his": "his", "him": "him", "He": "He"}
    if kind == "animal":
        return {"he": "it", "his": "its", "him": "it", "He": "It"}
    return {"he": "she", "his": "her", "him": "her", "He": "She"}


def fill(text, kind):
    p = pronouns(kind)
    return re.sub(r"\{(he|his|him|He)\}", lambda m: p[m.group(1)], text)


def allowed(who, kind):
    if kind == "animal":
        return who in ("all", "animal")
    if who == "animal":
        return False
    if who == "grown":
        return kind in ("woman", "man")
    return True


def energy_num(level):
    return {"calm": 1, "medium": 2, "high": 3}.get(level, level if isinstance(level, int) else 2)


def tempo_words(bpm):
    if not bpm:
        return ""
    if bpm >= 125:
        return f"quick bouncy moves timed to a fast {int(bpm)} BPM beat"
    if bpm >= 95:
        return f"moves timed to a steady {int(bpm)} BPM beat"
    return f"slower, swaying moves timed to a relaxed {int(bpm)} BPM beat"


def lyric_gestures(lines, kind):
    """Gestures suggested by the lyric words, in order, no duplicates."""
    found = []
    for line in lines or []:
        low = line.lower()
        words = set(re.findall(r"[\w']+", low))
        for keys, gesture in LYRIC_GESTURES:
            for k in keys:
                hit = (k in low) if re.search(r"[ऀ-ॿ]", k) else (k in words)
                if hit:
                    g = fill(gesture, kind)
                    if g not in found:
                        found.append(g)
                    break
    return found[:2]


def pick(energy, kind, used, last_part, rng=random):
    """A move at this energy, not used before in the reel, using a different body part than the last clip."""
    for spread in (0, 1, 2):
        pool = [m for m in MOVES if abs(m[1] - energy) <= spread and allowed(m[3], kind) and m[0] not in used]
        fresh = [m for m in pool if m[2] != last_part] or pool
        if fresh:
            exact = [m for m in fresh if m[1] == energy]
            m = rng.choice(exact or fresh)
            used.add(m[0])
            return m
    m = rng.choice([m for m in MOVES if allowed(m[3], kind)])
    return m


def examples(energy, kind, k=8, rng=random):
    pool = [fill(m[0], kind) for m in MOVES if m[1] == energy and allowed(m[3], kind)]
    rng.shuffle(pool)
    return pool[:k]
