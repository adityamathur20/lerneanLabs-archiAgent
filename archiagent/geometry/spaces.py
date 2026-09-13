"""Closed rooms and footprints from supported boundaries, with explicit voids.

Doorway continuity is supplied as virtual edges by semantic opening detection.
Missing exterior boundaries are never replaced with bounding boxes or hulls.
Unlabelled nested rings alone cannot distinguish a courtyard from a room;
callers must provide authoritative void rings to retain those exclusions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, polygonize, unary_union

from archiagent.geometry.junctions import WallGraph, resolve_junctions
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt


@dataclass(frozen=True)
class Space:
    boundary: tuple[Pt, ...]
    area_sqft: float
    holes: tuple[tuple[Pt, ...], ...] = ()


def _ring(coords):
    points = [tuple(round(v, 9) for v in p) for p in list(coords)[:-1]]
    if not points:
        return ()
    # Stable ring orientation/start for overlays, serialization and tests.
    options = []
    for seq in (points, list(reversed(points))):
        start = min(range(len(seq)), key=lambda i: seq[i])
        ring = seq[start:] + seq[:start]
        options.append(tuple(ring + ring[:1]))
    return min(options)


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [p for g in getattr(geometry, "geoms", ()) for p in _polygons(g)]


def _spaces(polygons, min_area):
    out = [Space(_ring(p.exterior.coords), p.area,
                 tuple(sorted(_ring(r.coords) for r in p.interiors)))
           for p in polygons if p.area >= min_area]
    return tuple(sorted(out, key=lambda s: (-s.area_sqft, s.boundary, s.holes)))


def _cells(graph):
    lines = [LineString((w.start, w.end)) for w in graph.walls if w.length_ft > 1e-9]
    return list(polygonize(unary_union(lines))) if lines else []


def _void_geometry(voids):
    polygons = []
    for ring in voids:
        p = Polygon(ring)
        if p.is_empty or not p.is_valid or p.area <= 0:
            raise ValueError("void rings must define valid positive-area polygons")
        polygons.append(p)
    return unary_union(polygons)


def _wall_bodies(walls):
    groups = {}
    for w in walls:
        if w.thickness_ft > 0 and w.length_ft > 1e-9 and w.detector != "virtual-opening":
            groups.setdefault(w.thickness_ft, []).append(LineString((w.start, w.end)))
    out = []
    for thickness, lines in sorted(groups.items()):
        noded = unary_union(lines)
        merged = noded if noded.geom_type == "LineString" else linemerge(noded)
        # Merge contiguous runs before buffering to retain mitred L corners.
        out.append(merged.buffer(thickness / 2, cap_style=2, join_style=2))
    return out


def detect_spaces(graph: WallGraph, min_area_sqft: float = 10.0,
                  *, clear_boundary: bool = False,
                  voids: tuple[tuple[Pt, ...], ...] = ()) -> tuple[Space, ...]:
    """Polygonize room boundaries; optionally return clear wall-face boundaries.

    Virtual opening edges divide cells but have no physical wall thickness.
    """
    if not math.isfinite(min_area_sqft) or min_area_sqft < 0:
        raise ValueError("min_area_sqft must be finite and nonnegative")
    exclusions = _void_geometry(voids)
    if clear_boundary:
        bodies = _wall_bodies(graph.walls)
        exclusions = unary_union([exclusions, *bodies])
    polygons = [p for cell in _cells(graph) for p in _polygons(cell.difference(exclusions))]
    return _spaces(polygons, min_area_sqft)


def detect_footprints(graph: WallGraph,
                      voids: tuple[tuple[Pt, ...], ...] = ()) -> tuple[Space, ...]:
    """Union closed cells and their supported wall bodies, subtracting voids.

    A closed room inside an incomplete drawing is only a partial footprint
    candidate. Completeness must be validated against exterior source evidence;
    this function does not claim the largest closed cell is the entire building.
    """
    exclusions = _void_geometry(voids)
    cells = _cells(graph)
    if not cells:
        return ()
    enclosed = unary_union(cells)
    supported = []
    for w in graph.walls:
        if w.thickness_ft <= 0 or w.detector == "virtual-opening":
            continue
        line = LineString((w.start, w.end))
        # Do not turn dangling walls into unsupported floor projections.
        if line.difference(enclosed).length <= 1e-7:
            supported.append(w)
    footprint = unary_union([enclosed, *_wall_bodies(supported)]).difference(exclusions)
    return _spaces(_polygons(footprint), 1e-9)


def room_graph_with_openings(walls: tuple[WallSeg, ...] | list[WallSeg],
                             openings) -> WallGraph:
    """Keep doorway axes virtual while subtracting only real ground-level wall.

    Windows above a sill remain physical room boundaries. Only hosted doors
    or generic openings starting at floor level remove threshold wall area;
    the caller retains the original full-height hosts for IFC and footprints.
    """
    intervals = {}
    for opening in openings:
        if opening.kind not in {"door", "opening"} or opening.sill_ft > 1e-7:
            continue
        if not math.isfinite(opening.sill_ft) or opening.sill_ft < 0:
            raise ValueError("opening sill must be finite and nonnegative")
        index = opening.host_wall_index
        if not 0 <= index < len(walls):
            raise ValueError("opening references a missing host wall")
        wall = walls[index]
        if wall.length_ft <= 1e-9:
            raise ValueError("opening host must have positive length")
        dx = (wall.end[0]-wall.start[0])/wall.length_ft
        dy = (wall.end[1]-wall.start[1])/wall.length_ft
        positions = []
        for p in (opening.start, opening.end):
            x, y = p[0]-wall.start[0], p[1]-wall.start[1]
            along, offset = x*dx+y*dy, abs(x*dy-y*dx)
            if (not math.isfinite(along) or not math.isfinite(offset)
                    or offset > 1e-5 or along < -1e-5 or along > wall.length_ft+1e-5):
                raise ValueError("opening endpoints must lie on their host span")
            positions.append(min(wall.length_ft, max(0.0, along)))
        lo, hi = sorted(positions)
        if hi-lo <= 1e-9:
            raise ValueError("opening width must be positive")
        intervals.setdefault(index, []).append((lo, hi, opening.source_ids))
    segments = []
    for index, wall in enumerate(walls):
        spans = intervals.get(index)
        if not spans:
            segments.append(wall)
            continue
        length = wall.length_ft
        dx = (wall.end[0]-wall.start[0])/length
        dy = (wall.end[1]-wall.start[1])/length
        cuts = sorted({0.0, length, *(p for lo, hi, _ in spans for p in (lo, hi))})
        for lo, hi in zip(cuts, cuts[1:]):
            if hi-lo <= 1e-9:
                continue
            mid = (lo+hi)/2
            covered = [ids for a,b,ids in spans if a <= mid <= b]
            start = (wall.start[0]+lo*dx, wall.start[1]+lo*dy)
            end = (wall.start[0]+hi*dx, wall.start[1]+hi*dy)
            if covered:
                ids = tuple(sorted(set(wall.source_ids).union(*covered)))
                segments.append(replace(wall, start=start, end=end,
                                        thickness_ft=0.0, detector="virtual-opening", source_ids=ids))
            else:
                segments.append(replace(wall, start=start, end=end))
    return space_boundary_graph(segments)


def space_boundary_graph(walls: tuple[WallSeg, ...] | list[WallSeg],
                         snap_in: float = 1.0,
                         bridge_in: float = 48.0,
                         virtual_edges: tuple[WallSeg, ...] = ()) -> WallGraph:
    """Build room topology using only explicitly supported opening edges.

    bridge_in is accepted for compatibility and intentionally has no geometric
    effect. Call with already resolved physical walls; there is no extra
    drafting snap/extension pass which could silently close an unknown gap.
    """
    for value, name in ((snap_in, "snap_in"), (bridge_in, "bridge_in")):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    return resolve_junctions(tuple(walls) + tuple(virtual_edges), snap_in=0,
                             extend_in=0, min_dangle_ft=0)
