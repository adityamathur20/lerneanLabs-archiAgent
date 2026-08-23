from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import (WALL_ROLES, LayerDecision,
                                        StubClassifier, layers_for_roles)
from archiagent.classify.roles import Role


def _stats(*names):
    return tuple(
        LayerStats(name=n, path_count=1, segment_count=1,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 1.0, 1.0),
                   length_p10=1.0, length_p50=1.0, length_p90=1.0)
        for n in names
    )


def test_decision_defaults_reason_and_source():
    d = LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.9)
    assert d.reason == ""
    assert d.source == "llm"


def test_stub_returns_decisions_in_inventory_order():
    stub = StubClassifier({"b": (Role.WALL_STRUCTURAL, 0.9)})
    out = stub.classify(_stats("a", "b", "c"))
    assert [d.layer for d in out] == ["a", "b", "c"]
    assert out[1].role is Role.WALL_STRUCTURAL
    assert out[1].confidence == 0.9


def test_stub_marks_every_layer_manual_including_unmapped_ones():
    """A partial --walls map is a deliberate statement that the rest are
    not walls. Marking the remainder 'default' would make validate() emit a
    warning per untouched layer and drown the real output."""
    out = StubClassifier({"b": (Role.WALL_STRUCTURAL, 0.9)}).classify(_stats("a", "b"))
    assert {d.source for d in out} == {"manual"}
    assert out[0].role is Role.IGNORE
    assert out[0].confidence == 0.0


def test_layers_for_roles_reads_the_tuple():
    c = (
        LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.98, "", "llm"),
        LayerDecision("walll", Role.WALL_PARTITION, 0.90, "", "llm"),
        LayerDecision("BEAM", Role.BEAM_OVERHEAD, 0.95, "", "llm"),
    )
    assert layers_for_roles(c, WALL_ROLES) == {"WALLS", "walll"}
    assert layers_for_roles(c, WALL_ROLES, min_confidence=0.95) == {"WALLS"}
