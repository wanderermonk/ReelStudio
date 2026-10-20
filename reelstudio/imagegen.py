"""Text-to-image on Comfy Cloud, used to build the character gallery.

The app picks the first engine whose model files exist on your Comfy Cloud account:
  1. your own workflow, if you export one to workflows/t2i_api.json (File -> Export (API))
  2. Z-Image Turbo (fast, 8 steps)   3. Qwen-Image + Lightning LoRA   4. Qwen-Image   5. SDXL base
"""
import copy
import json
import os
import random

from .comfy import ComfyError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_WF = os.path.join(ROOT, "workflows", "t2i_api.json")

NEG = ("blurry, low quality, cropped, cut off feet, cut off head, out of frame, extra limbs, extra fingers, "
       "deformed hands, distorted face, text, watermark, logo, signature, multiple people, duplicate")


def _zimage(prompt, neg, w, h, seed):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3, "model": ["1", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 8, "cfg": 1, "sampler_name": "res_multistep",
                                                  "scheduler": "simple", "denoise": 1, "model": ["4", 0],
                                                  "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "reelstudio/gallery", "images": ["9", 0]}},
    }


def _qwen(prompt, neg, w, h, seed, lightning):
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.1, "model": ["1", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["2", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 8 if lightning else 20, "cfg": 1 if lightning else 4,
                                                  "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                                                  "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                  "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "reelstudio/gallery", "images": ["9", 0]}},
    }
    if lightning:
        wf["11"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "Qwen-Image-Lightning-8steps-V1.0.safetensors",
                                                                     "strength_model": 1.0, "model": ["1", 0]}}
        wf["4"]["inputs"]["model"] = ["11", 0]
    return wf


def _sdxl(prompt, neg, w, h, seed):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["1", 1]}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1344, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 28, "cfg": 6, "sampler_name": "dpmpp_2m",
                                                  "scheduler": "karras", "denoise": 1, "model": ["1", 0],
                                                  "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "reelstudio/gallery", "images": ["9", 0]}},
    }


ENGINES = [
    {"id": "zimage", "name": "Z-Image Turbo", "needs": {"diffusion_models": "z_image_turbo_bf16.safetensors",
                                                        "text_encoders": "qwen_3_4b.safetensors", "vae": "ae.safetensors"},
     "build": _zimage, "est_seconds": 15},
    {"id": "qwen_lightning", "name": "Qwen-Image (Lightning 8-step)",
     "needs": {"diffusion_models": "qwen_image_fp8_e4m3fn.safetensors", "loras": "Qwen-Image-Lightning-8steps-V1.0.safetensors",
               "text_encoders": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "vae": "qwen_image_vae.safetensors"},
     "build": lambda p, n, w, h, s: _qwen(p, n, 928, 1664, s, True), "est_seconds": 25},
    {"id": "qwen", "name": "Qwen-Image", "needs": {"diffusion_models": "qwen_image_fp8_e4m3fn.safetensors",
                                                    "text_encoders": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                                    "vae": "qwen_image_vae.safetensors"},
     "build": lambda p, n, w, h, s: _qwen(p, n, 928, 1664, s, False), "est_seconds": 60},
    {"id": "sdxl", "name": "SDXL base", "needs": {"checkpoints": "sd_xl_base_1.0.safetensors"}, "build": _sdxl,
     "est_seconds": 20},
]


def _user_engine():
    if not os.path.exists(USER_WF):
        return None
    with open(USER_WF) as f:
        tpl = json.load(f)

    def build(prompt, neg, w, h, seed):
        wf = copy.deepcopy(tpl)
        sampler = next((n for n in wf.values() if n.get("class_type") in ("KSampler", "KSamplerAdvanced")), None)
        if not sampler:
            raise ComfyError("workflows/t2i_api.json has no KSampler node.")
        si = sampler["inputs"]
        pos = si.get("positive")
        if isinstance(pos, list) and wf.get(str(pos[0]), {}).get("class_type") == "CLIPTextEncode":
            wf[str(pos[0])]["inputs"]["text"] = prompt
        neg_l = si.get("negative")
        if isinstance(neg_l, list) and wf.get(str(neg_l[0]), {}).get("class_type") == "CLIPTextEncode":
            wf[str(neg_l[0])]["inputs"]["text"] = neg
        for k in ("seed", "noise_seed"):
            if k in si:
                si[k] = seed
        lat = si.get("latent_image")
        if isinstance(lat, list) and "width" in wf.get(str(lat[0]), {}).get("inputs", {}):
            wf[str(lat[0])]["inputs"].update(width=w, height=h)
        return wf

    return {"id": "user", "name": "Your workflow (workflows/t2i_api.json)", "needs": {}, "build": build, "est_seconds": 30}


