"""Background TTS daemon: keeps the model resident and speaks lines
sent over a tiny local HTTP endpoint. Non-blocking — POST returns immediately
and audio is generated + played in a worker thread. Newer requests supersede
queued-but-unstarted ones so the spoken voice always matches the latest reply.

Run:   python daemon.py [--port 8765]
Speak: curl -s -XPOST 127.0.0.1:8765/speak -d '{"text":"hi","voice":"sample"}'
Health: curl -s 127.0.0.1:8765/health
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import PORT as DEFAULT_PORT, SESSIONS_DIR, VOICE, logger
from engine import engine

# Session-refcount dir maintained by voiced.sh (one token file per live Claude
# session). The reaper frees the model once it goes empty — see _reap_when_idle.
IDLE_EXIT_GRACE_S = 60   # > worst-case goodbye latency, so a send-off is never cut off


class Speaker:
    """Single worker that OWNS the model. MLX GPU streams are thread-affine, so
    the model must be loaded and used on this same thread — never the main one.
    Only the most recent pending line is spoken (stay current)."""

    def __init__(self, warm_voice: "str | None" = None):
        self._pending = None                     # (text, voice) or None
        self._final = False                      # speak _pending, then exit the process
        self._cv = threading.Condition()
        self._warm_voice = warm_voice
        self.ready = False
        self.error = None                         # set if the worker dies during load
        self._shutting_down = False               # a /shutdown is speaking + exiting
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        threading.Thread(target=self._reap_when_idle, daemon=True).start()

    def submit(self, text: str, voice: str) -> bool:
        """Queue a line. Returns False if the worker is dead (won't be spoken)."""
        if self.error is not None:
            return False
        with self._cv:
            if self._pending is not None:
                # latest-wins: we're dropping an unspoken line — make it visible
                print(f"[daemon] dropping unspoken line (superseded): {self._pending[0][:60]!r}",
                      flush=True)
            self._pending = (text, voice)
            self._cv.notify()
        return True

    def submit_final(self, text: str, voice: str) -> None:
        """Speak one last line (the goodbye), then exit the process to free the
        model's RAM. Used by the /shutdown teardown path."""
        self._shutting_down = True
        with self._cv:
            self._pending = (text, voice)
            self._final = True
            self._cv.notify()

    def _run(self):
        # load on THIS thread so the GPU stream belongs to it
        print("[daemon] loading model...", flush=True)
        try:
            engine.load()
            if self._warm_voice:
                try:
                    engine.warm_voice(self._warm_voice)
                    print(f"[daemon] voice '{self._warm_voice}' warmed", flush=True)
                except Exception as e:
                    print(f"[daemon] warm voice '{self._warm_voice}' failed: {e}", flush=True)
                    logger.warning("warm voice %r failed", self._warm_voice, exc_info=True)
        except Exception as e:
            # fatal: model never loaded. Record it so /health and /speak surface it.
            self.error = f"model load failed: {e}"
            print(f"[daemon] {self.error}", flush=True)
            logger.exception("model load failed")
            return
        self.ready = True
        print("[daemon] model ready", flush=True)
        while True:
            with self._cv:
                while self._pending is None:
                    self._cv.wait()
                text, voice = self._pending
                self._pending = None
                final = self._final
                self._final = False
            try:
                engine.speak(text=text, voice=voice or None, play=True)
            except Exception as e:
                print(f"[daemon] speak failed: {e}", flush=True)
                logger.exception("speak failed for text: %r", text[:80])
            if final:
                # goodbye spoken (playback is synchronous) — free the model's RAM.
                print("[daemon] farewell spoken, exiting", flush=True)
                os._exit(0)

    def _reap_when_idle(self):
        """Safety net that GUARANTEES the model is offloaded. Once sessions have
        existed and then all ended (voiced.sh keeps one token file per session),
        free the RAM — even if the whole goodbye/teardown chain failed. The grace
        window exceeds a normal goodbye, and a /shutdown in progress
        (self._shutting_down) defers to that path so a spoken send-off is never
        cut off. Never fires if no session ever registered (e.g. a hand-run
        daemon), so manual use isn't reaped out from under you."""
        seen = False
        empty_since = None
        while True:
            time.sleep(10)
            if self._shutting_down:
                continue                          # /shutdown owns the (spoken) exit
            try:
                active = SESSIONS_DIR.is_dir() and any(SESSIONS_DIR.iterdir())
            except OSError:
                active = False
            if active:
                seen, empty_since = True, None
            elif seen:                            # had sessions, now none
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since >= IDLE_EXIT_GRACE_S:
                    print("[daemon] no active sessions — offloading model", flush=True)
                    os._exit(0)


speaker = None  # created in main() so the worker thread owns the model


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # so Python http.client doesn't RemoteDisconnect

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {
                "ok": speaker.error is None,
                "ready": speaker.ready,
                "error": speaker.error,
                "voices": engine.list_voices(),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/shutdown":
            return self._shutdown()
        if self.path != "/speak":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        text = (data.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "empty text"})
        voice = data.get("voice", VOICE)
        if not speaker.submit(text, voice):   # fire-and-forget
            return self._json(503, {"error": f"voice daemon not ready: {speaker.error}"})
        self._json(202, {"queued": True})

    def _shutdown(self):
        """Speak an optional final line, then exit the process (frees the model).
        Returns immediately; the worker speaks the goodbye and exits on its own."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            data = {}
        text = (data.get("text") or "").strip()
        voice = data.get("voice", VOICE)
        # Ignore a stale goodbye meant for a previous daemon instance: an old
        # session's detached goodbye.py can land here after a NEW daemon has taken
        # the port. When a pid is given, only honor it if it's ours.
        target = data.get("pid")
        if isinstance(target, int) and target != os.getpid():
            return self._json(409, {"error": "shutdown for another daemon instance"})
        speaker._shutting_down = True
        self._json(202, {"bye": True})
        if speaker.ready and text:
            speaker.submit_final(text, voice)
        else:
            # nothing to say (or model never loaded) — free the RAM anyway, but
            # let the 202 flush first.
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)),
                             daemon=True).start()

    def log_message(self, *a):
        pass                                      # quiet


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-warm", action="store_true", help="don't preload a default voice")
    args = p.parse_args()

    global speaker
    speaker = Speaker(warm_voice=None if args.no_warm else VOICE)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[daemon] listening on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
