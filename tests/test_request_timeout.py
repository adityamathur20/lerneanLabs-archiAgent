"""How long to wait for the model has to be reachable.

Measured: a vision call carrying 4 rendered layers to a reasoning model took
7m18s. The OpenAI SDK's default read timeout is 600s, and a timeout returns
nothing at all -- not a partial classification -- so a slow model plus a
generous escalation cap silently becomes a guaranteed failure.
"""
import pytest

from archiagent.llm.config import ENV_TIMEOUT, build_client, config_from_env


def _env(**kw):
    base = {"ARCHIAGENT_LLM_PROVIDER": "openai", "ARCHIAGENT_LLM_MODEL": "m"}
    base.update(kw)
    return base


def test_no_timeout_by_default_leaves_the_sdk_alone():
    assert config_from_env(env=_env()).timeout is None


def test_explicit_timeout_wins():
    assert config_from_env(env=_env(), timeout=1800).timeout == 1800


def test_the_environment_variable_is_read():
    assert config_from_env(env=_env(**{ENV_TIMEOUT: "900"})).timeout == 900.0


def test_explicit_beats_the_environment():
    cfg = config_from_env(env=_env(**{ENV_TIMEOUT: "900"}), timeout=60)
    assert cfg.timeout == 60


@pytest.mark.parametrize("raw", ["nonsense", "", "0", "-5"])
def test_an_unusable_value_falls_back_rather_than_failing_the_run(raw):
    """The variable is a convenience. A typo in it must not stop a drawing
    being processed."""
    assert config_from_env(env=_env(**{ENV_TIMEOUT: raw})).timeout is None


def test_the_timeout_reaches_the_openai_client(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, **kw):
            seen.update(kw)

    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    build_client(config_from_env(env=_env(), timeout=1234))
    assert seen["timeout"] == 1234


def test_no_timeout_means_the_key_is_not_passed_at_all(monkeypatch):
    """Passing timeout=None explicitly would override the SDK default with
    'no timeout', which is not what 'leave it alone' means."""
    seen = {}

    class _FakeOpenAI:
        def __init__(self, **kw):
            seen.update(kw)

    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    build_client(config_from_env(env=_env()))
    assert "timeout" not in seen
