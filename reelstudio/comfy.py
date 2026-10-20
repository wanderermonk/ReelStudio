"""Comfy Cloud client: upload images, fill in workflows, run them, download the video."""
import copy
import json
import os
import random
import time

import requests

TERMINAL_OK = {"success", "completed", "succeeded"}
TERMINAL_BAD = {"error", "non_retryable_error", "lost", "cancelled", "canceled", "failed", "expired"}


class ComfyError(Exception):
    pass


class ComfyCloud:
    def __init__(self, api_key, base_url="https://cloud.comfy.org", log=print):
        if not api_key:
            raise ComfyError("No Comfy Cloud API key. Add it in Settings (create one at platform.comfy.org/profile/api-keys).")
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.log = log

    # ---------- HTTP helpers ----------
    def _h(self, extra=None):
        h = {"X-API-Key": self.key}
        if extra:
            h.update(extra)
        return h

    def _check(self, r, what):
        if r.status_code == 401:
            raise ComfyError("Comfy Cloud rejected the API key (401). Check it in Settings.")
        if r.status_code == 402:
            raise ComfyError("Comfy Cloud says you are out of credits (402). Top up or wait for your plan to renew.")
        if r.status_code == 429:
            raise ComfyError("Comfy Cloud says your subscription is inactive or rate limited (429).")
        if r.status_code >= 400:
            raise ComfyError(f"{what} failed: HTTP {r.status_code} {r.text[:300]}")
        return r

    def check_key(self):
        r = requests.get(f"{self.base}/api/queue", headers=self._h(), timeout=30)
        self._check(r, "Checking the API key")
        return True

    def upload_image(self, path):
        with open(path, "rb") as f:
            files = {"image": (os.path.basename(path), f, "image/png")}
            r = requests.post(f"{self.base}/api/upload/image", headers=self._h(),
                              files=files, data={"type": "input", "overwrite": "true"}, timeout=120)
        self._check(r, "Uploading the image")
        j = r.json()
        name = j.get("name") or j.get("filename")
        sub = j.get("subfolder") or ""
        return f"{sub}/{name}" if sub else name

    _node_info = {}

    def node_info(self, cls):
        """Input spec of one node type from Comfy Cloud (cached). None if it can't be read."""
        if cls in ComfyCloud._node_info:
            return ComfyCloud._node_info[cls]
        info = None
        try:
            r = requests.get(f"{self.base}/api/object_info/{cls}", headers=self._h(), timeout=30)
            if r.status_code < 400:
                j = r.json()
                info = j.get(cls) if isinstance(j, dict) and cls in j else (j if isinstance(j, dict) and "input" in j else None)
        except (requests.RequestException, ValueError):
            info = None
        ComfyCloud._node_info[cls] = info
        return info

    def fill_defaults(self, workflow):
        """Comfy Cloud updates ComfyUI often and nodes gain new required inputs (e.g. ImageScaleToTotalPixels got
        'resolution_steps'). Fill any missing required input with the node's own default so old workflows keep working."""
        added = []
        for nid, node in workflow.items():
            info = self.node_info(node.get("class_type", ""))
            req = ((info or {}).get("input") or {}).get("required") or {}
            ins = node.setdefault("inputs", {})
            for name, spec in req.items():
                if name in ins or not isinstance(spec, (list, tuple)) or not spec:
                    continue
                typ = spec[0]
                opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if "default" in opts:
                    ins[name] = opts["default"]
                elif isinstance(typ, list) and typ:          # a dropdown: take its first choice
                    ins[name] = typ[0]
                elif typ == "COMBO" and opts.get("options"):
                    ins[name] = opts["options"][0]
                else:
                    continue                                 # a link input we can't invent
                added.append(f"{node.get('class_type')}.{name}={ins[name]!r}")
        if added:
            self.log(f"   Filled new required settings with their defaults: {', '.join(added)[:300]}")
        return workflow

    def submit(self, workflow):
        try:
            self.fill_defaults(workflow)
        except Exception:
            pass
        r = requests.post(f"{self.base}/api/prompt", headers=self._h({"Content-Type": "application/json"}),
                          data=json.dumps({"prompt": workflow}), timeout=60)
        self._check(r, "Submitting the workflow")
        j = r.json()
        if j.get("node_errors"):
            raise ComfyError(f"Comfy rejected the workflow: {json.dumps(j['node_errors'])[:500]}")
        pid = j.get("prompt_id") or j.get("id")
        if not pid:
            raise ComfyError(f"No job id returned: {r.text[:300]}")
        return pid

    def status(self, pid):
        r = requests.get(f"{self.base}/api/job/{pid}/status", headers=self._h(), timeout=30)
        if r.status_code == 404:
            return "queued"
        self._check(r, "Checking job status")
        return str(r.json().get("status", "")).lower()

    def job_details(self, pid):
        r = requests.get(f"{self.base}/api/jobs/{pid}", headers=self._h(), timeout=60)
        if r.status_code < 400:
            return r.json()
        # fallback: history list
        r = requests.get(f"{self.base}/api/history_v2", headers=self._h(), params={"max_items": 20}, timeout=60)
        self._check(r, "Reading job history")
        for e in r.json().get("history", []):
            if e.get("prompt_id") == pid:
                return e
        return {}

    def cancel(self, pids):
        """Remove jobs from the queue and stop the one that is running (saves credits)."""
        pids = [p for p in pids if p]
        if pids:
            try:
                requests.post(f"{self.base}/api/queue", headers=self._h({"Content-Type": "application/json"}),
                              data=json.dumps({"delete": pids}), timeout=30)
            except requests.RequestException:
                pass
        try:
            requests.post(f"{self.base}/api/interrupt", headers=self._h(), timeout=30)
        except requests.RequestException:
            pass

    def queue(self):
        r = requests.get(f"{self.base}/api/queue", headers=self._h(), timeout=30)
        self._check(r, "Reading the queue")
        j = r.json()
        return {"running": len(j.get("queue_running", [])), "pending": len(j.get("queue_pending", []))}

    def wait(self, pid, on_tick=None, timeout=60 * 60, should_stop=None):
        start = time.time()
        last = None
        while True:
            if should_stop and should_stop():
                self.cancel([pid])
                raise ComfyError("Disconnected: the Comfy Cloud job was cancelled to stop using credits.")
            st = self.status(pid)
            if st != last:
                self.log(f"   Comfy job {pid[:8]}: {st}")
                last = st
            if on_tick:
                on_tick(st, time.time() - start)
            if st in TERMINAL_OK:
                return self.job_details(pid)
            if st in TERMINAL_BAD:
                det = self.job_details(pid)
                err = det.get("execution_error") or {}
                msg = err.get("exception_message") or st
                raise ComfyError(f"The Comfy job failed: {msg}")
            if time.time() - start > timeout:
                raise ComfyError("The Comfy job took longer than an hour; stopped waiting.")
            time.sleep(5)

    @staticmethod
    def find_files(obj, found=None):
        """Collect every {filename, subfolder, type} dict in a job's outputs."""
        if found is None:
            found = []
        if isinstance(obj, dict):
            if "filename" in obj and isinstance(obj["filename"], str):
                found.append(obj)
            for v in obj.values():
                ComfyCloud.find_files(v, found)
        elif isinstance(obj, list):
            for v in obj:
                ComfyCloud.find_files(v, found)
        return found

    def download_output(self, details, dest, exts=(".mp4", ".webm", ".mov"), what="video"):
        outs = details.get("outputs") or {}
        files = self.find_files(outs) or self.find_files(details.get("preview_output") or {})
        pick = [f for f in files if f["filename"].lower().endswith(exts)] or files
        if not pick:
            raise ComfyError(f"The job finished but no {what} was found in its outputs.")
        f = pick[-1]
        params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
        r = requests.get(f"{self.base}/api/view", headers=self._h(), params=params,
                         allow_redirects=False, timeout=120)
        if r.status_code in (301, 302, 303, 307, 308):
            from urllib.parse import urljoin
            loc = urljoin(f"{self.base}/api/view", r.headers["Location"])
            r = requests.get(loc, timeout=300)  # signed URL: no auth header
        self._check(r, f"Downloading the {what}")
        with open(dest, "wb") as fh:
            fh.write(r.content)
        return dest

    def download_video(self, details, dest):
        return self.download_output(details, dest)

    def models(self, folder):
        """Model file names in one Comfy Cloud model folder (e.g. 'diffusion_models')."""
        r = requests.get(f"{self.base}/api/experiment/models/{folder}", headers=self._h(), timeout=30)
        self._check(r, f"Listing {folder}")
        return [m.get("name") for m in r.json() if isinstance(m, dict)]

    def usage(self, months=1):
        """Balance and this month's spend from Comfy Cloud billing (GET /api/billing/usage/timeseries)."""
        r = requests.get(f"{self.base}/api/billing/usage/timeseries", headers=self._h(), timeout=30,
                         params={"months": months, "granularity": "day", "group_by": "product"})
        self._check(r, "Reading credits")
        return r.json()


