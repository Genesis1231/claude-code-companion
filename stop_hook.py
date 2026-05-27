"""Stop hook: capture Claude's completed response into the session log.

Fires once when Claude finishes a turn. Reads the last assistant message
from transcript_path and appends a truncated version so reply_hook.py can
inject it as context on the next user message.

Recursion guard: nested `claude -p` calls (reply_hook, goodbye) set
COMPANION_NO_HOOK=1 — bail so their responses are never logged.
"""

import datetime
import json
import os
import sys
from pathlib import Path

if os.environ.get("COMPANION_NO_HOOK"):
    sys.exit(0)  # nested claude -p invocation — do nothing

from config import LOGS_DIR, logger


def _last_assistant_text(transcript_path: str) -> str:
    """Walk the transcript backwards for the last assistant text content."""
    try:
        lines = Path(transcript_path).read_text().splitlines()
    except OSError:
        return ""
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        content = (obj.get("message") or {}).get("content", [])
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = [b["text"] for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            text = " ".join(texts).strip()
            if text:
                return text
    return ""


def _truncate(text: str, head: int = 200, tail: int = 200) -> str:
    text = text.strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]} … {text[-tail:].lstrip()}"


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    session_id      = (payload.get("session_id") or "").strip()
    transcript_path = (payload.get("transcript_path") or "").strip()
    if not session_id or not transcript_path:
        return

    text = _last_assistant_text(transcript_path)
    if not text:
        return

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H:%M")
        with (LOGS_DIR / f"{session_id}.md").open("a") as fp:
            fp.write(f"- [{ts}] claude: {_truncate(text)}\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("stop_hook crashed")
