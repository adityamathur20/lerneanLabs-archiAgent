"""Comparable shapes shared by the drawing-seeded and library-seeded matchers.

A Part is one drawing primitive expressed in feet, with the signature that
decides whether two shapes are even worth comparing geometrically. Both matchers
build their candidate pool from the same function, so "the same shape" means one
thing in this codebase, not two.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from shapely.geometry import LineString, MultiLineString, MultiPoint


@dataclass(frozen=True)
class Part:
    index: int
    source_id: str
    layer: str
    geometry: object
    signature: tuple
    center: tuple[float, float]
    length: float
    points: tuple
    # Retained so a harvester can rebuild a template's rings; the signature
    # already folds in their vertex counts.
    holes: tuple = ()


def parts_from(ps, units_per_foot) -> tuple[Part, ...]:
    entities = {e.id: e for e in ps.entities}
    out = []
    for i, p in enumerate(ps.primitives):
        if not p.source_id or len(p.coords) < 2:
            continue
        e = entities.get(p.source_id)
        if e is not None and dict(e.metadata).get("region_partial") == "true":
            continue
        coords = tuple((x/units_per_foot, y/units_per_foot) for x, y in p.coords)
        if not all(math.isfinite(v) for point in coords for v in point):
            raise ValueError("template source coordinates must be finite")
        closed = p.closed or p.kind in {"rect", "fill"} or coords[0] == coords[-1]
        if closed and coords[0] != coords[-1]:
            coords += coords[:1]
        if len(set(coords)) < 2:
            continue
        holes = tuple(tuple((x/units_per_foot, y/units_per_foot) for x, y in ring)
                      for ring in (e.holes if e else ()))
        geometry = MultiLineString((coords, *holes)) if holes else LineString(coords)
        category = ("fill" if p.kind == "fill" else "curve" if p.kind == "curve"
                    else "closed" if closed else "path")
        signature = (category, len(coords)-1, tuple(sorted(len(ring) for ring in holes)))
        center = (geometry.centroid.x, geometry.centroid.y)
        out.append(Part(i, p.source_id, p.layer, geometry, signature, center,
                        geometry.length, coords, holes))
    return tuple(sorted(out, key=lambda p: (p.source_id, p.layer, p.points, p.index)))


def measure(parts):
    """Reduce matched parts to (position, width, depth, orientation, boundary).

    Returns None when the points cannot form a measurable extent.
    """
    points = [point for p in parts for point in p.points]
    rect = MultiPoint(points).minimum_rotated_rectangle
    if rect.geom_type == "Polygon":
        corners = list(rect.exterior.coords)
        sides = [(math.dist(a, b), a, b) for a, b in zip(corners, corners[1:])]
        width, a, b = max(sides)
        depth = min(s[0] for s in sides)
        boundary = tuple(corners)
    elif rect.geom_type == "LineString":
        a, b = sorted((tuple(rect.coords[0]), tuple(rect.coords[-1])))
        width = math.dist(a, b)
        depth = 0.0
        boundary = (a, b)
    else:
        return None
    orientation = math.atan2(b[1]-a[1], b[0]-a[0]) % math.pi
    return (rect.centroid.x, rect.centroid.y), width, depth, orientation, boundary
