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
    def __init__(self, content, finish_reason="stop", choices=None):
        self.choices = ([_Choice(content, finish_reason)]
                        if choices is None else choices)


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


def test_a_reply_with_no_content_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp(None, finish_reason="content_filter"))
    with pytest.raises(LLMSchemaError, match="content_filter"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_empty_choices_array_is_a_schema_error(monkeypatch):
    """Azure's content filter and some Groq/DeepSeek error shapes return
    HTTP 200 with choices: []. Indexing it raised IndexError, which is
    neither LLMUnavailable nor LLMSchemaError and escaped every handler in
    cli.py -- the user got a traceback and exit 1, not the documented 2."""
    c, _ = _client_with(monkeypatch, _Resp(None, choices=[]))
    with pytest.raises(LLMSchemaError, match="no choices"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_unexpected_sdk_exception_becomes_unavailable(monkeypatch):
    """A pydantic ValidationError inside create() is neither an APIError nor
    any exception cli.py handles. It still means the model was not reached,
    so exit 2 is the honest code."""
    c, _ = _client_with(monkeypatch, raises=TypeError("bad response body"))
    with pytest.raises(LLMUnavailable, match="TypeError"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_vision_request_sends_labelled_images_then_the_prompt_last(monkeypatch):
    """Nothing else pins this wire shape: there are no credentials in this
    environment, so a live call can never catch a malformed content array.
    This unit test is the only protection that exists."""
    import base64

    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json_vision(
        system="sys", user="usr", schema=SCHEMA,
        images=[("reference", b"REFDATA"), ("layer WALLS", b"WALLDATA")],
        max_tokens=1234)

    assert captured["messages"][0] == {"role": "system", "content": "sys"}
    content = captured["messages"][1]["content"]
    ref_b64 = base64.b64encode(b"REFDATA").decode("ascii")
    wall_b64 = base64.b64encode(b"WALLDATA").decode("ascii")
    assert content == [
        {"type": "text", "text": "reference"},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{ref_b64}"}},
        {"type": "text", "text": "layer WALLS"},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{wall_b64}"}},
        {"type": "text", "text": "usr"},
    ]
    assert captured["model"] == "llama-3.3-70b"
    assert captured["max_tokens"] == 1234
    assert captured["response_format"]["json_schema"]["strict"] is True


def test_vision_request_maps_transport_errors_the_same_as_classify_json(monkeypatch):
    import openai
    boom = openai.APIConnectionError(request=None)
    c, _ = _client_with(monkeypatch, raises=boom)
    with pytest.raises(LLMUnavailable, match="OpenAI-compatible"):
        c.classify_json_vision(system="s", user="u", schema=SCHEMA,
                               images=[("reference", b"X")])
