"""
Clipboard read/write via the Win32 API (ctypes, no dependency).

Reading is privacy-sensitive (people copy passwords). Locally that is
fine - the text goes to the local model only - but while the CLOUD
provider is active, reading needs the user's approval, because the
content would leave the PC. Clipboard content is untrusted input.
"""
from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from typing import Any, Dict, Optional

from pydantic import Field

from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MAX_CLIPBOARD_CHARS = 20_000


def _api():
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 64-bit handles/pointers: without explicit types ctypes truncates them to 32 bit.
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return user32, kernel32


def _open(user32) -> None:
    # Another app may hold the clipboard for a moment - retry briefly.
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("Die Zwischenablage ist gerade von einem anderen Programm belegt.")


def read_clipboard_text() -> Optional[str]:
    user32, kernel32 = _api()
    _open(user32)
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def write_clipboard_text(text: str) -> None:
    user32, kernel32 = _api()
    data = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("GlobalAlloc failed")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    _open(user32)
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("SetClipboardData failed")
        # On success the system owns `handle` - it must not be freed here.
    finally:
        user32.CloseClipboard()


@register_tool
class ReadClipboardTool(Tool):
    name = "read_clipboard"
    description = "Den Text aus der Zwischenablage lesen."
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        # Sending clipboard content to a cloud model needs explicit consent.
        if ctx is not None and ctx.provider == "cloud":
            return RiskTier.WRITE
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        return "Zwischenablage lesen"

    async def execute(self, db=None, **_) -> ToolResult:
        try:
            text = await asyncio.to_thread(read_clipboard_text)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        if text is None:
            return ToolResult(success=True, data={"empty": True, "note": "Die Zwischenablage enthält keinen Text."})
        return ToolResult(success=True, data={"text": text[:MAX_CLIPBOARD_CHARS], "truncated": len(text) > MAX_CLIPBOARD_CHARS})


class WriteClipboardArgs(ArgsModel):
    text: str = Field(..., max_length=MAX_CLIPBOARD_CHARS, description="Text, der in die Zwischenablage kopiert wird")


@register_tool
class WriteClipboardTool(Tool):
    name = "write_clipboard"
    description = "Text in die Zwischenablage kopieren."
    Args = WriteClipboardArgs
    risk = RiskTier.CONTROL
    # Copying attacker-chosen text (e.g. a malicious command) on behalf of a
    # web page would be a classic injection payload.
    allow_when_tainted = False
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    async def execute(self, db=None, text: str = "", **_) -> ToolResult:
        try:
            await asyncio.to_thread(write_clipboard_text, text)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"copied_chars": len(text)})

    def describe(self, args: Dict[str, Any]) -> str:
        text = args.get("text", "")
        return f"In die Zwischenablage kopieren: „{text[:60]}{'…' if len(text) > 60 else ''}“"
