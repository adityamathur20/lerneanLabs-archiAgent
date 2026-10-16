"""Declared wall thickness as evidence for layer classification, before vision.

A layer whose long parallel pairs sit at the thicknesses the user declared is
wall-like; one whose pairs sit at none of them is not. Columns, projection
lines and planters can match too, so this is evidence handed to the model and
to the escalation rule, never a verdict.
"""
import ezdxf
import pytest

from archiagent.classify import cache, prompt
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.escalate import escalation_candidates
from archiagent.classify.inventory import LayerStats, ThicknessEvidence
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
from archiagent.classify.thickness_evidence import attach_thickness_evidence, evidence_from_walls
from archiagent.geometry.walls import WallSeg

DECLARED = (4.5 / 12, 9 / 12)
TOL = 0.75 / 12


def pair(layer, length, thickness_in, y=0.0):
    return WallSeg((0.0, y), (length, y), thickness_in / 12, layer, "paired-line", "measured")


def stats(name, thickness=None, share=0.05):
    return LayerStats(name, 5, 20, .9, (), (), (0, 0, 100, 80), 1, 4, 60,
                      entity_mix=(("LINE", 20),), entity_share=share, thickness=thickness)


def evidence(paired, matched, long_ft):
    return ThicknessEvidence(paired, matched, long_ft, (4.5, 9.0))


def decision(layer, role, confidence):
    return LayerDecision(layer, role, confidence, "r", "llm")


# --- computing it -----------------------------------------------------------------------------


def test_evidence_counts_pairs_at_the_declared_thicknesses():
    walls = tuple(pair("WALL", 10, 9, y=i * 3) for i in range(4)) + (pair("WALL", 6, 3, y=20),)
    ev = evidence_from_walls(walls, DECLARED, TOL)["WALL"]
    assert ev.paired_ft == pytest.approx(46.0)
    assert ev.matched_ft == pytest.approx(40.0)
    assert ev.share == pytest.approx(40 / 46)
    assert ev.long_share == 1.0                  # every matched run is 4 ft or longer


def test_a_tolerance_edge_counts_as_matching():
    ev = evidence_from_walls((pair("WALL", 10, 9.75),), DECLARED, TOL)["WALL"]
    assert ev.matched_ft == pytest.approx(10.0)


def test_short_matching_runs_are_not_long():
    # Column sides: matching thickness but never wall-length.
    walls = tuple(pair("COL", 1.5, 9, y=i * 3) for i in range(10))
    ev = evidence_from_walls(walls, DECLARED, TOL)["COL"]
    assert ev.share == 1.0 and ev.long_share == 0.0


def test_a_layer_with_no_pairs_has_no_evidence_entry():
    assert evidence_from_walls((), DECLARED, TOL) == {}


def test_evidence_from_a_real_dxf_layer(tmp_path):
    doc = ezdxf.new()
    doc.layers.add("WALL")
    doc.layers.add("FURN")
    msp = doc.modelspace()
    for i in range(3):                               # 4.5 in walls, 12 ft long, at 12 units per foot
        y = i * 120
        msp.add_line((0, y), (144, y), dxfattribs={"layer": "WALL"})
        msp.add_line((0, y + 4.5), (144, y + 4.5), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 500), (60, 500), dxfattribs={"layer": "FURN"})
    msp.add_line((0, 503), (60, 503), dxfattribs={"layer": "FURN"})   # 3 in: not declared
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    from archiagent.ingest.dxf_vector import load_dxf
    from archiagent.classify.inventory import build_inventory
    ps, _ = load_dxf(path, 12.0)
    out = attach_thickness_evidence(build_inventory(ps), ps, 12.0, DECLARED, TOL)
    by = {s.name: s.thickness for s in out}
    assert by["WALL"].share == pytest.approx(1.0) and by["WALL"].matched_ft == pytest.approx(36.0)
    assert by["FURN"] is not None and by["FURN"].matched_ft == 0.0


# --- the prompt and the cache -----------------------------------------------------------------


def test_prompt_row_names_the_evidence_only_when_it_exists():
    plain = prompt.build_dxf_user_prompt((stats("A"),))
    shown = prompt.build_dxf_user_prompt((stats("A", evidence(100, 90, 0.9)),))
    assert "thickness" not in plain
    assert "thickness=" in shown and "90" in shown


def test_system_prompt_explains_the_evidence():
    assert "thickness=" in prompt.DXF_SYSTEM_PROMPT


def test_evidence_changes_the_cache_key():
    a = cache.inventory_key((stats("A"),), "m")
    b = cache.inventory_key((stats("A", evidence(100, 90, 0.9)),), "m")
    c = cache.inventory_key((stats("A", evidence(100, 10, 0.9)),), "m")
    assert len({a, b, c}) == 3


# --- escalation -------------------------------------------------------------------------------


def triggers(decisions, inventory):
    return dict(escalation_candidates(tuple(decisions), tuple(inventory)))


def test_without_evidence_escalation_is_unchanged():
    d = [decision("HAT", Role.WALL_PARTITION, .55)]
    assert triggers(d, [stats("HAT")]) == {"HAT": "low_confidence"}


def test_a_low_confidence_wall_layer_with_matching_pairs_is_not_escalated():
    d = [decision("HAT", Role.WALL_PARTITION, .55)]
    s = [stats("HAT", evidence(267, 169, 0.95))]
    assert triggers(d, s) == {}


def test_a_confident_wall_layer_with_no_matching_pairs_is_escalated():
    d = [decision("WALL", Role.WALL_STRUCTURAL, .9)]
    s = [stats("WALL", evidence(120, 0, 0.9))]
    assert triggers(d, s) == {"WALL": "thickness_disagrees"}


