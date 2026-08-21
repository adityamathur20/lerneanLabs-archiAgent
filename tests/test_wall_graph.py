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


def _asymmetric_ring():
    """A non-square shape with a nib wall (v1) sitting between the end of the
    bottom wall (h) and the real right wall (v2), so h can reach v2 only by
    hopping through v1 -- two individually-within-budget steps that must NOT
    compound into one over-budget jump. This is the exact shape of the order-
    dependence bug Fix 1 addressed: pre-fix, processing h before v1 let its
    running endpoint (already snapped to v1) get re-measured against v2 and
    jump straight through it; processing v1 first did not.

    v1 is deliberately shorter than the room height (6ft, not 8ft) so it
    never comes within budget of the top wall -- only h is ambiguous here.
    Nothing in this shape is symmetric under reflection or rotation, so a
    wrongly-extended wall shows up as a different endpoint, not hidden by
    coincidental symmetry.
    """
    return [
        _w((0.0, 0.0), (9.6, 0.0)),          # h: undershoots v1 by 4.8in
        _w((10.0, 0.0), (10.0, 6.0)),        # v1: the nib -- h's real target
        _w((10.4, 0.0), (10.4, 8.0)),        # v2: the real right wall
        _w((10.4, 8.0), (0.0, 8.0)),         # top: closes v2 and the left side
        _w((0.0, 8.0), (0.0, 0.0)),          # left: closes back down to h's start
    ]


def _wall_key(w):
    return (w.start, w.end, w.thickness_ft)


def test_resolution_is_order_independent():
    """A weaker version of this test compared junction POINTS only, on the
    symmetric square ring -- it passed even under the order-dependent
    extension bug (walls could be extended differently depending on
    processing order while still landing on the same junction points by
    symmetry). Compare the full resolved wall geometry instead, and use an
    asymmetric fixture where a wrong extension would show up as a different
    wall endpoint, not hide behind symmetry.
    """
    fwd = resolve_junctions(_asymmetric_ring())
    rev = resolve_junctions(list(reversed(_asymmetric_ring())))
    assert sorted(_wall_key(w) for w in fwd.walls) == \
           sorted(_wall_key(w) for w in rev.walls)
    assert sorted(j.point for j in fwd.junctions) == \
           sorted(j.point for j in rev.junctions)


def test_resolution_is_idempotent():
    once = resolve_junctions(_ring())
    twice = resolve_junctions(list(once.walls))
    assert sorted(j.point for j in once.junctions) == \
           sorted(j.point for j in twice.junctions)


def test_dangle_chain_prunes_to_a_fixpoint():
    """Removing one dangle exposes the next. A single pass computes every degree
    BEFORE any removal, so it keeps the chain's inner link: at evaluation time
    that link still sees its outer neighbour.

    Thresholds are deliberately separated. With the defaults, extend_in (6in) and
    min_dangle_ft (0.5ft) are the same length, so any wall short enough to be a
    dangle is also short enough for extension to fuse it — the fixpoint branch is
    unreachable. The chain is diagonal to avoid collinearity with the ring.
    """
    chain = [_w((10.0, 10.0), (11.0, 11.0)),   # inner link, touches the corner
             _w((11.0, 11.0), (12.0, 12.0))]   # outer link, free end
    graph = resolve_junctions(_ring() + chain, min_dangle_ft=2.0)
    assert len(graph.walls) == 4
    assert graph.unresolved == ()
