# ui/tray.py
from __future__ import annotations
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


_TONE_LABELS = [
    ("none",         "None (raw)"),
    ("cleanup",      "Cleanup"),
    ("casual",       "Casual"),
    ("professional", "Professional"),
    ("concise",      "Concise"),
]


class SystemTray(QSystemTrayIcon):
    toggle_recording = pyqtSignal()
    open_settings    = pyqtSignal()
    open_history     = pyqtSignal()
    quit_app         = pyqtSignal()
    tone_selected    = pyqtSignal(str)

    def __init__(self, app_icon: QIcon, parent=None) -> None:
        super().__init__(parent)
        self._app_icon    = app_icon
        self._recording   = False
        self._loading     = True
        self._tone_actions: dict[str, object] = {}
        self.setIcon(app_icon)
        self.setToolTip("Typra: Loading...")
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()

        self._action_toggle = menu.addAction("Start Recording")
        self._action_toggle.setEnabled(False)
        self._action_toggle.triggered.connect(self.toggle_recording)

        menu.addSeparator()

        # Tone submenu — checkable actions so active tone gets a dot
        tone_menu = menu.addMenu("Tone Mode")
        for code, label in _TONE_LABELS:
            a = tone_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, c=code: self._on_tone_clicked(c))
            self._tone_actions[code] = a

        menu.addSeparator()
        menu.addAction("History...").triggered.connect(self.open_history)
        menu.addAction("Settings").triggered.connect(self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit Typra").triggered.connect(self.quit_app)
        self.setContextMenu(menu)

    def _on_tone_clicked(self, code: str) -> None:
        self.set_active_tone(code)
        self.tone_selected.emit(code)

    def set_active_tone(self, code: str) -> None:
        """Update checkmark to reflect the active tone."""
        for c, action in self._tone_actions.items():
            action.setChecked(c == code)

    def set_model_ready(self) -> None:
        self._loading = False
        self._action_toggle.setEnabled(True)
        self._update()

    def update_recording_state(self, recording: bool) -> None:
        self._recording = recording
        self._update()

    def _update(self) -> None:
        self.setIcon(self._app_icon)
        if self._loading:
            self.setToolTip("Typra: Loading model...")
        elif self._recording:
            self._action_toggle.setText("Stop Recording")
            self.setToolTip("Typra: Recording")
        else:
            self._action_toggle.setText("Start Recording")
            self.setToolTip("Typra: Ready  (Ctrl+Shift+Space)")

    def _on_activated(self, reason) -> None:
        # A double-click delivers a Trigger first: reacting to both would
        # start and immediately stop recording.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_recording.emit()
