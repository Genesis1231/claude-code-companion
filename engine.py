"""Resident TTS engine with per-voice reference caching and streaming playback.

Loads the 5B model once and keeps it in memory. The expensive part of each
generation — encoding the multi-second reference clip into VQ tokens via the
audio codec — is cached per voice, so repeat calls with the same voice skip it.

Playback streams chunks to the audio device as they arrive (via a worker thread
+ OutputStream), so Time-To-First-Audio ≈ Time-To-First-Chunk rather than the
full generation time.
"""

import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv

from config import MODEL, VOICES_DIR, logger

load_dotenv()

# Applied to every chunk before playback. tanh() provides soft-clip limiting,
# replacing the global peak-normalize that required all audio up front.
_GAIN = 2.0


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
            t0 = time.time()
            from mlx_audio.tts.utils import load_model
            self._model = load_model(MODEL)
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

    def _decode_audio(self, path: str, target_sr: int = 44100):
        """Read an audio file into a float32 mono array at `target_sr`.

        Uses `soundfile` to read and falls back to a NumPy linear interpolation
        resampler (no scipy). Returns an `mlx.core` array when available.
        """
        try:
            import mlx.core as mx
        except ImportError:
            mx = None

        data, sr = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        if sr != target_sr:
            old_len = len(data)
            new_len = int(round(old_len * target_sr / sr))
            if new_len <= 0:
                data = np.zeros(0, dtype=np.float32)
            else:
                old_idx = np.arange(old_len)
                new_idx = np.linspace(0, old_len - 1, new_len)
                data = np.interp(new_idx, old_idx, data).astype(np.float32)

        data = data.astype(np.float32)
        return mx.array(data) if mx is not None else data

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
        play: bool = True,
        speed: float = 1.0,
        temperature: float = 0.7,
        top_p: float = 0.7,
        top_k: int = 30,
        max_tokens: int = 2048,
    ) -> dict:
        """Generate speech. For reference-cloning models (Fish S2 Pro), voice
        source priority is: named `voice` profile (cached reference) >
        `ref_audio_path` + `ref_text` > zero-shot. For built-in-voice models
        (Kokoro), `voice` is a built-in preset name (e.g. af_heart)."""
        self.load()
        import mlx.core as mx

        q: Optional[queue.Queue] = queue.Queue() if play else None
        player = threading.Thread(target=_stream_player, args=(q,), daemon=True) if play else None
        if player:
            player.start()

        total_samples = 0
        sr = None
        gen_time = 0.0

        try:
            with self._lock:
                t0 = time.time()
                if self._cloning:
                    gen_kwargs = dict(text=text, temperature=temperature, top_p=top_p,
                                      top_k=top_k, max_tokens=max_tokens, speed=speed)
                    if voice:
                        # Inject cached reference prompt, bypassing codec.encode.
                        cached = self._reference_prompt(voice)
                        orig = self._model._prepare_reference_prompt
                        self._model._prepare_reference_prompt = lambda *a, **k: cached
                        # generate() needs ref_audio truthy to take the reference path
                        gen_kwargs["ref_audio"] = mx.zeros((1,), dtype=mx.float32)
                        gen_kwargs["ref_text"] = cached[0][0] if cached[0] else ""
                        try:
                            total_samples, sr = self._generate(gen_kwargs, q)
                        finally:
                            self._model._prepare_reference_prompt = orig
                    else:
                        if ref_audio_path:
                            gen_kwargs["ref_audio"] = self._decode_audio(ref_audio_path)
                            gen_kwargs["ref_text"] = ref_text or ""
                        total_samples, sr = self._generate(gen_kwargs, q)
                else:
                    # Built-in-voice model (e.g. Kokoro): no cloning; `voice` is a
                    # preset name. Fish-only sampling params don't apply.
                    gen_kwargs = dict(text=text, speed=speed)
                    if voice:
                        gen_kwargs["voice"] = voice
                    total_samples, sr = self._generate(gen_kwargs, q)
                gen_time = time.time() - t0
        finally:
            # Always send sentinel so player thread can exit cleanly, even on error.
            if q is not None:
                q.put(None)
            if player is not None:
                player.join()

        audio_dur = total_samples / sr if sr else 0.0
        return {
            "audio_seconds": round(audio_dur, 2),
            "generation_seconds": round(gen_time, 2),
            "realtime_factor": round(gen_time / audio_dur, 2) if audio_dur else None,
            "voice": voice,
            "model_load_seconds": round(self._load_time, 2) if self._load_time else 0.0,
        }

    def _generate(self, gen_kwargs: dict, q: Optional[queue.Queue]) -> tuple[int, int]:
        """Iterate model.generate(), push chunks to q, return (total_samples, sr)."""
        total_samples, sr = 0, None
        for result in self._model.generate(**gen_kwargs):
            arr = np.asarray(result.audio, dtype=np.float32).ravel()
            sr = sr or result.sample_rate
            total_samples += len(arr)
            if q is not None:
                q.put((arr, sr))
        if not total_samples:
            raise RuntimeError("no audio produced")
        return total_samples, sr


def _stream_player(q: queue.Queue) -> None:
    """Pull (arr, sr) chunks from queue and write to OutputStream as they arrive.
    Runs in a daemon thread so the generator and playback overlap."""
    stream: Optional[sd.OutputStream] = None
    try:
        while True:
            item = q.get()
            if item is None:
                break
            arr, sr = item
            if stream is None:
                stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
                stream.start()
            stream.write(np.tanh(arr * _GAIN))
    except Exception:
        # This runs in a daemon thread; an unlogged exception here would just
        # vanish (silent no-audio). Record it. The producer's queue is unbounded,
        # so its q.put() won't block even though this consumer is gone, and
        # speak()'s player.join() returns once this thread exits.
        logger.exception("audio playback failed in stream player")
    finally:
        if stream is not None:
            stream.stop()
            stream.close()


# Module-level singleton for reuse across server / scripts.
engine = Engine()
