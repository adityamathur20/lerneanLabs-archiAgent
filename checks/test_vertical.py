"""Floors checked against each other at assembly: voids over rooms, double heights under voids, stairs aligned."""
from dataclasses import replace

from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.semantic import SymbolInstance
from archiagent.vertical import check_vertical
from checks.test_semantic_ifc import fixture_model


def square(x0, y0, x1, y1, **kw):
    return Space(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), abs((x1 - x0) * (y1 - y0)), **kw)


def floor(elevation, dx=0.0, spaces=(), symbols=()):
    walls = tuple(WallSeg((x0 + dx, y0), (x1 + dx, y1), 0.75, "W", "paired-line", "measured", (f"w{i}",))
                  for i, (x0, y0, x1, y1) in enumerate([(0, 0, 30, 0), (30, 0, 30, 20), (30, 20, 0, 20), (0, 20, 0, 0)]))
    moved = tuple(replace(s, boundary=tuple((x + dx, y) for x, y in s.boundary)) for s in spaces)
    syms = tuple(replace(s, position=(s.position[0] + dx, s.position[1]),
                         boundary=tuple((x + dx, y) for x, y in s.boundary)) for s in symbols)
    return replace(fixture_model(), walls=walls, spaces=moved, symbols=syms, openings=(), junctions=(),
                   elevation_ft=elevation, region_id=f"f{elevation}")


def codes(models):
    return sorted(i.code for i in check_vertical(models))


def test_a_void_over_a_room_is_consistent_even_when_the_floors_sit_side_by_side_on_the_sheet():
    ground = floor(0, spaces=(square(2, 2, 12, 12, region_class="room", double_height=True),))
    first = floor(10, dx=100, spaces=(square(2, 2, 12, 12, region_class="void"),))
    assert codes((ground, first)) == []


def test_a_void_with_nothing_below_is_reported():
    ground = floor(0, spaces=(square(20, 2, 28, 10),))
    first = floor(10, spaces=(square(2, 2, 12, 12, region_class="void"),))
    assert "void_without_room_below" in codes((ground, first))


def test_a_double_height_room_without_a_void_above_is_reported():
    ground = floor(0, spaces=(square(2, 2, 12, 12, double_height=True),))
    first = floor(10, spaces=(square(2, 2, 12, 12, region_class="room"),))
    assert "double_height_without_void_above" in codes((ground, first))


def test_stairs_that_do_not_line_up_are_reported():
    s0 = SymbolInstance("s0", "stair", (5, 5), 4, 8, boundary=((3, 1), (7, 1), (7, 9), (3, 9)))
    s1 = SymbolInstance("s1", "stair", (25, 15), 4, 8, boundary=((23, 11), (27, 11), (27, 19), (23, 19)))
    assert "stair_not_aligned" in codes((floor(0, symbols=(s0,)), floor(10, symbols=(s1,))))
    assert "stair_not_aligned" not in codes((floor(0, symbols=(s0,)), floor(10, dx=50, symbols=(s0,))))


def test_one_floor_has_nothing_to_check():
    assert codes((floor(0),)) == []
