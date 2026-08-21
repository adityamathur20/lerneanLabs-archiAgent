import pytest

from archiagent.geometry.junctions import extend_to_intersections
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def test_undershoot_l_corner_extends_to_meet():
    """Horizontal stops 3in short of the vertical -> extend to the corner."""
    h = _w((0.0, 0.0), (10.0 - 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_overshoot_l_corner_trims_back():
    """Horizontal runs 3in past the vertical -> trim to the corner."""
    h = _w((0.0, 0.0), (10.0 + 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_gap_beyond_budget_is_left_open():
    """A 24in gap is a doorway. It MUST NOT be closed."""
    h = _w((0.0, 0.0), (10.0 - 24.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((8.0, 0.0))


def test_t_junction_extends_the_terminating_wall():
    """Vertical stops 2in short of a horizontal's middle."""
    h = _w((0.0, 0.0), (20.0, 0.0))
    v = _w((10.0, 2.0 / 12), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[1].start == pytest.approx((10.0, 0.0))
    assert out[0].start == pytest.approx((0.0, 0.0))  # through-wall untouched
    assert out[0].end == pytest.approx((20.0, 0.0))


def test_parallel_walls_are_never_joined():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 2.0 / 12, 0.0), (20.0, 0.0))
    out = extend_to_intersections([a, b], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_extension_is_idempotent():
    h = _w((0.0, 0.0), (10.0 - 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    once = extend_to_intersections([h, v], extend_in=6.0)
    twice = extend_to_intersections(list(once), extend_in=6.0)
    assert once == twice


def test_mutual_undershoot_still_meets():
    """Both walls stop 2in short of their shared corner — the normal case, since
    paired-line detection ends each centerline half a wall-thickness short. A guard
    requiring the corner to lie strictly on the other wall deadlocks here: each
    wall waits for the other to arrive first."""
    h = _w((0.0, 0.0), (10.0 - 2.0 / 12, 0.0))
    v = _w((10.0, 2.0 / 12), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))
    assert out[1].start == pytest.approx((10.0, 0.0))
    assert out[0].end == out[1].start


def test_extension_budget_is_measured_from_original_endpoints():
    """Regression: budget must not compound across hops within one call.

    h stops 4.8in short of v1 (x=10.0), which itself sits 4.8in short of v2
    (x=10.4). Each hop is individually within the 6in budget, but if the
    budget is measured from the RUNNING endpoint rather than the original one,
    h can walk through v1 to v2 in two legal-looking steps -- covering 9.6in
    total, over budget, and walking straight through what could be a doorway.
    The correct behaviour is for h to stop at v1 (4.8in travel) regardless of
    the order walls are processed in.
    """
    h = _w((0.0, 0.0), (9.6, 0.0))
    v1 = _w((10.0, 0.0), (10.0, 8.0))
    v2 = _w((10.4, 0.0), (10.4, 8.0))

    out_fwd = extend_to_intersections([h, v1, v2], extend_in=6.0)
    out_rev = extend_to_intersections([h, v2, v1], extend_in=6.0)

    assert out_fwd[0].end == pytest.approx((10.0, 0.0))
    assert out_rev[0].end == pytest.approx((10.0, 0.0))
    assert out_fwd[0].end == out_rev[0].end

    travel_fwd = abs(out_fwd[0].end[0] - h.end[0])
    travel_rev = abs(out_rev[0].end[0] - h.end[0])
    assert travel_fwd <= 6.0 / 12.0
    assert travel_rev <= 6.0 / 12.0


def test_mixed_budget_leaves_a_doorway_open():
    """One wall is 2in short (within budget), the other 20in short (its own
    doorway). The guard on wall j must re-block independently: neither wall may
    close. This is the riskiest geometry the distance-to-segment guard admits —
    without it, loosening that guard could seal a doorway invisibly."""
    h = _w((0.0, 0.0), (10.0 - 2.0 / 12, 0.0))
    v = _w((10.0, 20.0 / 12), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0 - 2.0 / 12, 0.0))
    assert out[1].start == pytest.approx((10.0, 20.0 / 12))
    assert out[0].end != out[1].start
