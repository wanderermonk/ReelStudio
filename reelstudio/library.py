"""Image library and LoRA library, stored as JSON files under data/."""
import json
import os
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LIB_DIR = os.path.join(DATA, "library")
LIB_FILE = os.path.join(DATA, "library.json")
LORA_FILE = os.path.join(DATA, "loras.json")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
_lock = threading.Lock()
os.makedirs(LIB_DIR, exist_ok=True)


def _read(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------------- images ----------------

def _title_from(name):
    base = os.path.splitext(name)[0].replace("_", " ").replace("-", " ").strip()
    return base[:60] or "Image"


def images():
    """All library images. Files dropped straight into data/library/ are picked up automatically."""
    with _lock:
        items = _read(LIB_FILE, [])
        known = {i["file"] for i in items}
        changed = False
        for f in sorted(os.listdir(LIB_DIR)):
            if f.lower().endswith(IMG_EXT) and f not in known:
                items.append({"id": uuid.uuid4().hex[:8], "file": f, "title": _title_from(f), "tags": "",
                              "added": time.time()})
                changed = True
        kept = [i for i in items if os.path.exists(os.path.join(LIB_DIR, i["file"]))]
        if changed or len(kept) != len(items):
            _write(LIB_FILE, kept)
        return kept


def get_image(iid):
    for i in images():
        if i["id"] == iid:
            return i
    return None


def add_image(fileobj, filename, title="", tags="", meta=None):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMG_EXT:
        raise ValueError("Use a PNG, JPG or WEBP image.")
    iid = uuid.uuid4().hex[:8]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in os.path.splitext(filename)[0])[:40] or "image"
    name = f"{safe}_{iid}{ext}"
    fileobj.save(os.path.join(LIB_DIR, name)) if hasattr(fileobj, "save") else open(os.path.join(LIB_DIR, name), "wb").write(fileobj)
    images()  # registers the file
    with _lock:
        items = _read(LIB_FILE, [])
        for i in items:
            if i["file"] == name:
                i["id"], i["title"], i["tags"] = iid, (title or _title_from(filename)), tags
                if meta:
                    i.update(meta)
        _write(LIB_FILE, items)
    return get_image(iid)


def update_image(iid, title=None, tags=None):
    with _lock:
        items = _read(LIB_FILE, [])
        for i in items:
            if i["id"] == iid:
                if title is not None:
                    i["title"] = title[:80]
                if tags is not None:
                    i["tags"] = tags[:160]
        _write(LIB_FILE, items)
    return get_image(iid)


def delete_image(iid):
    with _lock:
        items = _read(LIB_FILE, [])
        for i in items:
            if i["id"] == iid:
                p = os.path.join(LIB_DIR, i["file"])
                if os.path.exists(p):
                    os.remove(p)
        _write(LIB_FILE, [i for i in items if i["id"] != iid])


def image_path(item):
    return os.path.join(LIB_DIR, item["file"])


# ---------------- LoRAs ----------------

BUILTIN_LORAS = [
    {"id": "none", "name": "No character LoRA", "trigger": "", "builtin": True,
     "what": "The character's look comes only from the start image and the written description. "
             "Fine for most reels; the face can drift a little over long reels."},
    {"id": "lightning", "name": "Lightning 4-step (Turbo)", "trigger": "", "builtin": True, "info_only": True,
     "what": "Speed LoRA already built into the Wan 2.2 workflows. It is switched on by the Turbo tick box, "
             "not here: about 1–2 minutes per clip instead of about 10. It has no trigger word."},
]


def loras():
    custom = _read(LORA_FILE, [])
    return BUILTIN_LORAS + custom


def get_lora(lid):
    for l in loras():
        if l["id"] == lid:
            return l
    return None


def save_lora(d):
    with _lock:
        items = _read(LORA_FILE, [])
        lid = d.get("id") or uuid.uuid4().hex[:8]
        if lid in ("none", "lightning"):
            raise ValueError("Built-in entries can't be changed.")
        it = next((x for x in items if x["id"] == lid), None)
        if it is None:
            it = {"id": lid}
            items.append(it)
        for k in ("name", "trigger", "what", "file_high", "file_low"):
            if k in d:
                it[k] = str(d[k] or "").strip()
        it["trigger"] = it.get("trigger", "").replace(" ", "")
        try:
            it["strength"] = max(0.0, min(2.0, float(d.get("strength", it.get("strength", 0.9)))))
        except (TypeError, ValueError):
            it["strength"] = 0.9
        if not it.get("name"):
            raise ValueError("Give the LoRA a name.")
        _write(LORA_FILE, items)
        return it


def delete_lora(lid):
    with _lock:
        _write(LORA_FILE, [x for x in _read(LORA_FILE, []) if x["id"] != lid])
