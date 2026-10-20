"""Ready-made original characters for the image gallery, and the job that generates them on Comfy Cloud.

All characters are invented (no real people, no known cartoon characters). Children are always cartoon-styled
and modestly dressed. Every image is a full-body, front-facing start pose that works well for Wan 2.2.
"""
import os
import threading
import time

from . import credits, imagegen, library
from .comfy import ComfyCloud, ComfyError

# (name, look, outfit, style, setting)
GIRLS = [
    ("Meera", "a cheerful 6-year-old Indian girl with two braids and a red bindi", "a yellow lehenga with tiny mirror work", "3D animated cartoon", "a marigold-decorated courtyard"),
    ("Zoya", "a 5-year-old girl with curly black hair and big brown eyes", "a pink frock with white polka dots and pink sneakers", "3D animated cartoon", "a sunny garden with flowers"),
    ("Anika", "a 7-year-old girl with a short bob haircut and freckles", "a purple ghagra choli with gold borders", "3D animated cartoon", "a festive stage with fairy lights"),
    ("Kavya", "a 4-year-old girl with a high ponytail and chubby cheeks", "a blue silk pavadai with a gold belt", "3D animated cartoon", "a South Indian temple courtyard with rangoli"),
    ("Lila", "a 6-year-old girl with wavy auburn hair and a big gap-toothed smile", "a mint green tutu dress and white ballet flats", "3D animated cartoon", "a bright dance studio"),
    ("Aisha", "a 6-year-old girl with dark curly hair in two puffs", "an orange and green festive salwar suit", "Anime", "a colourful street fair"),
    ("Mei", "a 5-year-old East Asian girl with a neat fringe", "a red qipao-style kids dress with gold flowers", "3D animated cartoon", "a lantern festival square"),
    ("Riya", "a 7-year-old girl with long plaited hair and jasmine flowers", "a green and gold silk half-saree", "3D animated cartoon", "a festive courtyard with diyas"),
    ("Tara", "a 5-year-old girl with short curly hair and round glasses", "yellow dungarees over a striped t-shirt", "Claymation", "a playroom with toys"),
    ("Nia", "a 6-year-old girl with box braids and a bright smile", "a sunflower-print dress and red sandals", "3D animated cartoon", "a sunny park"),
    ("Ishani", "a 6-year-old girl with a side ponytail and dimples", "a white and red Bengali-style frock", "Anime", "a Durga Puja pandal with lights"),
    ("Pihu", "a 4-year-old girl with tiny pigtails and rosy cheeks", "a sparkly pink lehenga", "3D animated cartoon", "a birthday party with balloons"),
    ("Sana", "a 7-year-old girl with long straight hair and a headband", "a sky-blue anarkali dress", "3D animated cartoon", "a wedding sangeet stage"),
    ("Elsa-May", "a 5-year-old girl with light blonde curls", "a lavender raincoat and yellow boots", "Claymation", "a rainy street with puddles"),
    ("Gauri", "a 6-year-old girl with a long braid and a nose stud sticker", "a nine-yard style kids saree in purple and green", "3D animated cartoon", "a Ganesh festival pandal"),
    ("Amara", "a 6-year-old girl with dark skin and a flower crown", "a bright kente-pattern dress", "3D animated cartoon", "a village celebration"),
]
BOYS = [
    ("Aarav", "a 6-year-old Indian boy with neat side-parted hair and big eyes", "a yellow kurta with white pyjama", "3D animated cartoon", "a marigold-decorated courtyard"),
    ("Kabir", "a 5-year-old boy with messy curly hair and a cheeky grin", "a blue denim jacket and red sneakers", "3D animated cartoon", "a city rooftop at sunset"),
    ("Vihaan", "a 7-year-old boy with short spiky hair", "a green sherwani with gold buttons", "3D animated cartoon", "a wedding stage with flowers"),
    ("Arjun", "a 6-year-old boy with a small turban and a wide smile", "a bright orange bhangra kurta and lungi", "3D animated cartoon", "a mustard field in Punjab"),
    ("Leo", "a 5-year-old boy with ginger hair and freckles", "a dinosaur t-shirt and shorts", "Claymation", "a playground"),
    ("Kenji", "a 6-year-old East Asian boy with a bowl haircut", "a red hoodie and cargo shorts", "Anime", "a cherry blossom park"),
    ("Ishaan", "a 4-year-old boy with chubby cheeks and a tilak", "a white dhoti and yellow kurta", "3D animated cartoon", "a temple courtyard"),
    ("Omar", "a 7-year-old boy with short black curly hair", "a cream kurta with a small embroidered vest", "3D animated cartoon", "an Eid street festival with lanterns"),
    ("Rohan", "a 6-year-old boy with glasses and a big smile", "a cricket jersey and shorts", "3D animated cartoon", "a cricket ground"),
    ("Kofi", "a 6-year-old boy with dark skin and a short fade haircut", "a colourful patterned shirt and shorts", "3D animated cartoon", "a sunny beach"),
    ("Aditya", "a 7-year-old boy with wavy hair", "a maroon kurta with golden jacket", "Anime", "a Diwali street with diyas"),
    ("Neel", "a 5-year-old boy with a buzz cut and dimples", "a blue sailor suit", "Claymation", "a harbour with boats"),
    ("Dev", "a 6-year-old boy with a tuft of hair", "a superhero-style red cape over a t-shirt", "3D animated cartoon", "a toy-filled bedroom"),
    ("Sami", "a 4-year-old boy with big curly hair", "green overalls and a yellow t-shirt", "3D animated cartoon", "a farm with a red barn"),
    ("Yash", "a 7-year-old boy with a side swept fringe", "a Gujarati kediyu top with mirror work and a dhoti", "3D animated cartoon", "a Navratri garba ground"),
    ("Mateo", "a 6-year-old boy with brown wavy hair", "a white shirt with a red bandana and jeans", "3D animated cartoon", "a fiesta street with paper flags"),
]
WOMEN = [
    ("Ananya", "a graceful Indian woman in her 20s with long wavy hair and a bindi", "a red and gold Banarasi saree", "3D animated cartoon", "a festive courtyard with diyas"),
    ("Priya", "a young Indian woman with a high ponytail and kohl eyes", "a teal anarkali suit with dupatta", "Stylised semi-realistic", "a palace terrace at sunset"),
    ("Neha", "a woman in her 30s with a short pixie haircut", "a mustard yellow jumpsuit and white sneakers", "3D animated cartoon", "a modern dance studio"),
    ("Kiran", "a Punjabi woman with long braids and a phulkari dupatta", "a bright pink salwar kameez", "3D animated cartoon", "a mustard field"),
    ("Lakshmi", "a South Indian woman with jasmine in her hair and gold jewellery", "a Kanjeevaram silk saree in green and magenta", "Stylised semi-realistic", "a temple courtyard with lamps"),
    ("Sofia", "a Latina woman with dark curly hair and hoop earrings", "a flowing red flamenco-inspired dress", "Stylised semi-realistic", "a plaza at night with string lights"),
    ("Hana", "a young East Asian woman with a sleek bob", "a pastel streetwear hoodie and pleated skirt", "Anime", "a neon city street"),
    ("Zara", "a woman with long straight black hair and bold eyeliner", "a sequinned emerald lehenga", "Photorealistic", "a glamorous wedding stage"),
    ("Grace", "a Black woman with natural afro hair and a big smile", "a bright yellow wrap dress", "Photorealistic", "a sunny rooftop garden"),
    ("Meenal", "a Marathi woman with a nath nose ring and a bun with gajra", "a nauvari saree in purple and gold", "3D animated cartoon", "a Gudi Padwa celebration street"),
    ("Isha", "a fit woman in her 20s with a long braid", "a sporty crop jacket and joggers", "Stylised semi-realistic", "an urban basketball court"),
    ("Rukhsar", "a woman with kohl eyes and a dupatta over her hair", "an embroidered ivory sharara suit", "Stylised semi-realistic", "a Mughal garden with fountains"),
    ("Emma", "a woman in her 30s with red hair in a messy bun", "a denim shirt dress and boots", "Claymation", "a country fair"),
    ("Devi", "a Bengali woman with a big red bindi and a loose bun", "a white saree with a red border", "3D animated cartoon", "a Durga Puja pandal"),
    ("Aiko", "a young Japanese woman with long hair and a flower clip", "a modern floral summer yukata", "Anime", "a summer festival with lanterns"),
    ("Mira", "a grandmother in her 60s with grey hair in a bun and kind eyes", "a soft blue cotton saree and shawl", "3D animated cartoon", "a cosy home courtyard"),
]
MEN = [
    ("Rahul", "a young Indian man with short wavy hair and a light beard", "a white kurta with a Nehru jacket", "3D animated cartoon", "a festive courtyard with diyas"),
    ("Gurpreet", "a Sikh man with a bright turban and a full beard", "an orange bhangra kurta with a waistcoat and lungi", "3D animated cartoon", "a Punjabi village with mustard fields"),
    ("Vikram", "a tall man in his 30s with slicked-back hair", "a black sherwani with gold embroidery", "Stylised semi-realistic", "a palace hall"),
    ("Arnav", "a young man with curly hair and an earring", "an oversized streetwear hoodie, cargo pants and sneakers", "Anime", "a graffiti street"),
    ("Suresh", "a South Indian man with a moustache", "a white veshti and silk shirt", "3D animated cartoon", "a temple festival"),
    ("Carlos", "a Latino man with a neat beard", "a white guayabera shirt and linen trousers", "Stylised semi-realistic", "a beach party at sunset"),
    ("Jin", "a young East Asian man with a K-pop style haircut", "a pastel suit with sneakers", "Anime", "a concert stage with lights"),
    ("Imran", "a man with short hair and a trimmed beard", "an ivory pathani suit", "Photorealistic", "an Eid evening street with lanterns"),
    ("David", "a Black man with a short beard and a warm smile", "a mustard sweater and chinos", "Photorealistic", "a city park"),
    ("Mohan", "a cheerful grandfather in his 60s with white hair and round glasses", "a cream kurta with a shawl", "3D animated cartoon", "a cosy home courtyard"),
    ("Karan", "a fit young man with a fade haircut", "a sporty tank top and track pants", "Stylised semi-realistic", "a gym-style dance floor"),
    ("Raju", "a jolly man with a big moustache and round face", "a bright checked lungi and shirt", "Claymation", "a village fair"),
    ("Ethan", "a young man with blond hair and a denim jacket", "a white t-shirt, jeans and boots", "3D animated cartoon", "a country barn dance"),
    ("Aditya Rao", "a groom with a small moustache and a safa turban", "a gold sherwani", "3D animated cartoon", "a baraat procession street"),
    ("Tenzin", "a young Himalayan man with a warm smile", "a colourful traditional chuba coat", "3D animated cartoon", "a mountain village with prayer flags"),
    ("Sam", "a man in his 40s with a salt-and-pepper beard", "a Hawaiian shirt and shorts", "Claymation", "a tropical beach"),
]
ANIMALS = [
    ("Bholu", "a fluffy golden retriever puppy standing on its hind legs, big floppy ears", "a tiny red bandana", "3D animated cartoon", "a sunny backyard"),
    ("Mishti", "a chubby orange tabby kitten standing upright, huge green eyes", "a small pink bow", "3D animated cartoon", "a cosy living room"),
    ("Gappu", "a baby elephant with big ears standing on its hind legs", "a decorated festival headpiece and anklets", "3D animated cartoon", "a temple courtyard"),
    ("Chiku", "a round fluffy panda cub standing upright", "a little green vest", "3D animated cartoon", "a bamboo forest"),
    ("Moti", "a cheerful monkey standing upright with a long curly tail", "a yellow cap", "3D animated cartoon", "a jungle clearing"),
    ("Rani", "a graceful peacock with a fanned colourful tail", "no clothes", "3D animated cartoon", "a palace garden"),
    ("Toffee", "a tiny brown teddy-bear-like bear cub standing upright", "red dungarees", "Claymation", "a honey-coloured meadow"),
    ("Pingo", "a small penguin standing upright, rosy cheeks", "a blue scarf", "3D animated cartoon", "an icy party with snow"),
    ("Sheru", "a friendly lion cub standing on its hind legs with a small fluffy mane", "a gold crown", "3D animated cartoon", "a savanna at sunset"),
    ("Bunny", "a fluffy white rabbit standing upright with long ears", "a carrot-patterned bow tie", "3D animated cartoon", "a carrot garden"),
    ("Coco", "a clever parrot with bright green and red feathers, standing upright", "tiny sunglasses", "3D animated cartoon", "a tropical tree house"),
    ("Dumdum", "a chubby baby hippo standing upright", "a pink tutu", "Claymation", "a riverside"),
    ("Foxy", "a small red fox standing upright with a bushy tail", "a green scarf", "Anime", "an autumn forest"),
    ("Kaalu", "a playful black Labrador puppy standing upright", "a blue collar with a bell", "3D animated cartoon", "a park with a fountain"),
    ("Moo", "a cute spotted cow calf standing upright", "a bell and a flower garland", "3D animated cartoon", "a village farm"),
    ("Sunny", "a fluffy yellow duckling standing upright", "a tiny rain hat", "3D animated cartoon", "a pond with lily pads"),
]
KIND_WORD = {"girl-child": "girl", "boy-child": "boy", "woman": "woman", "man": "man", "animal": "animal"}
GROUPS = [("girl-child", "Girls", GIRLS), ("boy-child", "Boys", BOYS), ("woman", "Women", WOMEN),
          ("man", "Men", MEN), ("animal", "Animals", ANIMALS)]


