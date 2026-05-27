"""MCP server exposing the TTS daemon as tools for Claude Code.

This is a THIN PROXY to the resident daemon — it holds no model itself,
so running it alongside the daemon never double-loads the 18GB weights. The
daemon (started by the SessionStart hook) owns the single model instance.

Register via .mcp.json. Tools:
  speak(text, voice, speed)  - generate + play through the daemon
  list_voices()       - available profiles
  voice_status()      - is the daemon up / model ready
"""

import functools
import json
import urllib.request

from mcp.server.fastmcp import FastMCP

from config import PORT, VOICE, logger
BASE = f"http://127.0.0.1:{PORT}"

# Bypass any system/env proxy — the daemon is on localhost. Without this, a
# configured HTTP proxy (e.g. a VPN app) hijacks 127.0.0.1 and resets the call.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

mcp = FastMCP("claude-code-companion")


def _logged(fn):
    """Record any unexpected crash in a tool body (a daemon-down case is already
    handled gracefully by _get/_post; this catches real bugs like a dropped
    import) instead of letting MCP swallow it silently."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("MCP tool %s crashed", fn.__name__)
            return {"error": f"{fn.__name__} failed; see logs/companion.log"}
    return wrapper


def _get(path: str, timeout: float = 2.0) -> dict:
    try:
        with _opener.open(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": f"daemon not reachable ({e}). Is the daemon running? Try: voiced.sh start"}


def _post(path: str, payload: dict, timeout: float = 3.0) -> dict:
    try:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": f"daemon not reachable ({e}). Is the daemon running? Try: voiced.sh start"}


@mcp.tool()
@_logged
def voice_status() -> dict:
    """Check whether the voice daemon is running and the model is loaded."""
    return _get("/health")


@mcp.tool()
@_logged
def list_voices() -> dict:
    """List available voice profiles (voices/<name>.wav + <name>.txt pairs)."""
    health = _get("/health")
    if "error" in health and "voices" not in health:
        return health
    return {"voices": health.get("voices", [])}


@mcp.tool()
@_logged
def speak(text: str, voice: str = VOICE) -> dict:
    """Speak text aloud in a cloned voice (fire-and-forget).

    Supports inline prosody tags: [excited] [whisper] [laughing] [sad] [angry]
    [pause] [sigh] and many more. Returns immediately; audio plays in the
    background on the daemon. Keep lines short (~10-15 words) so the spoken
    line stays close behind the text.

    speed: optional playback rate override (1.0 = natural). When omitted, the
    daemon uses the voice's configured default (voices/<name>.speed, else 1.0).
    """
    payload = {"text": text, "voice": voice}
    return _post("/speak", payload)


if __name__ == "__main__":
    try:
        mcp.run()
    except Exception:
        logger.exception("mcp_server crashed")
        raise
