"""Reel Studio MCP server.

Lets an AI assistant (Claude Desktop, Claude Code, Cursor, ...) drive Reel Studio by chat:
list characters and songs, plan a reel, check the cost, make it, watch progress and fix clips.

It is a thin layer over Reel Studio's local HTTP API, so the app must be running (./run.sh).
Run it with ./run_mcp.sh (stdio transport). See "MCP server" in README.md.
"""
import os
import time

import requests
try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

BASE = os.environ.get("REELSTUDIO_URL", "http://127.0.0.1:%s" % os.environ.get("PORT", "7860")).rstrip("/")
ROOT = os.path.dirname(os.path.abspath(__file__))

mcp = FastMCP("reel-studio", instructions=(
    "Reel Studio makes vertical AI dance reels from a character picture. make_reel and start_reel spend "
    "Comfy Cloud credits: always show the user the plan and cost estimate and get a yes before starting."))


class ReelStudioError(ToolError):
    """Shown to the AI assistant as a plain error message."""


def _call(method, path, json=None, params=None, timeout=60):
    try:
        r = requests.request(method, BASE + path, json=json, params=params, timeout=timeout)
    except requests.ConnectionError:
        raise ReelStudioError(f"Reel Studio is not running at {BASE}. Start it with ./run.sh and try again.")
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text[:300]}
    if r.status_code >= 400:
        msg = data.get("error") if isinstance(data, dict) else None
        raise ReelStudioError(msg or f"Reel Studio answered {r.status_code} for {path}: {r.text[:300]}")
    return data


