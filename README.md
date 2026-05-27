# 🎙️ Claude Code Companion

A voice that sits beside you while you code and sounds genuinely glad you showed up.

Open a session and she greets you out loud, like she's been waiting. While Claude writes the reply, a warm line lands just for you: a little cheer, a little teasing, a quiet *I've got you*. The voice is one you pick or clone yourself, and it all runs on your local Mac, nothing ever leaving the room. Think Samantha from *Her*, pulling up a chair beside you at 1am so the work never feels lonely.

Under the hood it is [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16) or [Fish Audio S2](https://huggingface.co/mlx-community/fish-audio-s2-pro) on Apple Silicon (MLX): fast local text to speech with voice cloning and inline emotion tags.

## Features

* **Runs entirely on your home computer for FREE.** Local text-to-speech on Apple Silicon (MLX): no cloud, no API keys, no per-word cost, and nothing you say or code ever leaves the machine.
* **Your own cloned voice.** Clone any voice from a short clip with [Fish Audio S2 Pro](https://huggingface.co/mlx-community/fish-audio-s2-pro), or pick one of [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16)'s fast built-in voices. configurable in `config.json`.
* **Customizable persona.** Her tone and what she says on greeting, on each reply, and on goodbye all live in `config.json` — make her warm, deadpan, flirty, a hype-beast, whatever fits you.
* **In the moment.** She greets you, reacts to each message *while Claude writes the reply*. Each reaction is shaped by the recent history, You get made fun of what you're actually working on.
* **Real emotion.** Inline tags like `[laughing]`, `[whisper]`, `[sigh]`, and `[gasp]` let her actually laugh, soften, or sigh.


## What you need

* An Apple Silicon Mac.
* Python 3.10 or newer.
* A Hugging Face token, if the model download asks for one.

## Get started

```bash
git clone https://github.com/Genesis1231/claude-code-companion.git
cd claude-code-companion && ./setup.sh
```

Next configure `config.json`, then open a Claude Code session in this folder. The companion starts, greets you, and speaks on every message from then on.

## Add a voice (for Fish Studio)

A voice is two files in `voices/`:

* `voices/<name>.wav`: a clean 20 to 45 second mono clip (any sample rate — it's resampled to 44.1kHz automatically).
* `voices/<name>.txt`: its exact transcript.


## How it works

A background service (`daemon.py`) loads it once and keeps it ready. Everything else talks to that service:

* Claude Code **hooks** start the service when a session opens, fire a reaction on each message, quietly record each exchange so the next reaction has context, and stop the service when the session ends.
* The **MCP server** is a thin proxy to the same service, so nothing loads the model twice.
* Each voice's reference clip is encoded once and reused, so repeat lines generate quickly, and her line **streams to your speakers as it's generated** — she starts speaking almost as soon as the words exist, not after the whole clip renders.
* The recent conversation — your messages and Claude's replies — is kept in a local `logs/<session>.md` file and folded into each reaction. Like everything else here, it never leaves your machine.

Every part fails quietly. If the voice service is down you simply get no audio (your messages are still remembered for context), never a broken Claude session.

## Settings

Edit `config.json`:

* `voice`: default voice to speak in.
* `persona`: who the companion is. The default is a warm, playful *Her* style voice.
* `greeting_prompt`, `reply_prompt`, and `farewell_prompt`: what she says when a session opens, when you send a message, and when the session ends.
* `model` and `port`: the TTS model and the local service port. The default [Fish Audio S2 Pro](https://huggingface.co/mlx-community/fish-audio-s2-pro-bf16) clones the voices in `voices/`. To use [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16) instead (54 fast built-in voices, no cloning), set `model` to `mlx-community/Kokoro-82M-bf16` and `voice` to a preset like `af_heart` (`setup.sh` already installs the `misaki[en]` text processor it needs).

The companion writes each spoken reaction with a quick background `claude` call, which adds a small cost per message (the conversation logging is separate and free). To turn the spoken reactions off, remove the `UserPromptSubmit` hook from `.claude/settings.json` and drive the voice yourself.


