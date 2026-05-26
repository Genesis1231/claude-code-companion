"""Detached goodbye worker. Generates a farewell line via `claude -p` and POSTs
it to the daemon's /shutdown (the daemon speaks it, then exits to free the model
RAM). On any failure it kills the daemon pid so the RAM is freed regardless.

Why a separate, detached process: SessionEnd hooks are killed when the CLI exits,
so the slow generation must run OUTSIDE the dying session. `voiced.sh stop`
launches this with `nohup ... & disown` so it outlives the session, the same way
the daemon itself survives.

Usage:  goodbye.py <daemon_pid>     (pid is the fallback kill target)

Recursion guard: the nested `claude -p` runs with COMPANION_NO_HOOK=1, so when it
ends and fires SessionEnd -> `voiced.sh stop`, that bails instead of looping.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
_cfg = json.loads((HERE / "config.json").read_text())
PORT = _cfg["port"]
VOICE = _cfg["voice"]
PERSONA = _cfg["persona"]
FAREWELL_PROMPT = _cfg.get("farewell_prompt", "")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _claude_bin():
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
              Path("/usr/local/bin/claude"), Path.home() / ".claude/local/claude"):
        if p.exists():
            return str(p)
    return None


def _post_shutdown(text: str, pid=None) -> bool:
    """Tell the daemon to speak the line (if any) then exit. True on a 2xx.
    `pid` targets a specific daemon instance, so this stale goodbye can't shut
    down a newer daemon that has since taken the port (the daemon 409s a mismatch)."""
    payload = {"text": text, "voice": VOICE}
    if pid is not None:
        payload["pid"] = pid
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/shutdown",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        _opener.open(req, timeout=8)
        return True
    except Exception:
        return False


def main():
    # Become our own session leader so signals sent to the exiting CLI's process
    # group (SIGHUP/SIGTERM on session teardown) don't kill us mid-goodbye. This
    # is the belt to nohup's braces; best-effort, harmless if it fails.
    try:
        os.setsid()
    except OSError:
        pass

    pid = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        pid = int(sys.argv[1])

    line = ""
    claude = _claude_bin()
    if claude:
        try:
            line = subprocess.run(
                # --strict-mcp-config: no MCP servers, so this text-only call
                # can't reach the `speak` tool and narrate a permission denial.
                [claude, "-p", "--strict-mcp-config", f"{PERSONA}\n\n{FAREWELL_PROMPT}"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "COMPANION_NO_HOOK": "1"},
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            line = ""

    # POST even an empty line: the daemon's /shutdown frees RAM either way (it
    # just won't speak). Only fall back to a hard kill if the daemon refuses it
    # (e.g. an old build with no /shutdown route).
    if not _post_shutdown(line, pid) and pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


if __name__ == "__main__":
    main()
