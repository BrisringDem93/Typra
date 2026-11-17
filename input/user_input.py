# input/user_input.py
"""
UserInputWatch: counts the keys and mouse clicks of the user (not the ones
Typra itself sends), so a live correction never backspaces into text after
the user typed or moved the caret.

Windows only: the low-level hooks mark injected events, which is how Typra's
own typing is told apart from the user's. Elsewhere the watch stays inactive
and corrections are not made.
"""
from __future__ import annotations
import sys

_LLKHF_INJECTED = 0x10
_LLMHF_INJECTED = 0x01
_KEY_DOWN = {0x0100, 0x0104}          # WM_KEYDOWN, WM_SYSKEYDOWN
_KEY_UP = {0x0101, 0x0105}
_MOUSE_DOWN = {0x0201, 0x0204, 0x0207, 0x020B}   # left, right, middle, X button
_SHIFT_VKS = {0x10, 0xA0, 0xA1}
# Ctrl, Alt, Win: with one of them held, a key is a shortcut
_SHORTCUT_VKS = {0x11, 0x12, 0x5B, 0x5C, 0xA2, 0xA3, 0xA4, 0xA5}
_F_KEYS = set(range(0x70, 0x7C))      # F1-F12: Typra's hotkey can be one of them
# Shortcuts that change the text or move the caret: paste, cut, undo, redo,
# word deletion, word and line moves
_EDITING_SHORTCUT_VKS = {ord("V"), ord("X"), ord("Z"), ord("Y"), 0x08, 0x2E, 0x0D,
                         0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x23, 0x24}


class UserInputWatch:
    def __init__(self) -> None:
        self.count = 0
        self._held: set[int] = set()
        self._listeners: list = []

    @property
    def active(self) -> bool:
        return bool(self._listeners)

    def start(self) -> None:
        if self._listeners or sys.platform != "win32":
            return
        try:
            from pynput import keyboard, mouse
            listeners = [keyboard.Listener(win32_event_filter=self._on_key),
                         mouse.Listener(win32_event_filter=self._on_mouse)]
            for listener in listeners:
                listener.daemon = True
                listener.start()
            self._listeners = listeners
        except Exception as exc:
            print(f"[UserInputWatch] Not available: {exc}")
            self.stop()

    def stop(self) -> None:
        for listener in self._listeners:
            try: listener.stop()
            except Exception: pass
        self._listeners = []
        self._held.clear()

    # The filters never return False: events reach other programs unchanged
    def _on_key(self, msg, data) -> None:
        if data.flags & _LLKHF_INJECTED:
            return
        vk = data.vkCode
        if vk in _SHORTCUT_VKS:
            if msg in _KEY_DOWN:
                self._held.add(vk)
            elif msg in _KEY_UP:
                self._held.discard(vk)
            return
        if msg not in _KEY_DOWN or vk in _SHIFT_VKS or vk in _F_KEYS:
            return
        if self._held and vk not in _EDITING_SHORTCUT_VKS:
            return   # Typra's hotkey or another shortcut that leaves the text alone
        self.count += 1

    def _on_mouse(self, msg, data) -> None:
        if msg in _MOUSE_DOWN and not data.flags & _LLMHF_INJECTED:
            self.count += 1
