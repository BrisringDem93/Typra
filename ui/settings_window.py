# ui/settings_window.py
"""Typra Settings — tabbed, theme-aware, custom CheckBox, HotkeyRecorder."""
from __future__ import annotations
import sys
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)
from audio.recorder import AudioRecorder
from config.settings import Settings
from stt.transcriber import MODEL_INFO
from stt.llm_processor import DEFAULT_MODEL, LLM_MODELS, LLMProcessor
from ui.widgets import CheckBox, HotkeyRecorder


class _DownloadBridge(QObject):
    """Carries download progress from the worker thread to the GUI thread
    (a QTimer.singleShot issued from a plain Python thread never fires)."""
    progress = pyqtSignal(int, float, float)   # percent, MB received, MB total
    finished = pyqtSignal(bool)


def _is_dark() -> bool:
    if sys.platform != "win32": return True
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k); return v == 0
    except Exception: return True


def _qss(dark: bool) -> str:
    bg   = "#16161c" if dark else "#f5f5f7"
    bg2  = "#22222e" if dark else "#ffffff"
    bdr  = "#3a3a50" if dark else "#d0d0d8"
    txt  = "#e0e0e8" if dark else "#1a1a2e"
    sel  = "#3a3a70" if dark else "#c8d0f0"
    dim  = "#a0a0b8" if dark else "#606070"
    btn  = "#2a2a38" if dark else "#e8e8f0"
    div  = "#2a2a38" if dark else "#e8e8ee"
    ttl  = "#e8e8ff" if dark else "#1a1a2e"
    sub  = "#555568" if dark else "#808090"
    sec  = "#c8c8e0" if dark else "#2a2a3e"
    tab  = "#1e1e28" if dark else "#eaeaf0"
    return f"""
QWidget {{background:{bg};color:{txt};font-family:"Segoe UI",Arial;font-size:13px;}}
QTabWidget::pane {{background:{bg2};border:1px solid {bdr};border-radius:6px;}}
QTabBar::tab {{background:{tab};color:{dim};border:none;padding:7px 16px;
               margin-right:2px;border-radius:5px 5px 0 0;}}
QTabBar::tab:selected {{background:{bg2};color:{txt};}}
QComboBox {{background:{bg2};border:1px solid {bdr};border-radius:6px;
            padding:5px 10px;min-height:28px;color:{txt};}}
QComboBox::drop-down {{border:none;width:22px;}}
QComboBox QAbstractItemView {{background:{bg2};border:1px solid {bdr};
    selection-background-color:{sel};color:{txt};}}
QLineEdit,QTextEdit {{background:{bg2};border:1px solid {bdr};border-radius:6px;
    padding:5px 10px;color:{txt};}}
QFrame#Divider {{background:{div};max-height:1px;min-height:1px;}}
QLabel#AppTitle {{color:{ttl};font-size:17px;font-weight:bold;}}
QLabel#AppSub   {{color:{sub};font-size:11px;}}
QLabel#Sec      {{color:{sec};font-size:12px;font-weight:bold;}}
QLabel#Hint     {{color:{dim};font-size:10px;}}
QLabel#StatusOk {{background:#1a3a1a;color:#60e080;border-radius:5px;padding:5px 8px;font-size:11px;}}
QPushButton#Btn {{background:{btn};border:1px solid {bdr};border-radius:5px;
    color:{dim};font-size:11px;min-height:28px;padding:0 14px;}}
QPushButton#Btn:hover {{background:{sel};}}
QPushButton#Green {{background:#2d5a27;border:none;border-radius:5px;
    color:#e0ffe0;font-size:11px;min-height:28px;padding:0 14px;}}
QPushButton#Green:hover {{background:#3a7a30;}}
QPushButton#Green:disabled {{background:#1e2e1e;color:#406040;}}
QScrollBar:vertical {{background:{bg};width:6px;border-radius:3px;}}
QScrollBar::handle:vertical {{background:{bdr};border-radius:3px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
"""