def _slug(s):
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")


def presets():
    out = []
    for kind, label, items in GROUPS:
        for name, look, outfit, style, setting in items:
            out.append({"id": f"{kind}-{_slug(name)}", "kind": kind, "group": label, "name": name, "look": look,
                        "outfit": "" if outfit == "no clothes" else outfit, "style": style, "setting": setting})
    return out


def prompt_for(p):
    child = p["kind"].endswith("child")
    style = {"3D animated cartoon": "3D animated cartoon render, soft rounded shapes, expressive big eyes, cute",
             "Anime": "anime illustration, clean line art, vibrant cel shading",
             "Claymation": "claymation stop-motion style, handmade clay texture",
             "Stylised semi-realistic": "stylised semi-realistic 3D character render",
             "Photorealistic": "photorealistic portrait photo of a fictional person, natural skin"}.get(p["style"], p["style"])
    pose = ("Facing the camera with a happy expression" if p["kind"] == "animal"
            else "Standing straight facing the camera, hands on hips, big friendly smile")
    outfit = f", wearing {p['outfit']}" if p["outfit"] else ""
    safe = ", age-appropriate modest clothing, wholesome" if child else ""
    return (f"{style}. Full-body shot of one character: {p['look']}{outfit}{safe}. {pose}, "
            f"the whole body visible from head to feet with space above the head and below the feet, centred, "
            f"in {p['setting']}, background slightly soft, bright even lighting, vertical 9:16 composition, high detail.")


