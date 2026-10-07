from __future__ import annotations

import pytest

from app.config import ConfigError, Settings, get_settings


@pytest.fixture()
def fresh_settings(monkeypatch):
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("var", "value", "expected_fragment"),
    [
        ("WHISPER_MODEL_SIZE", "smal", "WHISPER_MODEL_SIZE"),
        ("CORS_ORIGINS", "*", '"*" ist nicht erlaubt'),
        ("LOCAL_LLM_KEEP_ALIVE", "eine Stunde", "Dauer wie 30m"),
        ("API_PORT", "70000", "API_PORT"),
        ("LOCAL_LLM_BASE_URL", "localhost:11434", "http://"),
        ("LOCAL_LLM_NUM_CTX", "512", "LOCAL_LLM_NUM_CTX"),
        ("TIMEZONE", "Mars/Olympus", "Zeitzone"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL"),
    ],
)
def test_invalid_values_raise_german_config_error(fresh_settings, var, value, expected_fragment):
    fresh_settings.setenv(var, value)
    with pytest.raises(ConfigError) as exc_info:
        get_settings()
    message = str(exc_info.value)
    assert "Die Konfiguration ist ungültig" in message
    assert expected_fragment in message
    assert ".env.example" in message


def test_secrets_are_never_echoed_in_config_errors(fresh_settings):
    fresh_settings.setenv("USER_TOKEN", "super-secret-value")
    fresh_settings.setenv("API_PORT", "0")
    with pytest.raises(ConfigError) as exc_info:
        get_settings()
    assert "super-secret-value" not in str(exc_info.value)


def test_defaults_match_m0_benchmark_decisions(fresh_settings):
    for var in ("LOCAL_LLM_MODEL", "WHISPER_MODEL_SIZE", "LOCAL_LLM_NUM_CTX", "LOCAL_LLM_THINK", "LOCAL_LLM_KEEP_ALIVE"):
        fresh_settings.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.LOCAL_LLM_MODEL == "qwen3.5:9b"
    assert s.LOCAL_LLM_FALLBACK_MODEL == "qwen3.5:4b"
    assert s.LOCAL_LLM_NUM_CTX == 8192
    assert s.LOCAL_LLM_THINK is False
    assert s.WHISPER_MODEL_SIZE == "small"
    assert s.WHISPER_LANGUAGE == "de"


def test_old_env_variable_names_still_work(fresh_settings):
    fresh_settings.setenv("LOCAL_LLM_MODEL", "qwen2.5:7b")
    fresh_settings.setenv("WHISPER_MODEL_SIZE", "base")
    fresh_settings.setenv("ALLOWED_DIRECTORIES", r"C:\a, C:\b")
    s = get_settings()
    assert s.LOCAL_LLM_MODEL == "qwen2.5:7b"
    assert s.WHISPER_MODEL_SIZE == "base"
    assert s.allowed_directories_list == [r"C:\a", r"C:\b"]


def test_trailing_slash_is_stripped_from_urls(fresh_settings):
    fresh_settings.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/")
    assert get_settings().LOCAL_LLM_BASE_URL == "http://localhost:11434"


def test_relative_log_dir_resolves_against_project_root(fresh_settings):
    fresh_settings.setenv("LOG_DIR", "logs")
    s = get_settings()
    assert s.log_dir_path.name == "logs"
    assert s.log_dir_path.parent.name.lower() == "cypher"
