import types

import pytest

from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.openai_client import OpenAICompatClient

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


def _client_with(monkeypatch, response=None, raises=None):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return response

    c = OpenAICompatClient(model="llama-3.3-70b", api_key="test-key",
                           base_url="https://api.groq.com/openai/v1")
    monkeypatch.setattr(c, "_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create))))
    return c, captured


def test_missing_key_names_the_variable_and_the_bypass(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as e:
        OpenAICompatClient(model="gpt-4o")
    assert "OPENAI_API_KEY" in str(e.value)
    assert "--walls" in str(e.value)


def test_request_sends_the_schema_as_a_strict_json_schema(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=2048)

    fmt = captured["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == SCHEMA
    assert fmt["json_schema"]["strict"] is True


def test_request_sends_system_and_user_as_two_messages(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=77)

    assert captured["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert captured["model"] == "llama-3.3-70b"
    assert captured["max_tokens"] == 77


def test_parses_the_message_content(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"name": "a"}]}'))
    assert c.classify_json(system="s", user="u", schema=SCHEMA) == {
        "layers": [{"name": "a"}]}


def test_truncation_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"lay', finish_reason="length"))
    with pytest.raises(LLMSchemaError, match="truncated"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_unparseable_content_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp("not json"))
    with pytest.raises(LLMSchemaError, match="valid JSON"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_sdk_error_becomes_unavailable(monkeypatch):
    import openai
    boom = openai.APIConnectionError(request=None)
    c, _ = _client_with(monkeypatch, raises=boom)
    with pytest.raises(LLMUnavailable, match="OpenAI-compatible"):
        c.classify_json(system="s", user="u", schema=SCHEMA)
