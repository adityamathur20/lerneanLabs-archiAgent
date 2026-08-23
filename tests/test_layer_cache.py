import pytest

from archiagent.classify.cache import (CachingClassifier, cache_dir,
                                       inventory_key, read_cache, write_cache)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision, StubClassifier
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
    decisions = (LayerDecision("a", Role.FURNITURE, 0.8, "short", "llm"),)
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
