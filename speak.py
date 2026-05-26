"""Speak text via the resident TTS engine (MLX). Thin CLI over engine.Engine.

Usage:
    python speak.py "Hello [excited] this is a test [laughing]"
    python speak.py --voice her1_clean "say this in that voice"
    python speak.py --ref-audio voice.wav --ref-text "transcript" "ad-hoc clone"
    python speak.py --no-play "generate only"
    echo "piped text" | python speak.py -

Exits 0 on success, prints the wav path on the last line of stdout.
This loads its OWN model copy (no daemon). For repeated use, prefer the daemon
(voiced.sh) so the model stays resident.
"""

import argparse
import sys

from engine import VOICES_DIR, engine


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Generate + play speech via the configured TTS model (MLX).")
    p.add_argument("text", help="text to speak, or '-' to read from stdin")
    p.add_argument("--no-play", action="store_true", help="generate only, don't play")
    p.add_argument("--prefix", default=None, help="output filename prefix (default: timestamp)")
    p.add_argument("--voice", default=None, help=f"named profile in {VOICES_DIR}/ (<name>.wav + <name>.txt)")
    p.add_argument("--ref-audio", default=None, help="ad-hoc reference audio file for cloning")
    p.add_argument("--ref-text", default=None, help="transcript of --ref-audio (required with it)")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--top-p", type=float, default=0.7)
    p.add_argument("--top-k", type=int, default=30)
    p.add_argument("--max-tokens", type=int, default=2048)
    args = p.parse_args()

    text = sys.stdin.read().strip() if args.text == "-" else args.text
    if not text:
        log("error: empty text")
        return 2
    if args.voice and (args.ref_audio or args.ref_text):
        log("error: use either --voice or --ref-audio/--ref-text, not both")
        return 2
    if bool(args.ref_audio) ^ bool(args.ref_text):
        log("error: --ref-audio and --ref-text must be provided together")
        return 2
    if args.voice and args.voice not in engine.list_voices():
        log(f"error: voice '{args.voice}' not found in {VOICES_DIR}/ "
            f"(available: {', '.join(engine.list_voices()) or 'none'})")
        return 2

    log("loading model (use the daemon for repeated calls)...")
    try:
        result = engine.speak(
            text=text,
            voice=args.voice,
            ref_audio_path=args.ref_audio,
            ref_text=args.ref_text,
            prefix=args.prefix,
            play=not args.no_play,
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
            max_tokens=args.max_tokens,
        )
    except Exception as e:
        log(f"error: {e}")
        return 1

    log(f"generated {result['audio_seconds']}s in {result['generation_seconds']}s "
        f"(RTF {result['realtime_factor']}), model load {result['model_load_seconds']}s")
    print(result["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
