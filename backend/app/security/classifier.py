"""
Deterministic risk classification for PC control (FIX2).

The LLM proposes an action; THIS module decides how risky it is - from
fixed rules, never from the model. Anything it cannot classify with
certainty is `critical`.

    classify_path(path, op)          a file/folder for read/write/delete/execute
    classify_powershell(command)     a PowerShell command line (run_command)
    classify_process(name)           ending a process
    classify_open_target(target)     app name, URI scheme, URL or file to open

Levels (app/tools/base.py): safe < control < write < critical < blocked.
A Verdict also carries `categories` - some of them form the "red line"
(app/security/policy.py) that always asks, even with the safety prompt off:
    format, system_folders, boot, defender_firewall, cypher_secrets
"""
from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Sequence, Tuple

from app.config import PROJECT_ROOT
from app.pc.known_folders import map_first_part
from app.tools.base import RiskTier

SAFE, CONTROL, WRITE, CRITICAL, BLOCKED = RiskTier.SAFE, RiskTier.CONTROL, RiskTier.WRITE, RiskTier.CRITICAL, RiskTier.BLOCKED
RED_LINE_CATEGORIES = ("format", "system_folders", "boot", "defender_firewall", "cypher_secrets")


@dataclass(frozen=True)
class Verdict:
    level: RiskTier
    reasons: Tuple[str, ...] = ()
    categories: frozenset = field(default_factory=frozenset)

    @property
    def reason(self) -> str:
        return "; ".join(dict.fromkeys(self.reasons)) if self.reasons else ""


def verdict(level: RiskTier, reason: str = "", *categories: str) -> Verdict:
    return Verdict(level, (reason,) if reason else (), frozenset(categories))


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    items = list(verdicts)
    if not items:
        return verdict(SAFE)
    level = max((v.level for v in items), key=lambda r: r.rank)
    # Reasons of the deciding level first - they explain the card.
    reasons = [r for v in items if v.level == level for r in v.reasons] + [r for v in items if v.level != level for r in v.reasons]
    return Verdict(level, tuple(dict.fromkeys(reasons)), frozenset().union(*(v.categories for v in items)))


# ================================================================== paths
PathOp = Literal["read", "list", "write", "delete", "execute"]

