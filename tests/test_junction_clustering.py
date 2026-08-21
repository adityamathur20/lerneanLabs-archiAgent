import pytest

from archiagent.geometry.junctions import cluster_endpoints
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def test_near_miss_endpoints_snap_to_one_node():
    """0.5in apart, inside the 1in tolerance -> one shared node."""
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0 + 0.5 / 12, 8.0))
    walls, nodes = cluster_endpoints([a, b], snap_in=1.0)
    assert walls[0].end == walls[1].start
    assert len(nodes) == 3


def test_endpoints_beyond_tolerance_stay_separate():
    """3in apart -> two distinct nodes; a doorway must not be sealed."""
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 3.0 / 12, 0.0), (10.0 + 3.0 / 12, 8.0))
    walls, nodes = cluster_endpoints([a, b], snap_in=1.0)
    assert walls[0].end != walls[1].start
    assert len(nodes) == 4


def test_two_walls_meeting_at_a_corner_share_one_node():
    walls_in = [
        _w((0.0, 0.0), (10.0, 0.0)),
        _w((10.0 + 0.2 / 12, 0.0), (10.0, 8.0)),
    ]
    walls, nodes = cluster_endpoints(walls_in, snap_in=1.0)
    assert len(nodes) == 3


def test_clustering_is_idempotent():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0, 8.0))
    once, _ = cluster_endpoints([a, b], snap_in=1.0)
    twice, _ = cluster_endpoints(list(once), snap_in=1.0)
    assert once == twice


def test_clustering_is_order_independent():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0, 8.0))
    fwd, nodes_fwd = cluster_endpoints([a, b], snap_in=1.0)
    rev, nodes_rev = cluster_endpoints([b, a], snap_in=1.0)
    assert sorted(nodes_fwd) == sorted(nodes_rev)


def test_preserves_wall_attributes():
    a = _w((0.0, 0.0), (10.0, 0.0), t_in=8.0)
    walls, _ = cluster_endpoints([a], snap_in=1.0)
    assert walls[0].thickness_ft == pytest.approx(8 / 12)
    assert walls[0].source_layer == "WALLS"
    assert walls[0].thickness_source == "measured"


def test_clustering_chains_transitively_by_design():
    """Single-linkage: A-B and B-C are each 0.6in apart so both merge, pulling in
    C even though A-C spans 1.2in and exceeds the 1in tolerance. This is intended —
    a multi-wall corner with accumulated drafting error should collapse to one node.

    Documented rather than guarded: bridging a real 30in doorway would need ~30
    endpoints spaced <=1in along it, and paired-line detection emits exactly two
    endpoints per wall. On the real drawing every cluster has diameter 0.00in.

    Spacings are deliberately 0.6in, not 1.0in: asserting threshold behaviour AT
    the threshold is decided by floating-point noise, not by the algorithm.
    """
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.6 / 12, 0.0), (10.0 + 0.6 / 12, 8.0))
    c = _w((10.0 + 1.2 / 12, 0.0), (10.0 + 1.2 / 12, -8.0))
    walls, nodes = cluster_endpoints([a, b, c], snap_in=1.0)
    assert walls[0].end == walls[1].start == walls[2].start
    assert len(nodes) == 4


def test_empty_wall_list_returns_empty():
    assert cluster_endpoints([], snap_in=1.0) == ((), ())


def test_zero_length_wall_collapses_to_one_node():
    """A degenerate wall must not crash clustering; Task 10 drops these later."""
    z = _w((5.0, 5.0), (5.0, 5.0))
    walls, nodes = cluster_endpoints([z], snap_in=1.0)
    assert walls[0].start == walls[0].end == (5.0, 5.0)
    assert len(nodes) == 1
