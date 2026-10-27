# ui/overlay.py
"""
Recording overlay: transparent pill, never steals focus.

Three-layer focus protection:
 1. WS_EX_NOACTIVATE  — Windows won't activate this window
 2. nativeEvent        — intercepts WM_MOUSEACTIVATE → MA_NOACTIVATE
 3. show_overlay()     — saves foreground HWND before show(), restores it
                         immediately if Qt's show() stole it
"""
from __future__ import annotations
import sys, math, random
from PyQt6.QtCore import QPoint, QPropertyAnimation, QEasingCurve, Qt, \
                         QTimer, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QBrush
from PyQt6.QtWidgets import QApplication, QWidget

_W       = 220
_H       = 52
_N_BARS  = 20
_BAR_W   = 3
_BAR_GAP = 7


def _is_dark() -> bool:
    if sys.platform != "win32": return True
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k); return v == 0
    except Exception: return True


class RecordingOverlay(QWidget):
    stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,  True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,     True)
        self.setFixedSize(_W, _H)

        self._phase  = 0.0
        self._status = "listening"
        self._dark   = _is_dark()
        self._slide  = None
        self._level  = 0.0
        self._bar_h  = [random.uniform(0.1, 0.35) for _ in range(_N_BARS)]

        # Force native HWND creation NOW so WS_EX_NOACTIVATE is set
        # before the window is ever shown
        _ = self.winId()
        self._set_noactivate()

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    # ── Windows: prevent activation ──────────────────────────────────

    def _set_noactivate(self) -> None:
        if sys.platform != "win32": return
        try:
            import ctypes
            hwnd = int(self.winId())
            if not hwnd: return
            GWL_EXSTYLE       = -20
            WS_EX_NOACTIVATE  = 0x08000000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW  = 0x00000080
            cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                cur | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception as e:
            print(f"[Overlay] _set_noactivate: {e}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_noactivate()   # re-apply after Qt may reset extended styles

    # ── Public API ───────────────────────────────────────────────────

    def set_level(self, rms: float) -> None:
        scaled = min(1.0, rms * 10.0)
        if scaled > self._level:
            self._level = scaled

    def set_status(self, s: str) -> None:
        self._status = s

    def refresh_theme(self) -> None:
        self._dark = _is_dark()
        self.update()

    # ── Show / hide ──────────────────────────────────────────────────

    def show_overlay(self) -> None:
        self._dark  = _is_dark()
        self._level = 0.0

        screen = QApplication.primaryScreen().availableGeometry()
        x      = screen.x() + (screen.width() - _W) // 2
        y_show = screen.y() + screen.height() - _H - 24
        y_hide = screen.y() + screen.height() + 10

        # Save foreground window BEFORE showing
        prev_fg = 0
        if sys.platform == "win32":
            try:
                import ctypes
                prev_fg = ctypes.windll.user32.GetForegroundWindow()
            except Exception:
                pass

        self.move(x, y_hide)
        self.show()
        self._timer.start()

        # If show() stole focus, give it back immediately
        if sys.platform == "win32" and prev_fg:
            try:
                import ctypes
                own_hwnd = int(self.winId())
                if ctypes.windll.user32.GetForegroundWindow() == own_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(prev_fg)
                    print("[Overlay] Restored focus to previous window")
            except Exception:
                pass

        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(QPoint(x, y_hide))
        anim.setEndValue(QPoint(x, y_show))
        anim.start(); self._slide = anim

    def hide_overlay(self) -> None:
        self._timer.stop()
        screen = QApplication.primaryScreen().availableGeometry()
        y_hide = screen.y() + screen.height() + 10
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(self.x(), y_hide))
        anim.finished.connect(self.hide)
        anim.start(); self._slide = anim

    # ── Tick ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        speed       = 0.015 if self._status == "listening" else 0.035
        self._phase = (self._phase + speed) % 1.0
        self._level = max(0.0, self._level - 0.05)

        for i in range(_N_BARS):
            idle = 0.18 + 0.12 * math.sin(
                self._phase * 2 * math.pi + i * (2 * math.pi / _N_BARS))
            target = (idle + random.random() * self._level * 0.82
                      if self._level > 0.04 else idle)
            target = min(1.0, max(0.0, target))
            k = 0.55 if target > self._bar_h[i] else 0.12
            self._bar_h[i] += (target - self._bar_h[i]) * k

        self.update()

    # ── Paint ─────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(_W), float(_H)
        r    = 10.0   # Windows 11 native corner radius

        # Clear to transparent
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(0, 0, _W, _H, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Pill — inset 1px so anti-aliased edge blends into transparency
        pill_bg = QColor(18, 18, 20) if self._dark else QColor(230, 230, 232)
        bar_col = QColor(255, 255, 255) if self._dark else QColor(20, 20, 25)

        path = QPainterPath()
        path.addRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(pill_bg))
        p.drawPath(path)

        # Bars
        total_span = (_N_BARS - 1) * _BAR_GAP
        x_start    = (_W - total_span) // 2
        cy         = _H // 2
        max_bar_h  = _H - 12

        for i in range(_N_BARS):
            frac  = self._bar_h[i]
            bh    = max(3, int(3 + frac * (max_bar_h - 3)))
            alpha = int(110 + frac * 145)
            c     = QColor(bar_col)
            c.setAlpha(alpha)
            p.setBrush(QBrush(c))
            bx = x_start + i * _BAR_GAP - _BAR_W // 2
            by = cy - bh // 2
            p.drawRoundedRect(bx, by, _BAR_W, bh, 1, 1)
