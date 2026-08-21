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