# ---------- workflow templating ----------

def _link_target(wf, link):
    if isinstance(link, list) and link and str(link[0]) in wf:
        return str(link[0])
    return None


def _latent_node(wf, cls):
    for nid, n in wf.items():
        if n.get("class_type") == cls:
            return nid
    raise ComfyError(f"Workflow has no {cls} node")


def _set_seed(wf):
    for n in wf.values():
        if n.get("class_type") == "KSamplerAdvanced" and n["inputs"].get("add_noise") == "enable":
            n["inputs"]["noise_seed"] = random.randint(1, 2 ** 50)


def _set_size_len(wf, latent_id, width, height, frames, fps):
    li = wf[latent_id]["inputs"]
    li["width"], li["height"] = width, height
    if isinstance(li.get("length"), list):
        # i2v template computes length = floor(duration*fps+1) from primitives
        expr = wf[_link_target(wf, li["length"])]
        a = _link_target(wf, expr["inputs"].get("values.a"))
        b = _link_target(wf, expr["inputs"].get("values.b"))
        if a:
            wf[a]["inputs"]["value"] = (frames - 1) / fps
        if b:
            wf[b]["inputs"]["value"] = fps
    else:
        li["length"] = frames
    for n in wf.values():
        if n.get("class_type") == "CreateVideo" and not isinstance(n["inputs"].get("fps"), list):
            n["inputs"]["fps"] = fps


