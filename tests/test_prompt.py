import hashlib
import json

from archiagent.classify.inventory import LayerStats
from archiagent.classify.prompt import (PROMPT_VERSION, SYSTEM_PROMPT,
                                        build_user_prompt, response_schema)
from archiagent.classify.roles import Role


def _stat(name, **kw):
    base = dict(path_count=187, segment_count=187, axis_aligned_fraction=1.0,
                stroke_widths=(0.0,), dominant_colors=((0.0, 0.0, 0.0),),
                bbox=(0.0, 0.0, 1024.5, 780.25),
                length_p10=4.3, length_p50=23.2, length_p90=71.0)
    base.update(kw)
    return LayerStats(name=name, **base)


def test_schema_enumerates_exactly_the_role_vocabulary():
    """A role added to Role but missing from the schema is a silent gap:
    the model could never return it and no test would notice."""
    enum = response_schema()["properties"]["layers"]["items"]["properties"]["role"]["enum"]
    assert set(enum) == {r.value for r in Role}
    assert len(enum) == len(Role)


def test_schema_forbids_additional_properties_on_every_object():
    """Structured outputs REQUIRE additionalProperties: false on each object."""
    schema = response_schema()
    item = schema["properties"]["layers"]["items"]
    assert schema["additionalProperties"] is False
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"name", "role", "confidence", "reason"}


def test_schema_carries_no_numeric_constraints():
    """minimum/maximum are unsupported and get stripped; leaving them in
    would imply a guarantee the API does not make. Confidence is clamped in
    decisions_from_reply instead."""
    blob = json.dumps(response_schema())
    assert "minimum" not in blob
    assert "maximum" not in blob


def test_user_prompt_quotes_layer_names_so_spaces_are_unambiguous():
    prompt = build_user_prompt((_stat("WALL HATCH"), _stat("walll")))
    assert '"WALL HATCH"' in prompt
    assert '"walll"' in prompt


def test_user_prompt_reports_the_layer_count_and_one_row_each():
    stats = (_stat("a"), _stat("b"), _stat("c"))
    prompt = build_user_prompt(stats)
    assert "3 layers" in prompt
    rows = [ln for ln in prompt.splitlines() if ln.startswith('"')]
    assert len(rows) == 3


def test_user_prompt_renders_the_discriminating_numbers():
    prompt = build_user_prompt((_stat("walll"),))
    row = next(ln for ln in prompt.splitlines() if ln.startswith('"walll"'))
    assert "187" in row       # path count
    assert "100" in row       # axis-aligned percent
    assert "23.2" in row      # median segment length
    assert "1024.5" in row    # bbox width


def test_prompt_version_is_derived_from_the_prompt_and_the_schema():
    """PROMPT_VERSION was a hand-maintained "1". The cache key includes it so
    a stale answer is never served for a new prompt -- a guarantee that rested
    entirely on a developer remembering to bump it. It is now a digest of the
    content, so any edit to SYSTEM_PROMPT or the schema that fails to
    propagate breaks this test instead of silently serving stale answers."""
    expected = hashlib.sha256(
        (SYSTEM_PROMPT + json.dumps(response_schema(), sort_keys=True))
        .encode("utf-8")
    ).hexdigest()[:12]
    assert PROMPT_VERSION == expected
    assert len(PROMPT_VERSION) == 12
    assert all(c in "0123456789abcdef" for c in PROMPT_VERSION)


def test_system_prompt_warns_that_hatch_is_not_a_wall():
    """Measured fact, not prompt-tuning: classifying the demolition plan's
    hatch layers as walls produced a 40% scale error that still passed the
    R1 gate (PLAN.md, Finding C)."""
    assert "hatch" in SYSTEM_PROMPT.lower()
    assert "scale" in SYSTEM_PROMPT.lower()