def detect(cloud, log=print):
    """The best engine available on this account. Uses Comfy Cloud's model list; if that endpoint is not
    available, returns the candidates in order so generate() can try them one by one."""
    user = _user_engine()
    if user:
        return [user]
    have, listed = {}, True
    for folder in {f for e in ENGINES for f in e["needs"]}:
        try:
            have[folder] = set(cloud.models(folder))
        except Exception as e:
            log(f"Could not list Comfy Cloud models ({e}); will try image engines one by one.")
            listed = False
            break
    if not listed:
        return list(ENGINES)
    ok = [e for e in ENGINES if all(f in have.get(folder, set()) for folder, f in e["needs"].items())]
    if not ok:
        raise ComfyError("No supported text-to-image model was found on your Comfy Cloud account. Export any "
                         "text-to-image workflow from Comfy Cloud (File > Export (API)) and save it as "
                         "ReelStudio/workflows/t2i_api.json.")
    return ok


def generate(cloud, engines, prompt, dest, width=720, height=1280, negative=NEG, on_tick=None, should_stop=None, log=print):
    """Makes one image. Returns (engine used, seconds the job took, remaining engines)."""
    last = None
    while engines:
        eng = engines[0]
        wf = eng["build"](prompt, negative, width, height, random.randint(1, 2 ** 50))
        try:
            job = cloud.submit(wf)
        except ComfyError as e:
            msg = str(e)
            if "402" in msg or "401" in msg or "429" in msg:
                raise
            log(f"{eng['name']} is not available here ({msg[:160]}). Trying the next engine.")
            engines = engines[1:]
            last = e
            continue
        import time
        t0 = time.time()
        det = cloud.wait(job, on_tick=on_tick, should_stop=should_stop)
        cloud.download_output(det, dest, (".png", ".jpg", ".jpeg", ".webp"), "image")
        return eng, time.time() - t0, engines
    raise last or ComfyError("No image engine worked.")


# ---------------- image EDIT (keyframes that keep the character the same) ----------------
EDIT_WF = os.path.join(ROOT, "workflows", "edit_api.json")


def _find(names, *subs):
    for n in names or []:
        low = n.lower()
        if all(s in low for s in subs):
            return n
    return None


def _qwen_edit(files, plus=True):
    unet, lora = files["unet"], files.get("lora")

    def build(prompt, refs, w, h, seed):
        wf = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3, "model": ["1", 0]}},
            "5": {"class_type": "CFGNorm", "inputs": {"strength": 1, "model": ["4", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 4 if lora else 20, "cfg": 1 if lora else 2.5,
                                                      "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                                                      "model": ["5", 0], "positive": ["20", 0], "negative": ["21", 0],
                                                      "latent_image": ["7", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "reelstudio/keyframe", "images": ["9", 0]}},
        }
        if lora:
            wf["11"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": lora, "strength_model": 1.0, "model": ["1", 0]}}
            wf["4"]["inputs"]["model"] = ["11", 0]
        pos = {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0]}
        neg = {"clip": ["2", 0], "prompt": "", "vae": ["3", 0]}
        for i, ref in enumerate(refs[: 3 if plus else 1]):
            lid, sid = f"3{i}", f"4{i}"
            wf[lid] = {"class_type": "LoadImage", "inputs": {"image": ref}}
            wf[sid] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [lid, 0], "upscale_method": "lanczos", "megapixels": 1, "resolution_steps": 1}}
            key = f"image{i + 1}" if plus else "image"
            pos[key] = [sid, 0]
            neg[key] = [sid, 0]
        cls = "TextEncodeQwenImageEditPlus" if plus else "TextEncodeQwenImageEdit"
        wf["20"] = {"class_type": cls, "inputs": pos}
        wf["21"] = {"class_type": cls, "inputs": neg}
        return wf
    return build


def _kontext(files):
    def build(prompt, refs, w, h, seed):
        wf = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": files["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": files["clip_l"], "clip_name2": files["t5"], "type": "flux", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
            "30": {"class_type": "LoadImage", "inputs": {"image": refs[0]}},
            "31": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["30", 0]}},
            "32": {"class_type": "VAEEncode", "inputs": {"pixels": ["31", 0], "vae": ["3", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "6": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["5", 0], "latent": ["32", 0]}},
            "7": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": 2.5}},
            "12": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 20, "cfg": 1, "sampler_name": "euler",
                                                      "scheduler": "simple", "denoise": 1, "model": ["1", 0],
                                                      "positive": ["7", 0], "negative": ["12", 0], "latent_image": ["32", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "reelstudio/keyframe", "images": ["9", 0]}},
        }
        return wf
    return build


def _user_edit():
    if not os.path.exists(EDIT_WF):
        return None
    with open(EDIT_WF) as f:
        tpl = json.load(f)

    def build(prompt, refs, w, h, seed):
        wf = copy.deepcopy(tpl)
        loads = [k for k, n in wf.items() if n.get("class_type") == "LoadImage"]
        for k, ref in zip(loads, refs):
            wf[k]["inputs"]["image"] = ref
        for n in wf.values():
            ins = n.get("inputs", {})
            if n.get("class_type", "").startswith("TextEncodeQwenImageEdit") and isinstance(ins.get("prompt"), str) and ins["prompt"].strip():
                ins["prompt"] = prompt
            if n.get("class_type") == "CLIPTextEncode" and isinstance(ins.get("text"), str) and ins["text"].strip():
                ins["text"] = prompt
            for k in ("seed", "noise_seed"):
                if k in ins and not isinstance(ins[k], list):
                    ins[k] = seed
        return wf
    return {"id": "user_edit", "name": "Your edit workflow (workflows/edit_api.json)", "build": build, "refs": 3}


