"""Make an original, stylised character that resembles a person in a photo — only with that person's consent.

Rules built in:
  * a consent tick-box is required for every photo (stored with the result, with the time);
  * styles are always stylised (3D cartoon, anime, claymation, semi-realistic) — never photorealistic, so the
    result cannot pass as a real photo or video of the person; children are always cartoon-style;
  * the photo is only sent to Comfy Cloud to make the drawing, then deleted from ReelStudio; it is never added
    to the library.
"""
import os
import threading
import time
import uuid

from . import credits, imagegen, library
from .comfy import ComfyCloud, ComfyError

TMP = os.path.join(library.DATA, "photo_tmp")
STYLES = {
    "3D animated cartoon": "a 3D animated cartoon character render with soft rounded shapes and big expressive eyes",
    "Anime": "an anime character illustration with clean line art and cel shading",
    "Claymation": "a claymation stop-motion character with a handmade clay texture",
    "Stylised semi-realistic": "a stylised semi-realistic 3D game character render (clearly not a photograph)",
}
CHILD_STYLES = ("3D animated cartoon", "Anime", "Claymation")
JOB = {"state": "idle", "done": 0, "total": 0, "results": [], "log": [], "error": None}


def _log(msg):
    JOB["log"] = (JOB["log"] + [time.strftime("%H:%M:%S ") + msg])[-40:]
    print("[photo]", msg, flush=True)


def prompt_for(kind, style, outfit="", notes="", variant=0):
    child = kind.endswith("child")
    if child and style not in CHILD_STYLES:
        style = "3D animated cartoon"
    look = STYLES.get(style, STYLES["3D animated cartoon"])
    pose = ["standing straight facing the camera, hands on hips, big friendly smile",
            "standing facing the camera, waving with one hand, happy smile",
            "standing facing the camera, arms relaxed, cheerful smile"][variant % 3]
    wear = f"wearing {outfit}" if outfit else "wearing the same kind of clothes as in the photo"
    extra = f" {notes.strip().rstrip('.')}." if notes.strip() else ""
    safe = " Age-appropriate, modest clothing, wholesome." if child else ""
    return (f"Turn the person in the reference photo into {look}. Keep them recognisable: the same face shape, "
            f"hairstyle, hair colour, skin tone, eye shape, eyebrows, glasses or other accessories, and smile. "
            f"Full-body character from head to feet, {pose}, {wear}.{extra}{safe} Plain soft pastel background, "
            f"bright even lighting, centred, vertical 9:16, no text. It must look like an illustrated character, "
            f"not a photograph.")


def start(cfg, photo_bytes, ext, name, kind, style, outfit="", notes="", variations=2, connected=lambda: True):
    if JOB["state"] == "running":
        raise RuntimeError("A photo character is already being made.")
    if kind.endswith("child") and style not in CHILD_STYLES:
        style = "3D animated cartoon"
    if style not in STYLES:
        style = "3D animated cartoon"
    os.makedirs(TMP, exist_ok=True)
    path = os.path.join(TMP, f"photo_{uuid.uuid4().hex[:8]}{ext}")
    from PIL import Image, ImageOps
    import io
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    img.thumbnail((1536, 1536))
    path = os.path.splitext(path)[0] + ".png"
    img.save(path)
    cloud = ComfyCloud(cfg.get("comfy_api_key"), cfg.get("comfy_base_url") or "https://cloud.comfy.org", log=_log)
    JOB.update(state="running", done=0, total=max(1, min(3, int(variations))), results=[], log=[], error=None)
    meta = {"name": name, "kind": kind, "style": style, "outfit": outfit, "notes": notes,
            "consent": time.strftime("%Y-%m-%d %H:%M:%S")}
    threading.Thread(target=_run, args=(cloud, path, meta, connected), daemon=True).start()
    return JOB


def _run(cloud, path, meta, connected):
    try:
        engines = imagegen.detect_edit(cloud, log=_log)
        if not engines:
            raise ComfyError("No image-edit model was found on your Comfy Cloud account (Qwen-Image-Edit or FLUX Kontext). "
                             "A photo can only be turned into a character with one of these.")
        ref = cloud.upload_image(path)
        for v in range(JOB["total"]):
            if not connected():
                raise ComfyError("Disconnected from Comfy Cloud.")
            _log(f"Drawing version {v + 1} of {JOB['total']}…")
            dest = os.path.join(TMP, f"out_{uuid.uuid4().hex[:8]}.png")
            eng, secs, engines, _ = imagegen.edit_or_draw(
                cloud, engines, None, prompt_for(meta["kind"], meta["style"], meta["outfit"], meta["notes"], v),
                None, [ref], dest, width=768, height=1360, should_stop=lambda: not connected(), log=_log, allow_text=False)
            if "text only" in eng:
                raise ComfyError("The image-edit model was not available, so the photo could not be used.")
            credits.record("image", secs, note="photo-character")
            with open(dest, "rb") as fh:
                item = library.add_image(fh.read(), f"photo_{meta['name'] or 'character'}.png",
                                         title=f"{meta['name'] or 'Photo character'} (from photo)",
                                         tags=f"from photo {meta['style'].lower()}",
                                         meta={"kind": meta["kind"], "style": meta["style"], "name": meta["name"],
                                               "outfit": meta["outfit"], "look": meta["notes"], "from_photo": True,
                                               "consent_confirmed": meta["consent"]})
            os.remove(dest)
            JOB["results"].append(item)
            JOB["done"] += 1
            _log(f"Version {v + 1} done with {eng} ({secs:.0f} s).")
        JOB["state"] = "done"
    except Exception as e:
        JOB.update(state="error", error=str(e))
        _log(f"Stopped: {e}")
    finally:
        try:
            os.remove(path)  # the photo never stays on disk
        except OSError:
            pass
