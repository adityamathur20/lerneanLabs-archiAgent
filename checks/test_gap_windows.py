"""A window drawn as thin parallel lines across a gap in a wall, with no symbol."""
import json

import pytest

from archiagent.geometry.gap_windows import find_gap_windows
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.recognition import host_openings


def wall(a, b, t=0.75, sid="w"):
    return WallSeg(a, b, t, "WALL", "paired-line", "measured", (sid,))


def line(x0, y0, x1, y1, sid, layer="WIN"):
    return Primitive("line", ((x0, y0), (x1, y1)), layer, None, None, sid)


def sheet(*primitives):
    return PrimitiveSet(tuple(primitives), (), 100.0, 100.0, "t.dxf", "s" * 64)


WALLS = (wall((0, 0), (10, 0), sid="a"), wall((14, 0), (24, 0), sid="b"))


def test_thin_lines_across_a_collinear_gap_make_a_window():
    ps = sheet(line(10, -0.2, 14, -0.2, "l1"), line(10, 0.0, 14, 0.0, "l2"), line(10, 0.2, 14, 0.2, "l3"))
    (w,) = find_gap_windows(ps, WALLS, (), 1.0)
    props = dict(w.properties)
    assert w.kind == "window" and w.evidence == "wall-gap-lines"
    assert json.loads(props["opening_start_ft"]) == pytest.approx([10, 0])
    assert json.loads(props["opening_end_ft"]) == pytest.approx([14, 0])
    assert set(w.source_ids) == {"l1", "l2", "l3"}


def test_the_synthesised_window_is_hosted_by_the_existing_opening_logic():
    ps = sheet(line(10, -0.2, 14, -0.2, "l1"), line(10, 0.2, 14, 0.2, "l2"))
    windows = find_gap_windows(ps, WALLS, (), 1.0)
    _, openings = host_openings(WALLS, windows, 10.0)
    assert [o.kind for o in openings] == ["window"]
    assert openings[0].width_ft == pytest.approx(4.0)


def test_an_empty_gap_is_not_a_window():
    assert find_gap_windows(sheet(), WALLS, (), 1.0) == ()


def test_one_line_is_not_enough():
    assert find_gap_windows(sheet(line(10, 0, 14, 0, "l1")), WALLS, (), 1.0) == ()


def test_lines_that_do_not_span_the_gap_are_not_a_window():
    ps = sheet(line(10, -0.2, 11, -0.2, "l1"), line(10, 0.2, 11, 0.2, "l2"))
    assert find_gap_windows(ps, WALLS, (), 1.0) == ()


def test_lines_far_from_the_wall_axis_are_not_a_window():
    ps = sheet(line(10, 2.0, 14, 2.0, "l1"), line(10, 2.3, 14, 2.3, "l2"))
    assert find_gap_windows(ps, WALLS, (), 1.0) == ()


def test_a_gap_that_already_has_a_door_or_window_symbol_is_left_alone():
    from archiagent.semantic import SymbolInstance
    existing = SymbolInstance("d1", "door", (12.0, 0.0), 3.0, 0.5, boundary=((11, 0), (13, 0)))
    ps = sheet(line(10, -0.2, 14, -0.2, "l1"), line(10, 0.2, 14, 0.2, "l2"))
    assert find_gap_windows(ps, WALLS, (existing,), 1.0) == ()


def test_lines_already_used_by_a_wall_are_not_evidence():
    walls = (WallSeg((0, 0), (10, 0), 0.75, "WALL", "paired-line", "measured", ("a",)),
             WallSeg((14, 0), (24, 0), 0.75, "WALL", "paired-line", "measured", ("b",)),
             WallSeg((10, 0), (14, 0), 0.75, "WALL", "paired-line", "measured", ("l1", "l2")))
    ps = sheet(line(10, -0.2, 14, -0.2, "l1"), line(10, 0.2, 14, 0.2, "l2"))
    assert find_gap_windows(ps, walls, (), 1.0) == ()


def test_units_per_foot_scales_source_coordinates():
    ps = sheet(line(120, -2.4, 168, -2.4, "l1"), line(120, 2.4, 168, 2.4, "l2"))
    (w,) = find_gap_windows(ps, WALLS, (), 12.0)
    assert json.loads(dict(w.properties)["opening_end_ft"]) == pytest.approx([14, 0])
