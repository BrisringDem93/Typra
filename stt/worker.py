# stt/worker.py
"""
TranscriptionWorker: QThread consuming audio, applying voice commands
and optional LLM post-processing, emitting final text.
Dictated text is never written to the log, only its length.
"""
from __future__ import annotations
import collections, queue, re, time, traceback
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from audio.recorder import AudioRecorder
from config.settings import HALLUCINATION_PHRASES, Settings
from stt.transcriber import Transcriber
from stt.voice_commands import process_commands, CommandResult

# Audio kept from just before speech starts, so the first syllable survives
_PRE_ROLL_SECONDS = 0.3
# Ask to stop listening after this long without speech (mic not left on by mistake)
_AUTO_STOP_SECONDS = 300
# After stop: how long to wait for audio the recorder captured but we have not read
_DRAIN_SECONDS = 0.15


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def _is_prompt_echo(text: str, hotwords: str) -> bool:
    """On near-silent audio Whisper can repeat its initial prompt, i.e. the
    custom dictionary (names, clients...). Output made only of two or more
    dictionary entries is treated as such an echo; a single dictated term is not."""
    entries = sorted({_norm(w) for w in hotwords.splitlines() if _norm(w)}, key=len, reverse=True)
    rest, hits = _norm(text), 0
    for entry in entries:
        rest, n = re.subn(rf"\b{re.escape(entry)}\b", " ", rest)
        hits += n
    return hits >= 2 and not rest.strip()


