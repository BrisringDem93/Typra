# stt/transcriber.py
"""
Transcriber: runs faster-whisper in a subprocess (stt/subprocess_worker.py).
- Models come from pinned Hugging Face commits and are stored under
  ~/.whisper_dictation/whisper_models, next to the rest of the user data.
- Loading doubles as the compatibility probe: every attempt (GPU float16
  first if enabled, then CPU compute types) runs in a fresh worker and ends
  with a test transcription, so a native crash only costs that attempt.
- One reader thread per worker feeds a queue. A request that times out kills
  the worker, so a late answer can never be paired with the next request.
- subprocess stderr is drained continuously so it never blocks.
"""
from __future__ import annotations
import base64, json, os, queue, subprocess, sys, threading, time
from pathlib import Path
from typing import Callable, Optional
import numpy as np


def _get_worker_script() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    # -I: ignore PYTHONPATH/PYTHONHOME and the user site (the worker only
    # imports installed packages)
    return [sys.executable, "-I", str(Path(__file__).parent / "subprocess_worker.py")]


MODELS_DIR = Path.home() / ".whisper_dictation" / "whisper_models"


def worker_env() -> dict:
    """Environment for model workers: Hugging Face caches and logs go into
    Typra's data folder (removed with it), not ~/.cache/huggingface."""
    env = dict(os.environ)
    env["HF_HOME"] = str(Path.home() / ".whisper_dictation" / "hf_home")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env

