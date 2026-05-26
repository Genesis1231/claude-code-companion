# 🎙️ Claude Code Companion

A cloned voice that talks to you while you code with Claude.

Open a session and you are greeted out loud. Send a message and a warm, in character line reacts to it while Claude writes its reply. The voice is one you pick and clone yourself, and it runs entirely on your Mac. Think Samantha from *Her*, sitting beside you while you work.

Under the hood it is [Fish Audio S2 Pro](https://huggingface.co/mlx-community/fish-audio-s2-pro-bf16) on Apple Silicon (MLX): fast local text to speech with voice cloning and inline emotion tags.

## What you need

* An Apple Silicon Mac.
* Python 3.10 or newer.
* About 10GB of disk for the model and roughly 18GB of free RAM to run it.
* A Hugging Face token, if the model download asks for one.

## Get started

```bash
./setup.sh
```

This sets up a Python environment, installs everything, and connects the companion to Claude Code. It is safe to run again and never overwrites your settings.

Next add a voice (none ship with the repo, since voices are personal), then open a Claude Code session in this folder. The companion starts, greets you, and speaks on every message from then on.

## Add a voice

A voice is two files in `voices/`:

* `voices/<name>.wav`: a clean 20 to 45 second mono clip at 44.1kHz.
* `voices/<name>.txt`: its exact transcript.

```bash
# 1. cut a clip from a recording
ffmpeg -y -i source.m4a -t 45 -ac 1 -ar 44100 -sample_fmt s16 voices/NAME.wav

# 2. write its transcript with whisper
.venv/bin/python -c "import mlx_whisper; open('voices/NAME.txt','w').write(mlx_whisper.transcribe('voices/NAME.wav', path_or_hf_repo='mlx-community/whisper-large-v3-turbo')['text'].strip())"
```

A clean, quiet recording with varied intonation clones best. Set the default voice with the `voice` key in `config.json`.


## How it works

The model is large (about 18GB in memory), so loading it for every line would be far too slow. Instead a small background service (`daemon.py`) loads it once and keeps it ready. Everything else talks to that service:

* Claude Code **hooks** start the service when a session opens, send a reaction on each message, and stop it when the session ends.
* The **MCP server** is a thin proxy to the same service, so nothing loads the model twice.
* Each voice's reference clip is encoded once and reused, so repeat lines generate quickly (around 2.2x realtime).

Every part fails quietly. If the voice service is down you simply get no audio, never a broken Claude session.

## Settings

Edit `config.json`:

* `voice`: default voice to speak in.
* `persona`: who the companion is. The default is a warm, playful *Her* style voice.
* `greeting_prompt` and `reply_prompt`: what it says when a session opens and when you send a message.
* `model` and `port`: the TTS model and the local service port. The default [Fish Audio S2 Pro](https://huggingface.co/mlx-community/fish-audio-s2-pro-bf16) clones the voices in `voices/`. To use [Kokoro](https://huggingface.co/mlx-community/Kokoro-82M-bf16) instead (54 fast built-in voices, no cloning), set `model` to `mlx-community/Kokoro-82M-bf16` and `voice` to a preset like `af_heart` (`setup.sh` already installs the `misaki[en]` text processor it needs).

The companion writes each spoken reaction with a quick background `claude` call, which adds a small cost per message. To turn it off, remove the `UserPromptSubmit` hook from `.claude/settings.json` and drive the voice yourself.

## Notes

* Voice clips, generated audio, and your machine specific settings stay out of git.
* The voice service listens only on `127.0.0.1`.
* Dependencies install `mlx-audio` from git `main`, since the latest PyPI release has a Fish S2 Pro bug that produces noise ([PR #693](https://github.com/Blaizzy/mlx-audio/pull/693)).
