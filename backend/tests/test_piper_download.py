"""setup.bat's voice download: the archive's folder name differs from the
one Cypher expects (found on a fresh install during M7)."""
from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_piper_voice as dl  # noqa: E402


def _archive(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_archive_folder_is_renamed_to_the_expected_voice_dir(tmp_path):
    archive = _archive(
        tmp_path / "voice.tar.bz2",
        {
            "vits-piper-de_DE-thorsten-medium/de_DE-thorsten-medium.onnx": b"onnx",
            "vits-piper-de_DE-thorsten-medium/tokens.txt": b"a 1",
            "vits-piper-de_DE-thorsten-medium/espeak-ng-data/de_dict": b"x",
        },
    )
    target = tmp_path / "models" / "piper" / "de_DE-thorsten-medium"
    dl.install_from_archive(archive, target)
    assert dl.has_voice(target)
    assert (target / "espeak-ng-data" / "de_dict").exists()
    # no temporary leftovers next to the voice
    assert [p.name for p in target.parent.iterdir()] == ["de_DE-thorsten-medium"]


def test_incomplete_archive_is_refused_without_leftovers(tmp_path):
    archive = _archive(tmp_path / "voice.tar.bz2", {"vits-piper-x/tokens.txt": b"a 1"})
    target = tmp_path / "models" / "piper" / "de_DE-thorsten-medium"
    with pytest.raises(RuntimeError):
        dl.install_from_archive(archive, target)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_unsafe_archive_paths_are_refused(tmp_path):
    archive = _archive(tmp_path / "voice.tar.bz2", {"../evil.txt": b"x"})
    target = tmp_path / "models" / "piper" / "de_DE-thorsten-medium"
    with pytest.raises(tarfile.TarError):
        dl.install_from_archive(archive, target)
    assert not (tmp_path / "models" / "evil.txt").exists()
