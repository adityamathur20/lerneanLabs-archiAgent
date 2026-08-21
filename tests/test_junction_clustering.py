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
