"""
Cypher-Doktor: prüft die ganze Installation und sagt auf Deutsch, was fehlt.

    doctor.bat                      (Projekt-Root)
    python -m app.ops.doctor        (backend/, venv aktiv)
    python -m app.ops.doctor --json

Only reads - nothing is installed, started or changed. Exit code 0 = no
errors (warnings are fine), 1 = at least one error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional

OK, WARN, ERROR, INFO = "ok", "warn", "error", "info"
_SYMBOL = {OK: "✓", WARN: "!", ERROR: "✗", INFO: "·"}


@dataclass
class Check:
    area: str
    status: str
    message: str
    hint: str = ""


def _run(cmd: List[str], timeout: float = 8.0) -> Optional[str]:
    """Run a read-only command, return stdout (or None if it isn't available)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


# ------------------------------------------------------------------ checks
def check_python() -> List[Check]:
    checks = []
    version = sys.version_info
    if version < (3, 11):
        checks.append(Check("Python", ERROR, f"Python {platform.python_version()} ist zu alt.", "Python 3.12 installieren (python.org), dann setup.bat."))
    else:
        checks.append(Check("Python", OK, f"Python {platform.python_version()}"))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(Check("Python", OK if in_venv else WARN, "Virtuelle Umgebung aktiv" if in_venv else "Läuft nicht in backend\\.venv", "" if in_venv else "Über doctor.bat starten oder backend\\.venv\\Scripts\\activate."))
    missing = []
    for module in ("fastapi", "uvicorn", "pydantic_settings", "sqlalchemy", "httpx", "faster_whisper", "sherpa_onnx", "numpy", "openai"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if sys.platform == "win32":
        for module in ("pycaw", "winrt.windows.media.control"):
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
    checks.append(
        Check("Python", ERROR if missing else OK, f"Fehlende Pakete: {', '.join(missing)}" if missing else "Alle Python-Pakete installiert", "setup.bat ausführen (installiert backend/requirements.txt)." if missing else "")
    )
    return checks


def check_config():
    from app.config import ConfigError, PROJECT_ROOT, get_settings  # noqa: PLC0415

    checks = []
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        checks.append(Check("Konfiguration", WARN, ".env fehlt - es gelten die Standardwerte.", "setup.bat legt sie aus .env.example an."))
    try:
        settings = get_settings()
    except ConfigError as exc:
        checks.append(Check("Konfiguration", ERROR, "Die .env enthält ungültige Werte.", str(exc).strip().splitlines()[-1][:300]))
        return checks, None
    checks.append(Check("Konfiguration", OK, f".env gültig (Modell {settings.LOCAL_LLM_MODEL}, Whisper {settings.WHISPER_MODEL_SIZE})"))
    from app.security.tokens import get_tokens, is_weak  # noqa: PLC0415

    tokens = get_tokens(settings)
    exposed = not settings.is_localhost_only
    if is_weak(tokens.user_token) and exposed:
        checks.append(Check("Sicherheit", ERROR, f"API_HOST={settings.API_HOST} ist von aussen erreichbar, aber der Token ist schwach.", "USER_TOKEN aus der .env löschen - Cypher erzeugt dann einen starken."))
    else:
        where = "nur auf diesem PC" if not exposed else f"über {settings.API_HOST}"
        checks.append(Check("Sicherheit", OK, f"Backend erreichbar {where}, Token {'erzeugt' if tokens.user_source == 'generated' else 'aus .env'}"))
    return checks, settings


def check_backend(settings) -> List[Check]:
    import httpx  # noqa: PLC0415

    url = f"http://127.0.0.1:{settings.API_PORT}/api/health"
    try:
        resp = httpx.get(url, timeout=2.0)
        data = resp.json()
        state = data.get("status", "?")
        return [Check("Backend", OK if state == "ok" else WARN, f"Cypher läuft auf Port {settings.API_PORT} (Status: {state}).")]
    except Exception:  # noqa: BLE001
        pass
    import socket  # noqa: PLC0415

    with socket.socket() as sock:
        sock.settimeout(0.5)
        busy = sock.connect_ex(("127.0.0.1", settings.API_PORT)) == 0
    if busy:
        return [Check("Backend", ERROR, f"Port {settings.API_PORT} ist von einem anderen Programm belegt.", "Das Programm beenden oder API_PORT in der .env ändern.")]
    return [Check("Backend", INFO, "Cypher läuft gerade nicht (start_cypher.bat startet es).")]


def check_ollama(settings) -> List[Check]:
    from app.core.health import check_gpu, check_llm_model, check_ollama as ollama_health  # noqa: PLC0415
    from app.llm.model_manager import ModelManager  # noqa: PLC0415
    from app.llm.ollama import OllamaAdmin, model_matches  # noqa: PLC0415

    checks = []
    if shutil.which("ollama") is None and not (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe").exists():
        checks.append(Check("Ollama", ERROR, "Ollama ist nicht installiert.", "ollama.com/download installieren, dann setup.bat."))
        return checks

    async def probe():
        manager = ModelManager(settings)
        status = await manager.status(max_age=0)
        installed = await OllamaAdmin(settings.LOCAL_LLM_BASE_URL).installed_models() if status.reachable else []
        return status, installed

    status, installed = asyncio.run(probe())
    for name, health in (("Ollama", ollama_health(status, settings)), ("Sprachmodell", check_llm_model(status, settings)), ("Grafikkarte", check_gpu(status, settings))):
        level = {"ok": OK, "warn": WARN, "error": ERROR}.get(health.status, INFO)
        if name == "Sprachmodell" and health.status == "warn" and not status.loaded:
            level = INFO  # not loaded because Cypher isn't running - that's fine
        checks.append(Check(name, level, health.message, health.hint))
    if status.reachable:
        fallback = settings.LOCAL_LLM_FALLBACK_MODEL
        if fallback and not any(model_matches(n, fallback) for n in installed):
            checks.append(Check("Sprachmodell", WARN, f"Ersatzmodell {fallback} ist nicht installiert.", f"ollama pull {fallback}"))
        if status.other_loaded:
            checks.append(
                Check("Grafikkarte", WARN, f"Weitere Modelle im Grafikspeicher: {', '.join(status.other_loaded)}.", "Mehrere Modelle gleichzeitig führen zu langsamer Auslagerung. Cypher entlädt sie beim Start; oder: ollama stop <modell>.")
            )
    return checks


def check_voice(settings) -> List[Check]:
    from app.tts.piper_provider import voice_files_present  # noqa: PLC0415

    checks = []
    if voice_files_present(settings):
        checks.append(Check("Sprachausgabe", OK, "Piper-Stimme vorhanden"))
    else:
        checks.append(Check("Sprachausgabe", ERROR, "Die Piper-Stimme fehlt.", "setup.bat lädt sie herunter (python backend/scripts/download_piper_voice.py)."))
    try:
        from faster_whisper.utils import download_model  # noqa: PLC0415

        missing = []
        for size in dict.fromkeys([settings.WHISPER_MODEL_SIZE, settings.WAKE_WORD_MODEL if settings.WAKE_WORD_ENABLED else settings.WHISPER_MODEL_SIZE]):
            try:
                download_model(size, local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached yet
                missing.append(size)
        if missing:
            checks.append(Check("Spracherkennung", WARN, f"Whisper-Modell(e) noch nicht geladen: {', '.join(missing)}.", "Wird beim ersten Start automatisch heruntergeladen (einmalig, einige hundert MB)."))
        else:
            checks.append(Check("Spracherkennung", OK, f"Whisper {settings.WHISPER_MODEL_SIZE} bereit" + (f", Aktivierungswort mit {settings.WAKE_WORD_MODEL}" if settings.WAKE_WORD_ENABLED else "")))
    except ImportError:
        checks.append(Check("Spracherkennung", ERROR, "faster-whisper fehlt.", "setup.bat ausführen."))
    return checks


def check_frontend(settings) -> List[Check]:
    from app.config import PROJECT_ROOT  # noqa: PLC0415

    index = settings.frontend_dist_path / "index.html"
    if not index.exists():
        return [Check("Oberfläche", ERROR, "Die Oberfläche ist nicht gebaut.", "setup.bat ausführen (oder: cd frontend && npm run build).")]
    src = PROJECT_ROOT / "frontend" / "src"
    newest = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0)
    if newest > index.stat().st_mtime + 1:
        return [Check("Oberfläche", WARN, "Die gebaute Oberfläche ist älter als der Quellcode.", "start_cypher.bat baut sie neu, oder: cd frontend && npm run build.")]
    return [Check("Oberfläche", OK, "Oberfläche gebaut und aktuell")]


def check_data(settings) -> List[Check]:
    from app.config import PROJECT_ROOT  # noqa: PLC0415

    checks = []
    data = settings.data_dir_path
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".doctor-write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        checks.append(Check("Daten", OK, f"Datenordner beschreibbar ({data})"))
    except OSError as exc:
        checks.append(Check("Daten", ERROR, f"Datenordner nicht beschreibbar: {exc}"))
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///") and ":memory:" not in url:
        path = Path(url[len("sqlite:///") :])
        if not path.is_absolute():
            path = PROJECT_ROOT / "backend" / path
        if path.exists():
            try:
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                    result = conn.execute("PRAGMA quick_check").fetchone()[0]
                checks.append(Check("Daten", OK if result == "ok" else ERROR, f"Datenbank {path.name}: {'in Ordnung' if result == 'ok' else result}", "" if result == "ok" else "Backup einspielen (backup_cypher.bat --restore)."))
            except sqlite3.Error as exc:
                checks.append(Check("Daten", ERROR, f"Datenbank nicht lesbar: {exc}"))
        else:
            checks.append(Check("Daten", INFO, "Datenbank wird beim ersten Start angelegt."))
    backups = sorted(settings.backup_dir_path.glob("cypher-backup-*.zip")) if settings.backup_dir_path.exists() else []
    checks.append(Check("Daten", OK if backups else INFO, f"Letztes Backup: {backups[-1].name}" if backups else "Noch kein Backup.", "" if backups else "backup_cypher.bat erstellt eins."))
    return checks


def check_cloud(settings) -> List[Check]:
    from app.llm.factory import cloud_status  # noqa: PLC0415

    status = cloud_status(settings)
    text = {"ok": f"Cloud eingerichtet ({settings.CLOUD_LLM_MODEL}, Budget {settings.CLOUD_MONTHLY_BUDGET_USD:g} USD/Monat) - nur auf Wunsch", "disabled": "Cloud ausgeschaltet - alles bleibt lokal", "no_key": "Cloud eingeschaltet, aber ohne API-Schlüssel"}[status]
    return [Check("Cloud", WARN if status == "no_key" else INFO, text, "CLOUD_LLM_API_KEY in der .env setzen oder CLOUD_LLM_ENABLED=false." if status == "no_key" else "")]


def check_windows(settings) -> List[Check]:
    if sys.platform != "win32":
        return []
    checks = []
    # Tailscale (phone access, docs/MOBILE.md)
    from app.ops import tailscale  # noqa: PLC0415

    if tailscale.cli_path():
        state = tailscale.backend_state()
        url = tailscale.serve_url(settings.API_PORT)
        if state == "Running":
            if url:
                checks.append(Check("Handy", OK, f"Tailscale leitet HTTPS an Cypher weiter: {url}"))
            else:
                checks.append(Check("Handy", INFO, "Tailscale läuft, aber ohne 'tailscale serve' für Cypher.", f"Fürs Handy: tailscale serve --bg {settings.API_PORT} (docs/MOBILE.md)."))
        else:
            checks.append(Check("Handy", INFO, f"Tailscale installiert, Status: {state or 'unbekannt'}", "Für Handy-Zugriff Tailscale starten (docs/MOBILE.md)."))
    else:
        checks.append(Check("Handy", INFO, "Tailscale nicht installiert - Cypher ist nur auf diesem PC erreichbar.", "Für Handy-Zugriff: docs/MOBILE.md."))
    # Wake-on-LAN (read-only)
    if settings.WAKE_ON_LAN_MAC:
        raw = _run(["powershell", "-NoProfile", "-Command", "Get-NetAdapterPowerManagement | Where-Object WakeOnMagicPacket -eq 'Enabled' | Select-Object -ExpandProperty Name"])
        adapters = [line.strip() for line in (raw or "").splitlines() if line.strip()]
        if adapters:
            checks.append(Check("Wake-on-LAN", OK, f"Magic Packet aktiv auf: {', '.join(adapters)} (MAC {settings.WAKE_ON_LAN_MAC})"))
        else:
            checks.append(Check("Wake-on-LAN", WARN, "WAKE_ON_LAN_MAC ist gesetzt, aber keine Netzwerkkarte hat 'Wake on Magic Packet' aktiv.", "Geräte-Manager → Netzwerkadapter → Energieverwaltung bzw. BIOS-Einstellung prüfen."))
    else:
        checks.append(Check("Wake-on-LAN", INFO, "Nicht eingerichtet (WAKE_ON_LAN_MAC leer)."))
    # Autostart (read-only)
    task = _run(["schtasks", "/query", "/tn", "Cypher Autostart"])
    legacy = _run(["schtasks", "/query", "/tn", "JARVIS Autostart"])
    if task:
        checks.append(Check("Autostart", OK, "Cypher startet bei der Anmeldung (Aufgabe 'Cypher Autostart')."))
    elif legacy:
        checks.append(Check("Autostart", WARN, "Alte Aufgabe 'JARVIS Autostart' ist noch eingerichtet.", "Umstellen: python scripts/autostart.py install (ersetzt sie)."))
    else:
        checks.append(Check("Autostart", INFO, "Kein Autostart (optional: python scripts/autostart.py install)."))
    return checks


# ------------------------------------------------------------------ runner
def _hints(count: int) -> str:
    return "1 Hinweis" if count == 1 else f"{count} Hinweise"


def run_all() -> List[Check]:
    checks: List[Check] = []
    checks += check_python()
    cfg, settings = check_config()
    checks += cfg
    if settings is None:
        return checks
    steps: List[Callable] = [check_backend, check_ollama, check_voice, check_frontend, check_data, check_cloud, check_windows]
    for step in steps:
        try:
            checks += step(settings)
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            checks.append(Check(step.__name__.replace("check_", "").title(), ERROR, f"Prüfung fehlgeschlagen: {exc}"))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Cypher-Doktor - prüft die Installation (nur lesend).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    import logging  # noqa: PLC0415

    logging.disable(logging.WARNING)
    checks = run_all()
    errors_ = sum(c.status == ERROR for c in checks)
    warnings_ = sum(c.status == WARN for c in checks)
    if args.json:
        print(json.dumps({"errors": errors_, "warnings": warnings_, "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=2))
        return 1 if errors_ else 0
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    print("\nCypher-Doktor\n=============")
    area = None
    for c in checks:
        if c.area != area:
            area = c.area
            print(f"\n{area}")
        print(f"  {_SYMBOL[c.status]} {c.message}")
        if c.hint and c.status in (ERROR, WARN, INFO):
            print(f"      → {c.hint}")
    print()
    if errors_:
        print(f"{errors_} Fehler, {_hints(warnings_)}. Bitte die Fehler (✗) beheben.")
    else:
        print(f"Keine Fehler{', ' + _hints(warnings_) if warnings_ else ''}. Cypher ist startklar.")
    return 1 if errors_ else 0


if __name__ == "__main__":
    sys.exit(main())
