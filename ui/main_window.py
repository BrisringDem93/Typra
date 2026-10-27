# ui/main_window.py
"""
AppController: Typra v2 invisible coordinator.
Wires: recorder, transcriber, worker, LLM, history, hotkey,
       overlay, settings, tray, voice commands.
"""
from __future__ import annotations
import sys, os, time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QSystemTrayIcon as _QSTray

from audio.recorder import AudioRecorder
from config.settings import Settings
from db.history import HistoryStore
from input.hotkey import DEFAULT_HOTKEY, HotkeyManager, is_safe_hotkey
from input.typer import TextTyper
from stt.transcriber import Transcriber
from stt.worker import TranscriptionWorker
from ui.clipboard import copy_private
from ui.overlay import RecordingOverlay
from ui.settings_window import SettingsWindow
from ui.history_window import HistoryWindow
from ui.tray import SystemTray

_ASSETS         = Path(__file__).parent.parent / "assets"
_ICO_DARK       = _ASSETS / "typra_dark.ico"
_ICO_LIGHT      = _ASSETS / "typra_light.ico"


def _is_dark() -> bool:
    if sys.platform != "win32": return True
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k); return v == 0
    except Exception: return True


def _foreground_window() -> int:
    if sys.platform != "win32": return 0
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() or 0
    except Exception: return 0


def _foreground_is_ours() -> bool:
    if sys.platform != "win32": return False
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(_foreground_window(), ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception: return False


def _is_taskbar(hwnd: int) -> bool:
    """Clicking the tray icon brings the taskbar to the foreground."""
    if sys.platform != "win32" or not hwnd: return False
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
        return buf.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                             "NotifyIconOverflowWindow",
                             "TopLevelWindowForOverflowXamlIsland")
    except Exception: return False


@dataclass
class _Session:
    buf:    list = field(default_factory=list)   # everything dictated (history)
    late:   list = field(default_factory=list)   # copied to the clipboard instead
    target: int  = 0                             # window the text belongs in


def make_app_icon() -> QIcon:
    ico = _ICO_DARK if _is_dark() else _ICO_LIGHT
    if ico.exists():
        icon = QIcon(str(ico))
        if not icon.isNull(): return icon
    icon = QIcon()
    for size in (16, 32, 48):
        px = QPixmap(size, size); px.fill(QColor(0,0,0,0))
        p = QPainter(px); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(46,213,115)); p.setPen(QColor(0,0,0,0))
        m = size//8; p.drawEllipse(m,m,size-2*m,size-2*m); p.end()
        icon.addPixmap(px)
    return icon


