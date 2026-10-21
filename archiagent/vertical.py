"""Floors checked against each other when they are assembled into one building.

Nothing is moved or fixed: disagreements are reported for review. Floors drawn side by
side on one sheet do not share coordinates, so each floor is registered by the
lower-left corner of its walls' extent before comparing (a stated approximation: it
holds when floors share their outer walls, and is reported as such).
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.ops import unary_union

from archiagent.model import Issue

OVERLAP_SHARE = 0.5


def _origin(model):
    pts = [p for w in model.walls for p in (w.start, w.end)]
    if not pts:
        return (0.0, 0.0)
    return (min(p[0] for p in pts), min(p[1] for p in pts))


def _poly(ring, origin):
    return Polygon([(x - origin[0], y - origin[1]) for x, y in ring])


def _voids(model, origin):
    rings = list(getattr(model, "voids", ())) + [s.boundary for s in model.spaces if s.region_class == "void"]
    return [_poly(r, origin) for r in rings if len(r) >= 3]


def _covered(shape, others) -> bool:
    if not others or shape.area <= 0:
        return False
    return unary_union(others).intersection(shape).area >= OVERLAP_SHARE * shape.area


def check_vertical(models) -> tuple[Issue, ...]:
    floors = sorted(models, key=lambda m: m.elevation_ft or 0.0)
    issues = []
    for below, above in zip(floors, floors[1:]):
        ob, oa = _origin(below), _origin(above)
        rooms_below = [_poly(s.boundary, ob) for s in below.spaces if s.region_class != "void"]
        for v in _voids(above, oa):
            if not _covered(v, rooms_below):
                issues.append(Issue("warn", above.region_id or above.storey_name, "void_without_room_below",
                                    f"a void of {v.area:.0f} sq ft on {above.storey_name or above.region_id} "
                                    f"has no room under it on {below.storey_name or below.region_id}"))
        voids_above = _voids(above, oa)
        for s in below.spaces:
            if s.double_height and not _covered(_poly(s.boundary, ob), voids_above):
                issues.append(Issue("warn", below.region_id or below.storey_name, "double_height_without_void_above",
                                    f"double-height {s.label or 'room'} on {below.storey_name or below.region_id} "
                                    f"has no void over it on {above.storey_name or above.region_id}"))
        stairs_b = [_poly(s.boundary, ob) for s in below.symbols if s.kind == "stair" and len(s.boundary) >= 3]
        stairs_a = [_poly(s.boundary, oa) for s in above.symbols if s.kind == "stair" and len(s.boundary) >= 3]
        if stairs_b and stairs_a and not any(a.intersects(b) for a in stairs_a for b in stairs_b):
            issues.append(Issue("warn", above.region_id or above.storey_name, "stair_not_aligned",
                                f"no stair on {above.storey_name or above.region_id} overlaps a stair on "
                                f"{below.storey_name or below.region_id} (floors registered by wall extents)"))
    return tuple(issues)
