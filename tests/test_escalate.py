import pytest
from archiagent.classify.escalate import (
    ESCALATION_CAP, escalation_candidates, is_uninformative,
    select_for_escalation)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=1.0, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 1, 1),
                      length_p10=0.0, length_p50=1.0, length_p90=1.0,
                      entity_share=share)


def _d(name, role, conf):
    return LayerDecision(name, role, conf, "", "llm")


@pytest.mark.parametrize("name,expected", [
    ("0", True), ("Defpoints", True), ("123", True), ("A", True),
    ("WALLS", False), ("COLUM HATCH", False), ("win", False),
])
def test_uninformative_names(name, expected):
    assert is_uninformative(name) is expected


def test_low_confidence_escalates():
    d = (_d("FURN", Role.FURNITURE, 0.69),)
    s = (_s("FURN"),)
    assert [n for n, _ in escalation_candidates(d, s)] == ["FURN"]


def test_confidence_exactly_at_the_floor_does_not_escalate():
    d = (_d("FURN", Role.FURNITURE, 0.70),)
    assert escalation_candidates(d, (_s("FURN"),)) == ()


def test_big_non_wall_layer_escalates():
    """The layer-0 trap: 54.9% of Floor Plan.dxf sits on layer '0', and the
    walls are there. Confidence alone would never flag it."""
    d = (_d("0", Role.IGNORE, 0.95),)
    s = (_s("0", share=0.549),)
    assert [t for _, t in escalation_candidates(d, s)][0] == "large_non_wall_layer"


def test_big_wall_layer_does_not_escalate_on_share():
    d = (_d("WALLS", Role.WALL_STRUCTURAL, 0.95),)
    s = (_s("WALLS", share=0.549),)
    assert escalation_candidates(d, s) == ()


def test_column_role_counts_as_structural_for_the_share_trigger():
    d = (_d("COL", Role.COLUMN, 0.95),)
    assert escalation_candidates(d, (_s("COL", share=0.3),)) == ()


def test_two_wall_layers_both_escalate():
    """WALL vs WALLS: the electrical plan's open question."""
    d = (_d("WALL", Role.WALL_STRUCTURAL, 0.95),
         _d("WALLS", Role.WALL_PARTITION, 0.95))
    s = (_s("WALL", 0.02), _s("WALLS", 0.03))
    assert sorted(n for n, _ in escalation_candidates(d, s)) == ["WALL", "WALLS"]


def test_ordering_is_by_descending_entity_share():
    d = tuple(_d(f"L{i}", Role.FURNITURE, 0.5) for i in range(3))
    s = (_s("L0", 0.1), _s("L1", 0.5), _s("L2", 0.3))
    assert [n for n, _ in escalation_candidates(d, s)] == ["L1", "L2", "L0"]


def test_cap_keeps_the_largest_and_drops_the_rest():
    d = tuple(_d(f"L{i}", Role.FURNITURE, 0.5) for i in range(9))
    s = tuple(_s(f"L{i}", (9 - i) / 100) for i in range(9))
    assert len(escalation_candidates(d, s)) == 9
    picked = select_for_escalation(d, s)
    assert len(picked) == ESCALATION_CAP
    assert [n for n, _ in picked] == [f"L{i}" for i in range(ESCALATION_CAP)]


def test_a_layer_escalates_once_even_when_several_triggers_fire():
    d = (_d("0", Role.IGNORE, 0.2),)          # low confidence AND uninformative
    s = (_s("0", share=0.5),)                  # AND large non-wall
    assert len(escalation_candidates(d, s)) == 1