def _set_prompt(wf, latent_id, positive, negative=None):
    li = wf[latent_id]["inputs"]
    p = _link_target(wf, li["positive"])
    wf[p]["inputs"]["text"] = positive
    if negative:
        n = _link_target(wf, li["negative"])
        wf[n]["inputs"]["text"] = negative


def _set_prefix(wf, prefix):
    for n in wf.values():
        if n.get("class_type") == "SaveVideo":
            n["inputs"]["filename_prefix"] = prefix


def _upstream_unet(wf, nid, seen=None):
    seen = seen or set()
    if nid in seen or nid not in wf:
        return None
    seen.add(nid)
    n = wf[nid]
    if n.get("class_type") == "UNETLoader":
        return n["inputs"].get("unet_name", "")
    for v in n["inputs"].values():
        t = _link_target(wf, v)
        if t:
            r = _upstream_unet(wf, t, seen)
            if r is not None:
                return r
    return None


def apply_char_lora(wf, lora):
    """Insert the character LoRA (high- and low-noise files) just before each ModelSamplingSD3."""
    if not lora or not (lora.get("file_high") or lora.get("file_low")):
        return wf
    hi = lora.get("file_high") or lora.get("file_low")
    lo = lora.get("file_low") or lora.get("file_high")
    strength = float(lora.get("strength", 0.9))
    done = set()
    for sid, n in list(wf.items()):
        if n.get("class_type") != "KSamplerAdvanced":
            continue
        ms = _link_target(wf, n["inputs"].get("model"))
        if not ms or ms in done or wf[ms].get("class_type") != "ModelSamplingSD3":
            continue
        src = wf[ms]["inputs"]["model"]
        unet = _upstream_unet(wf, ms) or ""
        lid = f"rs_charlora_{ms}"
        wf[lid] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"lora_name": hi if "high_noise" in unet else lo, "strength_model": strength, "model": src}}
        wf[ms]["inputs"]["model"] = [lid, 0]
        done.add(ms)
    return wf


