"""Credits: live balance from Comfy Cloud billing, plus the app's own usage log and cost estimates.

Comfy Cloud: $1 = 211 credits; GPU time costs about 0.266 credits per second of active use
(support.comfy.org, "How credits work in Comfy"). The billing API reports money in cents.
"""
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "data", "usage.json")
CREDITS_PER_USD = 211
CREDITS_PER_GPU_SECOND = 0.266
# typical seconds of GPU time per 5-second clip (refined from your own runs once there are some)
DEFAULT_CLIP_SECONDS = {"turbo": 80, "dynamic": 140, "slow": 560}
_lock = threading.Lock()
_cache = {"t": 0, "data": None}


def _read():
    if not os.path.exists(LEDGER):
        return []
    try:
        with open(LEDGER) as f:
            return json.load(f)
    except Exception:
        return []


def record(kind, seconds, mode=None, note=""):
    """Log one cloud job. Returns its estimated credits. (Wall time includes queueing, so it slightly over-counts.)"""
    cr = round(max(0.0, float(seconds)) * CREDITS_PER_GPU_SECOND, 2)
    with _lock:
        rows = _read()
        rows.append({"t": time.time(), "kind": kind, "mode": mode, "seconds": round(seconds, 1), "credits": cr, "note": note})
        rows = rows[-5000:]
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER + ".tmp", "w") as f:
            json.dump(rows, f)
        os.replace(LEDGER + ".tmp", LEDGER)
    _cache["t"] = 0  # refresh the balance soon
    return cr


def clip_seconds(mode):
    rows = [r for r in _read() if r.get("kind") == "clip" and r.get("mode") == mode][-20:]
    if len(rows) >= 3:
        s = sorted(r["seconds"] for r in rows)
        return s[len(s) // 2]
    return DEFAULT_CLIP_SECONDS[mode]


def estimate(clips, turbo=True, dynamic=True):
    mode = "slow" if not turbo else ("dynamic" if dynamic else "turbo")
    per = clip_seconds(mode) * CREDITS_PER_GPU_SECOND
    total = per * clips
    return {"mode": mode, "per_clip": round(per, 1), "credits": round(total), "usd": round(total / CREDITS_PER_USD, 2),
            "minutes": round(clip_seconds(mode) * clips / 60, 1)}


def app_usage(days=None):
    rows = _read()
    now = time.localtime()
    month_start = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    m = [r for r in rows if r["t"] >= month_start]
    today = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    return {"month_credits": round(sum(r["credits"] for r in m), 1),
            "today_credits": round(sum(r["credits"] for r in rows if r["t"] >= today), 1),
            "month_clips": sum(1 for r in m if r["kind"] == "clip"),
            "month_images": sum(1 for r in m if r["kind"] == "image")}


def cents_to_credits(c):
    return round(float(c) / 100 * CREDITS_PER_USD)


def balance(cloud, max_age=60):
    """{'available': bool, 'credits', 'subscription', 'prepaid', 'usd', 'month_spend_credits', 'error'}"""
    if _cache["data"] and time.time() - _cache["t"] < max_age:
        return _cache["data"]
    out = {"available": False}
    try:
        j = cloud.usage()
        b = (j.get("summary") or {}).get("balance") or {}
        spend = (j.get("summary") or {}).get("spend_micros")
        if "amount_micros" in b:
            out.update(available=True, credits=cents_to_credits(b["amount_micros"]),
                       usd=round(float(b["amount_micros"]) / 100, 2),
                       subscription=cents_to_credits(b.get("cloud_credit_balance_micros", 0)),
                       prepaid=cents_to_credits(b.get("prepaid_balance_micros", 0)),
                       currency=b.get("currency", "USD"))
        else:
            out["error"] = "Comfy Cloud did not report a balance for this key."
        if spend is not None:
            out["month_spend_credits"] = round(float(spend) / 1e6 * CREDITS_PER_USD)
    except Exception as e:
        out["error"] = str(e)[:200]
    _cache.update(t=time.time(), data=out)
    return out
