"""STT/TTS providers with fake models - checks the parameters that came
out of the M0 benchmark actually reach the engines."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.config import Settings
from app.stt import whisper_provider
from app.tts import piper_provider


class FakeWhisper:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, source, **kwargs):
        self.kwargs = kwargs
        segments = iter([SimpleNamespace(text=" Hey Cypher,"), SimpleNamespace(text=" wie spät ist es?")])
        return segments, SimpleNamespace(language="de", duration=1.5)


@pytest.fixture()
def fake_whisper(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(whisper_provider, "get_whisper_model", lambda settings=None, size=None: fake)
    return fake


def test_transcribe_uses_language_beam_prompt_and_vad(fake_whisper):
    settings = Settings(_env_file=None, WHISPER_LANGUAGE="de", WHISPER_BEAM_SIZE=5, WHISPER_INITIAL_PROMPT="Hey Cypher.")
    result = whisper_provider.transcribe_audio(b"RIFF....", settings)
    assert result.text == "Hey Cypher, wie spät ist es?"
    kw = fake_whisper.kwargs
    assert kw["language"] == "de"
    assert kw["beam_size"] == 5
    assert kw["initial_prompt"] == "Hey Cypher."
    assert kw["vad_filter"] is True


def test_empty_language_means_auto_detect(fake_whisper):
    settings = Settings(_env_file=None, WHISPER_LANGUAGE="")
    whisper_provider.transcribe_audio(np.zeros(16000, dtype=np.float32), settings)
    assert fake_whisper.kwargs["language"] is None


def test_voice_files_present_detects_missing_voice(tmp_path):
    settings = Settings(_env_file=None, PIPER_MODEL_DIR=str(tmp_path / "de_DE-nobody"))
    assert piper_provider.voice_files_present(settings) is False
    voice = tmp_path / "de_DE-nobody"
    voice.mkdir()
    (voice / "de_DE-nobody.onnx").write_bytes(b"x")
    (voice / "tokens.txt").write_text("a", encoding="utf-8")
    assert piper_provider.voice_files_present(settings) is True


def test_speak_with_missing_voice_returns_german_503(client, user_headers, monkeypatch):
    def missing(text, settings=None):
        raise piper_provider.VoiceNotFoundError("nope")

    monkeypatch.setattr("app.api.speak.synthesize_speech", missing)
    resp = client.post("/speak", json={"text": "Hallo"}, headers=user_headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "tts_voice_missing"
    assert "setup.bat" in resp.json()["error"]["hint"]


def test_pcm_wav_encoding_is_valid():
    wav = piper_provider._pcm16_wav_bytes(np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32), 22050)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