# faster-whisper (CTranslate2) model repositories, pinned to reviewed commits.
MODEL_INFO = {
    "tiny":           {"size": "~75 MB",  "label": "Fastest · Lower accuracy",
                       "repo": "Systran/faster-whisper-tiny",
                       "revision": "d90ca5fe260221311c53c58e660288d3deb8d356"},
    "base":           {"size": "~145 MB", "label": "Fast · Good accuracy",
                       "repo": "Systran/faster-whisper-base",
                       "revision": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"},
    "small":          {"size": "~465 MB", "label": "Balanced · Better accuracy",
                       "repo": "Systran/faster-whisper-small",
                       "revision": "536b0662742c02347bc0e980a01041f333bce120"},
    "medium":         {"size": "~1.5 GB", "label": "Slow · High accuracy",
                       "repo": "Systran/faster-whisper-medium",
                       "revision": "08e178d48790749d25932bbc082711ddcfdfbc4f"},
    "large-v3-turbo": {"size": "~1.6 GB", "label": "Best quality · Requires patience",
                       "repo": "dropbox-dash/faster-whisper-large-v3-turbo",
                       "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"},
}

_LOAD_TIMEOUT       = 1800   # first use downloads the model (up to ~1.6 GB)
_RESTART_TIMEOUT    = 300    # model already on disk
_TRANSCRIBE_TIMEOUT = 60
_RETRY_BACKOFF      = 60     # after a failed restart, don't retry on every chunk
_CREATE_NO_WINDOW   = 0x08000000


class Transcriber:
    def __init__(self, model_size: str = "base", gpu: bool = False) -> None:
        self.model_size    = model_size if model_size in MODEL_INFO else "base"
        self.gpu           = gpu
        self._proc: Optional[subprocess.Popen] = None
        self._lines: queue.Queue = queue.Queue()
        self._loaded       = False
        self._compute_type = "auto"
        self._closing      = False   # set by shutdown(): never start new workers
        self._retry_after  = 0.0
        self.worker_failed = False   # the worker could not be (re)started

    @staticmethod
    def faster_whisper_available() -> bool:
        try:
            import ctranslate2, faster_whisper  # noqa
            return True
        except Exception:
            return False

    @staticmethod
    def cuda_available() -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    def load(self, status_cb: Optional[Callable[[str], None]] = None) -> None:
        attempts = [("cpu", ct) for ct in ("auto", "float32", "int8")]
        if self.gpu:
            if self.cuda_available():
                attempts.insert(0, ("cuda", "float16"))
            else:
                print("[Transcriber] CUDA not available — using CPU")
        if status_cb:
            status_cb(f"Loading '{self.model_size}' model "
                      f"({MODEL_INFO[self.model_size]['size']} download on first use)…")
        for device, compute_type in attempts:
            if self._closing:
                raise RuntimeError("Typra is closing")
            print(f"[Transcriber] Starting worker (model={self.model_size}, "
                  f"device={device}, compute={compute_type})")
            if self._start(device, compute_type, _LOAD_TIMEOUT):
                self.gpu, self._compute_type = device == "cuda", compute_type
                self._loaded = True
                print("[Transcriber] Worker ready.")
                if status_cb:
                    status_cb("Model ready — start speaking!")
                return
        raise RuntimeError(f"Could not load the '{self.model_size}' Whisper model "
                           "with any device/compute type (see typra.log).")

    def shutdown(self) -> None:
        self._closing = True
        if self._proc:
            try: self._send("QUIT")
            except Exception: pass
            try: self._proc.wait(timeout=3)
            except Exception: pass
            self._kill_worker()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def transcribe(self, audio: np.ndarray, language: str = "en",
                   initial_prompt: str = "") -> str:
        if not self._loaded or audio is None or len(audio) == 0:
            return ""
        return self._subprocess_transcribe(audio, language, initial_prompt)

    # ── Subprocess communication ──────────────────────────────────────

    def _start(self, device: str, compute_type: str, timeout: float) -> bool:
        """Start a fresh worker, load the model and run a test transcription."""
        self._kill_worker()
        if self._closing:
            return False
        info = MODEL_INFO[self.model_size]
        params = {"model": info["repo"], "revision": info["revision"],
                  "download_root": str(MODELS_DIR),
                  "device": device, "compute_type": compute_type}
        try:
            self._start_worker()
            if self._closing:   # shutdown() ran while the process was starting
                self._kill_worker()
                return False
            self._send("LOAD " + base64.b64encode(json.dumps(params).encode()).decode("ascii"))
        except OSError as e:
            print(f"[Transcriber] Could not start worker: {e}")
            self._kill_worker()
            return False
        response = self._readline(timeout)
        if response != "READY":
            print(f"[Transcriber] Load failed ({device}/{compute_type}): {response}")
            self._kill_worker()
            return False
        # Some compute types only crash at inference time
        if self._do_transcribe(np.zeros(16000, dtype=np.float32), "en", "") is None:
            print(f"[Transcriber] Test transcription failed ({device}/{compute_type})")
            self._kill_worker()
            return False
        return True

    def _start_worker(self) -> None:
        self._lines = queue.Queue()
        self._proc = subprocess.Popen(
            _get_worker_script(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            # the worker switches its pipes to UTF-8; "replace" keeps a stray
            # byte from killing the reader threads
            bufsize=1, encoding="utf-8", errors="replace",
            env=worker_env(),
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc, lines = self._proc, self._lines

        def _read_stdout():
            try:
                for line in proc.stdout:
                    lines.put(line.strip())
            except Exception:
                pass
            lines.put(None)   # EOF: the worker exited

        # Drain stderr continuously — prevents pipe buffer filling and blocking
        def _drain_stderr():
            try:
                for line in proc.stderr:
                    print(f"[Worker-stderr] {line.rstrip()}")
            except Exception:
                pass

        threading.Thread(target=_read_stdout, daemon=True, name="WorkerStdout").start()
        threading.Thread(target=_drain_stderr, daemon=True, name="WorkerStderr").start()

    def _kill_worker(self) -> None:
        if self._proc:
            try: self._proc.kill()
            except Exception: pass
            self._proc = None

    def _send(self, line: str) -> None:
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

    def _readline(self, timeout: float = 30.0) -> str:
        if not self._proc: return "ERROR no process"
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            # Never leave a request in flight: its late answer would be read
            # as the answer to the next one.
            print(f"[Transcriber] No answer within {timeout:.0f}s — stopping worker")
            self._kill_worker()
            return "ERROR timeout"
        return "ERROR worker exited" if line is None else line

    def _ensure_worker(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if time.monotonic() < self._retry_after:
            return False
        print("[Transcriber] Worker not running — restarting…")
        device = "cuda" if self.gpu else "cpu"
        ok = self._start(device, self._compute_type, _RESTART_TIMEOUT)
        if not ok and (device, self._compute_type) != ("cpu", "float32") and \
                self._start("cpu", "float32", _RESTART_TIMEOUT):
            self.gpu, self._compute_type = False, "float32"
            ok = True
        self.worker_failed = not ok
        if not ok:
            self._retry_after = time.monotonic() + _RETRY_BACKOFF
        return ok

    def _subprocess_transcribe(self, audio: np.ndarray,
                                language: str, initial_prompt: str) -> str:
        if not self._ensure_worker():
            return ""
        result = self._do_transcribe(audio, language, initial_prompt)
        # If GPU transcription errored, fall back to CPU and retry once
        if result is None and self.gpu:
            print("[Transcriber] GPU transcription failed — falling back to CPU")
            if self._start("cpu", "float32", _RESTART_TIMEOUT):
                self.gpu, self._compute_type = False, "float32"
                result = self._do_transcribe(audio, language, initial_prompt)
        return result or ""

    def _do_transcribe(self, audio: np.ndarray,
                       language: str, initial_prompt: str) -> Optional[str]:
        """Send TRANSCRIBE, return text or None on error."""
        if not language.isalpha():
            language = "en"   # the protocol is space-separated
        try:
            b64        = base64.b64encode(audio.astype(np.float32).tobytes()).decode("ascii")
            prompt_b64 = base64.b64encode(initial_prompt.encode()).decode("ascii")
            self._send(f"TRANSCRIBE {language} {prompt_b64} {b64}")
            resp = self._readline(_TRANSCRIBE_TIMEOUT)
            if resp.startswith("TEXT "):
                return base64.b64decode(resp[5:]).decode("utf-8")
            if resp == "EMPTY":
                return ""
            if resp.startswith("ERROR"):
                print(f"[Transcriber] Subprocess error: {resp[6:]}")
                return None   # signals "error occurred, consider fallback"
            print(f"[Transcriber] Unexpected response: {resp[:100]}")
            return ""
        except Exception as e:
            print(f"[Transcriber] _do_transcribe exception: {e}")
            self._kill_worker()
            return None
