"""Room detection by polygonizing a wall boundary graph.

This replaces vision entirely: rooms are not estimated, they are the
bounded faces of a healed wall graph -- a mathematical consequence of
Task 10's junction resolution. Every failure here is a junction failure
upstream.

One wrinkle: every room has a door, and a door is a genuine gap in the
wall run -- Task 9's junction resolution correctly refuses to close a
gap wider than its 6in drafting-slop budget, because doing so would seal
a real doorway shut. That means the ordinary wall graph (`resolve_junctions`
at its default budget) is a forest, not a set of closed rings, and
`detect_spaces` on it alone yields nothing.

A doorway, though, is an opening IN a room boundary -- not a break in
it. So room detection re-resolves the same walls with a door-width
budget via `space_boundary_graph`, producing a SEPARATE graph used only
for polygonization. Wall geometry itself is never touched: it keeps its
real openings, because a doorway must stay a doorway for every stage
that emits or validates wall geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from archiagent.geometry.junctions import WallGraph, resolve_junctions
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt


@dataclass(frozen=True)
class Space:
    boundary: tuple[Pt, ...]  # closed ring: first point repeated at the end
    area_sqft: float


def detect_spaces(graph: WallGraph, min_area_sqft: float = 10.0) -> tuple[Space, ...]:
    if not graph.walls:
        return ()

    lines = [LineString([w.start, w.end]) for w in graph.walls
             if w.start != w.end]
    if not lines:
        return ()

    noded = unary_union(lines)
    out: list[Space] = []
    for poly in polygonize(noded):
        if poly.area < min_area_sqft:
            continue
        ring = tuple((round(x, 6), round(y, 6))
                     for x, y in poly.exterior.coords)
        out.append(Space(boundary=ring, area_sqft=poly.area))

    return tuple(sorted(out, key=lambda s: -s.area_sqft))


def space_boundary_graph(walls: tuple[WallSeg, ...] | list[WallSeg],
                         snap_in: float = 1.0,
                         bridge_in: float = 48.0) -> WallGraph:
    """Build a SEPARATE graph for room detection, bridging door-width gaps.

    Wall geometry keeps its real openings -- a doorway must stay a doorway.
    But a doorway is an opening IN a room boundary, not a break in it, so
    for space detection we re-resolve the same walls with a door-width
    budget instead of the 6in drafting-slop budget. The result is used
    ONLY for polygonization; it never replaces the wall graph and is never
    emitted as geometry.
    """
    return resolve_junctions(walls, snap_in=snap_in, extend_in=bridge_in)
