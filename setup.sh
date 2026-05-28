#!/bin/bash
# One-shot installer for the Claude Code voice companion.
#
# Creates a Python venv, installs dependencies, and wires up Claude Code
# (MCP server + session hooks) with absolute paths for THIS machine.
#
# Safe to re-run: it never overwrites an existing .env, .mcp.json, or
# .claude/settings.json — it only fills in what's missing.
#
#   ./setup.sh
#
# Requirements: Apple Silicon (MLX), Python 3.10+, ~10GB disk for the model,
# ~18GB RAM to run it (bf16). Audio streams out via sounddevice (PortAudio).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PY="${PYTHON:-python3}"
VENV="$DIR/.venv"
VPY="$VENV/bin/python"

echo "==> claude-code-companion setup"
echo "    project: $DIR"

# --- 1. Python venv -----------------------------------------------------
if [ ! -x "$VPY" ]; then
  echo "==> creating venv (.venv)"
  "$PY" -m venv "$VENV"
fi
"$VPY" -m pip install --quiet --upgrade pip

# --- 2. Dependencies ----------------------------------------------------
echo "==> installing dependencies (first run pulls a lot — grab a coffee)"
# soundfile + sounddevice: engine.py decodes reference clips and streams playback
# through them. python-dotenv: loads .env (HF_TOKEN). These are declared explicitly
# rather than leaned on as transitive deps of mlx-audio.
"$VPY" -m pip install --quiet -r "$DIR/requirements.txt"

# Optional: text processor for the Kokoro model (a built-in-voice alternative to
# the default Fish Audio S2 Pro). Harmless if you only ever use Fish, and made
# non-fatal: a build failure here must not abort the rest of setup (which still
# has to write .env, .mcp.json, and the hooks below).
"$VPY" -m pip install --quiet -r "$DIR/requirements-optional.txt" \
  || echo "==> note: optional misaki[en] (Kokoro support) failed to install; skipping"

# --- 3. Directories -----------------------------------------------------
mkdir -p "$DIR/voices"

# --- 4. .env ------------------------------------------------------------
if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo "==> created .env from .env.example (add your HF_TOKEN if the model needs auth)"
fi

# --- 5. .mcp.json (don't clobber) --------------------------------------
if [ -f "$DIR/.mcp.json" ]; then
  echo "==> .mcp.json already exists — leaving it"
else
  cat > "$DIR/.mcp.json" <<EOF
{
  "mcpServers": {
    "claude-code-companion": {
      "command": "$VENV/bin/python",
      "args": ["$DIR/mcp_server.py"]
    }
  }
}
EOF
  echo "==> wrote .mcp.json"
fi

# --- 6. .claude/settings.json (don't clobber) --------------------------
# Default experience = automatic voice companion (persona lives in config.json):
#   SessionStart    -> voiced.sh start: daemon loads the model, speaks a greeting
#   UserPromptSubmit -> reply_hook.py: a warm claude-generated line each turn
#   Stop            -> stop_hook.py: logs Claude's reply so reactions have both
#                      sides of the recent conversation as context (no voice)
#   SessionEnd      -> voiced.sh stop: on the LAST session, dispatches goodbye.py
#                      (detached, so it outlives the exit) to speak a send-off,
#                      then frees the model's RAM.
# Drop the "UserPromptSubmit" block to drive the voice yourself.
mkdir -p "$DIR/.claude"
if [ -f "$DIR/.claude/settings.json" ]; then
  echo "==> .claude/settings.json already exists — leaving it"
else
  cat > "$DIR/.claude/settings.json" <<EOF
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "$DIR/voiced.sh start", "async": true, "statusMessage": "Starting voice daemon" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "$VENV/bin/python $DIR/reply_hook.py", "async": true } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "$VENV/bin/python $DIR/stop_hook.py", "async": true } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "$DIR/voiced.sh stop" } ] }
    ]
  }
}
EOF
  echo "==> wrote .claude/settings.json (auto-speak hooks)"
fi

chmod +x "$DIR/voiced.sh"

# --- 7. sanity check: the companion needs the `claude` CLI --------------
# reply_hook.py and voiced.sh shell out to `claude -p` for the greeting and the
# per-prompt lines. Those hooks fail silently, so warn here rather than leave
# the user wondering why the voice never speaks.
if ! command -v claude >/dev/null 2>&1; then
  echo
  echo "==> WARNING: 'claude' CLI not found on PATH."
  echo "    The voice companion (greeting + per-prompt reactions) needs it and"
  echo "    will stay silent without it. The speak.py CLI and MCP 'speak' tool"
  echo "    still work regardless."
fi

cat <<EOF

==> Setup complete.

Next steps:
  1. Use the included sample voice, or add your own voice profile:
       voices/<name>.wav   a clean 20-40s mono speech clip
       voices/<name>.txt   its exact transcript
     Quick transcript with the bundled whisper:
       $VENV/bin/mlx_whisper voices/<name>.wav --model mlx-community/whisper-large-v3-mlx \\
         --output-format txt --output-dir voices/
     The default voice is "sample" (set in config.json).

  2. Start the daemon (first run downloads the ~10GB model):
       ./voiced.sh start
     ...then:  ./voiced.sh say "hello, this is my cloned voice"

  3. Or just open a Claude Code session in this folder — the hooks handle
     start/speak/stop automatically.
EOF
