"""System-wide hotkey via Win32 RegisterHotKey, delivered through Qt.

RegisterHotKey posts WM_HOTKEY to the registering thread's message queue, which
Qt's event loop dispatches to native event filters — so GlobalHotkey must be
created and registered on the Qt GUI thread. The callback then also runs on the
GUI thread, so it may touch widgets directly.

Because the hotkey is registered with the OS (not the window), it fires even
when the app is unfocused or minimized — e.g. from a StreamDeck or macro key
sending the key combination. Windows-only; register() returns False elsewhere.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

from PyQt6.QtCore import QAbstractNativeEventFilter, QCoreApplication

WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000  # don't refire while the key is held down

_MODS = {
    "ctrl": 0x0002, "control": 0x0002,
    "alt": 0x0001,
    "shift": 0x0004,
    "win": 0x0008, "meta": 0x0008,
}

_NAMED_VK = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "pause": 0x13,
}


def parse_hotkey(sequence: str) -> Optional[tuple[int, int]]:
    """Parse 'ctrl+alt+s' style text into (modifier_flags, virtual_key).

    Accepts any mix of ctrl/alt/shift/win plus one key: a letter, a digit,
    F1–F24, or a name from _NAMED_VK. Returns None if the text is invalid.
    """
    mods = 0
    vk: Optional[int] = None
    for part in sequence.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part[0] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1  # VK_F1 = 0x70
        elif part in _NAMED_VK:
            vk = _NAMED_VK[part]
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class GlobalHotkey(QAbstractNativeEventFilter):
    _next_id = 1  # per-process unique RegisterHotKey ids

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self._callback = callback
        self._id = GlobalHotkey._next_id
        GlobalHotkey._next_id += 1
        self._registered = False
        self.sequence = ""

    def register(self, sequence: str) -> bool:
        """Register `sequence` system-wide. False if the text is invalid, the
        combination is already taken by another app, or not on Windows."""
        if sys.platform != "win32":
            return False
        self.unregister()
        parsed = parse_hotkey(sequence)
        if parsed is None:
            return False
        mods, vk = parsed
        import ctypes
        if not ctypes.windll.user32.RegisterHotKey(
                None, self._id, mods | _MOD_NOREPEAT, vk):
            return False
        self._registered = True
        self.sequence = sequence
        app = QCoreApplication.instance()
        if app:
            app.installNativeEventFilter(self)
        return True

    def unregister(self) -> None:
        if not self._registered:
            return
        import ctypes
        ctypes.windll.user32.UnregisterHotKey(None, self._id)
        self._registered = False
        app = QCoreApplication.instance()
        if app:
            app.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            import ctypes.wintypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                self._callback()
        return False, 0
