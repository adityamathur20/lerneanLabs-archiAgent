import pytest

from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.primitives import Primitive, PrimitiveSet


def _ps(lines, layer="WALLS"):
    prims = tuple(Primitive("line", (a, b), layer, None, None) for a, b in lines)
    return PrimitiveSet(primitives=prims, texts=(), width=1000.0, height=1000.0,
                        source_path="x", source_sha256="y")


def test_pairs_two_parallel_lines_into_one_wall():
    """Two horizontal lines 4in apart (scale 12pt/ft -> 4in = 4.0pt)."""
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 4.0), (120.0, 4.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    w = walls[0]
    assert w.thickness_ft == pytest.approx(4 / 12, abs=1e-6)
    assert w.start == pytest.approx((0.0, 1 / 6))   # centerline y = 2pt = 1/6 ft
    assert w.length_ft == pytest.approx(10.0)
    assert w.thickness_source == "measured"


def test_detects_vertical_walls():
    ps = _ps([((0.0, 0.0), (0.0, 120.0)), ((4.0, 0.0), (4.0, 120.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    assert not walls[0].is_horizontal
    assert walls[0].length_ft == pytest.approx(10.0)


def test_rejects_pairs_that_are_too_far_apart_to_be_a_wall():
    """36pt at 12pt/ft is 36in — a room, not a wall."""
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 36.0), (120.0, 36.0))])
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_rejects_pairs_with_no_overlap():
    ps = _ps([((0.0, 0.0), (50.0, 0.0)), ((80.0, 4.0), (130.0, 4.0))])
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_ignores_layers_not_requested():
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 4.0), (120.0, 4.0))], layer="FURN")
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_wall_spans_only_the_overlapping_extent():
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((24.0, 4.0), (180.0, 4.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    assert walls[0].start[0] == pytest.approx(2.0)   # 24pt = 2ft
    assert walls[0].end[0] == pytest.approx(10.0)    # 120pt = 10ft