DEFAULT_EDIT_FILES = {
    "qwen_edit_plus": {"unet": "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                       "vae": "qwen_image_vae.safetensors", "lora": None},
    "qwen_edit": {"unet": "qwen_image_edit_fp8_e4m3fn.safetensors", "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                  "vae": "qwen_image_vae.safetensors", "lora": None},
    "kontext": {"unet": "flux1-dev-kontext_fp8_scaled.safetensors", "clip_l": "clip_l.safetensors",
                "t5": "t5xxl_fp8_e4m3fn_scaled.safetensors", "vae": "ae.safetensors"},
}


def detect_edit(cloud, log=print):
    """Image-edit engines that keep the character identical, best first. The last resort is plain text-to-image."""
    user = _user_edit()
    if user:
        return [user]
    m = {}
    try:
        for folder in ("diffusion_models", "text_encoders", "vae", "loras"):
            m[folder] = cloud.models(folder)
    except Exception as e:
        log(f"Could not list Comfy Cloud models ({e}); will try edit engines one by one.")
        m = None
    out = []
    if m is None:
        f = DEFAULT_EDIT_FILES
        out = [{"id": "qwen_edit_plus", "name": "Qwen-Image-Edit 2509", "build": _qwen_edit(f["qwen_edit_plus"], True), "refs": 3},
               {"id": "qwen_edit", "name": "Qwen-Image-Edit", "build": _qwen_edit(f["qwen_edit"], False), "refs": 1},
               {"id": "kontext", "name": "FLUX.1 Kontext", "build": _kontext(f["kontext"]), "refs": 1}]
    else:
        dm, te, va, lo = m["diffusion_models"], m["text_encoders"], m["vae"], m["loras"]
        clip, qvae = _find(te, "qwen_2.5_vl_7b"), _find(va, "qwen_image_vae")
        plus = _find(dm, "qwen_image_edit_2509") or _find(dm, "qwen_image_edit_2511") or _find(dm, "qwen-image-edit-2509")
        if plus and clip and qvae:
            lora = _find(lo, "edit", "2509", "lightning", "4step") or _find(lo, "edit", "lightning", "4step")
            out.append({"id": "qwen_edit_plus", "name": "Qwen-Image-Edit 2509" + (" + Lightning" if lora else ""),
                        "build": _qwen_edit({"unet": plus, "clip": clip, "vae": qvae, "lora": lora}, True), "refs": 3})
        old = next((n for n in dm if "qwen_image_edit" in n.lower() and "2509" not in n and "2511" not in n), None)
        if old and clip and qvae:
            out.append({"id": "qwen_edit", "name": "Qwen-Image-Edit", "build": _qwen_edit({"unet": old, "clip": clip, "vae": qvae, "lora": None}, False), "refs": 1})
        kon, cl, t5, ae = _find(dm, "kontext"), _find(te, "clip_l"), _find(te, "t5xxl"), _find(va, "ae.")
        if kon and cl and t5 and ae:
            out.append({"id": "kontext", "name": "FLUX.1 Kontext", "build": _kontext({"unet": kon, "clip_l": cl, "t5": t5, "vae": ae}), "refs": 1})
    if out:
        log("Keyframe engine: " + out[0]["name"] + (f" (fallbacks: {', '.join(e['name'] for e in out[1:])})" if len(out) > 1 else ""))
    else:
        log("No image-edit model found; keyframes will be drawn from text only (characters may look a little different).")
    return out


def edit_or_draw(cloud, edit_engines, t2i_engines, prompt_edit, prompt_text, refs, dest, width=768, height=1360,
                 should_stop=None, log=print, allow_text=True):
    """Make one keyframe. refs = uploaded image names. Returns (engine name, seconds, edit_engines, t2i_engines)."""
    import time
    while edit_engines and refs:
        eng = edit_engines[0]
        wf = eng["build"](prompt_edit, refs[: eng.get("refs", 1)], width, height, random.randint(1, 2 ** 50))
        try:
            job = cloud.submit(wf)
        except ComfyError as e:
            if any(c in str(e) for c in ("401", "402", "429")):
                raise
            log(f"{eng['name']} is not available ({str(e)[:140]}). Trying the next engine.")
            edit_engines = edit_engines[1:]
            continue
        t0 = time.time()
        det = cloud.wait(job, should_stop=should_stop)
        cloud.download_output(det, dest, (".png", ".jpg", ".jpeg", ".webp"), "image")
        return eng["name"], time.time() - t0, edit_engines, t2i_engines
    if not allow_text:
        raise ComfyError("No image-edit model worked on your Comfy Cloud account, so the reference image could not be used.")
    if t2i_engines is None:
        t2i_engines = detect(cloud, log=log)
    eng, secs, t2i_engines = generate(cloud, t2i_engines, prompt_text, dest, 720, 1280, should_stop=should_stop, log=log)
    return eng["name"] + " (text only)", secs, edit_engines, t2i_engines