LANGUAGES = [
    ("en","English"),("de","German"),("fr","French"),("es","Spanish"),
    ("it","Italian"),("pt","Portuguese"),("ja","Japanese"),("zh","Chinese"),
    ("ko","Korean"),("nl","Dutch"),("pl","Polish"),("ru","Russian"),
    ("ar","Arabic"),("tr","Turkish"),("sv","Swedish"),("da","Danish"),
    ("fi","Finnish"),("no","Norwegian"),("cs","Czech"),("hu","Hungarian"),
]


class SettingsWindow(QWidget):
    language_changed  = pyqtSignal(str)
    mic_changed       = pyqtSignal(object)
    model_changed     = pyqtSignal(str)
    hotkey_changed    = pyqtSignal(str, str)
    download_started  = pyqtSignal()
    download_finished = pyqtSignal()

    def __init__(self, settings: Settings, app_icon: QIcon, parent=None) -> None:
        super().__init__(parent,
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint)
        self._settings = settings
        self.setWindowTitle("Typra: Settings")
        self.setWindowIcon(app_icon)
        self.setFixedWidth(440)
        self._apply_theme()
        self._build_ui()
        # Snapshot value at open-time so the banner hides if the user reverts
        self._orig_model = self._settings.model_size

    def _apply_theme(self) -> None:
        self.setStyleSheet(_qss(_is_dark()))

    def refresh_theme(self) -> None:
        self._apply_theme()

    # ── Build ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        col = QVBoxLayout(); col.setSpacing(1)
        t = QLabel("Typra"); t.setObjectName("AppTitle")
        s = QLabel("Settings"); s.setObjectName("AppSub")
        col.addWidget(t); col.addWidget(s)
        hdr.addLayout(col); hdr.addStretch()
        root.addLayout(hdr)
        root.addWidget(self._div())

        tabs = QTabWidget()
        tabs.addTab(self._tab_general(),       "General")
        tabs.addTab(self._tab_transcription(), "Transcription")
        tabs.addTab(self._tab_ai(),            "AI Cleanup")
        tabs.addTab(self._tab_advanced(),      "Advanced")
        root.addWidget(tabs)

        root.addWidget(self._div())
        btn = QPushButton("Close"); btn.setObjectName("Btn")
        btn.clicked.connect(self.hide)
        root.addWidget(btn)
        self.adjustSize()

    # ── General tab ────────────────────────────────────────────────────

    def _tab_general(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(12,12,12,12); v.setSpacing(8)

        v.addWidget(self._sec("Microphone"))
        self._mic_combo = QComboBox()
        self._populate_mics()
        self._mic_combo.currentIndexChanged.connect(self._on_mic)
        v.addWidget(self._mic_combo)

        v.addWidget(self._div())
        v.addWidget(self._sec("Language"))
        self._lang_combo = QComboBox()
        active = self._settings.language
        sel_i = 0
        for i,(code,name) in enumerate(LANGUAGES):
            self._lang_combo.addItem(name, code)
            if code == active: sel_i = i
        self._lang_combo.setCurrentIndex(sel_i)
        self._lang_combo.currentIndexChanged.connect(self._on_lang)
        v.addWidget(self._lang_combo)

        v.addWidget(self._div())
        v.addWidget(self._sec("Global Hotkey"))
        v.addWidget(self._hint("Click the field below and press your desired key combination."))
        self._hotkey_rec = HotkeyRecorder(
            initial=self._settings.get("hotkey", "ctrl+shift+space"))
        self._hotkey_rec.hotkey_recorded.connect(self._on_hotkey_recorded)
        v.addWidget(self._hotkey_rec)

        v.addStretch()
        return w

    # ── Transcription tab ──────────────────────────────────────────────

    def _tab_transcription(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(12,12,12,12); v.setSpacing(8)

        v.addWidget(self._sec("Whisper Model"))
        self._model_combo = QComboBox()
        for mid, info in MODEL_INFO.items():
            self._model_combo.addItem(
                f"{mid}  ({info['size']})  {info['label']}", mid)
        cur = self._settings.model_size
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == cur:
                self._model_combo.setCurrentIndex(i); break
        self._model_combo.currentIndexChanged.connect(self._on_model)
        v.addWidget(self._model_combo)
        v.addWidget(self._hint("Model downloads automatically on first use."))
        self._model_restart_lbl = self._restart_notice()
        v.addWidget(self._model_restart_lbl)

        v.addWidget(self._div())
        self._vc_check = CheckBox("Enable voice commands (e.g. 'new line', 'period')")
        self._vc_check.setChecked(self._settings.voice_commands_enabled)
        self._vc_check.stateChanged.connect(
            lambda s: self._settings.set("voice_commands_enabled", bool(s)))
        v.addWidget(self._vc_check)

        v.addWidget(self._div())
        v.addWidget(self._sec("Custom Dictionary / Hotwords"))
        v.addWidget(self._hint("One word or phrase per line. Biases Whisper toward these terms."))
        self._hotwords_edit = QTextEdit()
        self._hotwords_edit.setPlaceholderText("MyBrand\nAcronym XYZ\nPerson Name")
        self._hotwords_edit.setPlainText(self._settings.custom_hotwords)
        self._hotwords_edit.setMaximumHeight(90)
        self._hotwords_edit.textChanged.connect(
            lambda: self._settings.set("custom_hotwords",
                                       self._hotwords_edit.toPlainText()))
        v.addWidget(self._hotwords_edit)
        v.addStretch()
        return w

    # ── AI Cleanup tab ─────────────────────────────────────────────────

    def _tab_ai(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(12,12,12,12); v.setSpacing(8)

        v.addWidget(self._sec("Local LLM Post-Processing"))
        v.addWidget(self._hint(
            "Uses a small local AI model to clean up transcriptions.\n"
            "Fully offline. Requires 1-3.5 GB of free disk space."))
        if not LLMProcessor.llama_cpp_available():
            missing = self._hint(
                "Needs the optional llama-cpp-python package (0.2.72 or newer), "
                "which is not installed. See README > AI Cleanup.")
            missing.setStyleSheet("color: #e8a030; font-size: 11px;")
            v.addWidget(missing)

        self._llm_check = CheckBox("Enable LLM cleanup")
        self._llm_check.setChecked(self._settings.llm_enabled)
        self._llm_check.stateChanged.connect(
            lambda s: self._settings.set("llm_enabled", bool(s)))
        v.addWidget(self._llm_check)

        v.addWidget(self._div())
        v.addWidget(self._sec("LLM Model Selection"))

        self._llm_model_combo = QComboBox()
        cur_llm = self._settings.get("llm_model_id", DEFAULT_MODEL)
        for i, (mid, info) in enumerate(LLM_MODELS.items()):
            installed = " ✓" if LLMProcessor.model_downloaded_for(mid) else ""
            label = f"{info['name']}{installed}"
            tooltip = info['name'] + "\n" + info['desc']
            self._llm_model_combo.addItem(label, mid)
            self._llm_model_combo.setItemData(i, tooltip, Qt.ItemDataRole.ToolTipRole)
            if mid == cur_llm:
                self._llm_model_combo.setCurrentIndex(i)
        self._llm_model_combo.setToolTip("Hover over an item in the list to see details")
        self._llm_model_combo.currentIndexChanged.connect(self._on_llm_model_change)
        v.addWidget(self._llm_model_combo)

        # Status — only shown when installed
        self._llm_status = QLabel()
        self._llm_status.setObjectName("StatusOk")
        self._llm_status.setWordWrap(True)
        self._update_llm_status()
        v.addWidget(self._llm_status)

        self._dl_btn = QPushButton("Download selected model")
        self._dl_btn.setObjectName("Green")
        self._dl_btn.clicked.connect(self._download_llm)
        self._update_dl_btn()
        v.addWidget(self._dl_btn)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setValue(0)
        self._dl_progress.setTextVisible(True)
        self._dl_progress.setStyleSheet(
            "QProgressBar{border:1px solid #3a3a50;border-radius:4px;height:16px;text-align:center;font-size:10px;}"
            "QProgressBar::chunk{background:#2d5a27;border-radius:3px;}")
        self._dl_progress.hide()
        v.addWidget(self._dl_progress)

        v.addWidget(self._div())
        v.addWidget(self._sec("Tone Mode"))
        v.addWidget(self._hint("How the LLM rewrites your transcription."))
        self._tone_combo = QComboBox()
        tones = [
            ("cleanup",      "Cleanup: fix grammar and remove fillers"),
            ("casual",       "Casual: friendly and conversational"),
            ("professional", "Professional: formal and polished"),
            ("concise",      "Concise: shortest version without losing meaning"),
        ]
        cur_tone = self._settings.llm_tone
        for i,(code,label) in enumerate(tones):
            self._tone_combo.addItem(label, code)
            if code == cur_tone: self._tone_combo.setCurrentIndex(i)
        self._tone_combo.currentIndexChanged.connect(self._on_tone_changed)
        v.addWidget(self._tone_combo)
        v.addStretch()
        return w

    # ── Advanced tab ───────────────────────────────────────────────────

    def _tab_advanced(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(12,12,12,12); v.setSpacing(8)

        v.addWidget(self._sec("GPU Acceleration"))
        from stt.transcriber import Transcriber
        gpu_ok = Transcriber.cuda_available()
        self._gpu_check = CheckBox(
            "Use GPU (CUDA) for faster transcription" if gpu_ok
            else "Use GPU (CUDA): not available on this machine")
        self._gpu_check.setChecked(self._settings.gpu_enabled and gpu_ok)
        self._gpu_check.setEnabled(gpu_ok)
        self._gpu_check.stateChanged.connect(
            lambda s: self._settings.set("gpu_enabled", bool(s)))
        v.addWidget(self._gpu_check)
        stat = QLabel("GPU detected" if gpu_ok else "No CUDA GPU detected")
        stat.setObjectName("Hint")
        v.addWidget(stat)

        v.addWidget(self._div())
        v.addWidget(self._sec("Privacy"))
        self._history_check = CheckBox("Save dictations to History")
        self._history_check.setChecked(self._settings.save_history)
        self._history_check.stateChanged.connect(
            lambda s: self._settings.set("save_history", bool(s)))
        v.addWidget(self._history_check)
        v.addWidget(self._hint(
            "Stored only on this computer, in .whisper_dictation\\history.db "
            "inside your user folder."))

        v.addStretch()
        return w

    # ── LLM status helpers ─────────────────────────────────────────────

    def _update_llm_status(self) -> None:
        mid = self._llm_model_combo.currentData() if hasattr(self, "_llm_model_combo") \
              else self._settings.get("llm_model_id", DEFAULT_MODEL)
        if LLMProcessor.model_downloaded_for(mid):
            info = LLM_MODELS[mid]
            self._llm_status.setText(f"Installed: {info['name']}")
            self._llm_status.show()
        else:
            self._llm_status.hide()

    def _update_dl_btn(self) -> None:
        if not hasattr(self, "_llm_model_combo"): return
        mid = self._llm_model_combo.currentData()
        if LLMProcessor.model_downloaded_for(mid):
            self._dl_btn.setText("Model already installed")
            self._dl_btn.setEnabled(False)
        else:
            self._dl_btn.setText("Download selected model")
            self._dl_btn.setEnabled(True)

    def _on_llm_model_change(self, _) -> None:
        mid = self._llm_model_combo.currentData()
        self._settings.set("llm_model_id", mid)
        self._update_llm_status()
        self._update_dl_btn()
        # Hide progress bar and re-enable recording if we switched to installed model
        if LLMProcessor.model_downloaded_for(mid):
            self._dl_progress.hide()
            self._dl_progress.setValue(0)
            self.download_finished.emit()

    def _download_llm(self) -> None:
        mid = self._llm_model_combo.currentData()
        info = LLM_MODELS[mid]
        self._dl_btn.setEnabled(False)
        self._dl_btn.setText("Downloading...")
        self._llm_status.setText(f"Downloading {info['name']} — recording disabled until complete.")
        self._llm_status.show()
        self._dl_progress.setValue(0)
        self._dl_progress.show()
        # Notify main controller to block recording during download
        self.download_started.emit()
        import threading

        bridge = _DownloadBridge(self)
        bridge.progress.connect(self._on_dl_progress)
        bridge.finished.connect(self._on_dl_done)
        bridge.finished.connect(bridge.deleteLater)
        last_pct = [-1]

        def _progress(received: int, total: int) -> None:
            pct = int(received * 100 / total) if total > 0 else 0
            if pct != last_pct[0]:
                last_pct[0] = pct
                bridge.progress.emit(pct, received / 1_048_576, total / 1_048_576)

        def _do():
            ok = False
            try:
                ok = LLMProcessor(model_id=mid).download_model(progress_cb=_progress, model_id=mid)
            except Exception as exc:
                print(f"[Settings] download error: {exc}")
            finally:
                # Always signal the end: recording stays blocked until then
                bridge.finished.emit(ok)
        threading.Thread(target=_do, daemon=True).start()

    def _on_dl_progress(self, pct: int, mb_recv: float, mb_total: float) -> None:
        self._dl_progress.setValue(pct)
        self._dl_progress.setFormat(f"{mb_recv:.0f} / {mb_total:.0f} MB  ({pct}%)")

    def _on_dl_done(self, ok: bool) -> None:
        self._dl_progress.hide()
        self._update_llm_status()
        self._update_dl_btn()
        self.download_finished.emit()
        if ok:
            self._llm_status.setText("Download complete and verified (SHA-256).")
            self._llm_status.show()
        else:
            self._llm_status.setText(
                "Download failed or the file did not pass verification. "
                "Check your internet connection and try again.")
            self._llm_status.show()

    # ── Helpers ────────────────────────────────────────────────────────

    def _populate_mics(self) -> None:
        self._mic_combo.blockSignals(True)
        self._mic_combo.clear()
        mics = AudioRecorder.list_microphones()
        saved      = self._settings.microphone_index
        saved_name = self._settings.microphone_name
        if not mics:
            self._mic_combo.addItem("No microphones found", None)
        else:
            for mic in mics:
                lbl = mic.name[:48]+"..." if len(mic.name)>48 else mic.name
                self._mic_combo.addItem(lbl, mic.index)
                i = self._mic_combo.count() - 1
                self._mic_combo.setItemData(i, mic.name, Qt.ItemDataRole.ToolTipRole)
                selected = (mic.name == saved_name) if saved_name else (mic.index == saved)
                if selected:
                    self._mic_combo.setCurrentIndex(i)
        self._mic_combo.blockSignals(False)

    @staticmethod
    def _div() -> QFrame:
        f = QFrame(); f.setObjectName("Divider")
        f.setFrameShape(QFrame.Shape.HLine); return f

    @staticmethod
    def _sec(text: str) -> QLabel:
        l = QLabel(text); l.setObjectName("Sec"); return l

    @staticmethod
    def _hint(text: str) -> QLabel:
        l = QLabel(text); l.setObjectName("Hint")
        l.setWordWrap(True); return l

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_lang(self, _):
        code = self._lang_combo.currentData()
        self._settings.set("language", code)
        self.language_changed.emit(code)

    def _on_mic(self, i):
        self._settings.set("microphone_index", self._mic_combo.currentData())
        self._settings.set("microphone_name",
                           self._mic_combo.itemData(i, Qt.ItemDataRole.ToolTipRole) or "")
        self.mic_changed.emit(self._mic_combo.currentData())

    def _on_model(self, _):
        m = self._model_combo.currentData()
        self._settings.set("model_size", m)
        self.model_changed.emit(m)
        self._model_restart_lbl.setVisible(m != self._orig_model)

    def _on_hotkey_recorded(self, combo: str) -> None:
        self._settings.set("hotkey", combo)
        self.hotkey_changed.emit(combo, "toggle")

    def _on_tone_changed(self, _) -> None:
        # Takes effect with the next dictated chunk
        self._settings.set("llm_tone", self._tone_combo.currentData())

    @staticmethod
    def _restart_notice():
        from PyQt6.QtWidgets import QLabel
        lbl = QLabel("Quit and reopen Typra for this change to take effect.")
        lbl.setObjectName("Hint")
        lbl.setStyleSheet("color: #e8a030; font-size: 11px;")
        lbl.setWordWrap(True)
        lbl.setVisible(False)
        return lbl

    def closeEvent(self, e): e.ignore(); self.hide()