_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_CREDENTIAL_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker"}
_CREDENTIAL_FILES = (
    ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.kdbx", "*.kdb", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    ".git-credentials", ".netrc", "_netrc", "credentials", "credentials.*", "wallet.dat", "login data*", "cookies", "cookies.sqlite",
    "web data", "logins.json", "key3.db", "key4.db", "cert9.db", "signons.sqlite", "local state",
)
_CREDENTIAL_PATH_PARTS = (
    "\\microsoft\\credentials\\", "\\microsoft\\protect\\", "\\microsoft\\vault\\", "\\microsoft\\crypto\\", "\\microsoft\\systemcertificates\\",
)
_SCRIPT_EXTENSIONS = {".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".reg", ".scr", ".jar", ".py", ".pyw", ".com", ".cpl", ".msc", ".inf"}
_PROGRAM_EXTENSIONS = {".exe", ".lnk", ".appref-ms", ".url"}


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _within(path: str, root: str) -> bool:
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _env_path(var: str) -> Optional[str]:
    value = os.environ.get(var)
    return os.path.realpath(value) if value else None


def user_profile() -> str:
    return os.path.realpath(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def _system_roots() -> List[str]:
    roots = [_env_path(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")]
    drive = os.environ.get("SystemDrive", "C:")
    roots += [os.path.join(drive + os.sep, name) for name in ("System Volume Information", "$Recycle.Bin", "Recovery", "Boot", "EFI", "PerfLogs")]
    return [r for r in roots if r]


def _cypher_secret_paths() -> List[str]:
    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    # configured locations plus the defaults (a moved DATA_DIR must not unprotect the old files)
    paths = [str(PROJECT_ROOT / ".env"), str(settings.data_dir_path / "secrets.json"), str(settings.log_dir_path),
             str(PROJECT_ROOT / "data" / "secrets.json"), str(PROJECT_ROOT / "logs"), str(PROJECT_ROOT / "backend" / "jarvis.db")]
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///") and ":memory:" not in url:
        db = Path(url[len("sqlite:///"):])
        paths.append(str(db if db.is_absolute() else PROJECT_ROOT / "backend" / db))
    return [os.path.realpath(p) for p in paths]


def resolve_path(raw: str, cwd: Optional[str] = None) -> str:
    expanded = os.path.expanduser(os.path.expandvars(raw.strip().strip('"').strip("'")))
    if not os.path.isabs(expanded):
        # "Dokumente/x.txt" -> the real Documents folder (may live in OneDrive).
        known = None if cwd else map_first_part(expanded)
        expanded = known or os.path.join(cwd or user_profile(), expanded)
    return os.path.realpath(expanded)  # follows symlinks and junctions


def _syntax_problem(raw: str) -> Optional[str]:
    if not raw or not raw.strip():
        return "kein Pfad angegeben"
    if "\x00" in raw:
        return "ungültiger Pfad"
    body = raw.strip().strip('"')
    if ":" in body[2:] or re.match(r"^[^a-zA-Z]:", body):
        return "Pfad mit Doppelpunkt (Datenstrom oder ungültig)"
    for part in re.split(r"[\\/]", body):
        if part.split(".")[0].strip().lower() in _DEVICE_NAMES:
            return "Gerätename als Pfad"
    return None


def is_credential_path(resolved: str) -> bool:
    lower = _norm(resolved)
    parts = Path(lower).parts
    if any(part in _CREDENTIAL_DIRS for part in parts):
        return True
    if any(marker in lower + "\\" for marker in _CREDENTIAL_PATH_PARTS):
        return True
    name = Path(lower).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in _CREDENTIAL_FILES)


def is_downloaded(resolved: str) -> bool:
    """Mark of the Web (Zone.Identifier stream) or a download/temp folder."""
    lower = _norm(resolved)
    for folder in (os.path.join(user_profile(), "Downloads"), tempfile.gettempdir()):
        if _within(lower, os.path.realpath(folder)):
            return True
    try:
        with open(resolved + ":Zone.Identifier", "rb"):
            return True  # Windows marks every file that came from the internet
    except OSError:
        return False


def classify_path(raw: str, op: PathOp, *, cwd: Optional[str] = None, is_dir: Optional[bool] = None) -> Verdict:
    problem = _syntax_problem(raw)
    if problem:
        return verdict(BLOCKED, problem)
    if raw.strip().strip('"').startswith(("\\\\", "//")):
        return verdict(CRITICAL, "Netzwerkpfad", "network_path")
    path = resolve_path(raw, cwd)
    if is_dir is None:
        is_dir = os.path.isdir(path)

    # Cypher's own token, config, database and logs.
    for secret in _cypher_secret_paths():
        if _within(path, secret):
            if op in ("read", "list") and not path.lower().endswith((".env", "secrets.json")):
                return verdict(CRITICAL, "liest Cyphers eigene Datenbank/Logs", "cypher_files")
            return verdict(CRITICAL, "Cyphers Token, Konfiguration oder Audit-Log", "cypher_secrets")
    if is_credential_path(path):
        return verdict(CRITICAL, "Zugangs- oder Anmeldedaten (Schlüssel, Passwörter, Cookies, .env)", "credentials")
    if _within(path, str(PROJECT_ROOT)) and op not in ("read", "list"):
        return verdict(CRITICAL, "ändert Cyphers eigene Dateien", "cypher_files")

    if op in ("read", "list"):
        return verdict(SAFE)

    if op == "execute":
        return classify_executable(path)

    in_system = any(_within(path, root) for root in _system_roots())
    drive_root = re.fullmatch(r"[a-z]:\\?", _norm(path)) is not None
    if op == "delete":
        if in_system or drive_root:
            return verdict(CRITICAL, "löscht in einem Systemordner oder ein ganzes Laufwerk", "system_folders", "delete")
        if not _within(path, user_profile()):
            return verdict(CRITICAL, "löscht ausserhalb deines Benutzerordners", "outside_profile", "delete")
        if is_dir or _norm(path) == _norm(user_profile()):
            return verdict(CRITICAL, "löscht einen ganzen Ordner", "folder_delete")
        return verdict(WRITE, "in den Papierkorb")
    # write
    if in_system or drive_root:
        return verdict(CRITICAL, "schreibt in einen Systemordner", "system_write")
    if not _within(path, user_profile()):
        return verdict(CRITICAL, "schreibt ausserhalb deines Benutzerordners", "outside_profile")
    startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    if os.environ.get("APPDATA") and _within(path, os.path.realpath(startup)):
        return verdict(CRITICAL, "Autostart-Ordner", "autostart")
    if is_dir and os.path.exists(path):
        return verdict(CRITICAL, "überschreibt einen ganzen Ordner", "folder_overwrite")
    return verdict(WRITE)


# ================================================================== programs
# Code-execution hosts and admin tools: starting them is never "just an app".
_CRITICAL_PROGRAMS = {
    "cmd": "Kommandozeile", "powershell": "PowerShell", "pwsh": "PowerShell", "wscript": "Skript-Host", "cscript": "Skript-Host",
    "mshta": "Skript-Host", "rundll32": "führt DLL-Code aus", "regsvr32": "registriert DLLs", "regedit": "Registry-Editor",
    "reg": "Registry", "sc": "Dienste", "schtasks": "geplante Aufgaben", "netsh": "Netzwerk/Firewall", "bcdedit": "Boot-Konfiguration",
    "bcdboot": "Boot-Konfiguration", "bootrec": "Boot-Konfiguration", "diskpart": "Partitionen", "format": "Formatieren",
    "cipher": "überschreibt freien Speicher/verschlüsselt", "takeown": "Besitzrechte", "icacls": "Berechtigungen", "cacls": "Berechtigungen",
    "wmic": "Systemverwaltung", "vssadmin": "Schattenkopien", "wbadmin": "Sicherungen", "shutdown": "Herunterfahren/Neustart",
    "msiexec": "Installation", "winget": "Software installieren", "choco": "Software installieren", "certutil": "Zertifikate/Download",
    "bitsadmin": "Download", "net": "Benutzer/Netzwerk", "net1": "Benutzer/Netzwerk", "sfc": "Systemdateien", "dism": "Windows-Features",
    "manage-bde": "BitLocker", "fsutil": "Dateisystem", "gpedit": "Gruppenrichtlinien", "secpol": "Sicherheitsrichtlinien",
    "mountvol": "Laufwerke", "pnputil": "Treiber", "bcdedit.exe": "Boot-Konfiguration", "attrib": "Dateiattribute", "setx": "Umgebungsvariablen",
    "runas": "Rechteausweitung", "psexec": "Fernausführung", "python": "führt Code aus", "pythonw": "führt Code aus", "node": "führt Code aus",
    "java": "führt Code aus", "javaw": "führt Code aus",
}
_RED_LINE_PROGRAMS = {"format": "format", "diskpart": "format", "mountvol": "format", "bcdedit": "boot", "bcdboot": "boot", "bootrec": "boot", "manage-bde": "boot"}
# Everyday Windows apps that only open a window.
_SAFE_PROGRAMS = {
    "notepad", "calc", "calculator", "mspaint", "explorer", "write", "wordpad", "charmap", "snippingtool", "taskmgr", "magnify", "osk",
    "control", "msinfo32", "resmon", "perfmon", "mstsc", "devmgmt", "services", "eventvwr", "compmgmt", "ms-settings", "winver",
    "dxdiag", "clipchamp", "photos", "spotify", "discord", "chrome", "msedge", "firefox", "code", "steam", "vlc", "obs64", "teams",
}


def _program_key(name_or_path: str) -> str:
    base = Path(name_or_path.strip().strip('"').strip("'")).name.lower()
    for ext in (".exe", ".com", ".msc", ".cpl", ".lnk"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    return base


def classify_program_name(name: str) -> Verdict:
    key = _program_key(name)
    if key in _CRITICAL_PROGRAMS:
        cats = ("program_" + key,) + ((_RED_LINE_PROGRAMS[key],) if key in _RED_LINE_PROGRAMS else ())
        return verdict(CRITICAL, f"{key}: {_CRITICAL_PROGRAMS[key]}", *cats)
    if key in _SAFE_PROGRAMS:
        return verdict(CONTROL)
    return verdict(CRITICAL, f"unbekanntes Programm '{key}'", "unknown_program")


def classify_executable(path: str) -> Verdict:
    """Opening/starting a file that exists (or should) at `path`."""
    lower = _norm(path)
    ext = Path(lower).suffix
    key = _program_key(path)
    if is_downloaded(path) and (ext in _SCRIPT_EXTENSIONS or ext in _PROGRAM_EXTENSIONS or ext == ""):
        return verdict(CRITICAL, "Programm oder Skript aus dem Internet/Downloads/Temp", "downloaded")
    if ext in _SCRIPT_EXTENSIONS:
        return verdict(CRITICAL, f"führt ein Skript/Installationspaket aus ({ext})", "script")
    if key in _CRITICAL_PROGRAMS:
        return classify_program_name(key)
    if ext in _PROGRAM_EXTENSIONS:
        trusted = [r for r in (_env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"), _env_path("SystemRoot")) if r]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            trusted += [os.path.join(local, "Programs"), os.path.join(local, "Microsoft", "WindowsApps"), os.path.join(local, "Discord"), os.path.join(local, "Spotify")]
        start_menus = [os.path.join(os.environ.get(v, ""), "Microsoft", "Windows", "Start Menu") for v in ("APPDATA", "ProgramData")]
        if any(_within(lower, os.path.realpath(r)) for r in trusted + start_menus if r):
            return verdict(CONTROL)
        return verdict(CRITICAL, "Programm ausserhalb der Installationsordner", "unknown_program")
    return verdict(CONTROL)  # a document, picture, folder: opens in its app


def classify_start_menu_app(target: str, name: str = "") -> Verdict:
    """An app from the Start menu index (app/pc/app_index.py). Being in the
    Start menu means "installed", so its folder does not matter - but code
    hosts and admin tools (PowerShell, cmd, regedit ...) stay critical."""
    for candidate in (target, name):
        if candidate and _program_key(candidate) in _CRITICAL_PROGRAMS:
            return classify_program_name(candidate)
    if target:
        if Path(_norm(target)).suffix in _SCRIPT_EXTENSIONS:
            return verdict(CRITICAL, "startet ein Skript", "script")
        if os.path.isabs(target) and is_downloaded(target):
            return verdict(CRITICAL, "Programm aus dem Internet/Downloads/Temp", "downloaded")
    return verdict(CONTROL)


# ================================================================== processes
_SYSTEM_PROCESSES = {
    "system", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass", "lsaiso", "svchost", "dwm", "fontdrvhost",
    "sihost", "spoolsv", "taskhostw", "audiodg", "explorer", "conhost", "runtimebroker", "searchindexer", "ctfmon", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "dllhost", "wudfhost", "memory compression", "secure system", "idle",
    # security
    "msmpeng", "nissrv", "securityhealthservice", "securityhealthsystray", "mssense", "sgrmbroker", "smartscreen", "mpdefendercoreservice",
    # Cypher's own infrastructure
    "ollama", "ollama app",
}


def classify_process(name: str, pid: Optional[int] = None) -> Verdict:
    key = name.lower().removesuffix(".exe").strip()
    if not key:
        return verdict(CRITICAL, "Prozess nicht eindeutig", "process")
    if pid is not None and pid in (os.getpid(), os.getppid()):
        return verdict(CRITICAL, "das ist Cypher selbst", "cypher_files", "process")
    if key in _SYSTEM_PROCESSES:
        return verdict(CRITICAL, f"System-, Sicherheits- oder Cypher-Prozess ({key})", "system_process")
    return verdict(WRITE, "ungesicherte Daten in der App können verloren gehen")


# ================================================================== open targets
_SAFE_URI_SCHEMES = {
    "ms-settings", "spotify", "discord", "steam", "mailto", "ms-windows-store", "msteams", "zoommtg", "ms-clock", "calculator",
    "microsoft-edge", "ms-photos", "ms-screenclip", "ms-paint", "whatsapp", "obsidian", "vscode", "ms-word", "ms-excel", "ms-powerpoint",
}


def classify_open_target(target: str) -> Verdict:
    text = target.strip()
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(.*)$", text)
    if match and len(match.group(1)) > 1:  # not a drive letter
        scheme = match.group(1).lower()
        if scheme in ("http", "https"):
            if re.match(r"^https?://(localhost|127\.|\[::1\]|0\.0\.0\.0)", text, re.IGNORECASE):
                return verdict(CRITICAL, "Adresse auf diesem PC (z. B. Cyphers eigene API)", "cypher_secrets")
            return verdict(WRITE, "öffnet eine Webseite (die Adresse geht ins Netz)")
        if scheme == "file":
            return classify_path(re.sub(r"^file:/*", "", text), "execute")
        if scheme == "steam" and re.search(r"uninstall|remove", text, re.IGNORECASE):
            return verdict(CRITICAL, "deinstalliert über Steam", "software")
        if scheme in _SAFE_URI_SCHEMES:
            return verdict(CONTROL)
        return verdict(CRITICAL, f"unbekanntes URI-Schema '{scheme}:'", "unknown_uri")
    if re.search(r"[\\/]", text) or re.match(r"^[a-zA-Z]:", text):
        return classify_path(text, "execute")
    return verdict(CONTROL)  # an app name from the Start menu; resolved by the tool (app_index.py)


# ================================================================== PowerShell
class _Unparsable(Exception):
    pass


_ALIASES = {
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem", "cat": "get-content", "type": "get-content", "gc": "get-content",
    "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item", "mv": "move-item", "move": "move-item", "mi": "move-item", "ren": "rename-item",
    "rni": "rename-item", "rm": "remove-item", "del": "remove-item", "erase": "remove-item", "ri": "remove-item", "rd": "remove-item",
    "rmdir": "remove-item", "md": "new-item", "mkdir": "new-item", "ni": "new-item", "echo": "write-output", "write": "write-output",
    "sl": "set-location", "cd": "set-location", "chdir": "set-location", "pwd": "get-location", "gl": "get-location", "ps": "get-process",
    "gps": "get-process", "kill": "stop-process", "spps": "stop-process", "saps": "start-process", "start": "start-process",
    "iwr": "invoke-webrequest", "curl": "invoke-webrequest", "wget": "invoke-webrequest", "irm": "invoke-restmethod",
    "iex": "invoke-expression", "icm": "invoke-command", "sc": "set-content", "ac": "add-content", "sv": "set-variable",
    "gv": "get-variable", "select": "select-object", "where": "where-object", "?": "where-object", "%": "foreach-object",
    "foreach": "foreach-object", "sort": "sort-object", "measure": "measure-object", "ft": "format-table", "fl": "format-list",
    "fw": "format-wide", "gi": "get-item", "gp": "get-itemproperty", "sp": "set-itemproperty", "si": "set-item", "clc": "clear-content",
    "cls": "clear-host", "clear": "clear-host", "gsv": "get-service", "sasv": "start-service", "spsv": "stop-service", "gal": "get-alias",
    "gcm": "get-command", "man": "get-help", "help": "get-help", "tee": "tee-object", "group": "group-object", "sls": "select-string",
    "gdr": "get-psdrive", "h": "get-history", "history": "get-history", "r": "invoke-history", "pushd": "push-location", "popd": "pop-location",
    "nal": "new-alias", "sal": "set-alias", "epal": "export-alias", "ipal": "import-alias", "ipmo": "import-module", "rv": "remove-variable",
    "ii": "invoke-item", "sleep": "start-sleep", "nv": "new-variable", "scb": "set-clipboard", "gcb": "get-clipboard",
}

_SAFE_CMDLETS = {
    "get-childitem", "get-location", "get-date", "get-process", "get-service", "test-path", "resolve-path", "split-path", "join-path",
    "measure-object", "select-object", "where-object", "sort-object", "group-object", "format-table", "format-list", "format-wide",
    "out-string", "out-null", "out-host", "write-output", "write-host", "write-verbose", "write-information", "get-help", "get-command",
    "get-alias", "get-member", "get-psdrive", "get-volume", "get-disk", "get-partition", "get-netadapter", "get-netipaddress",
    "get-netipconfiguration", "get-dnsclientserveraddress", "get-netconnectionprofile", "test-connection", "test-netconnection",
    "get-computerinfo", "get-ciminstance", "get-wmiobject", "get-hotfix", "get-filehash", "select-string", "convertto-json",
    "convertfrom-json", "convertto-csv", "convertfrom-csv", "convertto-html", "get-uptime", "get-timezone", "get-culture", "get-host",
    "get-variable", "get-history", "set-location", "push-location", "pop-location", "start-sleep", "get-random", "get-unique",
    "compare-object", "get-clipboard", "get-scheduledtask", "get-scheduledtaskinfo", "get-appxpackage", "get-package", "get-module",
    "get-netfirewallrule", "get-netfirewallprofile", "get-mppreference", "get-mpcomputerstatus", "get-printer", "get-pnpdevice",
    "get-eventlog", "get-winevent", "get-localuser", "get-localgroup", "get-executionpolicy", "get-acl", "foreach-object", "clear-host",
    "new-timespan", "get-itemproperty", "get-item", "get-content", "measure-command", "get-counter", "get-startapps",
}
_CONTROL_CMDLETS = {"set-clipboard", "invoke-item"}
_PATH_WRITE_CMDLETS = {
    "new-item", "set-content", "add-content", "out-file", "copy-item", "move-item", "rename-item", "clear-content", "export-csv",
    "export-clixml", "tee-object", "compress-archive", "expand-archive", "set-item", "new-itemproperty", "set-itemproperty",
    "remove-itemproperty", "rename-itemproperty", "clear-item", "clear-itemproperty",
}
_CRITICAL_CMDLETS = {
    # code execution / obfuscation
    "invoke-expression": ("führt Text als Code aus", "code_exec"), "invoke-command": ("führt Code aus", "code_exec"),
    "start-job": ("Hintergrund-Code", "code_exec"), "add-type": ("kompiliert Code", "code_exec"), "new-alias": ("Alias", "code_exec"),
    "set-alias": ("Alias", "code_exec"), "import-module": ("lädt Code", "code_exec"), "invoke-history": ("wiederholt Befehle", "code_exec"),
    "set-variable": ("Variable", "code_exec"), "new-variable": ("Variable", "code_exec"), "set-executionpolicy": ("Ausführungsrichtlinie", "settings"),
    # services, tasks, autostart
    "start-service": ("Dienst", "services"), "stop-service": ("Dienst", "services"), "restart-service": ("Dienst", "services"),
    "set-service": ("Dienst", "services"), "new-service": ("Dienst", "services"), "remove-service": ("Dienst", "services"),
    "suspend-service": ("Dienst", "services"), "register-scheduledtask": ("geplante Aufgabe", "autostart"),
    "unregister-scheduledtask": ("geplante Aufgabe", "autostart"), "set-scheduledtask": ("geplante Aufgabe", "autostart"),
    "enable-scheduledtask": ("geplante Aufgabe", "autostart"), "disable-scheduledtask": ("geplante Aufgabe", "autostart"),
    "start-scheduledtask": ("geplante Aufgabe", "autostart"),
    # software, drivers, features
    "install-package": ("Software", "software"), "uninstall-package": ("Software", "software"), "install-module": ("Software", "software"),
    "uninstall-module": ("Software", "software"), "add-appxpackage": ("Software", "software"), "remove-appxpackage": ("Software", "software"),
    "enable-windowsoptionalfeature": ("Windows-Feature", "software"), "disable-windowsoptionalfeature": ("Windows-Feature", "software"),
    "install-windowsfeature": ("Windows-Feature", "software"), "update-help": ("lädt aus dem Netz", "network"),
    # power
    "stop-computer": ("Herunterfahren", "power"), "restart-computer": ("Neustart", "power"), "suspend-bitlocker": ("BitLocker", "boot"),
    # network / firewall / defender
    "set-mppreference": ("Defender-Einstellung", "defender_firewall"), "add-mppreference": ("Defender-Ausnahme", "defender_firewall"),
    "remove-mppreference": ("Defender-Einstellung", "defender_firewall"), "set-netfirewallprofile": ("Firewall", "defender_firewall"),
    "new-netfirewallrule": ("Firewall", "defender_firewall"), "set-netfirewallrule": ("Firewall", "defender_firewall"),
    "remove-netfirewallrule": ("Firewall", "defender_firewall"), "disable-netfirewallrule": ("Firewall", "defender_firewall"),
    "enable-netfirewallrule": ("Firewall", "defender_firewall"), "set-netipaddress": ("Netzwerk", "network_config"),
    "new-netipaddress": ("Netzwerk", "network_config"), "remove-netipaddress": ("Netzwerk", "network_config"),
    "set-dnsclientserveraddress": ("DNS", "network_config"), "disable-netadapter": ("Netzwerk", "network_config"),
    "enable-netadapter": ("Netzwerk", "network_config"), "set-netconnectionprofile": ("Netzwerk", "network_config"),
    "restart-netadapter": ("Netzwerk", "network_config"),
    # disks
    "format-volume": ("Formatieren", "format"), "clear-disk": ("Datenträger löschen", "format"), "initialize-disk": ("Datenträger", "format"),
    "new-partition": ("Partition", "format"), "remove-partition": ("Partition", "format"), "resize-partition": ("Partition", "format"),
    "set-partition": ("Partition", "format"),
    # messages, accounts, permissions, processes
    "send-mailmessage": ("sendet Mail", "messages"), "new-localuser": ("Benutzerkonto", "accounts"), "set-localuser": ("Benutzerkonto", "accounts"),
    "remove-localuser": ("Benutzerkonto", "accounts"), "add-localgroupmember": ("Benutzerrechte", "accounts"), "set-acl": ("Berechtigungen", "settings"),
    "set-date": ("Systemzeit", "settings"), "invoke-wmimethod": ("Systemverwaltung", "settings"), "invoke-cimmethod": ("Systemverwaltung", "settings"),
    "set-ciminstance": ("Systemverwaltung", "settings"), "remove-ciminstance": ("Systemverwaltung", "settings"), "set-timezone": ("Zeitzone", "settings"),
    "start-bitstransfer": ("Download", "network"), "remove-item": ("löscht endgültig (ohne Papierkorb)", "permanent_delete"),
}
_NETWORK_CMDLETS = {"invoke-webrequest", "invoke-restmethod"}
_SAFE_NATIVE = {
    "ipconfig", "ping", "tracert", "pathping", "nslookup", "whoami", "hostname", "systeminfo", "tasklist", "where", "driverquery", "getmac",
    "netstat", "arp", "ver", "tree", "findstr", "fc", "sort", "more", "chcp", "query", "quser", "nvidia-smi", "git",
}
_STATEMENT_SPLIT = re.compile(r"(;|\|\||&&|\||\n)")


def _strip_comments(text: str) -> str:
    text = re.sub(r"<#.*?#>", " ", text, flags=re.DOTALL)
    out, quote = [], None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or text[i - 1] in " \t\n;|"):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_top(text: str) -> List[Tuple[str, str]]:
    """Split into (separator_before, segment) at ; | && || newlines outside
    quotes, (), {} and $()."""
    segments: List[Tuple[str, str]] = []
    depth, quote, buf, sep = 0, None, [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "`" and quote == '"' and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "({[":
            depth += 1
            buf.append(ch)
        elif ch in ")}]":
            depth -= 1
            if depth < 0:
                raise _Unparsable("Klammern stimmen nicht")
            buf.append(ch)
        elif depth == 0 and (ch in ";|\n" or text.startswith("&&", i)):
            token = "&&" if text.startswith("&&", i) else ("||" if text.startswith("||", i) else ch)
            segments.append((sep, "".join(buf)))
            buf, sep = [], token
            i += len(token)
            continue
        else:
            buf.append(ch)
        i += 1
    if quote or depth:
        raise _Unparsable("Anführungszeichen oder Klammern nicht geschlossen")
    segments.append((sep, "".join(buf)))
    return [(s, seg.strip()) for s, seg in segments if seg.strip()]


def _tokens(segment: str) -> List[str]:
    tokens, buf, quote, depth = [], [], None, 0
    i = 0
    while i < len(segment):
        ch = segment[i]
        if quote:
            buf.append(ch)
            if ch == "`" and quote == '"' and i + 1 < len(segment):
                buf.append(segment[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "({[":
            depth += 1
            buf.append(ch)
        elif ch in ")}]":
            depth -= 1
            buf.append(ch)
        elif ch.isspace() and depth == 0:
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        tokens.append("".join(buf))
    return tokens


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


_SAFE_METHODS = {"tostring", "toupper", "tolower", "trim", "trimstart", "trimend", "split", "replace", "substring", "contains", "startswith",
                 "endswith", "gettype", "equals", "indexof", "padleft", "padright", "tostring", "join", "format", "isnullorempty", "round",
                 "floor", "ceiling", "abs", "max", "min", "now", "today", "getfilename", "getextension", "getdirectoryname", "count"}


def _inner_blocks(token: str) -> List[str]:
    """Contents of $( ), { } and @( ) inside a token - classified as commands too."""
    blocks, stack, start = [], [], {}
    for i, ch in enumerate(token):
        if ch in "({":
            stack.append(i)
        elif ch in ")}" and stack:
            j = stack.pop()
            if not stack:
                blocks.append(token[j + 1 : i])
    return blocks


def _expression_verdict(text: str) -> Verdict:
    """Something that is not a command: a literal, variable, comparison or method call."""
    for method in re.findall(r"\.([A-Za-z_]\w*)\s*\(", text):
        if method.lower() not in _SAFE_METHODS:
            return verdict(CRITICAL, f"Methodenaufruf .{method}()", "code_exec")
    for type_name, _member in re.findall(r"\[([\w.]+)\]::(\w+)", text):
        if type_name.lower() not in {"math", "system.math", "string", "system.string", "datetime", "system.datetime", "char", "int", "double", "system.io.path", "io.path", "timespan", "guid"}:
            return verdict(CRITICAL, f"[{type_name}]-Aufruf", "code_exec")
    if re.search(r"\$env:\w+\s*=", text):
        return verdict(CRITICAL, "ändert Umgebungsvariablen", "settings")
    return verdict(SAFE)


def _param_values(args: Sequence[str], names: Iterable[str]) -> List[str]:
    wanted = {n.lower() for n in names}
    values = []
    for i, arg in enumerate(args):
        if arg.startswith("-") and arg[1:].lower().rstrip(":") in wanted and i + 1 < len(args):
            values.append(args[i + 1])
    return values


def _positional(args: Sequence[str], *, keep_param_values: bool = False) -> List[str]:
    """Positional arguments. keep_param_values: an unknown parameter may be a
    switch, so the token after it may be the real target - keep it too
    ("rm -rf /": is "/" the value of -rf or the path? Assume the worst)."""
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
            if keep_param_values:
                out.append(arg)
            continue
        if arg.startswith("-") and len(arg) > 1 and not re.match(r"^-\d", arg):
            # switch or named parameter; named ones take the next token
            skip = not arg.lower().lstrip("-") in {"recurse", "force", "whatif", "confirm", "verbose", "file", "directory", "hidden",
                                                   "passthru", "nonewline", "raw", "append", "noclobber", "wait", "nonewwindow", "usebasicparsing",
                                                   "parallel"}
            continue
        out.append(arg)
    return out


def _path_targets(cmd: str, args: Sequence[str]) -> List[str]:
    named = _param_values(args, ("path", "literalpath", "destination", "filepath", "outfile", "newname", "target", "pspath"))
    return named or _positional(args, keep_param_values=True)


def _expand_known_variables(value: str) -> str:
    """$env:USERPROFILE, ${env:APPDATA}, $HOME -> their values (only these)."""
    value = re.sub(r"\$\{env:(\w+)\}|\$env:(\w+)", lambda m: os.environ.get(m.group(1) or m.group(2), m.group(0)), value, flags=re.IGNORECASE)
    return re.sub(r"\$home\b", lambda _m: user_profile(), value, flags=re.IGNORECASE)


def _classify_paths(paths: Iterable[str], op: PathOp, cwd: Optional[str]) -> Verdict:
    results = []
    for raw in paths:
        value = _expand_known_variables(_unquote(raw))
        if not value or "$" in value or "`" in value or "*" in value and op != "read":
            results.append(_unknown_target(op, f"Pfad nicht sicher bestimmbar ({value or 'leer'})"))
            continue
        if re.match(r"^(hk(lm|cu|cr|u|cc)|registry)::?", value, re.IGNORECASE) or value.lower().startswith(("hklm:", "hkcu:")):
            if op in ("read", "list"):
                results.append(verdict(SAFE))
            else:
                results.append(verdict(CRITICAL, "Registry-Änderung", "registry"))
            continue
        if value.lower().startswith(("env:", "variable:", "function:", "alias:", "cert:", "wsman:")):
            results.append(verdict(SAFE) if op in ("read", "list") else verdict(CRITICAL, "ändert Umgebung, Variablen oder Zertifikate", "settings"))
            continue
        results.append(classify_path(value, op, cwd=cwd))
    return worst(results) if results else _unknown_target(op, "Ziel fehlt")


def _unknown_target(op: PathOp, reason: str) -> Verdict:
    # A delete whose target is unknown could hit a system folder: red line,
    # so it asks even with the safety prompt switched off.
    if op == "delete":
        return verdict(CRITICAL, reason + " - könnte ein Systemordner sein", "unclassifiable", "system_folders")
    return verdict(CRITICAL, reason, "unclassifiable")


def _classify_simple(segment: str, cwd: Optional[str], depth: int) -> Verdict:
    seg = segment.strip()
    if not seg:
        return verdict(SAFE)
    # output redirection to a file
    redirects: List[Verdict] = []
    for target in re.findall(r"(?<![<>&\d*])\d?\*?>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;|]+)", seg):
        if _unquote(target).lower() not in ("$null", "null", "&1", "&2"):
            redirects.append(_classify_paths([target], "write", cwd))
    seg = re.sub(r"\d?\*?>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;|]+)", " ", seg).strip()
    tokens = _tokens(seg)
    if not tokens:
        return worst(redirects)
    first = tokens[0]
    inner = [_classify(block, cwd, depth + 1) for t in tokens for block in _inner_blocks(t)]

    # assignment / expression statements
    if first.startswith("$") or first.startswith("[") or first.startswith("(") or first.startswith("@") or re.match(r"^-?\d", first) or first[0] in "'\"":
        if "=" in tokens[1:2] or re.match(r"^\$[\w:]+=", first):
            rhs = seg.split("=", 1)[1]
            return worst([_classify(rhs, cwd, depth + 1), _expression_verdict(seg), *redirects])
        if first.startswith(("'", '"')) and len(tokens) == 1:
            return worst([verdict(SAFE), *inner, *redirects])
        return worst([_expression_verdict(seg), *inner, *redirects])
    if first in ("&", "."):
        if len(tokens) < 2 or tokens[1].startswith(("$", "(", "{")):
            return verdict(CRITICAL, "Aufruf über Variable oder Skriptblock", "code_exec")
        target = _unquote(tokens[1])
        return worst([classify_path(target, "execute", cwd=cwd), *inner, *redirects])
    if "`" in first or "$" in first or "(" in first:
        return verdict(CRITICAL, "verschleierter Befehlsname", "code_exec")

    name = _unquote(first)
    lower = name.lower()
    cmd = _ALIASES.get(lower, lower)
    args = tokens[1:]
    joined = " ".join(args).lower()
    key = _program_key(name)

    # PowerShell's own -EncodedCommand / encoded payloads, anywhere
    if re.search(r"(^|\s)-e(nc(odedcommand)?|c)?\s", " " + joined + " ") and key in ("powershell", "pwsh"):
        return verdict(CRITICAL, "verschlüsselter Befehl (-EncodedCommand)", "code_exec")
    if re.search(r"frombase64string|downloadstring|downloadfile|net\.webclient|invoke-expression|\biex\b|scriptblock\]::create", joined):
        return verdict(CRITICAL, "lädt oder baut Code zur Laufzeit", "code_exec")

    if cmd in _CRITICAL_CMDLETS:
        reason, category = _CRITICAL_CMDLETS[cmd]
        extra = [verdict(CRITICAL, reason, category)]
        if cmd == "remove-item":
            extra.append(_classify_paths(_path_targets(cmd, args), "delete", cwd))
            if re.search(r"hk(lm|cu|cr|u)|registry::", joined):
                extra.append(verdict(CRITICAL, "Registry-Änderung", "registry"))
        if category == "defender_firewall" or category == "format":
            extra.append(verdict(CRITICAL, reason, category))
        return worst([*extra, *inner, *redirects])
    if cmd in _SAFE_CMDLETS:
        checks = [verdict(SAFE), *inner, *redirects]
        if cmd in ("get-content", "select-string", "get-filehash", "get-item", "get-itemproperty", "get-childitem", "get-acl"):
            targets = _path_targets(cmd, args)
            op: PathOp = "list" if cmd in ("get-childitem", "get-item", "get-itemproperty", "get-acl") else "read"
            if targets:
                checks.append(_classify_paths(targets, op, cwd))
        if cmd in ("get-ciminstance", "get-wmiobject") and re.search(r"-(method|query)\b.*\b(delete|create|set)", joined):
            checks.append(verdict(CRITICAL, "Systemverwaltung", "settings"))
        return worst(checks)
    if cmd in _CONTROL_CMDLETS:
        checks = [verdict(CONTROL), *inner, *redirects]
        if cmd == "invoke-item":
            checks.append(_classify_paths(_path_targets(cmd, args), "execute", cwd))
        return worst(checks)
    if cmd in _PATH_WRITE_CMDLETS:
        targets = _path_targets(cmd, args)
        if cmd in ("copy-item", "move-item") and len(targets) >= 1:
            positional = _positional(args)
            sources = _param_values(args, ("path", "literalpath")) or positional[:1]
            dests = _param_values(args, ("destination",)) or positional[1:2]
            checks = [_classify_paths(dests or ["$unknown"], "write", cwd)]
            if cmd == "move-item":
                checks.append(_classify_paths(sources, "delete", cwd))
            else:
                checks.append(_classify_paths(sources, "read", cwd))
        elif cmd == "rename-item":
            checks = [_classify_paths(targets[:1], "write", cwd)]
        elif "itemproperty" in cmd or cmd in ("set-item", "clear-item"):
            checks = [_classify_paths(targets[:1] or ["$unknown"], "write", cwd)]
        else:
            checks = [_classify_paths(targets[:1] or ["$unknown"], "write", cwd)]
        return worst([*checks, *inner, *redirects])
    if cmd in _NETWORK_CMDLETS:
        checks = [verdict(WRITE, "Netzwerkzugriff (Daten können den PC verlassen)", "network")]
        if re.search(r"-method\s+(post|put|delete|patch)|-body\b|-infile\b", joined):
            checks.append(verdict(CRITICAL, "sendet Daten ins Internet", "messages"))
        if re.search(r"(localhost|127\.0\.0\.1|\[::1\])", joined):
            checks.append(verdict(CRITICAL, "spricht Cyphers eigene API an", "cypher_secrets"))
        outfile = _param_values(args, ("outfile",))
        if outfile:
            checks.append(_classify_paths(outfile, "write", cwd))
        return worst([*checks, *inner, *redirects])
    if cmd in ("stop-process",):
        names = _param_values(args, ("name", "processname")) or [a for a in _positional(args) if not a.isdigit()]
        if _param_values(args, ("id",)) or any(a.isdigit() for a in _positional(args)) or not names:
            return verdict(CRITICAL, "Prozess über Nummer - nicht eindeutig bestimmbar", "process")
        return worst([classify_process(_unquote(n)) for n in ",".join(names).split(",")] + inner + redirects)
    if cmd == "start-process":
        file_path = _param_values(args, ("filepath",)) or _positional(args)[:1]
        if re.search(r"-verb\s+['\"]?runas", joined):
            return verdict(BLOCKED, "Cypher erhöht sich nie selbst (Als Administrator ausführen)")
        if not file_path:
            return verdict(CRITICAL, "Programm fehlt", "unclassifiable")
        target = _unquote(file_path[0])
        checks = [classify_open_target(target) if not re.search(r"[\\/]", target) else classify_path(target, "execute", cwd=cwd)]
        if not re.search(r"[\\/:]", target):
            checks.append(classify_program_name(target))
        if _param_values(args, ("argumentlist", "args")):
            checks.append(worst([classify_program_name(target)]) if _program_key(target) in _CRITICAL_PROGRAMS else verdict(CONTROL))
        return worst([*checks, *inner, *redirects])

    # native programs
    if key in ("winget",):
        sub = args[0].lower() if args else ""
        if sub in ("list", "search", "show", "--version", "-v", "--info"):
            return worst([verdict(SAFE), *redirects])
        return verdict(CRITICAL, "Software installieren/ändern (winget)", "software")
    if key == "reg":
        sub = args[0].lower() if args else ""
        return verdict(SAFE) if sub in ("query",) else verdict(CRITICAL, "Registry-Änderung (reg)", "registry")
    if key == "schtasks":
        return verdict(SAFE) if re.search(r"/query", joined) and not re.search(r"/(create|delete|change|run|end)", joined) else verdict(CRITICAL, "geplante Aufgabe", "autostart")
    if key == "sc":
        return verdict(SAFE) if args and args[0].lower() in ("query", "queryex", "qc", "qdescription") else verdict(CRITICAL, "Dienst (sc)", "services")
    if key == "ipconfig" and re.search(r"/(release|renew|flushdns|registerdns|setclassid)", joined):
        return verdict(CRITICAL, "Netzwerkkonfiguration", "network_config")
    if key == "netstat" or (key == "git" and args and args[0].lower() in ("status", "log", "diff", "branch", "show", "remote", "rev-parse")):
        return worst([verdict(SAFE), *redirects])
    if key == "git":
        return verdict(CRITICAL, "Git-Befehl, der etwas ändert", "git")
    if key == "taskkill":
        names = re.findall(r"/im\s+(\"[^\"]+\"|\S+)", joined)
        if re.search(r"/pid\b|/fi\b", joined) or not names:
            return verdict(CRITICAL, "Prozess über Nummer/Filter - nicht eindeutig bestimmbar", "process")
        return worst([classify_process(_unquote(n)) for n in names] + redirects)
    if key in _SAFE_NATIVE:
        return worst([verdict(SAFE), *redirects])
    if key in _SAFE_PROGRAMS:
        # an everyday app, maybe with a file to open
        files = [a for a in _positional(args)]
        return worst([verdict(CONTROL), *([_classify_paths(files, "read", cwd)] if files else []), *redirects])
    if key == "netsh" and "firewall" in joined:
        return verdict(CRITICAL, "Firewall (netsh)", "defender_firewall", "program_netsh")
    if key in _CRITICAL_PROGRAMS:
        return classify_program_name(key)
    if re.search(r"[\\/]", name):
        return worst([classify_path(name, "execute", cwd=cwd), *redirects])
    return verdict(CRITICAL, f"unbekannter Befehl '{name}'", "unknown_command")


def _classify(text: str, cwd: Optional[str], depth: int = 0) -> Verdict:
    if depth > 6:
        raise _Unparsable("zu tief verschachtelt")
    segments = _split_top(text)
    return worst(_classify_simple(segment, cwd, depth) for _sep, segment in segments)


def classify_powershell(command: str, cwd: Optional[str] = None) -> Verdict:
    """Verdict for a whole PowerShell command line (as run_command gets it)."""
    if not command or not command.strip():
        return verdict(BLOCKED, "leerer Befehl")
    if len(command) > 4000:
        return verdict(CRITICAL, "sehr langer Befehl - nicht sicher einstufbar", "unclassifiable")
    if re.search(r"@['\"]\s*\n", command):
        return verdict(CRITICAL, "Here-String - nicht sicher einstufbar", "unclassifiable")
    if re.search(r"[​-‏‪-‮⁠﻿]", command):
        return verdict(CRITICAL, "unsichtbare Steuerzeichen", "unclassifiable")
    try:
        result = _classify(_strip_comments(command), cwd)
    except _Unparsable as exc:
        return verdict(CRITICAL, f"nicht sicher einstufbar ({exc})", "unclassifiable")
    # Mentions of Cypher's own token/config/audit anywhere -> red line.
    lowered = command.lower().replace("/", "\\")
    if result.level.rank >= WRITE.rank and any(marker in lowered for marker in ("secrets.json", "jarvis.db", "cypher.db", "\\cypher\\.env", "cypher.log", "tool_audit", "runtime_settings")):
        result = worst([result, verdict(CRITICAL, "Cyphers Token, Konfiguration oder Audit-Log", "cypher_secrets")])
    return result
