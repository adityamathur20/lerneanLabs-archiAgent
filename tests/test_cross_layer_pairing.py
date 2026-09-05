"""A wall is built from ONE layer's own geometry.

_pair_family used to pair any two parallel segments in the selected layer
set, regardless of which layer each came from. Measured on PLAN.dxf: a layer
named sSTAIR yielded 5 walls when its own geometry yields only 2 -- the other
3 were stair lines paired against lines on WALL and 0. Layers named window,
DIM, CHAMBER and NAMED FAME yielded walls the same way.

That also made layer classification meaningless: every layer could be
classified correctly and stair walls would still appear, because the pairing
reached across into layers the user never approved.
"""
from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.primitives import Primitive, PrimitiveSet


def _ps(*prims):
    return PrimitiveSet(tuple(prims), (), 100.0, 100.0, "t", "h")


def _line(x0, y0, x1, y1, layer):
    return Primitive("line", ((x0, y0), (x1, y1)), layer, None, None)


def test_two_faces_on_one_layer_make_a_wall():
    ps = _ps(_line(0, 0, 20, 0, "WALL"), _line(0, 0.5, 20, 0.5, "WALL"))
    walls = detect_walls_paired_lines(ps, {"WALL"}, 1.0)
    assert len(walls) == 1
    assert walls[0].source_layer == "WALL"


def test_faces_on_different_layers_do_not_make_a_wall():
    """The regression: a stair line and a wall line, a plausible thickness
    apart, must not become a wall."""
    ps = _ps(_line(0, 0, 20, 0, "WALL"), _line(0, 0.5, 20, 0.5, "sSTAIR"))
    walls = detect_walls_paired_lines(ps, {"WALL", "sSTAIR"}, 1.0)
    assert walls == ()


def test_each_layer_still_pairs_within_itself_when_both_are_selected():
    ps = _ps(_line(0, 0, 20, 0, "WALL"), _line(0, 0.5, 20, 0.5, "WALL"),
             _line(0, 40, 20, 40, "PARTITION"), _line(0, 40.5, 20, 40.5, "PARTITION"))
    walls = detect_walls_paired_lines(ps, {"WALL", "PARTITION"}, 1.0)
    assert {w.source_layer for w in walls} == {"WALL", "PARTITION"}
    assert len(walls) == 2


def test_a_nearer_cross_layer_face_does_not_block_the_real_pairing():
    """An intruding line from another layer sits between the two real faces.
    It must be skipped, not consume the pairing and leave the wall unfound."""
    ps = _ps(_line(0, 0, 20, 0, "WALL"),
             _line(0, 0.25, 20, 0.25, "sSTAIR"),
             _line(0, 0.5, 20, 0.5, "WALL"))
    walls = detect_walls_paired_lines(ps, {"WALL", "sSTAIR"}, 1.0)
    assert len(walls) == 1
    assert walls[0].source_layer == "WALL"
