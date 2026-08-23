import pytest

from archiagent.llm.client import LLMUnavailable
from archiagent.llm.config import LLMConfig, build_client, config_from_env


def test_defaults_are_anthropic_and_haiku():
    cfg = config_from_env(env={})
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.base_url is None


def test_environment_overrides_the_defaults():
    cfg = config_from_env(env={
        "ARCHIAGENT_LLM_PROVIDER": "openai",
        "ARCHIAGENT_LLM_MODEL": "moonshotai/kimi-k2",
        "ARCHIAGENT_LLM_BASE_URL": "https://api.groq.com/openai/v1",
    })
    assert cfg == LLMConfig("openai", "moonshotai/kimi-k2",
                            "https://api.groq.com/openai/v1")


def test_explicit_arguments_override_the_environment():
    cfg = config_from_env(
        env={"ARCHIAGENT_LLM_PROVIDER": "anthropic",
             "ARCHIAGENT_LLM_MODEL": "claude-haiku-4-5"},
        provider="openai", model="gpt-4o")
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"


def test_switching_provider_without_a_model_is_refused(monkeypatch):
    """claude-haiku-4-5 on an OpenAI endpoint is a 404 with a confusing
    message. Catch it here, where we can say what to set."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(LLMUnavailable, match="ARCHIAGENT_LLM_MODEL"):
        build_client(LLMConfig("openai", "claude-haiku-4-5", None))


def test_an_unknown_provider_lists_the_ones_that_exist():
    with pytest.raises(LLMUnavailable) as e:
        build_client(LLMConfig("bedrock", "some-model", None))
    assert "anthropic" in str(e.value)
    assert "openai" in str(e.value)


def test_build_client_returns_an_anthropic_adapter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = build_client(config_from_env(env={}))
    assert type(client).__name__ == "AnthropicClient"
