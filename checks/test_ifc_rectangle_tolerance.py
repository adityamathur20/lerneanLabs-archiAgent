"""A rectangular wall run far from the origin is still a rectangle.

Coordinates are rounded to a nanofoot, so a small wall 30,000 ft from the origin
misses its bounding rectangle by a few parts in 1e9 of its own area. A purely
relative tolerance of 1e-9 then calls a perfect rectangle "not a rectangle" and
the IFC check reports wall_profile_not_parametric (seen on RAJEEV, 2026-10-13).
"""
import pytest
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon

from archiagent.ifc.author import is_rectangular

FAR = (30826.123456789, -3300.987654321)


def rounded_rectangle(width, height, degrees):
    base = Polygon([(0, 0), (width, 0), (width, height), (0, height)])
    moved = translate(rotate(base, degrees, origin=(0, 0)), *FAR)
    return Polygon([(round(x, 9), round(y, 9)) for x, y in moved.exterior.coords])


@pytest.mark.parametrize("degrees", [3, 11, 17, 29, 44, 61, 77])
@pytest.mark.parametrize("size", [(0.75, 0.8125), (0.375, 1.5), (0.5, 3.7969)])
def test_a_rounded_rectangle_far_from_the_origin_is_a_rectangle(size, degrees):
    r = rounded_rectangle(*size, degrees)
    assert is_rectangular(r.area, r.minimum_rotated_rectangle.area, r.length)


def test_an_angled_joint_is_still_not_a_rectangle():
    # One end cut at 45 degrees: a trapezoid, nowhere near its bounding rectangle.
    trapezoid = Polygon([(0, 0), (4, 0), (3.25, 0.75), (0, 0.75)])
    moved = translate(trapezoid, *FAR)
    assert not is_rectangular(moved.area, moved.minimum_rotated_rectangle.area, moved.length)


def test_a_sliver_of_a_percent_is_not_forgiven():
    a = 10.0
    assert not is_rectangular(a, a * 1.001, 14.0)


def test_the_perimeter_is_optional_and_the_old_call_still_works():
    assert is_rectangular(4.0, 4.0)
    assert not is_rectangular(4.0, 4.1)
