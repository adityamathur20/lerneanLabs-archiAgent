"""Vision escalation goes out in batches, not one giant call.

Measured: 4 rendered layers to a reasoning model took 7m18s. One call
carrying 20 would exceed the SDK's 600s ceiling and return NOTHING -- not a
partial classification. Batches bound each request, let independent batches
run together, and mean one failure costs four layers rather than all of them.
"""
import threading

import pytest

from archiagent.classify.dxf_classifier import (VISION_BATCH_SIZE,
                                                DxfLayerClassifier, _batches)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=0.9, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 10, 10),
                      length_p10=1.0, length_p50=5.0, length_p90=9.0,
                      entity_share=share)


# ---- the grouping rule -------------------------------------------------

def test_batches_are_capped_at_the_batch_size():
    cands = [(f"L{i}", "low_confidence") for i in range(9)]
    out = _batches(cands, 4)
    assert [len(b) for b in out] == [4, 4, 1]


def test_wall_rivals_ride_together():
    """competing_wall_layers only means anything side by side: the question
    is which of two confident wall layers is really the walls. Splitting them
    across batches destroys the comparison the trigger exists to make."""
    cands = ([(f"F{i}", "low_confidence") for i in range(6)]
             + [("WALL", "competing_wall_layers"),
                ("WALLS", "competing_wall_layers")])
    out = _batches(cands, 4)
    rival_batch = next(b for b in out if any(n == "WALL" for n, _ in b))
    assert {n for n, _ in rival_batch} == {"WALL", "WALLS"}


def test_no_empty_batches():
    assert _batches([], 4) == []
    assert all(b for b in _batches([("A", "low_confidence")], 4))


def test_every_candidate_lands_in_exactly_one_batch():
    cands = ([(f"F{i}", "low_confidence") for i in range(7)]
             + [("W1", "competing_wall_layers"), ("W2", "competing_wall_layers")])
    flat = [c for b in _batches(cands, 4) for c in b]
    assert sorted(flat) == sorted(cands)


# ---- the calls ---------------------------------------------------------

class _Client:
    """Records each vision call. Optionally fails for a named layer."""

    def __init__(self, reply_role="wall_structural", fail_on=None):
        self.calls = []
        self.lock = threading.Lock()
        self._role = reply_role
        self._fail_on = fail_on

    def classify_json(self, **kw):
        return {"layers": []}

    def classify_json_vision(self, **kw):
        labels = [lbl for lbl, _ in kw["images"]]
        with self.lock:
            self.calls.append(labels)
        names = [l[len("layer "):] for l in labels if l.startswith("layer ")]
        if self._fail_on and self._fail_on in names:
            raise RuntimeError("boom")
        return {"layers": [{"name": n, "role": self._role,
                            "confidence": 0.95, "reason": "seen"} for n in names]}


def _classifier(tmp_path, client, monkeypatch, layers, batch_size=4):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png",
                         {n: tmp_path / f"{n}.png" for n in layers}))
    (tmp_path / "ref.png").write_bytes(b"REF")
    for n in layers:
        (tmp_path / f"{n}.png").write_bytes(f"IMG{n}".encode())
    return DxfLayerClassifier(client, tmp_path / "x.dxf", vision=True,
                              cache_dir=tmp_path, batch_size=batch_size)


def test_nine_layers_go_out_as_three_calls(tmp_path, monkeypatch):
    layers = [f"L{i}" for i in range(9)]
    c = _Client()
    clf = _classifier(tmp_path, c, monkeypatch, layers)
    clf._escalate((), tuple(_s(n, 0.5) for n in layers),
                  tuple((n, "low_confidence") for n in layers))
    assert len(c.calls) == 3
    assert sorted(len(x) - 1 for x in c.calls) == [1, 4, 4]   # minus reference


def test_every_batch_carries_the_reference_image_first(tmp_path, monkeypatch):
    """A layer means nothing except relative to its drawing, so the reference
    cannot be sent only in the first batch."""
    layers = [f"L{i}" for i in range(9)]
    c = _Client()
    clf = _classifier(tmp_path, c, monkeypatch, layers)
    clf._escalate((), tuple(_s(n, 0.5) for n in layers),
                  tuple((n, "low_confidence") for n in layers))
    assert all(labels[0] == "reference" for labels in c.calls)


def test_one_failing_batch_does_not_lose_the_others(tmp_path, monkeypatch):
    """The whole point: a single 400 used to wipe out every escalated layer."""
    from archiagent.classify.layers import LayerDecision
    layers = [f"L{i}" for i in range(8)]
    issues = []
    c = _Client(fail_on="L0")
    clf = _classifier(tmp_path, c, monkeypatch, layers)
    clf._on_issue = issues.append
    stage1 = tuple(LayerDecision(n, Role.FURNITURE, 0.5, "", "llm") for n in layers)
    out = {d.layer: d for d in clf._escalate(
        stage1, tuple(_s(n, 0.5) for n in layers),
        tuple((n, "low_confidence") for n in layers))}

    # the surviving batch was applied
    assert out["L4"].role is Role.WALL_STRUCTURAL
    # the failed batch kept stage 1 and was reported
    assert out["L0"].role is Role.FURNITURE
    assert any(i.code == "layer_escalation_skipped" and i.entity == "L0"
               for i in issues)


def test_the_default_batch_size_is_four():
    assert VISION_BATCH_SIZE == 4