def make_dynamic(wf, first_steps=2, total=6, cfg=3.5):
    """'Dynamic Turbo': run the first steps of the high-noise model WITHOUT the 4-step Lightning LoRA (real cfg),
    then finish with Lightning. The no-LoRA steps decide the big motion, so moves come out larger and livelier.

    Turbo graph:   [high+LoRA 0..h] -> [low+LoRA h..end]
    Dynamic graph: [high, no LoRA, cfg 3.5, 0..2] -> [high+LoRA 2..3] -> [low+LoRA 3..6]
    """
    samplers = [nid for nid, n in wf.items() if n.get("class_type") == "KSamplerAdvanced"]
    hi = next((s for s in samplers if wf[s]["inputs"].get("add_noise") == "enable"), None)
    lo = next((s for s in samplers if s != hi), None)
    if not hi or not lo:
        return wf
    unet_hi = next((nid for nid, n in wf.items() if n.get("class_type") == "UNETLoader"
                    and "high_noise" in n["inputs"].get("unet_name", "")), None)
    if not unet_hi:
        return wf
    hs = wf[hi]["inputs"]
    ms_old = _link_target(wf, hs["model"])
    shift = wf[ms_old]["inputs"].get("shift", 5.0) if ms_old and wf[ms_old].get("class_type") == "ModelSamplingSD3" else 5.0
    wf["rs_dyn_ms"] = {"class_type": "ModelSamplingSD3", "inputs": {"shift": shift, "model": [unet_hi, 0]}}
    wf["rs_dyn_ks"] = {"class_type": "KSamplerAdvanced", "inputs": {
        "add_noise": "enable", "noise_seed": random.randint(1, 2 ** 50), "steps": total, "cfg": cfg,
        "sampler_name": hs.get("sampler_name", "euler"), "scheduler": hs.get("scheduler", "simple"),
        "start_at_step": 0, "end_at_step": first_steps, "return_with_leftover_noise": "enable",
        "model": ["rs_dyn_ms", 0], "positive": hs["positive"], "negative": hs["negative"],
        "latent_image": hs["latent_image"]}}
    mid = first_steps + max(1, (total - first_steps) // 4)
    hs.update({"add_noise": "disable", "noise_seed": 0, "steps": total, "cfg": 1.0, "start_at_step": first_steps,
               "end_at_step": mid, "return_with_leftover_noise": "enable", "latent_image": ["rs_dyn_ks", 0]})
    ls = wf[lo]["inputs"]
    ls.update({"steps": total, "cfg": 1.0, "start_at_step": mid, "end_at_step": 10000, "latent_image": [hi, 0]})
    return wf


def build_i2v(template, start_image, prompt, negative, width, height, frames, fps, turbo=True, prefix="video/reelstudio",
              char_lora=None, dynamic=False):
    wf = copy.deepcopy(template)
    lat = _latent_node(wf, "WanImageToVideo")
    wf[_link_target(wf, wf[lat]["inputs"]["start_image"])]["inputs"]["image"] = start_image
    _set_prompt(wf, lat, prompt, negative)
    _set_size_len(wf, lat, width, height, frames, fps)
    for n in wf.values():  # the template's "turbo" switch (lightx2v 4-step LoRA)
        if n.get("class_type") == "PrimitiveBoolean":
            n["inputs"]["value"] = bool(turbo)
    if turbo and dynamic:
        make_dynamic(wf)
    apply_char_lora(wf, char_lora)
    _set_seed(wf)
    _set_prefix(wf, prefix)
    return wf


def build_flf(template, start_image, end_image, prompt, negative, width, height, frames, fps, turbo=True,
              lora_high="wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
              lora_low="wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
              prefix="video/reelstudio", char_lora=None, dynamic=False):
    wf = copy.deepcopy(template)
    lat = _latent_node(wf, "WanFirstLastFrameToVideo")
    li = wf[lat]["inputs"]
    wf[_link_target(wf, li["start_image"])]["inputs"]["image"] = start_image
    wf[_link_target(wf, li["end_image"])]["inputs"]["image"] = end_image
    _set_prompt(wf, lat, prompt, negative)
    _set_size_len(wf, lat, width, height, frames, fps)
    if turbo:
        # add the same 4-step lightning LoRAs the i2v template uses: 4 steps, cfg 1, split at 2
        samplers = [nid for nid, n in wf.items() if n.get("class_type") == "KSamplerAdvanced"]
        for sid in samplers:
            s = wf[sid]["inputs"]
            ms_id = _link_target(wf, s["model"])           # ModelSamplingSD3
            unet_id = _link_target(wf, wf[ms_id]["inputs"]["model"])
            high = "high_noise" in wf[unet_id]["inputs"].get("unet_name", "")
            lid = f"rs_lora_{sid}"
            wf[lid] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"lora_name": lora_high if high else lora_low, "strength_model": 1.0,
                                  "model": [unet_id, 0]}}
            wf[ms_id]["inputs"]["model"] = [lid, 0]
            wf[ms_id]["inputs"]["shift"] = 5.0
            s["steps"], s["cfg"] = 4, 1.0
            if s.get("add_noise") == "enable":
                s["start_at_step"], s["end_at_step"] = 0, 2
            else:
                s["start_at_step"], s["end_at_step"] = 2, 10000
        if dynamic:
            make_dynamic(wf)
    apply_char_lora(wf, char_lora)
    _set_seed(wf)
    _set_prefix(wf, prefix)
    return wf
