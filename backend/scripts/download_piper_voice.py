"""
One-time setup: download the German Piper voice (de_DE-thorsten-medium)
used by app/tts/piper_provider.py.

The voice files are ~78 MB and deliberately NOT committed to git (see
.gitignore: /backend/models/) - run this once per machine instead.
Safe to re-run: skips the download if the target directory already
has the expected files.

Usage (from backend/):
    python scripts/download_piper_voice.py
"""
from __future__ import annotations

import sys
import tarfile
import urllib.request
from pathlib import Path

VOICE_NAME = "de_DE-thorsten-medium"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-{VOICE_NAME}.tar.bz2"

_BACKEND_DIR = Path(__file__).resolve().parent.parent
TARGET_DIR = _BACKEND_DIR / "models" / "piper" / VOICE_NAME


def already_downloaded() -> bool:
    return (TARGET_DIR / f"{VOICE_NAME}.onnx").exists() and (TARGET_DIR / "tokens.txt").exists()


def main() -> None:
    if already_downloaded():
        print(f"Voice already present at {TARGET_DIR} - nothing to do.")
        return

    TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
    archive_path = TARGET_DIR.parent / f"{VOICE_NAME}.tar.bz2"

    print(f"Downloading {ARCHIVE_URL} ...")
    urllib.request.urlretrieve(ARCHIVE_URL, archive_path)

    print(f"Extracting to {TARGET_DIR.parent} ...")
    with tarfile.open(archive_path, "r:bz2") as tar:
        tar.extractall(TARGET_DIR.parent)

    archive_path.unlink()

    if not already_downloaded():
        print("ERROR: extraction finished but expected files are missing - check the archive layout.", file=sys.stderr)
        sys.exit(1)

    print(f"Done. Voice ready at {TARGET_DIR}")


if __name__ == "__main__":
    main()
