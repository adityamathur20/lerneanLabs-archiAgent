import json

import pytest

from archiagent.classify.cache import (CachingClassifier, cache_dir,
                                       inventory_key, read_cache, write_cache)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


def _stats(*names, p50=23.2):
    return tuple(
        LayerStats(name=n, path_count=187, segment_count=187,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 10.0, 10.0),
                   length_p10=4.3, length_p50=p50, length_p90=71.0)
        for n in names
    )


class _CountingClassifier:
    def __init__(self, decisions):
        self._decisions = decisions
        self.calls = 0

    def classify(self, stats):
        self.calls += 1
        return self._decisions


def test_cache_dir_honours_the_environment(isolated_cache):
    assert cache_dir() == isolated_cache


def test_key_is_stable_for_the_same_inventory_and_model():
    a = inventory_key(_stats("walll", "FURNITURE"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll", "FURNITURE"), "claude-haiku-4-5")
    assert a == b


def test_key_changes_when_the_geometry_changes():
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll", p50=99.9), "claude-haiku-4-5")
    assert a != b


def test_key_changes_when_the_model_changes():
    """A different model is a different answer. Serving one model's cached
    classification for another is silently wrong."""
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll"), "gpt-4o")
    assert a != b


def test_key_changes_when_the_prompt_version_changes(monkeypatch):
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    monkeypatch.setattr("archiagent.classify.cache.PROMPT_VERSION", "99")
    b = inventory_key(_stats("walll"), "claude-haiku-4-5")
    assert a != b


def test_round_trips_every_field():
    decisions = (
        LayerDecision("walll", Role.WALL_STRUCTURAL, 0.95, "long runs", "llm"),
        LayerDecision("HATCH1", Role.ANNOTATION, 0.7, "zero-length", "llm"),
    )
    write_cache("k1", decisions)
    assert read_cache("k1") == decisions


def test_a_miss_returns_none():
    assert read_cache("never-written") is None


def test_a_corrupt_entry_is_a_miss_not_a_crash(isolated_cache):
    """A half-written cache file must not take down the pipeline."""
    isolated_cache.mkdir(parents=True, exist_ok=True)
    (isolated_cache / "broken.json").write_text("{not json")
    assert read_cache("broken") is None


def test_caching_classifier_calls_through_once_then_serves_the_cache():
    # The classification must contain a wall layer: a wall-less one is
    # deliberately not persisted (see the sticky-failure test below).
    decisions = (LayerDecision("a", Role.WALL_STRUCTURAL, 0.8, "long", "llm"),)
    inner = _CountingClassifier(decisions)
    stats = _stats("a")

    first = CachingClassifier(inner, model="m").classify(stats)
    second = CachingClassifier(inner, model="m").classify(stats)

    assert inner.calls == 1
    assert first == second == decisions


def test_caching_classifier_disabled_always_calls_through():
    decisions = (LayerDecision("a", Role.FURNITURE, 0.8, "short", "llm"),)
    inner = _CountingClassifier(decisions)
    stats = _stats("a")

    CachingClassifier(inner, model="m", enabled=False).classify(stats)
    CachingClassifier(inner, model="m", enabled=False).classify(stats)

    assert inner.calls == 2


def test_key_changes_when_the_provider_changes():
    a = inventory_key(_stats("walll"), "m", provider="anthropic")
    b = inventory_key(_stats("walll"), "m", provider="openai")
    assert a != b


def test_key_changes_when_the_base_url_changes():
    """`--provider openai --model llama-3.3-70b` reaches Groq or DeepSeek
    depending only on the base URL. Serving one endpoint's classification for
    the other is silently wrong."""
    a = inventory_key(_stats("walll"), "m", provider="openai",
                      base_url="https://api.groq.com/openai/v1")
    b = inventory_key(_stats("walll"), "m", provider="openai",
                      base_url="https://api.deepseek.com")
    assert a != b


def test_key_rounding_is_at_least_as_fine_as_the_prompt_renders_it():
    """The key's invariant: it may distinguish inventories the prompt renders
    identically (a needless MISS -- safe), never the reverse (a false HIT
    serving another drawing's answer -- unsafe). Two inventories that differ
    only below the key's rounding must therefore render identically in the
    prompt. This fails if anyone coarsens a key field or adds precision to
    prompt._row."""
    from archiagent.classify.prompt import _row

    # Key rounds p50 to 3dp; _row prints it to 1dp. A difference at the 5th
    # decimal is invisible to BOTH.
    a, b = _stats("L", p50=23.20000)[0], _stats("L", p50=23.200004)[0]
    assert inventory_key((a,), "m") == inventory_key((b,), "m")
    assert _row(a) == _row(b)

    # A difference the PROMPT can see must change the key.
    c = _stats("L", p50=23.3)[0]
    assert _row(a) != _row(c)
    assert inventory_key((a,), "m") != inventory_key((c,), "m")


def test_a_wall_less_classification_is_not_cached(isolated_cache):
    """If every layer comes back ignore, extract() raises "no layers were
    classified as walls". Persisting that makes the failure STICKY: the key
    does not change when the user retries, so every later run reproduces it
    without ever calling the model."""
    decisions = (LayerDecision("a", Role.IGNORE, 0.0, "unanswered", "default"),)
    inner = _CountingClassifier(decisions)
    stats = _stats("a")

    CachingClassifier(inner, model="m").classify(stats)
    CachingClassifier(inner, model="m").classify(stats)

    assert inner.calls == 2
    assert list(isolated_cache.glob("*.json")) == []


def test_a_cache_entry_with_a_bogus_source_is_a_miss(isolated_cache):
    """`source` drives validate(): only "default" warns. A hand-edited "LLM"
    or "model" would permanently silence layer_unclassified for that layer."""
    isolated_cache.mkdir(parents=True, exist_ok=True)
    (isolated_cache / "bogus.json").write_text(json.dumps([
        {"layer": "a", "role": "wall_structural", "confidence": 0.9,
         "reason": "", "source": "LLM"}]))
    assert read_cache("bogus") is None


def test_a_hit_naming_different_layers_than_the_drawing_is_a_miss(
        isolated_cache):
    """read_cache rebuilds each decision's layer from JSON, not from the
    current stats, so a corrupt entry is the one route by which a layer name
    absent from the drawing could reach layers_for_roles."""
    stats = _stats("a")
    key = inventory_key(stats, "m")
    write_cache(key, (LayerDecision("GHOST", Role.WALL_STRUCTURAL, 0.9, "",
                                    "llm"),))
    inner = _CountingClassifier(
        (LayerDecision("a", Role.WALL_STRUCTURAL, 0.9, "", "llm"),))

    out = CachingClassifier(inner, model="m").classify(stats)

    assert inner.calls == 1
    assert [d.layer for d in out] == ["a"]


def test_write_leaves_no_temporary_file_behind(isolated_cache):
    """write_cache renames a .tmp into place so a crash mid-write cannot
    leave a half-written entry under the real key."""
    write_cache("k", (LayerDecision("a", Role.WALL_STRUCTURAL, 0.9, "", "llm"),))
    assert [p.name for p in sorted(isolated_cache.iterdir())] == ["k.json"]