def test_a_large_non_wall_layer_with_no_matching_pairs_is_cleared():
    d = [decision("FURN", Role.FURNITURE, .9)]
    s = [stats("FURN", evidence(160, 20, 0.2), share=0.3)]
    assert triggers(d, s) == {}                  # was large_non_wall_layer


def test_a_non_wall_layer_with_wall_like_pairs_is_escalated():
    d = [decision("PROJ", Role.ANNOTATION, .9)]
    s = [stats("PROJ", evidence(300, 250, 0.95))]
    assert triggers(d, s) == {"PROJ": "thickness_suggests_walls"}


def test_a_column_layer_with_matching_short_pairs_is_left_alone():
    d = [decision("COL", Role.COLUMN, .9)]
    s = [stats("COL", evidence(250, 250, 0.0))]
    assert triggers(d, s) == {}


def test_a_small_wall_like_layer_is_not_worth_a_picture():
    d = [decision("SMALL", Role.FURNITURE, .9)]
    s = [stats("SMALL", evidence(30, 30, 1.0))]      # under 40 ft matched
    assert triggers(d, s) == {}


# --- the classifier ---------------------------------------------------------------------------


class Client:
    def __init__(self, role, confidence, layer="HAT"):
        self.reply = {"layers": [{"name": layer, "role": role, "confidence": confidence, "reason": "r"}]}
        self.prompts, self.vision_calls = [], 0

    def classify_json(self, **kw):
        self.prompts.append(kw["user"])
        return self.reply

    def classify_json_vision(self, **kw):
        self.vision_calls += 1
        return self.reply


def test_corroborated_low_confidence_wall_layer_is_raised_to_the_wall_floor_without_vision(tmp_path):
    from archiagent.classify.layers import WALL_CONFIDENCE_FLOOR
    client = Client("wall_partition", .55)
    issues = []
    classifier = DxfLayerClassifier(client, tmp_path / "x.dxf", vision=True, on_issue=issues.append)
    (out,) = classifier.classify((stats("HAT", evidence(267, 169, 0.95)),))
    assert out.role is Role.WALL_PARTITION
    assert out.confidence >= WALL_CONFIDENCE_FLOOR
    assert "thickness" in out.reason
    assert client.vision_calls == 0
    assert any(i.code == "layer_corroborated_by_thickness" for i in issues)
    assert "thickness=" in client.prompts[0]


def test_without_evidence_the_low_confidence_layer_keeps_its_confidence(tmp_path):
    client = Client("wall_partition", .55)
    classifier = DxfLayerClassifier(client, tmp_path / "x.dxf", vision=False)
    (out,) = classifier.classify((stats("HAT"),))
    assert out.confidence == pytest.approx(.55)


def test_non_wall_decisions_are_never_raised(tmp_path):
    client = Client("furniture", .55)
    classifier = DxfLayerClassifier(client, tmp_path / "x.dxf", vision=False)
    (out,) = classifier.classify((stats("HAT", evidence(267, 169, 0.95)),))
    assert out.role is Role.FURNITURE and out.confidence == pytest.approx(.55)


# --- the command line -------------------------------------------------------------------------


def _drawing(tmp_path):
    doc = ezdxf.new()
    doc.layers.add("WALL")
    msp = doc.modelspace()
    for y in (0, 120):
        msp.add_line((0, y), (144, y), dxfattribs={"layer": "WALL"})
        msp.add_line((0, y + 4.5), (144, y + 4.5), dxfattribs={"layer": "WALL"})
    path = tmp_path / "c.dxf"
    doc.saveas(path)
    from archiagent.classify.inventory import build_inventory
    from archiagent.ingest.dxf_vector import load_dxf
    ps, _ = load_dxf(path, 12.0)
    return ps, build_inventory(ps)


def test_cli_attaches_evidence_only_when_thickness_is_declared_and_scale_known(tmp_path):
    from archiagent.cli import _parser, _with_thickness_evidence
    ps, inventory = _drawing(tmp_path)
    base = ["--dxfFilePath", "c.dxf", "--outputDir", "out"]
    declared = _parser().parse_args(base + ["--wall-thickness", "4.5", "9"])
    none = _parser().parse_args(base)
    (with_evidence,) = _with_thickness_evidence(declared, ps, inventory, 12.0)
    assert with_evidence.thickness is not None and with_evidence.thickness.share == pytest.approx(1.0)
    assert _with_thickness_evidence(none, ps, inventory, 12.0) == inventory
    assert _with_thickness_evidence(declared, ps, inventory, None) == inventory


def test_vision_cannot_undo_a_thickness_corroboration(tmp_path, monkeypatch):
    # Two wall layers compete, so both go to vision, which answers 0.60 again.
    from archiagent.classify import dxf_classifier
    from archiagent.classify.layers import WALL_CONFIDENCE_FLOOR

    class TwoLayers(Client):
        def __init__(self):
            self.reply = {"layers": [{"name": n, "role": "wall_partition", "confidence": .6, "reason": "r"}
                                     for n in ("WALL", "HAT")]}
            self.prompts, self.vision_calls = [], 0

    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(dxf_classifier, "render_for_escalation",
                        lambda dxf, names, cache: (png, {n: png for n in names}))
    client = TwoLayers()
    out = DxfLayerClassifier(client, tmp_path / "x.dxf", vision=True).classify(
        (stats("WALL", evidence(1349, 1079, 0.9)), stats("HAT", evidence(267, 169, 0.95))))
    assert client.vision_calls >= 1
    assert all(d.confidence >= WALL_CONFIDENCE_FLOOR for d in out)