def _find_character(name_or_id):
    chars = _call("GET", "/api/characters")
    key = (name_or_id or "").strip().lower()
    for c in chars:
        if c["id"].lower() == key:
            return c
    matches = [c for c in chars if (c.get("name") or "").lower() == key] or \
              [c for c in chars if key and key in (c.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(f"{c.get('name')} ({c['id']})" for c in chars)
    if not matches:
        raise ReelStudioError(f"No character called '{name_or_id}'. Characters: {names}")
    raise ReelStudioError(f"'{name_or_id}' matches several characters: "
                          + ", ".join(f"{c.get('name')} ({c['id']})" for c in matches))


def _clips_for(seconds):
    return max(1, int(round(int(seconds) / 5)))


def _summary(d, log_lines=5):
    clips = d.get("clips") or []
    out = {
        "reel_id": d.get("id"),
        "title": d.get("title"),
        "type": d.get("type", "dance"),
        "status": d.get("status"),
        "clips_done": sum(1 for c in clips if c.get("state") == "done" or c.get("video")),
        "clips_total": len(clips),
        "clip_states": [c.get("state") for c in clips],
        "turbo": (d.get("settings") or {}).get("turbo"),
        "music": (d.get("music") or {}).get("mode"),
        "recent_log": (d.get("log") or [])[-log_lines:],
    }
    if d.get("error"):
        out["error"] = d["error"]
    if d.get("output_copy"):
        out["final_video"] = os.path.join(ROOT, d["output_copy"])
    elif d.get("final"):
        out["final_video"] = os.path.join(ROOT, "projects", d["id"], d["final"])
    return out


# ---------------- read-only tools ----------------

@mcp.tool()
def reel_studio_status() -> dict:
    """Check that Reel Studio is running and what is set up: Comfy Cloud key, prompt writers
    (Claude / Ollama), Turbo mode and the Comfy Cloud credit balance."""
    st = _call("GET", "/api/status")
    turbo = _call("GET", "/api/turbo")
    try:
        cr = _call("GET", "/api/credits")
    except ReelStudioError as e:
        cr = {"balance": {"available": False, "error": str(e)}}
    return {"running": True, "url": BASE, "comfy_key_set": st.get("comfy"), "claude_key_set": st.get("claude"),
            "ollama_models": st.get("ollama"), "turbo_on": turbo.get("enabled"),
            "turbo_problems": turbo.get("problems"), "credits": cr.get("balance"),
            "used_this_month": (cr.get("app") or {}).get("month_credits")}


@mcp.tool()
def list_characters() -> list:
    """List the saved characters (id, name, kind, age, style, look, outfit). Use the name or id in other tools."""
    return [{k: c.get(k) for k in ("id", "name", "kind", "age", "style", "look", "outfit")}
            | {"has_image": bool(c.get("image"))} for c in _call("GET", "/api/characters")]


@mcp.tool()
def list_songs() -> list:
    """List uploaded songs (id, name, BPM, duration, whether lyrics are saved). Use the id with music_mode='song'."""
    return _call("GET", "/api/songs")


@mcp.tool()
def list_reels(limit: int = 10) -> list:
    """List the most recent reels and stories with their status (ready / running / done / error)."""
    return _call("GET", "/api/projects")[:max(1, min(limit, 50))]


@mcp.tool()
def estimate_cost(seconds: int = 15, turbo: bool = True) -> dict:
    """Estimate Comfy Cloud credits, US dollars and minutes for a dance reel of this length (5 s per clip)."""
    return _call("GET", "/api/credits/estimate",
                 params={"clips": _clips_for(seconds), "turbo": int(turbo), "dynamic": 1})


@mcp.tool()
def reel_status(reel_id: str, log_lines: int = 8) -> dict:
    """Progress of one reel: overall status, each clip's state, recent log lines and, when finished,
    the path of the final video."""
    return _summary(_call("GET", f"/api/projects/{reel_id}"), log_lines)


# ---------------- planning (free: no cloud credits) ----------------

def _plan(character, seconds, setting, mood, music_mode, song_id, song_start):
    ch = _find_character(character)
    body = {"character_id": ch["id"], "seconds": int(seconds), "setting": setting, "mood": mood,
            "music": {"mode": music_mode}}
    if music_mode == "song":
        if not song_id:
            raise ReelStudioError("music_mode='song' needs a song_id (see list_songs).")
        body.update(song_id=song_id, song_start=song_start)
    return ch, _call("POST", "/api/plan", json=body, timeout=300)


@mcp.tool()
def plan_reel(character: str, seconds: int = 15, setting: str = "", mood: str = "",
              music_mode: str = "original", song_id: str = "", song_start: float = 0) -> dict:
    """Write the clip-by-clip motion prompts for a dance reel without making anything.
    Free (no cloud credits). Good for previewing or editing the plan before make_reel.

    character: name or id. seconds: 5-30 (5 s per clip). setting: e.g. 'a Diwali courtyard'.
    music_mode: 'original' (generated dhol track), 'song' (needs song_id) or 'none'."""
    ch, p = _plan(character, seconds, setting, mood, music_mode, song_id, song_start)
    return {"character": ch["name"], "planner": p.get("planner"),
            "clips": [{"clip": i + 1, "motion": c["motion"], "energy": c.get("energy")}
                      for i, c in enumerate(p["clips"])],
            "log": p.get("log")}


# ---------------- actions ----------------

@mcp.tool()
def make_reel(character: str, seconds: int = 15, setting: str = "", mood: str = "",
              music_mode: str = "original", song_id: str = "", song_start: float = 0,
              title: str = "", turbo: bool = True, upscale: bool = True, confirm: bool = False) -> dict:
    """Plan and create a dance reel, then start it on Comfy Cloud.

    This SPENDS Comfy Cloud credits. With confirm=False (the default) it only plans the reel,
    creates it in the app and returns the cost estimate, so you can show the user first.
    Call start_reel(reel_id) (or make_reel again with confirm=True) once they agree.
    Progress: reel_status(reel_id). Takes about 2-3 minutes per 5 s clip with Turbo."""
    ch, p = _plan(character, seconds, setting, mood, music_mode, song_id, song_start)
    music = {"mode": music_mode}
    if music_mode == "song":
        music.update(song_id=song_id, start=p.get("song_start") or song_start, snap=True)
    body = {"character_id": ch["id"], "clips": p["clips"], "title": title or None, "setting": setting,
            "mood": mood, "turbo": turbo, "upscale": upscale, "beat_sync": True, "music": music}
    d = _call("POST", "/api/projects", json=body)
    est = _call("GET", "/api/credits/estimate", params={"clips": len(p["clips"]), "turbo": int(turbo), "dynamic": 1})
    out = {"reel_id": d["id"], "title": d["title"], "planner": p.get("planner"), "estimate": est,
           "clips": [c["motion"] for c in p["clips"]]}
    if not confirm:
        out["started"] = False
        out["next_step"] = "Show the user the plan and estimate, then call start_reel with this reel_id."
        return out
    out.update(_start(d["id"], 0, False))
    return out


def _start(reel_id, from_clip, allow_slow):
    d = _call("POST", f"/api/projects/{reel_id}/start", json={"from_clip": int(from_clip), "allow_slow": allow_slow})
    return {"started": True, "status": d.get("status"),
            "next_step": "Poll reel_status every minute or two until status is 'done'."}


@mcp.tool()
def start_reel(reel_id: str, from_clip: int = 1, allow_slow: bool = False) -> dict:
    """Start, resume or redo a reel on Comfy Cloud (SPENDS credits). from_clip is 1-based:
    1 makes everything, 3 keeps clips 1-2 and remakes from clip 3 onward.
    allow_slow=True is needed if Turbo is off for this reel (several times the cost)."""
    return {"reel_id": reel_id, **_start(reel_id, max(0, int(from_clip) - 1), allow_slow)}


@mcp.tool()
def stop_reel(reel_id: str, now: bool = False) -> dict:
    """Stop a running reel. now=False stops after the current clip (keeps it);
    now=True cancels the Comfy Cloud job immediately."""
    return _call("POST", f"/api/projects/{reel_id}/stop", json={"now": now})


@mcp.tool()
def edit_clip(reel_id: str, clip_number: int, motion: str) -> dict:
    """Replace the motion prompt of one clip (1-based). Then call start_reel(reel_id, from_clip=clip_number)
    to remake it and the clips after it."""
    d = _call("GET", f"/api/projects/{reel_id}")
    n = len(d.get("clips") or [])
    if not 1 <= clip_number <= n:
        raise ReelStudioError(f"This reel has clips 1-{n}.")
    clips = [{} for _ in range(n)]
    clips[clip_number - 1] = {"motion": motion}
    d = _call("POST", f"/api/projects/{reel_id}/clips", json={"clips": clips})
    return {"reel_id": reel_id, "clip": clip_number, "motion": d["clips"][clip_number - 1]["motion"]}


@mcp.tool()
def set_turbo(enabled: bool = True, reel_id: str = "") -> dict:
    """Turn Turbo mode on or off as the default and, optionally, for one reel (not while it is running).
    Turbo is about 1-2 min per clip and far fewer credits."""
    body = {"enabled": enabled}
    if reel_id:
        body["project"] = reel_id
    return _call("POST", "/api/turbo", json=body)


@mcp.tool()
def wait_for_reel(reel_id: str, max_seconds: int = 240) -> dict:
    """Wait (up to max_seconds, at most 290) for a running reel to change state, then return its status.
    Handy for following progress without polling by hand."""
    end = time.time() + max(10, min(int(max_seconds), 290))
    first = _summary(_call("GET", f"/api/projects/{reel_id}"))
    while time.time() < end and first["status"] == "running":
        time.sleep(10)
        now = _summary(_call("GET", f"/api/projects/{reel_id}"))
        if now["status"] != "running" or now["clips_done"] != first["clips_done"]:
            return now
    return _summary(_call("GET", f"/api/projects/{reel_id}"))


if __name__ == "__main__":
    mcp.run()
