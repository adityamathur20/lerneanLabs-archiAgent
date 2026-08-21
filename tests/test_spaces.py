import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces, space_boundary_graph
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def test_closed_ring_yields_exactly_one_space():
    spaces = detect_spaces(resolve_junctions(_ring()))
    assert len(spaces) == 1
    assert spaces[0].area_sqft == pytest.approx(100.0)


def test_ring_with_a_missing_wall_yields_no_space():
    spaces = detect_spaces(resolve_junctions(_ring()[:3]))
    assert spaces == ()


def test_divided_ring_yields_two_spaces():
    walls = _ring(10.0) + [_w((5.0, 0.0), (5.0, 10.0))]
    spaces = detect_spaces(resolve_junctions(walls))
    assert len(spaces) == 2
    assert sorted(s.area_sqft for s in spaces) == pytest.approx([50.0, 50.0])


def test_slivers_below_the_area_floor_are_dropped():
    walls = _ring(10.0) + [_w((0.05, 0.0), (0.05, 10.0))]
    spaces = detect_spaces(resolve_junctions(walls), min_area_sqft=10.0)
    assert all(s.area_sqft >= 10.0 for s in spaces)


def test_boundary_is_a_closed_simple_polygon():
    spaces = detect_spaces(resolve_junctions(_ring()))
    boundary = spaces[0].boundary
    assert boundary[0] == boundary[-1]
    assert len(set(boundary)) == 4


def test_space_boundary_graph_bridges_a_doorway_the_wall_graph_leaves_open():
    """A wall that stops 2ft short of the corner it should meet -- a doorway
    right at the corner, the common real-drawing pattern where a partition
    is drafted short of an exterior wall to leave a swing clearance. The
    ordinary wall graph (6in undershoot budget) leaves it as a free end, so
    detect_spaces on it yields nothing. The door-width boundary graph (48in
    budget) closes the 24in undershoot and recovers the room -- without
    mutating the original wall list."""
    short_left_wall = _w((0.0, 10.0), (0.0, 2.0))  # 2ft short of the (0,0) corner
    walls = [short_left_wall] + _ring(10.0)[:3]  # bottom, right, top; left is the short one

    assert detect_spaces(resolve_junctions(walls)) == ()

    graph = space_boundary_graph(walls)
    spaces = detect_spaces(graph)
    assert len(spaces) == 1
    assert spaces[0].area_sqft == pytest.approx(100.0, abs=1.0)

    # the input walls themselves must be untouched -- the doorway gap survives
    assert walls[0].end == (0.0, 2.0)


def test_real_drawing_yields_spaces_only_with_bridging(demolition_pdf):
    """Documents the M1-M5 ceiling. The wall graph alone is a forest -- every
    room has a door, and doors are real gaps -- so it yields no closed rings.
    The separate door-width boundary graph recovers some. Coverage stays
    partial until the hatch-body wall detector lands in M6."""
    from archiagent.ingest.pdf_vector import load_pdf
    from archiagent.scale.dimensions import extract_dimensions
    from archiagent.scale.resolve import candidate_runs, resolve_scale
    from archiagent.geometry.walls import detect_walls_paired_lines
    from archiagent.geometry.junctions import resolve_junctions

    ps = load_pdf(demolition_pdf)
    sc = resolve_scale(extract_dimensions(ps), candidate_runs(ps, {"walll", "wall"}))
    walls = detect_walls_paired_lines(ps, {"walll", "wall"}, sc.units_per_foot)

    assert detect_spaces(resolve_junctions(walls)) == ()          # forest: no rings
    bridged = detect_spaces(space_boundary_graph(walls), min_area_sqft=15.0)
    assert len(bridged) >= 1
    assert all(s.boundary[0] == s.boundary[-1] for s in bridged)
