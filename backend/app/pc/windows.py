"""
Top-level windows via the Win32 API (ctypes, no dependency):
list, focus, minimize, maximize, restore, close.

"close" posts WM_CLOSE - the same as clicking the X, so the app can still
ask "Änderungen speichern?". Nothing is killed here (that is processes).
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import List, Optional

import psutil

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
WM_CLOSE = 0x0010
# Desktop, taskbar and other shell surfaces - never listed or touched.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "ApplicationFrameInputSinkWindow"}


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    process: str
    pid: int
    class_name: str
    minimized: bool


def _user32():
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    return user32


def _text(user32, hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class(user32, hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _describe(user32, hwnd) -> Optional[Window]:
    from ctypes import wintypes  # noqa: PLC0415

    title = _text(user32, hwnd)
    cls = _class(user32, hwnd)
    if not title or cls in _SHELL_CLASSES:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        process = psutil.Process(pid.value).name()
    except psutil.Error:
        process = ""
    return Window(hwnd=int(hwnd), title=title, process=process, pid=pid.value, class_name=cls, minimized=bool(user32.IsIconic(hwnd)))


def list_windows() -> List[Window]:
    if sys.platform != "win32":
        return []
    from ctypes import wintypes  # noqa: PLC0415

    user32 = _user32()
    found: List[Window] = []
    GW_OWNER = 4

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        # Visible, unowned windows = what the taskbar shows.
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER):
            window = _describe(user32, hwnd)
            if window is not None:
                found.append(window)
        return True

    user32.EnumWindows(callback, 0)
    return found


def foreground() -> Optional[Window]:
    if sys.platform != "win32":
        return None
    user32 = _user32()
    hwnd = user32.GetForegroundWindow()
    return _describe(user32, hwnd) if hwnd else None


def window_at(x: int, y: int) -> Optional[Window]:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes  # noqa: PLC0415

    user32 = _user32()
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    return _describe(user32, root)


@dataclass
class Found:
    window: Optional[Window]
    candidates: List[str]


def find(title: str, windows: Optional[List[Window]] = None) -> Found:
    """Window whose title or process name contains `title` (case-insensitive).
    Several different matches -> candidates, no window."""
    wanted = title.strip().lower().removesuffix(".exe")
    if not wanted:
        return Found(None, [])
    windows = list_windows() if windows is None else windows
    hits = [w for w in windows if wanted in w.title.lower() or wanted == w.process.lower().removesuffix(".exe")]
    if len(hits) == 1:
        return Found(hits[0], [])
    exact = [w for w in hits if w.title.lower() == wanted]
    if len(exact) == 1:
        return Found(exact[0], [])
    return Found(None, [w.title for w in hits[:8]])


def apply(window: Window, action: str) -> bool:
    user32 = _user32()
    hwnd = window.hwnd
    if action == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True
    if action == "maximize":
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return True
    if action == "restore":
        user32.ShowWindow(hwnd, SW_RESTORE)
        return True
    if action == "focus":
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # SetForegroundWindow only works for the process that sent the last
        # input event - a synthetic ALT tap makes that us (known workaround).
        VK_MENU, KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYUP, 0)
        return bool(user32.SetForegroundWindow(hwnd))
    if action == "close":
        return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
    raise ValueError(action)
