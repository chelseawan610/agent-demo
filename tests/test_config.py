import pytest

from travel_agent.config import Settings


def test_settings_read_openai_compatible_environment(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-for-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("TRIP_OUTPUT_MODE", "auto")

    settings = Settings.from_env()

    assert settings.base_url == "https://example.test/v1"
    assert settings.model == "test-model"
    assert settings.output_mode == "auto"


def test_settings_reject_invalid_output_mode(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-for-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("TRIP_OUTPUT_MODE", "invalid")

    with pytest.raises(ValueError, match="TRIP_OUTPUT_MODE"):
        Settings.from_env()


def test_settings_accepts_one_run_model_override(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-for-test")
    monkeypatch.setenv("LLM_MODEL", "default-model")

    settings = Settings.from_env(model="qwen/qwen3.7-flash")

    assert settings.model == "qwen/qwen3.7-flash"