def _make_logo_pixmap(square_size: int = 40) -> QPixmap:
    ico = _ICO_DARK if _is_dark() else _ICO_LIGHT
    px = QPixmap(square_size, square_size); px.fill(QColor(0,0,0,0))
    if ico.exists():
        loaded = QPixmap(str(ico))
        if not loaded.isNull():
            scaled = loaded.scaled(square_size, square_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            x=(square_size-scaled.width())//2; y=(square_size-scaled.height())//2
            p=QPainter(px); p.drawPixmap(x,y,scaled); p.end()
    return px


class ModelLoader(QThread):
    status_update = pyqtSignal(str)
    finished_ok   = pyqtSignal()
    finished_err  = pyqtSignal(str)

    def __init__(self, transcriber: Transcriber, parent=None):
        super().__init__(parent); self._t = transcriber

    def run(self):
        import traceback
        try:
            self._t.load(status_cb=self.status_update.emit)
            self.finished_ok.emit()
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class AppController(QObject):

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings    = settings
        self._recording   = False
        self._model_ready = False
        self._downloading = False
        # Recording sessions by id. The worker may still deliver text for a
        # session after it stopped (or after a new one started).
        self._sessions: dict[int, _Session] = {}
        self._session_id  = 0      # the session being recorded
        self._last_text   = ""     # for "delete that"
        self._last_hwnd   = 0      # window that received _last_text
        self._last_sid    = 0      # session of _last_text
        self._quitting    = False
        self._last_dark   = _is_dark()

        # Hotkeys saved by older versions were not validated
        hotkey = settings.get("hotkey", DEFAULT_HOTKEY)
        self._hotkey_was_reset = not is_safe_hotkey(hotkey)
        if self._hotkey_was_reset:
            settings.set("hotkey", DEFAULT_HOTKEY)

        self._app_icon = make_app_icon()
        QApplication.instance().setWindowIcon(self._app_icon)

        self._recorder    = AudioRecorder(sample_rate=settings.sample_rate)
        self._transcriber = Transcriber(
            model_size=settings.model_size,
            gpu=settings.gpu_enabled,
        )
        self._typer   = TextTyper()
        self._hotkey  = HotkeyManager(
            hotkey_str=settings.get("hotkey", DEFAULT_HOTKEY),
        )
        self._worker  = TranscriptionWorker(
            recorder=self._recorder,
            transcriber=self._transcriber,
            settings=settings,
        )
        self._history = HistoryStore()
        self._overlay = RecordingOverlay()
        self._settings_win = SettingsWindow(settings, self._app_icon)
        self._history_win  = HistoryWindow(self._history, self._app_icon)
        self._tray    = SystemTray(app_icon=self._app_icon)

        self._wire()

    # ── Start ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self._tray.show()
        self._tray.set_active_tone(self._settings.get('llm_tone', 'none'))
        self._worker.start()
        self._hotkey.start()
        self._typer.start_tracking(0)

        self._model_loader = ModelLoader(self._transcriber)
        self._model_loader.status_update.connect(self._on_loader_status)
        self._model_loader.finished_ok.connect(self._on_model_loaded)
        self._model_loader.finished_err.connect(self._on_model_error)
        self._model_loader.start()

        # Theme watcher
        self._theme_timer = QTimer()
        self._theme_timer.setInterval(3000)
        self._theme_timer.timeout.connect(self._check_theme)
        self._theme_timer.start()

        QTimer.singleShot(300, self._apply_taskbar_icon)
        if self._hotkey_was_reset:
            self._tray.showMessage(
                "Typra", "Your saved hotkey could be triggered while typing, so it was "
                "reset to Ctrl+Shift+Space. You can change it in Settings.",
                self._app_icon, 6000)

    # ── Wire ───────────────────────────────────────────────────────────

    def _wire(self) -> None:
        # Tray
        self._tray.toggle_recording.connect(self._toggle_recording)
        self._tray.open_settings.connect(self._open_settings)
        self._tray.open_history.connect(self._open_history)
        self._tray.quit_app.connect(self._quit)
        self._tray.tone_selected.connect(self._on_tone)

        # Overlay
        self._overlay.stop_requested.connect(self._stop_recording)

        # Hotkey
        self._hotkey.activated.connect(self._toggle_recording)

        # Audio level
        self._recorder.set_level_callback(self._overlay.set_level)

        # Worker
        self._worker.text_ready.connect(self._on_text_ready)
        self._worker.command_delete.connect(self._on_command_delete)
        self._worker.status_changed.connect(self._on_worker_status)
        self._worker.session_done.connect(self._on_session_done)
        self._worker.silence_timeout.connect(self._on_silence_timeout)
        self._worker.error_occurred.connect(
            lambda msg: self._tray.showMessage("Typra", msg, _QSTray.MessageIcon.Warning, 6000))

        # Settings signals
        self._settings_win.language_changed.connect(lambda _: None)
        self._settings_win.mic_changed.connect(self._on_mic_changed)
        self._settings_win.model_changed.connect(self._on_model_changed)
        self._settings_win.hotkey_changed.connect(self._on_hotkey_changed)
        self._settings_win.download_started.connect(self._on_download_started)
        self._settings_win.download_finished.connect(self._on_download_finished)

        # History re-paste
        self._history_win.paste_requested.connect(self._retype)

    # ── Recording ──────────────────────────────────────────────────────

    @pyqtSlot()
    def _toggle_recording(self) -> None:
        if not self._model_ready: return
        if self._recording: self._stop_recording()
        else: self._start_recording()

    @pyqtSlot()
    def _start_recording(self) -> None:
        if not self._model_ready or self._recording or self._downloading: return
        device = self._settings.microphone_index
        mic_name = self._settings.microphone_name
        if mic_name:
            device = AudioRecorder.find_microphone(mic_name)
            if device is None:
                self._tray.showMessage(
                    "Typra", f"Microphone \"{mic_name}\" not found: using the default microphone.",
                    self._app_icon, 3000)
        self._recording = True
        # Before the recorder starts: marks where this session's audio begins
        self._session_id = self._worker.start_transcribing()
        self._sessions[self._session_id] = _Session(target=_foreground_window())
        try:
            self._recorder.start(device_index=device)
        except Exception as e:
            self._recording = False
            self._worker.stop_transcribing()
            self._tray.showMessage("Typra: Mic Error", str(e), _QSTray.MessageIcon.Warning, 3000); return
        self._overlay.show_overlay()
        self._tray.update_recording_state(True)

    @pyqtSlot()
    def _stop_recording(self) -> None:
        if not self._recording: return
        self._recording = False
        self._worker.stop_transcribing()
        self._recorder.stop()
        self._overlay.hide_overlay()
        self._tray.update_recording_state(False)
        # Text still being transcribed goes where the user is now, unless the
        # stop came from the tray or a Typra window: then the target stays the
        # window that received the dictation.
        session = self._sessions.get(self._session_id)
        fg = _foreground_window()
        if session is not None and not (_foreground_is_ours() or _is_taskbar(fg)):
            session.target = fg

    @pyqtSlot(int)
    def _on_session_done(self, sid: int) -> None:
        session = self._sessions.pop(sid, None)
        if session is not None:
            self._save_history(" ".join(session.buf))

    def _save_history(self, text: str) -> None:
        """Store a finished session as one history entry (if enabled)."""
        full = text.strip()
        if not full or not self._settings.save_history:
            return
        self._history.add(
            text=full,
            language=self._settings.language,
            tone=self._settings.llm_tone if self._settings.llm_enabled else "none",
        )
        if self._history_win.isVisible():
            self._history_win.refresh()

    # ── Text handling ──────────────────────────────────────────────────

    def _copy_instead(self, text: str, reason: str) -> None:
        copy_private(text)
        self._tray.showMessage("Typra", f"{reason} The text was copied to the clipboard.",
                               self._app_icon, 4000)

    def _copy_late(self, session: _Session, text: str, reason: str) -> None:
        """Put text on the clipboard instead of typing it. Later pieces of the
        same session are appended, not overwritten."""
        session.late.append(text)
        copy_private(" ".join(session.late))
        self._last_text = ""
        if len(session.late) == 1:   # one notification per session
            self._tray.showMessage("Typra", f"{reason} The text was copied to the clipboard.",
                                   self._app_icon, 4000)

    @pyqtSlot(str, int)
    def _on_text_ready(self, text: str, sid: int) -> None:
        if not text: return
        session = self._sessions.setdefault(sid, _Session())
        # Accumulate in the session — saved to history when the session ends
        session.buf.append(text)
        # Modifier keys first: the window can change while they are released
        if not self._typer.wait_for_modifiers_release():
            self._copy_late(session, text, "Typing was skipped because keys were held down.")
            return
        fg = _foreground_window()
        if _foreground_is_ours():
            self._copy_late(session, text, "Typra does not type into its own windows.")
            return
        live = self._recording and sid == self._session_id
        if not live and fg != session.target:
            # Arrived after this dictation ended and the user is now in another
            # window: typing there (a chat, a terminal...) is not what they meant.
            self._copy_late(session, text, "The window changed after dictation stopped.")
            return
        if not self._typer.type_text(text,
                trailing_space=self._settings.add_trailing_space,
                restore_focus=True):
            self._copy_late(session, text, "Typing was skipped because keys were held down.")
            return
        session.target = fg
        self._last_text, self._last_hwnd, self._last_sid = text, fg, sid

    @pyqtSlot(int)
    def _on_command_delete(self, sid: int) -> None:
        """Delete the last typed text segment using backspace — only in the
        window that received it: anywhere else the backspaces would destroy
        unrelated content."""
        if not self._last_text or sid != self._last_sid:
            return
        # Wait for modifiers first (Ctrl+Backspace deletes words, and the
        # window can change while they are released), then check the window.
        if not self._typer.wait_for_modifiers_release():
            print("[AppController] delete skipped: modifier keys held")
            self._last_text = ""
            return
        if _foreground_window() != self._last_hwnd or _foreground_is_ours():
            print("[AppController] delete skipped: the foreground window changed")
            self._tray.showMessage("Typra", "\"Delete that\" was skipped: the window changed.",
                                   self._app_icon, 3000)
            self._last_text = ""
            return
        session = self._sessions.get(sid)
        if session and session.buf and session.buf[-1] == self._last_text:
            session.buf.pop()   # keep deleted text out of the history
        try:
            from pynput.keyboard import Controller, Key
            kb = Controller()
            n = len(self._last_text) + (1 if self._settings.add_trailing_space else 0)
            for _ in range(n):
                kb.press(Key.backspace)
                kb.release(Key.backspace)
        except Exception as exc:
            print(f"[AppController] delete error: {exc}")
        self._last_text = ""

    @pyqtSlot(str)
    def _retype(self, text: str) -> None:
        self._typer.wait_for_modifiers_release()
        if _foreground_is_ours():
            # Never type into Typra's own windows
            self._copy_instead(text, "Click where the text should go and press Ctrl+V.")
            return
        if not self._typer.type_text(text, trailing_space=True, restore_focus=True):
            self._copy_instead(text, "Typing was skipped because keys were held down.")

    # ── Slots ──────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_loader_status(self, msg: str) -> None:
        self._tray.setToolTip(f"Typra: {msg}")

    @pyqtSlot()
    def _on_model_loaded(self) -> None:
        self._model_ready = True
        self._tray.set_model_ready()
        print("[Typra] Model ready.")

    @pyqtSlot(str)
    def _on_model_error(self, err: str) -> None:
        print(f"\n{'='*60}\nMODEL ERROR:\n{err}\n{'='*60}\n")
        if self._quitting: return
        self._tray.setToolTip("Typra: Model error")
        box = QMessageBox()
        box.setWindowTitle("Typra: Model Error")
        box.setWindowIcon(self._app_icon)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setWindowFlags(box.windowFlags()|Qt.WindowType.WindowStaysOnTopHint)
        box.setText(
            "<b>The speech model failed to load.</b><br><br>"
            "On first launch the model is downloaded once: check your internet connection.<br><br>"
            "Otherwise install <b>Microsoft Visual C++ Redistributable 2022 (x64)</b>:<br>"
            "<code>https://aka.ms/vs/17/release/vc_redist.x64.exe</code><br><br>"
            "Click <b>Show Details</b> for the full error.")
        box.setDetailedText(err); box.exec()

    @pyqtSlot(str)
    def _on_worker_status(self, status: str) -> None:
        self._overlay.set_status(status)

    @pyqtSlot()
    def _on_silence_timeout(self) -> None:
        if not self._recording: return
        self._stop_recording()
        self._tray.showMessage("Typra", "Dictation stopped after 5 minutes without speech.",
                               self._app_icon, 4000)

    @pyqtSlot(object)
    def _on_mic_changed(self, _) -> None:
        if self._recording: self._stop_recording()

    @pyqtSlot(str)
    def _on_model_changed(self, model: str) -> None:
        pass  # setting already saved; user restarts via tray

    @pyqtSlot(str, str)
    def _on_hotkey_changed(self, hotkey: str, mode: str) -> None:
        self._hotkey.update(hotkey)

    @pyqtSlot(str)
    def _on_tone(self, tone: str) -> None:
        # Takes effect with the next dictated chunk
        self._settings.set("llm_tone", tone)
        self._tray.set_active_tone(tone)
        if tone != "none" and not self._settings.llm_enabled:
            self._tray.showMessage(
                "Typra: AI Cleanup is off",
                "Enable it in Settings > AI Cleanup to apply this tone.",
                self._app_icon, 4000)

    # ── Settings / History ─────────────────────────────────────────────

    @pyqtSlot()
    def _on_download_started(self) -> None:
        self._downloading = True
        if self._recording:
            self._stop_recording()
        self._tray.setToolTip("Typra: Downloading model — recording disabled")
        print("[Typra] Download started — recording blocked")

    @pyqtSlot()
    def _on_download_finished(self) -> None:
        self._downloading = False
        self._tray.setToolTip("Typra: Ready  (Ctrl+Shift+Space)")
        print("[Typra] Download finished — recording re-enabled")

    def _open_settings(self) -> None:
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _open_history(self) -> None:
        self._history_win.refresh()
        self._history_win.show()
        self._history_win.raise_()
        self._history_win.activateWindow()

    # ── Theme ──────────────────────────────────────────────────────────

    @pyqtSlot()
    def _check_theme(self) -> None:
        dark = _is_dark()
        if dark != self._last_dark:
            self._last_dark = dark
            self._app_icon  = make_app_icon()
            QApplication.instance().setWindowIcon(self._app_icon)
            self._tray.setIcon(self._app_icon)
            self._settings_win.setWindowIcon(self._app_icon)
            self._history_win.setWindowIcon(self._app_icon)
            self._settings_win.refresh_theme()
            QTimer.singleShot(100, self._apply_taskbar_icon)

    def _apply_taskbar_icon(self) -> None:
        if sys.platform != "win32": return
        try:
            import ctypes, tempfile, os
            ico = _ICO_DARK if _is_dark() else _ICO_LIGHT
            if not ico.exists(): return
            tmp = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
            tmp.write(ico.read_bytes()); tmp.close()
            IMAGE_ICON=1; LR_LOADFROMFILE=0x10; WM_SETICON=0x80
            h32=ctypes.windll.user32.LoadImageW(None,tmp.name,IMAGE_ICON,32,32,LR_LOADFROMFILE)
            h16=ctypes.windll.user32.LoadImageW(None,tmp.name,IMAGE_ICON,16,16,LR_LOADFROMFILE)
            for win in [self._settings_win, self._overlay, self._history_win]:
                try:
                    hwnd=int(win.winId())
                    if h32: ctypes.windll.user32.SendMessageW(hwnd,WM_SETICON,1,h32)
                    if h16: ctypes.windll.user32.SendMessageW(hwnd,WM_SETICON,0,h16)
                except Exception: pass
            os.unlink(tmp.name)
        except Exception as e: print(f"[Icon] {e}")

    # ── Quit ───────────────────────────────────────────────────────────

    def _quit(self) -> None:
        self._quitting = True
        if self._recording: self._stop_recording()
        for fn in [
            lambda: (self._worker.stop_transcribing(),
                     self._worker.requestInterruption(),
                     self._worker.quit(), self._worker.wait(5000)),
            # Unblock the worker if it is waiting for the LLM or Whisper process
            lambda: self._worker.shutdown_llm(),
            lambda: self._transcriber.shutdown(),
            lambda: self._worker.wait(3000),
            # shutdown() makes a model load in progress give up quickly
            lambda: self._model_loader.wait(5000),
            lambda: self._hotkey.stop(),
            lambda: self._typer.stop_tracking(),
            lambda: self._theme_timer.stop(),
        ]:
            try: fn()
            except Exception: pass
        # Deliver what the worker emitted while stopping, then save the sessions
        QApplication.processEvents()
        for sid in list(self._sessions):
            self._on_session_done(sid)
        QApplication.quit()


MainWindow = AppController
