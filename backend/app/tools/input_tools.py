"""
Screenshot and keyboard/mouse input (FIX2 point 3).

    screenshot      PNG of all screens into Bilder\\Screenshots      control
    input_control   type text, press keys ("strg+s"), click, scroll  FRAGILE
                    - last resort; apps, commands and URIs come first

Rules for input_control (app/pc/sendinput.py does the typing):
  * NEVER into Cypher's own window: approval cards and the safety-prompt
    switch are for the human only -> blocked
  * into a command line, the Run dialog, Start search, regedit,
    Task Manager ... -> critical (typed text there runs as a command)
  * Windows-key shortcuts -> critical (Win+R opens "Ausführen" ...)
  * after untrusted content in the turn -> at least write (asks)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from app.pc import sendinput
from app.pc import windows as win
from app.pc.known_folders import known_folder
from app.security.classifier import BLOCKED, CONTROL, CRITICAL, WRITE, Verdict, verdict, worst
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

# Windows where typed text becomes a command or a system change.
_COMMAND_PROCESSES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe", "powershell_ise.exe",
    "regedit.exe", "mmc.exe", "taskmgr.exe", "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe", "putty.exe", "ssh.exe",
    "bash.exe", "wsl.exe", "python.exe", "pythonw.exe", "consent.exe",
}


def _windows_only(cls, settings: Any = None) -> bool:
    return sys.platform == "win32"


def is_cypher_window(window: Optional[win.Window]) -> bool:
    return window is not None and "cypher" in window.title.lower()


def target_verdict(window: Optional[win.Window]) -> Verdict:
    if window is None:
        return verdict(CONTROL)
    if is_cypher_window(window):
        return verdict(BLOCKED, "Eingaben in Cyphers eigene Oberfläche sind gesperrt (Freigaben bedient nur der Mensch)", "cypher_ui")
    process = window.process.lower()
    if process in _COMMAND_PROCESSES:
        return verdict(CRITICAL, f"Eingabe in {window.process}: getippter Text wird dort ausgeführt", "shell_input")
    if process == "explorer.exe" and window.class_name == "#32770":
        return verdict(CRITICAL, "Eingabe in einen System-Dialog (z. B. 'Ausführen')", "shell_input")
    return verdict(CONTROL)


# ------------------------------------------------------------------ screenshot
def _screenshot_path() -> str:
    folder = os.path.join(known_folder("pictures") or os.path.join(os.path.expanduser("~"), "Pictures"), "Screenshots")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"Cypher_{datetime.now():%Y-%m-%d_%H-%M-%S}.png")


def take_screenshot() -> Dict[str, Any]:
    from PIL import ImageGrab  # noqa: PLC0415

    image = ImageGrab.grab(all_screens=True)
    path = _screenshot_path()
    image.save(path, format="PNG")
    return {"path": path, "width": image.width, "height": image.height}


@register_tool
class ScreenshotTool(Tool):
    name = "screenshot"
    description = "Ein Bildschirmfoto als PNG unter Bilder\\Screenshots speichern."
    risk = RiskTier.CONTROL
    confirm_writes = False
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def describe(self, args: Dict[str, Any]) -> str:
        return "Bildschirmfoto speichern"

    async def execute(self, db=None, **_) -> ToolResult:
        try:
            data = await asyncio.to_thread(take_screenshot)
        except (OSError, ImportError) as exc:
            return ToolResult(success=False, error=f"Bildschirmfoto fehlgeschlagen: {exc}")
        return ToolResult(success=True, data=data)


# ------------------------------------------------------------------ input
class InputArgs(ArgsModel):
    action: Literal["type", "key", "click", "double_click", "right_click", "move", "scroll"]
    text: Optional[str] = Field(None, max_length=2000, description="type: Text; key: Tasten wie 'strg+s', 'enter'")
    x: Optional[int] = None
    y: Optional[int] = None
    amount: Optional[int] = Field(None, ge=-50, le=50, description="scroll: + hoch, - runter")


@register_tool
class InputControlTool(Tool):
    name = "input_control"
    description = "Notlösung, fragil: Tastatur/Maus steuern (tippen, Tasten, klicken, scrollen). Nur wenn kein anderes Werkzeug passt."
    Args = InputArgs
    risk = RiskTier.CONTROL
    confirm_writes = False
    fragile = True
    exposed_by_default = True

    is_available = classmethod(_windows_only)

    def classify(self, args: Dict[str, Any], ctx=None):
        action = args.get("action")
        parts = []
        if action in ("click", "double_click", "right_click"):
            if args.get("x") is None or args.get("y") is None:
                return verdict(CONTROL)  # execute() reports the missing coordinates
            parts.append(target_verdict(win.window_at(int(args["x"]), int(args["y"]))))
        elif action in ("type", "key"):
            parts.append(target_verdict(win.foreground()))
        else:  # move, scroll
            parts.append(verdict(CONTROL))
        if action == "key":
            try:
                keys = sendinput.parse_keys(args.get("text") or "")
            except ValueError:
                keys = []
            if "win" in keys or "windows" in keys:
                parts.append(verdict(CRITICAL, "Windows-Tastenkürzel (z. B. Win+R öffnet 'Ausführen')", "system_hotkey"))
            elif "alt" in keys and "f4" in keys:
                parts.append(verdict(WRITE, "schließt das aktive Fenster"))
        if ctx is not None and ctx.tainted:
            parts.append(verdict(WRITE, "nach fremdem Inhalt (Webseite, Datei ...)"))
        return worst(parts)

    def describe(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "")
        if action == "type":
            text = args.get("text") or ""
            return f"Tippen: „{text[:80]}{'…' if len(text) > 80 else ''}“"
        if action == "key":
            return f"Tasten: {args.get('text', '')}"
        if action == "scroll":
            return f"Scrollen: {args.get('amount', 0)}"
        return f"Maus {action} bei ({args.get('x')}, {args.get('y')})"

    async def execute(self, db=None, action: str = "", text: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None, amount: Optional[int] = None, **_) -> ToolResult:
        # Check the target again right before acting: the focus may have moved.
        if action in ("type", "key") and is_cypher_window(await asyncio.to_thread(win.foreground)):
            return ToolResult(success=False, error="Eingaben in Cyphers eigene Oberfläche sind gesperrt.")
        try:
            if action == "type":
                if not text:
                    return ToolResult(success=False, error="Welcher Text?")
                await asyncio.to_thread(sendinput.type_text, text)
            elif action == "key":
                await asyncio.to_thread(sendinput.press, text or "")
            elif action in ("click", "double_click", "right_click", "move"):
                if x is None or y is None:
                    return ToolResult(success=False, error="x und y (Bildschirmpixel) fehlen.")
                if action != "move" and is_cypher_window(await asyncio.to_thread(win.window_at, x, y)):
                    return ToolResult(success=False, error="Klicks in Cyphers eigene Oberfläche sind gesperrt.")
                if action == "move":
                    await asyncio.to_thread(sendinput.move, x, y)
                else:
                    await asyncio.to_thread(sendinput.click, x, y, "right" if action == "right_click" else "left", action == "double_click")
            elif action == "scroll":
                await asyncio.to_thread(sendinput.scroll, amount or -3)
            else:
                return ToolResult(success=False, error=f"Unbekannte Aktion '{action}'.")
        except (OSError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"action": action, "note": "Eingabe gesendet - ob die App reagiert hat, ist nicht geprüft (fragil)."})
