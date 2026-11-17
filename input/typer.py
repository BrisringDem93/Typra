# input/typer.py
"""
TextTyper: types transcribed text into the currently focused window.

Now that the overlay uses WS_EX_NOACTIVATE (never steals focus),
the user's target window keeps focus throughout recording.
We simply type to whatever window has focus — no focus manipulation needed.

Uses pynput Controller.type() which calls SendInput internally and
handles full Unicode (umlauts, CJK, etc.) correctly.

Injected keys combine with keys the user is still holding: typing while
Ctrl/Shift/Alt/Win are down would turn the text into shortcuts (a space would
even re-trigger Ctrl+Shift+Space), so typing waits until they are released.
"""
from __future__ import annotations
import sys
import threading
import time
import unicodedata

_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)   # Shift, Ctrl, Alt, left/right Win


def _modifiers_held() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    key_state = ctypes.windll.user32.GetAsyncKeyState
    return any(key_state(vk) & 0x8000 for vk in _MODIFIER_VKS)


class TextTyper:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def start_tracking(self, own_hwnd: int = 0) -> None:
        pass  # no longer needed

    def stop_tracking(self) -> None:
        pass

    @staticmethod
    def wait_for_modifiers_release(timeout: float = 2.0) -> bool:
        """True once no modifier key is held, False if still held after `timeout`."""
        deadline = time.monotonic() + timeout
        while _modifiers_held():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    @staticmethod
    def prepare(text: str, trailing_space: bool) -> str:
        """The characters type_text() sends for `text`."""
        # Control characters would be sent as real keys (Backspace, Esc...)
        text = "".join(c for c in text
                       if c in "\n\t" or not unicodedata.category(c).startswith("C"))
        if not text:
            return ""
        return text + (" " if trailing_space and not text.endswith(" ") else "")

    def type_text(
        self,
        text: str,
        trailing_space: bool = True,
        restore_focus: bool = True,
    ) -> bool:
        """Type `text`; returns False if it was not typed."""
        full = self.prepare(text, trailing_space)
        if not full:
            return True
        if not self.wait_for_modifiers_release():
            print("[TextTyper] modifier keys still held — not typing")
            return False
        with self._lock:
            try:
                from pynput.keyboard import Controller
                kb = Controller()
                rest = full
                while rest:
                    try:
                        kb.type(rest)
                        break
                    except kb.InvalidCharacterException as exc:
                        # e.g. an emoji pynput cannot send: skip it, type the rest
                        rest = rest[exc.args[0] + 1:]
            except Exception as exc:
                print(f"[TextTyper] type error: {exc}")
                return False
        return True

    def backspace(self, count: int) -> None:
        """Press Backspace `count` times (callers check the target window first)."""
        if count <= 0:
            return
        with self._lock:
            try:
                from pynput.keyboard import Controller, Key
                kb = Controller()
                for _ in range(count):
                    kb.press(Key.backspace)
                    kb.release(Key.backspace)
            except Exception as exc:
                print(f"[TextTyper] backspace error: {exc}")


class MirroredTyper:
    """Types through `typer` (None: only simulates) and keeps a copy of the
    text it produced, so a later correction knows exactly what is on screen."""

    def __init__(self, typer: TextTyper | None, text: str = "") -> None:
        self._typer = typer
        self.text = text

    @staticmethod
    def wait_for_modifiers_release(timeout: float = 2.0) -> bool:
        return TextTyper.wait_for_modifiers_release(timeout)

    def type_text(self, text: str, trailing_space: bool = True,
                  restore_focus: bool = True) -> bool:
        full = TextTyper.prepare(text, trailing_space)
        if self._typer is not None and not self._typer.type_text(
                text, trailing_space=trailing_space, restore_focus=restore_focus):
            return False
        self.text += full
        return True

    def backspace(self, count: int) -> None:
        if count <= 0:
            return
        if self._typer is not None:
            self._typer.backspace(count)
        self.text = self.text[:max(0, len(self.text) - count)]
