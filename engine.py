"""Resident TTS engine with per-voice reference caching.

Loads the 5B model once and keeps it in memory. The expensive part of each
generation — encoding the multi-second reference clip into VQ tokens via the
audio codec — is cached per voice, so repeat calls with the same voice skip it.
"""

import json
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

HERE = Path(__file__).parent
VOICES_DIR = HERE / "voices"
# Generated wavs are throwaway — each is played once. Keep them in the system
# temp dir ($TMPDIR) so the OS reaps them; never write them into the project.
OUT_DIR = Path(tempfile.gettempdir()) / "claude-code-companion"

_cfg = json.loads((HERE / "config.json").read_text())
MODEL_ID = _cfg["model"]


def _load_env(path: Path = HERE / ".env") -> None:
    import os
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if os.environ.get("HF_TOKEN") and not os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        os.environ["HUGGING_FACE_HUB_TOKEN"] = os.environ["HF_TOKEN"]


class Engine:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()           # serialize generation (single GPU)
        self._ref_cache: dict[str, tuple] = {}  # voice -> (prompt_texts, prompt_tokens)
        self._load_time: Optional[float] = None
        self._cloning = False                    # set at load(): does this model clone?

    # ---- model lifecycle -------------------------------------------------
    def load(self) -> float:
        """Load the model once. Returns seconds taken (0 if already loaded)."""
        if self._model is not None:
            return 0.0
        with self._lock:
            if self._model is not None:
                return 0.0
            _load_env()
            t0 = time.time()
            from mlx_audio.tts.utils import load_model
            self._model = load_model(MODEL_ID)
            # Fish-style models clone from a reference clip via this method;
            # built-in-voice models (e.g. Kokoro) lack it and pick a named preset.
            self._cloning = hasattr(self._model, "_prepare_reference_prompt")
            self._load_time = time.time() - t0
            return self._load_time

    @property
    def ready(self) -> bool:
        return self._model is not None

    # ---- voices ----------------------------------------------------------
    def list_voices(self) -> list[str]:
        return sorted(
            p.stem for p in VOICES_DIR.glob("*.wav")
            if (VOICES_DIR / f"{p.stem}.txt").exists()
        )

    def voice_speed(self, voice: Optional[str]) -> float:
        """Per-voice default speed from optional voices/<name>.speed (else 1.0)."""
        if not voice:
            return 1.0
        f = VOICES_DIR / f"{voice}.speed"
        try:
            return float(f.read_text().strip())
        except (OSError, ValueError):
            return 1.0

    def _decode_audio(self, path: str, target_sr: int = 44100):
        import miniaudio
        import mlx.core as mx
        decoded = miniaudio.decode_file(
            path, output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1, sample_rate=target_sr,
        )
        floats = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
        return mx.array(floats)

    def _reference_prompt(self, voice: str):
        """Return cached (prompt_texts, prompt_tokens) for a voice, computing once."""
        if voice in self._ref_cache:
            return self._ref_cache[voice]
        wav = VOICES_DIR / f"{voice}.wav"
        txt = VOICES_DIR / f"{voice}.txt"
        if not wav.exists() or not txt.exists():
            raise ValueError(f"voice '{voice}' not found (have: {', '.join(self.list_voices()) or 'none'})")
        ref_audio = self._decode_audio(str(wav))
        ref_text = txt.read_text().strip()
        # This runs the codec encoder over the whole clip — the part we cache.
        prompt = self._model._prepare_reference_prompt(ref_audio, ref_text)
        self._ref_cache[voice] = prompt
        return prompt

    def warm_voice(self, voice: str) -> None:
        self.load()
        if self._cloning:
            self._reference_prompt(voice)
        # built-in-voice models (e.g. Kokoro) have nothing to pre-encode

    # ---- generation ------------------------------------------------------
    def speak(
        self,
        text: str,
        voice: Optional[str] = None,
        ref_audio_path: Optional[str] = None,
        ref_text: Optional[str] = None,
        prefix: Optional[str] = None,
        play: bool = True,
        temperature: float = 0.7,
        top_p: float = 0.7,
        top_k: int = 30,
        max_tokens: int = 2048,
        speed: Optional[float] = None,
    ) -> dict:
        """Generate speech. For reference-cloning models (Fish S2 Pro), voice
        source priority is: named `voice` profile (cached reference) >
        `ref_audio_path` + `ref_text` > zero-shot. For built-in-voice models
        (Kokoro), `voice` is a built-in preset name (e.g. af_heart)."""
        self.load()
        import mlx.core as mx

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        prefix = prefix or f"clip_{int(time.time())}"
        out_path = OUT_DIR / f"{prefix}.wav"

        if speed is None or speed <= 0:
            speed = self.voice_speed(voice)

        with self._lock:
            t0 = time.time()
            if self._cloning:
                gen_kwargs = dict(text=text, temperature=temperature, top_p=top_p,
                                  top_k=top_k, max_tokens=max_tokens, speed=speed)
                if voice:
                    # Inject the cached reference prompt, bypassing codec.encode.
                    cached = self._reference_prompt(voice)
                    orig = self._model._prepare_reference_prompt
                    self._model._prepare_reference_prompt = lambda *a, **k: cached
                    # generate() needs ref_audio truthy to take the reference path
                    gen_kwargs["ref_audio"] = mx.zeros((1,), dtype=mx.float32)
                    gen_kwargs["ref_text"] = cached[0][0] if cached[0] else ""
                    try:
                        chunks, sr = self._run(gen_kwargs)
                    finally:
                        self._model._prepare_reference_prompt = orig
                else:
                    if ref_audio_path:
                        gen_kwargs["ref_audio"] = self._decode_audio(ref_audio_path)
                        gen_kwargs["ref_text"] = ref_text or ""
                    chunks, sr = self._run(gen_kwargs)
            else:
                # Built-in-voice model (e.g. Kokoro): no cloning; `voice` is a
                # preset name. Fish-only sampling params don't apply.
                gen_kwargs = dict(text=text, speed=speed)
                if voice:
                    gen_kwargs["voice"] = voice
                chunks, sr = self._run(gen_kwargs)
            gen_time = time.time() - t0

        audio = mx.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        samples = _normalize(np.asarray(audio, dtype=np.float32), target_peak=0.95)
        _write_wav(out_path, samples, sr)
        audio_dur = len(samples) / sr

        if play:
            _play(out_path)

        _prune_outputs(keep=8)

        return {
            "path": str(out_path),
            "audio_seconds": round(audio_dur, 2),
            "generation_seconds": round(gen_time, 2),
            "realtime_factor": round(gen_time / audio_dur, 2) if audio_dur else None,
            "voice": voice,
            "model_load_seconds": round(self._load_time, 2) if self._load_time else 0.0,
        }

    def _run(self, gen_kwargs):
        chunks, sr = [], None
        for result in self._model.generate(**gen_kwargs):
            chunks.append(result.audio)
            sr = sr or result.sample_rate
        if not chunks:
            raise RuntimeError("no audio produced")
        return chunks, sr


def _prune_outputs(keep: int = 8) -> None:
    """Keep only the most recent `keep` generated wavs in OUT_DIR; they're
    throwaway one-shot clips."""
    outputs = sorted(OUT_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in outputs[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _normalize(samples: "np.ndarray", target_peak: float = 0.95) -> "np.ndarray":
    """Peak-normalize so quiet reference voices come out at consistent loudness."""
    samples = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-4:
        return samples
    return samples * (target_peak / peak)


def _write_wav(path: Path, samples: "np.ndarray", sample_rate: int) -> None:
    # vectorized: clip → int16 little-endian → bytes, in one pass (no per-sample loop)
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _play(path: Path) -> None:
    import shutil
    import subprocess
    import sys
    try:
        if sys.platform == "darwin" and shutil.which("afplay"):
            subprocess.run(["afplay", str(path)], check=False, timeout=300)
        elif shutil.which("ffplay"):
            subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                           check=False, timeout=300)
        else:
            print(f"[engine] no audio player (afplay/ffplay) found — wav at {path}",
                  file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        print(f"[engine] playback timed out for {path}", file=sys.stderr, flush=True)


# Module-level singleton for reuse across server / scripts.
engine = Engine()
