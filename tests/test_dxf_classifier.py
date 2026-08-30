from pathlib import Path

import pytest

from archiagent.classify.dxf_classifier import DEFAULT_CACHE_DIR, DxfLayerClassifier
from archiagent.classify.escalate import ESCALATION_CAP
from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=0.9, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 10, 10),
                      length_p10=1.0, length_p50=5.0, length_p90=9.0,
                      entity_share=share)


class FakeClient:
    def __init__(self, stage1, stage2=None):
        self.stage1, self.stage2 = stage1, stage2
        self.vision_calls = 0

    def classify_json(self, **kw):
        return self.stage1

    def classify_json_vision(self, **kw):
        self.vision_calls += 1
        self.last_images = kw["images"]
        return self.stage2


def _reply(pairs):
    return {"layers": [{"name": n, "role": r, "confidence": c, "reason": "x"}
                       for n, r, c in pairs]}


def test_stage1_only_when_vision_is_off(tmp_path):
    c = FakeClient(_reply([("0", "ignore", 0.95), ("WALLS", "wall_structural", 0.9)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549), _s("WALLS", 0.05)))}
    assert out["0"].role is Role.IGNORE
    assert c.vision_calls == 0


def test_escalation_skipped_is_reported_when_vision_is_off(tmp_path):
    issues = []
    c = FakeClient(_reply([("0", "ignore", 0.95)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False,
                             on_issue=issues.append)
    clf.classify((_s("0", 0.549),))
    codes = [i.code for i in issues]
    assert "layer_escalation_skipped" in codes
    assert any("0" in i.entity for i in issues)


def test_stage2_revises_the_role_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"\x89PNG ref")
    (tmp_path / "0.png").write_bytes(b"\x89PNG zero")

    issues = []
    c = FakeClient(_reply([("0", "ignore", 0.95)]),
                   _reply([("0", "wall_structural", 0.92)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.WALL_STRUCTURAL
    assert c.vision_calls == 1
    assert "layer_role_revised" in [i.code for i in issues]


def test_stage2_sees_the_reference_image_first(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"REF")
    (tmp_path / "0.png").write_bytes(b"ZERO")
    c = FakeClient(_reply([("0", "ignore", 0.5)]), _reply([("0", "ignore", 0.6)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True, cache_dir=tmp_path)
    clf.classify((_s("0", 0.5),))
    assert c.last_images[0][1] == b"REF"


def test_a_broken_stage2_reply_keeps_stage1(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"R"); (tmp_path / "0.png").write_bytes(b"Z")
    issues = []
    c = FakeClient(_reply([("0", "furniture", 0.95)]), {"garbage": True})
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.FURNITURE          # stage 1 survives
    assert issues                                    # and it was reported


def test_missing_renderer_degrades_to_stage1(tmp_path, monkeypatch):
    from archiagent.classify.thumbnails import RenderUnavailable

    def boom(*a, **k):
        raise RenderUnavailable("no matplotlib")

    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation", boom)
    issues = []
    c = FakeClient(_reply([("0", "furniture", 0.95)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.FURNITURE
    assert issues


def test_quoted_layer_names_in_a_reply_still_match(tmp_path):
    """Live testing showed models echoing our JSON quotes into the name."""
    c = FakeClient(_reply([('"WALLS"', "wall_structural", 0.9)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False)
    out = {d.layer: d for d in clf.classify((_s("WALLS", 0.4),))}
    assert out["WALLS"].role is Role.WALL_STRUCTURAL
    assert out["WALLS"].source == "llm"


def test_layers_dropped_by_the_escalation_cap_are_all_reported(tmp_path):
    """More than ESCALATION_CAP layers trigger escalation. The cap keeps
    the vision call cheap, but a layer it drops must not fall through with
    zero signal -- it is exactly as untrustworthy as the ones the cap
    keeps."""
    names = [f"L{i}" for i in range(ESCALATION_CAP + 2)]
    # Descending shares so ordering (by -entity_share) is exactly L0..L7,
    # and the cap keeps the first ESCALATION_CAP, drops the rest.
    stats = tuple(_s(name, 0.9 - i * 0.1) for i, name in enumerate(names))
    c = FakeClient(_reply([(name, "ignore", 0.5) for name in names]))
    issues = []
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False,
                             on_issue=issues.append)
    clf.classify(stats)

    reported = {i.entity for i in issues}
    assert reported == set(names)                   # nobody falls through

    for name in names[ESCALATION_CAP:]:
        msgs = [i.msg for i in issues if i.entity == name]
        assert any("dropped by the escalation cap" in m for m in msgs)
    for name in names[:ESCALATION_CAP]:
        msgs = [i.msg for i in issues if i.entity == name]
        assert any("vision is off" in m for m in msgs)


def test_a_layer_missing_from_the_render_map_is_reported_even_if_others_render(
        tmp_path, monkeypatch):
    """3 candidates escalate; GRID has no entities and render_for_escalation
    omits it while "0" and "WALLS" render fine. GRID must still get an
    Issue, and the other two must still go to vision."""
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png",
                         {"0": tmp_path / "0.png", "WALLS": tmp_path / "WALLS.png"}))
    (tmp_path / "ref.png").write_bytes(b"REF")
    (tmp_path / "0.png").write_bytes(b"ZERO")
    (tmp_path / "WALLS.png").write_bytes(b"WALLS")

    issues = []
    c = FakeClient(
        _reply([("0", "ignore", 0.5), ("WALLS", "ignore", 0.5),
               ("GRID", "ignore", 0.5)]),
        _reply([("0", "wall_structural", 0.9), ("WALLS", "wall_structural", 0.9)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    clf.classify((_s("0", 0.5), _s("WALLS", 0.4), _s("GRID", 0.3)))

    assert c.vision_calls == 1
    assert [label for label, _ in c.last_images] == [
        "reference", "layer 0", "layer WALLS"]

    grid_issues = [i for i in issues if i.entity == "GRID"]
    assert grid_issues
    assert grid_issues[0].code == "layer_escalation_skipped"
    assert "render unavailable" in grid_issues[0].msg


def test_default_cache_dir_is_scoped_to_the_engagement():
    """.archiagent-cache/ is git-ignored and scoped to this engagement --
    unlike a home cache directory, it is not a shared bucket that
    accumulates pixel-perfect renders of every client's drawing."""
    assert DEFAULT_CACHE_DIR == Path(".archiagent-cache") / "thumbnails"


def test_no_cache_dir_argument_resolves_under_dot_archiagent_cache(
        tmp_path, monkeypatch):
    captured = {}

    def fake_render(dxf_path, layers, cache_dir):
        captured["cache_dir"] = cache_dir
        return tmp_path / "ref.png", {}

    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation", fake_render)
    (tmp_path / "ref.png").write_bytes(b"REF")

    c = FakeClient(_reply([("0", "ignore", 0.5)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True)  # cache_dir omitted
    clf.classify((_s("0", 0.5),))

    assert captured["cache_dir"] == Path(".archiagent-cache") / "thumbnails"