def existing_presets():
    return {i.get("preset") for i in library.images() if i.get("preset")}


JOB = {"state": "idle", "done": 0, "total": 0, "current": "", "errors": [], "engine": "", "log": [], "credits": 0.0}
CONTROL = {"stop": False}


def _log(msg):
    JOB["log"] = (JOB["log"] + [time.strftime("%H:%M:%S ") + msg])[-60:]
    print("[gallery]", msg, flush=True)


def start(cfg, ids=None, kinds=None, again=False, connected=lambda: True):
    if JOB["state"] == "running":
        raise RuntimeError("The gallery is already being built.")
    have = set() if again else existing_presets()
    todo = [p for p in presets() if (not ids or p["id"] in ids) and (not kinds or p["kind"] in kinds) and p["id"] not in have]
    if not todo:
        raise RuntimeError("Those gallery images are already in your library.")
    cloud = ComfyCloud(cfg.get("comfy_api_key"), cfg.get("comfy_base_url") or "https://cloud.comfy.org", log=_log)
    JOB.update(state="running", done=0, total=len(todo), current="", errors=[], engine="", log=[], credits=0.0)
    CONTROL["stop"] = False
    threading.Thread(target=_run, args=(cloud, todo, connected), daemon=True).start()
    return JOB


def _run(cloud, todo, connected):
    try:
        engines = imagegen.detect(cloud, log=_log)
        _log(f"Image engine: {engines[0]['name']}" + (f" (fallbacks: {', '.join(e['name'] for e in engines[1:])})" if len(engines) > 1 else ""))
        tmp_dir = os.path.join(library.DATA, "gallery_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        for p in todo:
            if CONTROL["stop"] or not connected():
                JOB["state"] = "stopped"
                _log("Stopped.")
                return
            JOB["current"] = f"{p['group']}: {p['name']}"
            dest = os.path.join(tmp_dir, f"{p['id']}.png")
            try:
                eng, secs, engines = imagegen.generate(
                    cloud, engines, prompt_for(p), dest, log=_log,
                    should_stop=lambda: CONTROL["stop"] or not connected())
                JOB["engine"] = eng["name"]
                cr = credits.record("image", secs, note=p["id"])
                JOB["credits"] = round(JOB["credits"] + cr, 1)
                jpg = dest[:-4] + ".jpg"
                try:  # JPEG keeps the library small (about 0.2 MB per image)
                    from PIL import Image
                    Image.open(dest).convert("RGB").save(jpg, quality=90)
                    os.remove(dest)
                except Exception:
                    jpg = dest
                with open(jpg, "rb") as fh:
                    library.add_image(fh.read(), f"{p['kind']}_{_slug(p['name'])}{os.path.splitext(jpg)[1]}",
                                      title=f"{p['name']} ({KIND_WORD[p['kind']]})",
                                      tags=f"{p['group'].lower()} {p['style'].lower()} gallery",
                                      meta={k: p[k] for k in ("kind", "style", "look", "outfit", "name")} | {"preset": p["id"]})
                os.remove(jpg)
                _log(f"{p['name']}: done in {secs:.0f} s (about {cr:.1f} credits).")
            except ComfyError as e:
                JOB["errors"].append(f"{p['name']}: {e}")
                _log(f"{p['name']}: failed ({e}).")
                if any(code in str(e) for code in ("401", "402", "429")) or "No supported" in str(e):
                    JOB["state"] = "error"
                    return
            JOB["done"] += 1
        JOB["state"] = "done"
        JOB["current"] = ""
        _log(f"Finished: {JOB['done'] - len(JOB['errors'])} new images, about {JOB['credits']:.0f} credits.")
    except Exception as e:
        JOB["state"] = "error"
        JOB["errors"].append(str(e))
        _log(f"Stopped: {e}")
