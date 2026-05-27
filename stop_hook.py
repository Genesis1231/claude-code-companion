"""Stop hook: record Claude's reply into the session log so the companion's
per-message reactions (reply_hook.py) have both sides of the conversation as
context, not just the user's lines.

The Stop payload carries `last_assistant_message` (the full text of the reply
that just finished), so we read it straight from there — no transcript parsing.
Logging only: no claude call, no speaking. Fail-silent like the other hooks —
a problem just means a thinner log, never a broken session.

Recursion guard: a nested `claude -p` (used by reply_hook/goodbye) is itself a
Claude Code session and fires Stop too. It runs with COMPANION_NO_HOOK=1, so we
bail on that — otherwise every generated line would log its own reply.
"""

import datetime
import json
import os
import sys

if os.environ.get("COMPANION_NO_HOOK"):
    sys.exit(0)  # nested claude -p invocation — do nothing

from config import LOGS_DIR, session_log, logger

_HEAD_TAIL = 200    # keep the first and last N chars of a long reply


def _condense(text: str) -> str:
    """Flatten to one line. For a long reply keep the opening and the closing
    (which usually holds the summary / next step) and elide the middle."""
    text = " ".join(text.split())
    if len(text) <= _HEAD_TAIL * 2:
        return text
    return f"{text[:_HEAD_TAIL]} \n [...TRUNCATED...]\n {text[-_HEAD_TAIL:]}"


def _append_assistant(session_id: str, text: str) -> None:
    if not session_id or not text:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H:%M")
        with session_log(session_id).open("a") as fp:
            fp.write(f"- [{ts}] Coding Agent: {text}\n")
    except OSError:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    session_id = (payload.get("session_id") or "").strip()
    reply = (payload.get("last_assistant_message") or "").strip()
    if not session_id or not reply:
        return
    _append_assistant(session_id, _condense(reply))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("stop_hook crashed")
