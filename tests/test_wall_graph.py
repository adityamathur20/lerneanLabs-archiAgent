import pytest

from archiagent.geometry.junctions import (WallGraph, resolve_junctions,
                                           split_through_walls)
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


def test_split_through_wall_at_a_t_junction():
    through = _w((0.0, 0.0), (20.0, 0.0))
    parts = split_through_walls([through], [(10.0, 0.0)], tol_in=1.0)
    assert len(parts) == 2
    assert parts[0].end == pytest.approx((10.0, 0.0))
    assert parts[1].start == pytest.approx((10.0, 0.0))


def test_split_ignores_nodes_at_the_ends():
    through = _w((0.0, 0.0), (20.0, 0.0))
    parts = split_through_walls([through], [(0.0, 0.0), (20.0, 0.0)], tol_in=1.0)
    assert len(parts) == 1


def test_closed_ring_produces_four_corner_junctions():
    graph = resolve_junctions(_ring())
    assert len(graph.junctions) == 4
    assert all(j.kind == "L" for j in graph.junctions)
    assert graph.unresolved == ()


def test_t_junction_is_classified_and_splits_the_through_wall():
    walls = _ring() + [_w((5.0, 0.0), (5.0, 10.0))]
    graph = resolve_junctions(walls)
    kinds = {j.kind for j in graph.junctions}
    assert "T" in kinds
    # bottom and top walls each split in two -> 4 ring walls become 6
    assert len(graph.walls) == 7


def test_open_ring_reports_an_unresolved_endpoint():
    walls = _ring()[:3]  # leave one side missing
    graph = resolve_junctions(walls)
    assert len(graph.unresolved) == 2


def test_dangle_below_minimum_length_is_pruned():
    walls = _ring() + [_w((5.0, 0.0), (5.0, 0.25))]
    graph = resolve_junctions(walls, min_dangle_ft=0.5)
    assert len(graph.walls) == 4


def test_resolution_is_order_independent():
    fwd = resolve_junctions(_ring())
    rev = resolve_junctions(list(reversed(_ring())))
    assert sorted(j.point for j in fwd.junctions) == \
           sorted(j.point for j in rev.junctions)


def test_resolution_is_idempotent():
    once = resolve_junctions(_ring())
    twice = resolve_junctions(list(once.walls))
    assert sorted(j.point for j in once.junctions) == \
           sorted(j.point for j in twice.junctions)
