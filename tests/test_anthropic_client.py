"""No live calls. These assert the REQUEST SHAPE and the error mapping --
the two things that break silently against a real provider."""

import types

import pytest

from archiagent.llm.anthropic_client import DEFAULT_MODEL, AnthropicClient
from archiagent.llm.client import LLMSchemaError, LLMUnavailable

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason


def _client_with(monkeypatch, response=None, raises=None):
    """Build an AnthropicClient whose messages.create is ours."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return response

    c = AnthropicClient(model="claude-haiku-4-5", api_key="test-key")
    monkeypatch.setattr(c, "_client",
                        types.SimpleNamespace(
                            messages=types.SimpleNamespace(create=create)))
    return c, captured


def test_default_model_is_haiku_4_5():
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_missing_key_names_the_variable_and_the_bypass(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as e:
        AnthropicClient()
    assert "ANTHROPIC_API_KEY" in str(e.value)
    assert "--walls" in str(e.value)


def test_request_uses_output_config_not_the_deprecated_output_format(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=2048)

    assert captured["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "output_format" not in captured


def test_request_sends_no_effort_and_no_thinking(monkeypatch):
    """effort: max errors on Haiku 4.5, and neither parameter helps a
    shallow classification. Sending them is a 400 waiting to happen."""
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA)

    assert "thinking" not in captured
    assert "effort" not in captured.get("output_config", {})


def test_request_passes_model_system_and_max_tokens(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=99)

    assert captured["model"] == "claude-haiku-4-5"
    assert captured["system"] == "sys"
    assert captured["max_tokens"] == 99
    assert captured["messages"] == [{"role": "user", "content": "usr"}]


def test_parses_the_json_out_of_the_text_block(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"name": "a"}]}'))
    assert c.classify_json(system="s", user="u", schema=SCHEMA) == {
        "layers": [{"name": "a"}]}


def test_truncation_is_a_schema_error_not_a_parse_attempt(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"na',
                                           stop_reason="max_tokens"))
    with pytest.raises(LLMSchemaError, match="max_tokens"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_unparseable_text_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp("not json at all"))
    with pytest.raises(LLMSchemaError, match="valid JSON"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_a_reply_with_no_text_block_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp(None, stop_reason="refusal"))
    with pytest.raises(LLMSchemaError, match="refusal"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_sdk_error_becomes_unavailable_with_the_message_preserved(monkeypatch):
    import anthropic
    boom = anthropic.APIConnectionError(request=None)
    c, _ = _client_with(monkeypatch, raises=boom)
    with pytest.raises(LLMUnavailable, match="Anthropic"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_unexpected_sdk_exception_becomes_unavailable(monkeypatch):
    """A pydantic ValidationError inside create() is neither an APIError nor
    any exception cli.py handles, so it escaped main() as a traceback and
    exit 1. It still means the model was not reached: exit 2 is honest."""
    c, _ = _client_with(monkeypatch, raises=TypeError("bad response body"))
    with pytest.raises(LLMUnavailable, match="TypeError"):
        c.classify_json(system="s", user="u", schema=SCHEMA)