class TranscriptionWorker(QThread):
    """Each recording session has an id. start_transcribing() puts the new id
    into the audio queue as a marker, so audio captured before it is still
    processed for (and attributed to) the earlier session."""
    text_ready      = pyqtSignal(str, int)   # text, session id
    command_delete  = pyqtSignal(int)        # session id
    status_changed  = pyqtSignal(str)
    session_done    = pyqtSignal(int)        # all audio of that session processed
    error_occurred  = pyqtSignal(str)
    silence_timeout = pyqtSignal()

    def __init__(self, recorder: AudioRecorder, transcriber: Transcriber,
                 settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._recorder    = recorder
        self._transcriber = transcriber
        self._settings    = settings
        self._active      = False
        self._session     = 0
        self._llm         = None
        self._llm_id      = None
        self._error_shown = False

    def start_transcribing(self) -> int:
        """Begin a new session (call before starting the recorder); returns its id."""
        self._session += 1
        print(f"[Worker] start_transcribing() session={self._session}")
        self._recorder.audio_queue.put(self._session)   # session boundary marker
        self._active = True
        return self._session

    def stop_transcribing(self) -> None:
        print("[Worker] stop_transcribing()")
        self._active = False

    def shutdown_llm(self) -> None:
        """Stop the LLM worker process (also unblocks a request in progress)."""
        if self._llm is not None:
            self._llm.shutdown()

    def run(self) -> None:
        print("[Worker] Thread started")
        sr = self._settings.sample_rate
        next_session = None

        while not self.isInterruptionRequested():
            if not self._active and next_session is None:
                try: self._recorder.audio_queue.get(timeout=0.05)
                except queue.Empty: pass
                continue

            session = next_session if next_session is not None else self._session
            next_session = None
            self.status_changed.emit("listening")
            buf:        list[np.ndarray] = []
            buffered    = 0
            silence_n   = 0
            last_voice  = time.monotonic()
            max_samples = int(self._settings.chunk_seconds * sr)
            sil_thresh  = int(self._settings.silence_seconds * sr)
            rms_thresh  = self._settings.silence_threshold
            # Silence before speech is not buffered (it used to pile up without
            # limit and force a flush on the first word), only a short pre-roll.
            pre_roll = collections.deque(
                maxlen=max(1, int(_PRE_ROLL_SECONDS * sr / AudioRecorder._BLOCK_SIZE)))

            print(f"[Worker] Listening — chunk={self._settings.chunk_seconds}s "
                  f"rms_thresh={rms_thresh} sil_sec={self._settings.silence_seconds}s")

            while not self.isInterruptionRequested():
                # After stop, still process what the recorder already captured
                # (it piles up while a chunk is being transcribed)
                try:
                    item = self._recorder.audio_queue.get(
                        timeout=0.05 if self._active else _DRAIN_SECONDS)
                except queue.Empty:
                    if self._active:
                        continue
                    break
                if isinstance(item, int):          # session marker
                    if item <= session:
                        continue                   # this session's own start
                    next_session = item            # a newer session started:
                    break                          # finish this one first
                chunk = item
                silent = float(np.sqrt(np.mean(chunk ** 2))) < rms_thresh
                if not silent:
                    last_voice = time.monotonic()
                elif self._active and time.monotonic() - last_voice > _AUTO_STOP_SECONDS:
                    print("[Worker] No speech for a while — requesting stop")
                    self.silence_timeout.emit()
                    last_voice = time.monotonic()
                if not buf:
                    if silent:
                        pre_roll.append(chunk)
                        continue
                    buf = list(pre_roll)
                    buffered = sum(c.shape[0] for c in buf)
                    pre_roll.clear()
                buf.append(chunk)
                buffered += chunk.shape[0]
                silence_n = silence_n + chunk.shape[0] if silent else 0
                if silence_n >= sil_thresh or buffered >= max_samples:
                    print(f"[Worker] Flushing {buffered} samples")
                    self._flush(buf, sr, session)
                    buf = []; buffered = 0; silence_n = 0

            if buf:
                self._flush(buf, sr, session)
            self.status_changed.emit("idle")
            self.session_done.emit(session)

        if self._llm is not None:
            self._llm.shutdown()
        print("[Worker] Thread exiting")

    def _flush(self, buf: list[np.ndarray], sr: int, session: int) -> None:
        # An unexpected error must not end the thread (dictation would stop)
        try:
            self._process(np.concatenate(buf), sr, session)
        except Exception:
            print(f"[Worker] Error while processing audio:\n{traceback.format_exc()}")
            self.status_changed.emit("listening")

    def _process(self, audio: np.ndarray, sr: int, session: int) -> None:
        duration = len(audio) / sr
        print(f"[Worker] _flush: {duration:.2f}s lang={self._settings.language}")
        self.status_changed.emit("processing")

        if not self._transcriber.is_loaded:
            print("[Worker] Transcriber not loaded — skipping")
            self.status_changed.emit("listening"); return

        hotwords = self._settings.custom_hotwords.strip()
        raw = self._transcriber.transcribe(
            audio,
            language=self._settings.language,
            initial_prompt=hotwords if hotwords else "",
        )
        print(f"[Worker] Transcriber returned {len(raw)} chars")

        if self._transcriber.worker_failed:
            if not self._error_shown:   # tell the user once, not on every chunk
                self._error_shown = True
                self.error_occurred.emit("The speech model stopped working. "
                                         "Quit and reopen Typra (details in typra.log).")
        else:
            self._error_shown = False

        if not raw:
            self.status_changed.emit("listening"); return

        if self._settings.filter_hallucinations:
            if raw.strip().lower() in HALLUCINATION_PHRASES or (
                    hotwords and _is_prompt_echo(raw, hotwords)):
                print("[Worker] Hallucination filtered")
                self.status_changed.emit("listening"); return

        raw = re.sub(r"  +", " ", raw.strip())

        if self._settings.voice_commands_enabled:
            result: CommandResult = process_commands(raw, self._settings.language)
            if result.delete_last:
                self.command_delete.emit(session)
                self.status_changed.emit("listening"); return
            text = result.text
        else:
            text = raw

        if not text:
            self.status_changed.emit("listening"); return

        if self._settings.llm_enabled and self._settings.llm_tone != "none":
            text = self._apply_llm(text)
        elif self._llm is not None:
            # AI Cleanup turned off: release the model's memory
            self._llm.shutdown()
            self._llm = self._llm_id = None

        if text:
            print(f"[Worker] Emitting {len(text)} chars")
            self.text_ready.emit(text, session)
        self.status_changed.emit("listening")

    def _apply_llm(self, text: str) -> str:
        model_id = self._settings.get("llm_model_id")
        if self._llm is None or self._llm_id != model_id:
            if self._llm is not None:
                self._llm.shutdown()
            from stt.llm_processor import LLMProcessor
            self._llm, self._llm_id = LLMProcessor(model_id=model_id), model_id
        if not self._llm.is_ready():
            return text
        return self._llm.process(text, tone=self._settings.llm_tone)
