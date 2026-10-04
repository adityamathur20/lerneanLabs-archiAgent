"""Token accounting for every LLM call a run makes.

Nothing recorded usage before this, so "what does one conversion cost?" could
only be guessed at. The ledger is process-wide because the calls happen in
three unrelated places (layer classification, wall adjudication, scale
verification) and the question is about the run as a whole.
"""
import json

import pytest

from archiagent.llm.usage import UsageLedger, purpose


def test_a_recorded_call_appears_in_the_totals():
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="claude-haiku-4-5",
                  input_tokens=1200, output_tokens=340, purpose="layer-classification")
    totals = ledger.totals()
    assert totals["calls"] == 1
    assert totals["input_tokens"] == 1200
    assert totals["output_tokens"] == 340


def test_totals_sum_across_calls_and_purposes():
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="m", input_tokens=10, output_tokens=1, purpose="a")
    ledger.record(provider="anthropic", model="m", input_tokens=20, output_tokens=2, purpose="b")
    ledger.record(provider="anthropic", model="m", input_tokens=30, output_tokens=3, purpose="a")
    totals = ledger.totals()
    assert (totals["calls"], totals["input_tokens"], totals["output_tokens"]) == (3, 60, 6)


def test_usage_is_broken_down_by_purpose():
    """The point of the report: which stage spends the tokens."""
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="m", input_tokens=10, output_tokens=1, purpose="layers")
    ledger.record(provider="anthropic", model="m", input_tokens=90, output_tokens=9, purpose="walls")
    ledger.record(provider="anthropic", model="m", input_tokens=10, output_tokens=1, purpose="layers")
    by = ledger.by_purpose()
    assert by["layers"]["calls"] == 2
    assert by["layers"]["input_tokens"] == 20
    assert by["walls"]["input_tokens"] == 90


def test_vision_calls_are_distinguished():
    """A vision call carries images, so its input tokens are not comparable to
    a text call's — the report must not blend them silently."""
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="m", input_tokens=5000, output_tokens=10,
                  purpose="layers", vision=True)
    ledger.record(provider="anthropic", model="m", input_tokens=500, output_tokens=10,
                  purpose="layers", vision=False)
    by = ledger.by_purpose()["layers"]
    assert by["vision_calls"] == 1
    assert by["text_calls"] == 1


def test_cache_reads_are_recorded_separately_from_fresh_input():
    """Anthropic bills a cache read differently from fresh input, so a report
    that folds them together cannot be turned into a cost."""
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="m", input_tokens=100, output_tokens=5,
                  cached_input_tokens=900, purpose="layers")
    totals = ledger.totals()
    assert totals["cached_input_tokens"] == 900
    assert totals["input_tokens"] == 100, "cached reads must not inflate fresh input"


def test_the_purpose_context_tags_calls_made_inside_it():
    ledger = UsageLedger()
    with purpose("wall-adjudication", ledger=ledger):
        ledger.record(provider="anthropic", model="m", input_tokens=7, output_tokens=1)
    assert ledger.by_purpose()["wall-adjudication"]["input_tokens"] == 7


def test_an_untagged_call_is_recorded_rather_than_dropped():
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="m", input_tokens=7, output_tokens=1)
    assert ledger.by_purpose()["unattributed"]["calls"] == 1


def test_the_report_is_json_serialisable_and_names_the_models_used():
    ledger = UsageLedger()
    ledger.record(provider="anthropic", model="claude-haiku-4-5", input_tokens=1, output_tokens=1,
                  purpose="layers")
    report = ledger.report()
    json.dumps(report)  # must not raise
    assert report["models"] == ["anthropic/claude-haiku-4-5"]
    assert "totals" in report and "by_purpose" in report


def test_an_empty_ledger_reports_zero_rather_than_nothing():
    """A cached run makes no calls at all, and the report must say so clearly
    instead of being absent or empty."""
    report = UsageLedger().report()
    assert report["totals"]["calls"] == 0
    assert report["models"] == []


# --- the clients must actually record what the provider reports --------------

from archiagent.llm import usage as usage_mod


class _FakeUsage:
    def __init__(self, i, o, cached=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = cached
        # OpenAI's names, so one fake serves both clients' readers.
        self.prompt_tokens = i
        self.completion_tokens = o


class _FakeBlock:
    type = "text"
    text = '{"ok": true}'


class _FakeAnthropicResponse:
    stop_reason = "end_turn"
    content = [_FakeBlock()]

    def __init__(self, usage):
        self.usage = usage


def test_the_anthropic_client_records_what_the_api_reported(monkeypatch):
    from archiagent.llm.anthropic_client import AnthropicClient

    usage_mod.reset()
    client = AnthropicClient(model="claude-haiku-4-5", api_key="test-key")

    def fake_create(**_):
        return _FakeAnthropicResponse(_FakeUsage(1234, 56, cached=789))

    monkeypatch.setattr(client._client.messages, "create", fake_create)

    with usage_mod.purpose("layer-classification"):
        assert client.classify_json(system="s", user="u", schema={}) == {"ok": True}

    totals = usage_mod.current().totals()
    assert totals["input_tokens"] == 1234
    assert totals["output_tokens"] == 56
    assert totals["cached_input_tokens"] == 789
    assert usage_mod.current().by_purpose()["layer-classification"]["calls"] == 1
    usage_mod.reset()


def test_a_vision_call_is_recorded_as_vision(monkeypatch):
    from archiagent.llm.anthropic_client import AnthropicClient

    usage_mod.reset()
    client = AnthropicClient(model="claude-haiku-4-5", api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create",
                        lambda **_: _FakeAnthropicResponse(_FakeUsage(9000, 20)))

    client.classify_json_vision(system="s", user="u", schema={},
                                images=[("ref", b"\x89PNG fake")])
    assert usage_mod.current().totals()["vision_calls"] == 1
    assert usage_mod.current().totals()["text_calls"] == 0
    usage_mod.reset()


def test_a_response_without_usage_does_not_break_the_run(monkeypatch):
    """Usage is telemetry. A provider that omits it must cost a conversion
    nothing — the report says 0, the IFC is still written."""
    from archiagent.llm.anthropic_client import AnthropicClient

    usage_mod.reset()
    client = AnthropicClient(model="claude-haiku-4-5", api_key="test-key")

    class NoUsage(_FakeAnthropicResponse):
        def __init__(self):
            self.usage = None

    monkeypatch.setattr(client._client.messages, "create", lambda **_: NoUsage())
    assert client.classify_json(system="s", user="u", schema={}) == {"ok": True}
    assert usage_mod.current().totals()["calls"] == 1
    assert usage_mod.current().totals()["input_tokens"] == 0
    usage_mod.reset()
