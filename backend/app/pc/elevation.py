"""
Admin rights (UAC). Cypher never runs elevated and never elevates itself
(FIX2): no "Start-Process -Verb RunAs" (the classifier blocks it), no
scheduled task tricks. When a command fails because it needs admin
rights, Cypher says so and tells the user what to do instead.
"""
from __future__ import annotations

import ctypes
import re
import sys
from typing import Optional

# Typical messages (German and English Windows) and Win32 error codes.
_ADMIN_PATTERNS = re.compile(
    r"zugriff verweigert|access (is )?denied|erhöhte(n)? rechte|erfordert erhöhte|requires elevation|elevation required"
    r"|administratorrechte|administrator privileges|als administrator ausführen|run as administrator"
    r"|unauthorizedaccessexception|permissiondenied|0x80070005|0x800702e4|nicht über die erforderlichen berechtigungen"
    r"|you must be an administrator|must be run as an administrator",
    re.IGNORECASE,
)
_ADMIN_EXIT_CODES = {5, 740}  # ERROR_ACCESS_DENIED, ERROR_ELEVATION_REQUIRED


def is_elevated() -> bool:
    """True if this process runs with admin rights (it should not)."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def needs_admin(output: str, exit_code: Optional[int]) -> bool:
    return (exit_code in _ADMIN_EXIT_CODES) or bool(_ADMIN_PATTERNS.search(output or ""))


def admin_hint(command: str) -> str:
    """German explanation for the user (spoken/displayed by the model)."""
    return (
        "Dafür braucht es Administratorrechte. Cypher läuft bewusst ohne Adminrechte und erhöht sich nie selbst. "
        "Wenn du das wirklich willst: Startmenü öffnen, 'PowerShell' eintippen, Rechtsklick > 'Als Administrator ausführen' "
        f"und dort selbst eingeben: {command}"
    )
