#!/bin/bash
# Control the TTS daemon. Used by Claude Code hooks (SessionStart/SessionEnd)
# and usable by hand. Idempotent.
#
#   ./voiced.sh start    # launch daemon if not already running; register session
#   ./voiced.sh stop     # release this session; kill daemon when none remain
#   ./voiced.sh status   # report health
#   ./voiced.sh say "text"  # speak a line via the running daemon
#
# Run by hand, `stop` force-kills the daemon. Run as a SessionEnd hook (gets a
# session_id on stdin), `stop` only kills it once no other session is using it.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# When invoked by a nested `claude -p` (used to generate greeting/reply lines),
# the SessionStart/SessionEnd hooks fire again. Bail on those so the nested
# Claude never restarts — or kills — the daemon out from under the real session.
if [ -n "${COMPANION_NO_HOOK:-}" ]; then
  case "${1:-}" in start|stop) exit 0 ;; esac
fi

PY="$DIR/.venv/bin/python"
cfg() { PYTHONPATH="$DIR" "$PY" -c "import config; print(getattr(config,'$1',''))" 2>/dev/null || true; }
PORT="$(cfg PORT)";   PORT="${PORT:-8765}"
VOICE="$(cfg VOICE)"; VOICE="${VOICE:-sample}"
# GREETING_PROMPT / PERSONA are read inside `start` only (the sole place they're used).

RUNTIME="$(cfg RUNTIME_DIR)"; RUNTIME="${RUNTIME:-${TMPDIR:-/tmp}/claude-code-companion}"
LOG="$RUNTIME/daemon.log"
PIDFILE="$RUNTIME/daemon.pid"     # PID of the daemon we launched (verified before kill)
SESS_DIR="$RUNTIME/sessions"      # one token file per live Claude session (refcount)

is_up()    { curl -s -m 1 "127.0.0.1:$PORT/health" >/dev/null 2>&1; }
is_ready() { curl -s -m 1 "127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ready": *true'; }
is_our_daemon() { ps -p "$1" -o command= 2>/dev/null | grep -q "daemon.py"; }

# Echo the PID of OUR daemon (never an unrelated process on the port): prefer the
# pidfile, else any port owner that verifies as daemon.py. Empty if none.
daemon_pid() {
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && is_our_daemon "$pid"; then echo "$pid"; return; fi
  for pid in $(lsof -ti:"$PORT" 2>/dev/null); do
    if is_our_daemon "$pid"; then echo "$pid"; return; fi
  done
}

# Resolve the claude CLI even when a hook's PATH is minimal (GUI/launchd launch).
find_claude() {
  if command -v claude 2>/dev/null; then return; fi
  for p in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude "$HOME/.claude/local/claude"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
}

# Session id from the hook's stdin JSON (used to refcount the shared daemon).
# Empty when run by hand from a terminal — manual runs don't refcount.
session_id() {
  if [ -t 0 ]; then return 0; fi
  cat 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true
}

case "${1:-status}" in
  start)
    SID="$(session_id)"
    if is_up; then
      echo "already running"
    else
      GREETING_PROMPT="$(cfg GREETING_PROMPT)"
      PERSONA="$(cfg PERSONA)"
      rm -rf "$SESS_DIR"          # fresh daemon: any leftover tokens are from dead sessions
      mkdir -p "$RUNTIME"
      nohup "$PY" "$DIR/daemon.py" --port "$PORT" >"$LOG" 2>&1 &
      DPID=$!
      echo "$DPID" > "$PIDFILE"
      echo "starting daemon (pid $DPID), loading model in background; log: $LOG"
      # wait for model ready, then speak a claude-generated greeting
      (
        CLAUDE="$(find_claude || true)"
        for i in $(seq 1 30); do
          sleep 2
          if is_ready; then
            if [ -n "$CLAUDE" ]; then
              # --strict-mcp-config: load NO MCP servers, so this text-only
              # generator can't see (and try to call) the project's `speak` tool
              # and narrate a permission denial into the spoken greeting.
              GREETING="$(COMPANION_NO_HOOK=1 "$CLAUDE" -p --strict-mcp-config "$PERSONA"$'\n\n'"$GREETING_PROMPT" 2>/dev/null)" || GREETING="Ready when you are."
            else
              GREETING="Ready when you are."
            fi
            curl -s -m 2 -XPOST "127.0.0.1:$PORT/speak" \
              -H 'Content-Type: application/json' \
              -d "{\"text\": $("$PY" -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))' <<< "$GREETING"), \"voice\": \"$VOICE\"}" \
              >/dev/null
            break
          fi
        done
      ) &
    fi
    [ -n "$SID" ] && { mkdir -p "$SESS_DIR"; : > "$SESS_DIR/$SID"; }
    ;;
  stop)
    # Is this a genuine manual run (interactive terminal)? Capture BEFORE reading
    # stdin. ONLY a manual run force-tears-down; a hook run must never kill a
    # daemon another window is still using.
    if [ -t 0 ]; then MANUAL=1; else MANUAL=0; fi
    SID="$(session_id)"
    if [ "$MANUAL" = 1 ]; then
      rm -rf "$SESS_DIR"          # manual stop: force teardown regardless of refcount
    else
      # Hook run: release just this session's token (if we could identify it).
      [ -n "$SID" ] && rm -f "$SESS_DIR/$SID"
      # If any other session still holds a token, leave the daemon for them. An
      # empty/unknown SID in a hook NEVER force-kills — that was the bug that tore
      # the shared daemon down when a second window closed.
      if [ -n "$(ls -A "$SESS_DIR" 2>/dev/null)" ]; then
        echo "daemon still in use by another session; leaving it"; exit 0
      fi
    fi
    pid="$(daemon_pid)"
    if [ -n "$pid" ]; then
      if is_ready; then
        # Say goodbye, then free the model. SessionEnd hooks are killed when the
        # CLI exits, so the slow part (generate the line + speak it) runs in a
        # DETACHED process that outlives this session — like the daemon itself.
        # goodbye.py POSTs /shutdown (daemon speaks, then exits); if that fails it
        # kills $pid so the RAM is freed regardless. The COMPANION_NO_HOOK bail at
        # the top keeps goodbye.py's nested `claude -p` from re-entering teardown.
        nohup "$PY" "$DIR/goodbye.py" "$pid" >/dev/null 2>&1 &
        disown 2>/dev/null || true
        echo "goodbye dispatched; daemon shutting down (pid $pid)"
      else
        # model not ready (still loading) — nothing to speak; just free the RAM.
        kill "$pid" 2>/dev/null && echo "stopped (pid $pid)" || echo "not running"
      fi
      rm -f "$PIDFILE"
    else
      echo "not running"
    fi
    ;;
  status)
    if is_up; then curl -s "127.0.0.1:$PORT/health"; echo; else echo "down"; fi
    ;;
  say)
    curl -s -m 2 -XPOST "127.0.0.1:$PORT/speak" \
      -H 'Content-Type: application/json' \
      -d "{\"text\": $(printf '%s' "${2:-}" | "$PY" -c 'import json,sys;print(json.dumps(sys.stdin.read()))'), \"voice\": \"$VOICE\"}"
    echo
    ;;
  *)
    echo "usage: $0 {start|stop|status|say <text>}"; exit 1 ;;
esac
