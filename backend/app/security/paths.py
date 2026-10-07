"""
Filesystem sandbox for every tool that touches files.

A path from the LLM is never trusted as-is:
  1. syntactic checks: no NUL bytes, no NTFS alternate data streams
     ("file.txt:hidden"), no device names (CON, NUL, COM1 ...), no UNC
     network paths;
  2. the path is fully resolved (symlinks AND Windows junctions, "..")
     with os.path.realpath before any check - so a link inside an allowed
     folder that points to C:\\Windows is judged by its real target;
  3. the real path must lie inside one of ALLOWED_DIRECTORIES;
  4. even inside an allowed folder, sensitive locations stay off-limits:
     Windows/program folders, AppData (browser profiles, saved logins),
     key/credential folders (.ssh, .aws, ...), secret files (.env, *.pem,
     *.kdbx, ...) and Cypher's own data (tokens, database).
"""
from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings


class PathNotAllowedError(Exception):
    """Path rejected by the sandbox. The message is safe to show the user."""


_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

# Folder names that are never accessible, wherever they appear.
_DENIED_DIR_NAMES = {
    "appdata",
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".config",
    "$recycle.bin",
    "system volume information",
}

# File name patterns that are never accessible (case-insensitive).
_DENIED_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.kdbx",
    "*.ppk",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "secrets.json",
    "credentials*",
    "*.sqlite",
    "*.db",
    "ntuser.dat*",
)


def _system_roots() -> list[Path]:
    roots = []
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(os.path.realpath(value)))
    return roots


def _cypher_private_paths(settings: Settings) -> list[Path]:
    return [
        Path(os.path.realpath(settings.data_dir_path)),
        Path(os.path.realpath(PROJECT_ROOT / "backend" / "models")),
    ]


def allowed_roots(settings: Settings | None = None) -> list[Path]:
    settings = settings or get_settings()
    roots = []
    for raw in settings.allowed_directories_list:
        path = Path(os.path.realpath(raw))
        if path.is_dir():
            roots.append(path)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    # Case-insensitive on Windows (realpath keeps the on-disk casing, but
    # configured roots may be typed differently).
    p, r = os.path.normcase(str(path)), os.path.normcase(str(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _syntax_check(path_str: str) -> None:
    if not path_str or not path_str.strip():
        raise PathNotAllowedError("Es wurde kein Pfad angegeben.")
    if "\x00" in path_str:
        raise PathNotAllowedError("Ungültiger Pfad.")
    if path_str.startswith(("\\\\", "//")):
        raise PathNotAllowedError("Netzwerkpfade sind nicht erlaubt.")
    # A colon is only valid as the drive separator ("C:\...").
    if ":" in path_str[2:] or re.match(r"^[^a-zA-Z]:", path_str):
        raise PathNotAllowedError("Ungültiger Pfad (Doppelpunkt).")
    for part in re.split(r"[\\/]", path_str):
        stem = part.split(".")[0].strip().lower()
        if stem in _DEVICE_NAMES:
            raise PathNotAllowedError("Gerätenamen wie CON oder NUL sind als Pfad nicht erlaubt.")


def check_sensitive(resolved: Path, settings: Settings) -> None:
    for root in _system_roots():
        if _is_within(resolved, root):
            raise PathNotAllowedError("System- und Programmordner sind gesperrt.")
    for private in _cypher_private_paths(settings):
        if _is_within(resolved, private):
            raise PathNotAllowedError("Cyphers eigene Daten sind für Datei-Tools gesperrt.")
    # The system temp folder itself lives in AppData\Local\Temp; inside it
    # only the path below Temp is checked (so ".ssh" in Temp stays locked,
    # but Temp's own AppData prefix does not lock every temp file).
    temp_root = Path(os.path.realpath(tempfile.gettempdir()))
    if _is_within(resolved, temp_root) and resolved != temp_root:
        checked = resolved.relative_to(temp_root).parts if str(resolved).lower().startswith(str(temp_root).lower()) else resolved.parts
    else:
        checked = resolved.parts
    if any(part.lower() in _DENIED_DIR_NAMES for part in checked):
        raise PathNotAllowedError("Dieser Ordner ist gesperrt (enthält z. B. Zugangsdaten oder Browserprofile).")
    name = resolved.name.lower()
    if any(fnmatch.fnmatch(name, pattern) for pattern in _DENIED_FILE_PATTERNS):
        raise PathNotAllowedError("Diese Datei ist gesperrt (mögliche Zugangsdaten oder Datenbank).")


def resolve_allowed_path(path_str: str, settings: Settings | None = None) -> Path:
    """Resolve an LLM-supplied path or raise PathNotAllowedError."""
    settings = settings or get_settings()
    _syntax_check(path_str)
    roots = allowed_roots(settings)
    if not roots:
        raise PathNotAllowedError(
            "Für Datei-Tools ist noch kein Ordner freigegeben. Trage einen Ordner in ALLOWED_DIRECTORIES in der .env ein."
        )
    candidate = Path(path_str)
    if not candidate.is_absolute():
        # Relative paths resolve against the first allowed folder, never
        # against the backend's working directory.
        candidate = roots[0] / candidate
    resolved = Path(os.path.realpath(candidate))
    if not any(_is_within(resolved, root) for root in roots):
        raise PathNotAllowedError("Dieser Pfad liegt außerhalb der freigegebenen Ordner.")
    check_sensitive(resolved, settings)
    return resolved
