"""
One-time setup: download the German Piper voice (de_DE-thorsten-medium)
used by app/tts/piper_provider.py.

The voice files are ~78 MB and deliberately NOT committed to git (see
.gitignore: /backend/models/) - run this once per machine instead
(setup.bat does). Safe to re-run: skips the download if the target
directory already has the expected files.

The sherpa-onnx archive unpacks into "vits-piper-<voice>/", while Cypher
expects models/piper/<voice>/ (PIPER_MODEL_DIR) - so the archive is
unpacked into a temporary folder first and the voice folder is then moved
into place. Nothing half-finished is left behind if anything fails.

Usage (from backend/):
    python scripts/download_piper_voice.py
"""
from __future__ import annotations

import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

VOICE_NAME = "de_DE-thorsten-medium"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-{VOICE_NAME}.tar.bz2"

_BACKEND_DIR = Path(__file__).resolve().parent.parent
TARGET_DIR = _BACKEND_DIR / "models" / "piper" / VOICE_NAME


def has_voice(folder: Path, voice: str = VOICE_NAME) -> bool:
    return (folder / f"{voice}.onnx").exists() and (folder / "tokens.txt").exists()


def already_downloaded() -> bool:
    return has_voice(TARGET_DIR)


def find_voice_dir(root: Path, voice: str = VOICE_NAME) -> Optional[Path]:
    """The folder inside an unpacked archive that holds the voice files."""
    for onnx in root.rglob(f"{voice}.onnx"):
        if has_voice(onnx.parent, voice):
            return onnx.parent
    return None


def install_from_archive(archive_path: Path, target_dir: Path, voice: str = VOICE_NAME) -> None:
    """Unpack `archive_path` and move the voice folder to `target_dir`."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_dir.parent) as tmp:
        with tarfile.open(archive_path, "r:bz2") as tar:
            # filter="data": refuses absolute paths, "..", links outside the folder
            tar.extractall(tmp, filter="data")
        found = find_voice_dir(Path(tmp), voice)
        if found is None:
            raise RuntimeError(f"Im Archiv fehlt {voice}.onnx bzw. tokens.txt.")
        if target_dir.exists():
            shutil.rmtree(target_dir)  # an incomplete earlier attempt
        shutil.move(str(found), str(target_dir))


def main() -> int:
    if already_downloaded():
        print(f"Stimme schon vorhanden: {TARGET_DIR}", flush=True)
        return 0
    TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TARGET_DIR.parent) as tmp:
        archive_path = Path(tmp) / f"{VOICE_NAME}.tar.bz2"
        print(f"Lade die Stimme herunter (~78 MB): {ARCHIVE_URL}", flush=True)
        try:
            urllib.request.urlretrieve(ARCHIVE_URL, archive_path)
            install_from_archive(archive_path, TARGET_DIR)
        except (OSError, tarfile.TarError, RuntimeError) as exc:
            print(f"FEHLER: Stimme konnte nicht installiert werden: {exc}", file=sys.stderr, flush=True)
            return 1
    if not already_downloaded():
        print("FEHLER: Nach dem Entpacken fehlen Dateien der Stimme.", file=sys.stderr, flush=True)
        return 1
    print(f"Fertig. Stimme bereit: {TARGET_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
