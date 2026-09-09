"""A layer the model barely believes in must not build geometry unannounced.

layers_for_roles defaults min_confidence to 0.0 and the pipeline passed
nothing, so a layer at 5% confidence contributed walls exactly as heavily as
one at 95%. It was flagged for a second look, but with vision off it still
went straight into the model.
"""
import pytest

from archiagent.classify.layers import (WALL_CONFIDENCE_FLOOR, LayerDecision,
                                        StubClassifier)
from archiagent.classify.roles import Role
from archiagent.ingest.dxf_vector import load_dxf
from archiagent.pipeline import _no_wall_layers, extract_from_dxf


def _d(name, conf, role=Role.WALL_STRUCTURAL):
    return LayerDecision(name, role, conf, "", "llm")


def test_a_doubtful_wall_layer_is_refused_and_named():
    with pytest.raises(ValueError, match="confidence floor") as e:
        _no_wall_layers((_d("WALLS", 0.5),))
    msg = str(e.value)
    assert "WALLS" in msg and "50%" in msg
    assert "--walls" in msg          # the escape hatch is offered


def test_nothing_wall_shaped_gives_the_other_message():
    with pytest.raises(ValueError, match="no layers were classified as walls"):
        _no_wall_layers((_d("FURN", 0.95, Role.FURNITURE),))


def test_the_two_failures_are_distinguishable():
    """They want different fixes, so they must not share a message."""
    with pytest.raises(ValueError) as unsure:
        _no_wall_layers((_d("WALLS", 0.5),))
    with pytest.raises(ValueError) as absent:
        _no_wall_layers((_d("FURN", 0.95, Role.FURNITURE),))
    assert str(unsure.value) != str(absent.value)


def test_manual_walls_clear_the_floor(tmp_path):
    """--walls uses MANUAL_WALL_CONFIDENCE = 1.0, so naming a layer by hand
    must never be blocked by a floor meant for the model's guesses."""
    import ezdxf
    doc = ezdxf.new(); doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for y in (0, 8):        # 8 units at 12 units/ft = an 8in wall
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "W"})
    p = tmp_path / "t.dxf"; doc.saveas(p)

    ps, upf = load_dxf(p)
    model = extract_from_dxf(ps, StubClassifier({"W": (Role.WALL_STRUCTURAL, 1.0)}),
                             units_per_foot=upf)
    assert len(model.walls) >= 1


def test_the_floor_matches_the_escalation_floor():
    """One number, not two: the doubt that sends a layer to the vision stage
    is the doubt that keeps it out of the geometry."""
    from archiagent.classify.escalate import CONFIDENCE_FLOOR
    assert WALL_CONFIDENCE_FLOOR == CONFIDENCE_FLOOR
