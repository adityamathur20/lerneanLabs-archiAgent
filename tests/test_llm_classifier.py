import pytest

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.llm_classifier import (LLMLayerClassifier,
                                                decisions_from_reply)
from archiagent.classify.roles import Role
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from tests.fakes import FakeLLMClient


def _stats(*names):
    return tuple(
        LayerStats(name=n, path_count=1, segment_count=1,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 1.0, 1.0),
                   length_p10=1.0, length_p50=1.0, length_p90=1.0)
        for n in names
    )


def _reply(*entries):
    return {"layers": [
        {"name": n, "role": r, "confidence": c, "reason": why}
        for n, r, c, why in entries
    ]}


def test_happy_path_maps_roles_and_keeps_the_reason():
    out = decisions_from_reply(
        _reply(("walll", "wall_structural", 0.95, "long axis-aligned runs")),
        _stats("walll"))
    assert out == (
        LayerDecision("walll", Role.WALL_STRUCTURAL, 0.95,
                      "long axis-aligned runs", "llm"),
    )


def test_output_follows_inventory_order_not_reply_order():
    out = decisions_from_reply(
        _reply(("c", "ignore", 0.5, ""), ("a", "furniture", 0.8, "")),
        _stats("a", "b", "c"))
    assert [d.layer for d in out] == ["a", "b", "c"]


def test_a_layer_the_model_omitted_defaults_to_ignore_and_is_marked():
    out = decisions_from_reply(_reply(("a", "furniture", 0.8, "")),
                               _stats("a", "b"))
    b = out[1]
    assert b.role is Role.IGNORE
    assert b.confidence == 0.0
    assert b.source == "default"
    assert b.reason != ""


def test_a_layer_the_model_invented_is_dropped():
    """A name that is not in the drawing cannot affect geometry. Dropping it
    is safer than failing a whole run over one hallucinated row."""
    out = decisions_from_reply(
        _reply(("a", "furniture", 0.8, ""), ("ghost", "furniture", 0.8, "")),
        _stats("a"))
    assert [d.layer for d in out] == ["a"]


def test_confidence_is_clamped_because_the_schema_cannot_constrain_it():
    out = decisions_from_reply(
        _reply(("a", "furniture", 1.7, ""), ("b", "furniture", -0.2, "")),
        _stats("a", "b"))
    assert out[0].confidence == 1.0
    assert out[1].confidence == 0.0


def test_a_role_outside_the_vocabulary_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="not_a_role"):
        decisions_from_reply(_reply(("a", "not_a_role", 0.9, "")), _stats("a"))


def test_a_reply_without_a_layers_list_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="layers"):
        decisions_from_reply({"result": []}, _stats("a"))


def test_a_non_numeric_confidence_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="confidence"):
        decisions_from_reply(_reply(("a", "furniture", "high", "")), _stats("a"))


def test_classifier_sends_the_schema_and_both_prompts():
    fake = FakeLLMClient(_reply(("a", "furniture", 0.8, "short segments")))
    LLMLayerClassifier(fake).classify(_stats("a"))
    call = fake.calls[0]
    assert call["max_tokens"] == 2048
    assert "classify" in call["system"].lower()
    assert '"a"' in call["user"]
    assert "wall_structural" in str(call["schema"])


def test_classifier_lets_unavailability_through_untouched():
    fake = FakeLLMClient(error=LLMUnavailable("no key"))
    with pytest.raises(LLMUnavailable, match="no key"):
        LLMLayerClassifier(fake).classify(_stats("a"))
