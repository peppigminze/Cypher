"""
Backup and restore of everything Cypher knows about you.

    backup_cypher.bat                       -> backups/cypher-backup-<datum>.zip
    backup_cypher.bat --restore <zip>       (asks before overwriting)
    python -m app.ops.backup [--restore ZIP] [--yes]

In the zip:
    cypher.db           the SQLite database (copied with SQLite's online
                        backup API - consistent even while Cypher runs)
    data/...            core_memory.json, notes, generated tokens (secrets.json,
                        only if the tokens are not set in .env)
    manifest.json       what, when, which version

NOT in the zip: .env (it may contain your cloud API key) and the models
(they can be downloaded again). Keep the backup private: it contains your
memory, notes and your access token.

Restore stops nothing by itself - stop Cypher first (stop_cypher.bat).
Before overwriting anything, the current state is saved as another backup.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import PROJECT_ROOT, Settings, get_settings

DB_NAME = "cypher.db"


def database_path(settings: Settings) -> Optional[Path]:
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    path = Path(url[len("sqlite:///") :])
    return path if path.is_absolute() else PROJECT_ROOT / "backend" / path


def _free_name(target_dir: Path, stem: str) -> Path:
    """Never overwrite an existing backup (two backups in the same second -
    e.g. the safety copy while restoring - must not share a file name)."""
    target = target_dir / f"{stem}.zip"
    counter = 2
    while target.exists():
        target = target_dir / f"{stem}-{counter}.zip"
        counter += 1
    return target


def create_backup(settings: Optional[Settings] = None, target_dir: Optional[Path] = None, label: str = "") -> Path:
    settings = settings or get_settings()
    target_dir = target_dir or settings.backup_dir_path
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = _free_name(target_dir, f"cypher-backup-{stamp}" + (f"-{label}" if label else ""))
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "version": 1, "files": []}
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        db = database_path(settings)
        if db is not None and db.exists():
            copy = Path(tmp) / DB_NAME
            # closing(): sqlite3's own context manager only commits - an open
            # handle would keep the temp file locked on Windows.
            with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(copy)) as dest:
                source.backup(dest)  # consistent snapshot, also while the backend writes
            zf.write(copy, DB_NAME)
            manifest["files"].append(DB_NAME)
        data = settings.data_dir_path
        if data.exists():
            for path in sorted(data.rglob("*")):
                if path.is_file() and path.suffix not in {".pid", ".tmp"} and not path.name.startswith(".doctor"):
                    arc = "data/" + path.relative_to(data).as_posix()
                    zf.write(path, arc)
                    manifest["files"].append(arc)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return target


def restore_backup(archive: Path, settings: Optional[Settings] = None) -> Path:
    """Restore `archive`; returns the safety backup of the state before."""
    settings = settings or get_settings()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("Das ist kein Cypher-Backup (manifest.json fehlt).")
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsicherer Pfad im Backup: {name}")
        safety = create_backup(settings, label="vor-restore")  # never lose the current state
        db = database_path(settings)
        if DB_NAME in names and db is not None:
            db.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(DB_NAME) as src, open(db, "wb") as dst:
                shutil.copyfileobj(src, dst)
        data = settings.data_dir_path
        for name in names:
            if name.startswith("data/") and not name.endswith("/"):
                out = data / name[len("data/") :]
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return safety


def _backend_running(settings: Settings) -> bool:
    import socket  # noqa: PLC0415

    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", settings.API_PORT)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cypher-Backup erstellen oder einspielen.")
    parser.add_argument("--restore", type=Path, help="Backup-Zip einspielen (überschreibt Gedächtnis, Notizen, Datenbank)")
    parser.add_argument("--yes", action="store_true", help="ohne Rückfrage einspielen")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    settings = get_settings()
    if args.restore is None:
        target = create_backup(settings)
        size_kb = target.stat().st_size / 1024
        print(f"Backup erstellt: {target} ({size_kb:.0f} KB)")
        token = " und den Zugangs-Token" if (settings.data_dir_path / "secrets.json").exists() else ""
        print(f"Enthält Gedächtnis, Notizen, Gesprächsverlauf{token} - bitte privat aufbewahren. Die .env ist NICHT enthalten.")
        return 0
    archive = args.restore
    if not archive.is_absolute() and not archive.exists():
        archive = PROJECT_ROOT / archive  # backup_cypher.bat runs in backend/, paths are meant from the project root
    if not archive.exists():
        print(f"Datei nicht gefunden: {args.restore}")
        return 2
    args.restore = archive
    if _backend_running(settings):
        print("Cypher läuft noch. Bitte zuerst stop_cypher.bat ausführen, dann erneut versuchen.")
        return 2
    if not args.yes:
        answer = input(f"'{args.restore.name}' einspielen? Gedächtnis, Notizen und Datenbank werden ersetzt (vorher wird automatisch gesichert). Tippe JA: ")
        if answer.strip() != "JA":
            print("Abgebrochen - nichts geändert.")
            return 1
    safety = restore_backup(args.restore, settings)
    print(f"Eingespielt. Der vorherige Stand liegt sicherheitshalber in: {safety}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
