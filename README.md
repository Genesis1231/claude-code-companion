# Claude Code Companion

A private voice companion for Claude Code, powered by local TTS.

It starts a resident Apple Silicon TTS daemon, greets you when a Claude Code session opens, reacts while Claude is writing, and says goodbye when the session ends. Use [Fish Audio S2](https://huggingface.co/mlx-community/fish-audio-s2-pro) for cloned voices, or [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16) for fast built-in voices.

No cloud TTS. No speech API keys. No audio or code leaves your machine.

## Features

* **Private local speech.** Run Fish Audio S2 or Kokoro through MLX, with no cloud speech service or per-word cost.
* **Claude Code aware.** Greet on session start, react while Claude writes, remember recent context, and shut down cleanly at the end.
* **Resident model daemon.** Load the model once and reuse it through hooks, the CLI, and the MCP server.
* **Voice cloning.** Use `voices/<name>.wav` + `voices/<name>.txt` with Fish Audio S2.
* **Configurable persona.** Tune the greeting, reaction, and farewell prompts in `config.json`.
* **Inline emotion tags.** Tags like `[laughing]`, `[whisper]`, `[sigh]`, and `[gasp]` let supported models shape the delivery.


## What you need

* An Apple Silicon Mac.
* Python 3.10 or newer.
* A Hugging Face token, if the model download asks for one.

## Get started

```bash
git clone https://github.com/Genesis1231/claude-code-companion.git
cd claude-code-companion && ./setup.sh
```

Next, configure `config.json`, then open a Claude Code session. The companion starts, greets you, and speaks on every message from then on. The default voice is the included `voices/sample.wav` + `voices/sample.txt` pair.

## Add a voice (for Fish Studio)

A voice is two files in `voices/`. The repo includes a small `sample` voice so setup works out of the box:

* `voices/<name>.wav`: a clean 20 to 45 second mono clip (any sample rate — it's resampled to 44.1kHz automatically).
* `voices/<name>.txt`: its exact transcript.


## How it works

A background service (`daemon.py`) loads it once and keeps it ready. Everything else talks to that service:

* Claude Code **hooks** start the service when a session opens, fire a reaction on each message, quietly record each exchange so the next reaction has context, and stop the service when the session ends.
* The **MCP server** is a thin proxy to the same service, so nothing loads the model twice.
* Each voice's reference clip is encoded once and reused, so repeat lines generate quickly, and speech **streams to your speakers as it is generated** — playback starts almost as soon as the first chunks exist, not after the whole clip renders.
* The recent conversation — your messages and Claude's replies — is kept in a local `logs/<session>.md` file and folded into each reaction. Like everything else here, it never leaves your machine.

Every part fails quietly. If the voice service is down you simply get no audio (your messages are still remembered for context), never a broken Claude session.

## Settings

Edit `config.json`:

* `voice`: default voice to speak in.
* `persona`: who the companion is. The default is a warm, playful voice.
* `greeting_prompt`, `reply_prompt`, and `farewell_prompt`: what she says when a session opens, when you send a message, and when the session ends.
* `model` and `port`: the TTS model and the local service port. The default [Fish Audio S2 Pro](https://huggingface.co/mlx-community/fish-audio-s2-pro-bf16) clones the voices in `voices/`. To use [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16) instead (54 fast built-in voices, no cloning), set `model` to `mlx-community/Kokoro-82M-bf16` and `voice` to a preset like `af_heart` (`setup.sh` already installs the `misaki[en]` text processor it needs).

Note: The companion adds a small cost per message. To turn the spoken reactions off, remove the `UserPromptSubmit` hook from `.claude/settings.json` and drive the voice yourself.
