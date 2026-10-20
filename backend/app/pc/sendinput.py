"""
Keyboard and mouse via SendInput (Win32, ctypes).

FRAGILE by nature: it types into whatever window has the focus and clicks
pixels. The input_control tool is the last resort - commands, URIs and app
starts are preferred (the tool description says so).
"""
from __future__ import annotations

import ctypes
import functools
import time
from typing import List, Sequence

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
MOUSEEVENTF_WHEEL = 0x800
WHEEL_DELTA = 120

KEYS = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "leertaste": 0x20,
    "backspace": 0x08, "delete": 0x2E, "entf": 0x2E, "insert": 0x2D, "einfg": 0x2D, "home": 0x24, "pos1": 0x24, "end": 0x23, "ende": 0x23,
    "pageup": 0x21, "bildauf": 0x21, "pagedown": 0x22, "bildab": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "strg": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "umschalt": 0x10, "win": 0x5B, "windows": 0x5B,
    "printscreen": 0x2C, "druck": 0x2C, "capslock": 0x14, "menu": 0x5D, "apps": 0x5D,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
}
_EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D}
MODIFIERS = {"ctrl", "strg", "control", "alt", "shift", "umschalt", "win", "windows"}


def parse_keys(combo: str) -> List[str]:
    """'Strg+Shift+S' -> ['strg', 'shift', 's']; unknown names raise ValueError."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("keine Taste angegeben")
    unknown = [p for p in parts if p not in KEYS]
    if unknown:
        raise ValueError(f"unbekannte Taste(n): {', '.join(unknown)}")
    return parts


@functools.lru_cache(maxsize=1)  # one set of classes - ctypes arrays need the same type
def _structs():
    from ctypes import wintypes  # noqa: PLC0415

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    return user32, INPUT, KEYBDINPUT, MOUSEINPUT


def _send(events: Sequence) -> None:
    user32, INPUT, _k, _m = _structs()
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError("Windows hat die Eingabe blockiert (z. B. weil ein Programm mit Adminrechten im Vordergrund ist).")


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0):
    _u, INPUT, KEYBDINPUT, _m = _structs()
    event = INPUT(type=INPUT_KEYBOARD)
    event.u.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def type_text(text: str) -> None:
    events = []
    for char in text:
        if char == "\n":
            events += [_key_event(KEYS["enter"]), _key_event(KEYS["enter"], flags=KEYEVENTF_KEYUP)]
            continue
        units = char.encode("utf-16-le")
        for i in range(0, len(units), 2):  # surrogate pairs = two units
            code = int.from_bytes(units[i:i + 2], "little")
            events += [_key_event(scan=code, flags=KEYEVENTF_UNICODE), _key_event(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    for start in range(0, len(events), 200):
        _send(events[start:start + 200])
        time.sleep(0.01)


def press(combo: str) -> None:
    names = parse_keys(combo)
    codes = [KEYS[n] for n in names]
    down = [_key_event(c, flags=KEYEVENTF_EXTENDEDKEY if c in _EXTENDED else 0) for c in codes]
    up = [_key_event(c, flags=KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if c in _EXTENDED else 0)) for c in reversed(codes)]
    _send(down + up)


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    user32, INPUT, _k, MOUSEINPUT = _structs()
    if not user32.SetCursorPos(x, y):
        raise OSError("Mauszeiger konnte nicht gesetzt werden")
    down_flag, up_flag = MOUSE_FLAGS[button]
    events = []
    for _ in range(2 if double else 1):
        for flag in (down_flag, up_flag):
            event = INPUT(type=INPUT_MOUSE)
            event.u.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
            events.append(event)
    _send(events)


def move(x: int, y: int) -> None:
    user32, *_rest = _structs()
    if not user32.SetCursorPos(x, y):
        raise OSError("Mauszeiger konnte nicht gesetzt werden")


def scroll(clicks: int) -> None:
    """Positive = up, negative = down (one click = one wheel notch)."""
    _u, INPUT, _k, MOUSEINPUT = _structs()
    event = INPUT(type=INPUT_MOUSE)
    event.u.mi = MOUSEINPUT(dx=0, dy=0, mouseData=ctypes.c_uint32(clicks * WHEEL_DELTA).value, dwFlags=MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
    _send([event])
