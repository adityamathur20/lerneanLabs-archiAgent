"""Door-sized gaps between wall ends, closed for room detection only.

Drawings leave about a third of all wall ends touching nothing: a door or window
interrupts the wall, or one wall stops short of another. A room needs a closed
ring, so those gaps hide most rooms.

A gap is closed here by an explicit virtual edge with no thickness. Walls are
never moved, extended or added, and every closure is recorded with the evidence
that supported it, so a reviewer can see what was assumed.

Evidence, strongest first:
  door-window  a recognised door or window symbol sits on the gap
  collinear    two wall ends face each other along one axis (a wall continues)
  corner       two perpendicular wall ends nearly meet
  stops-short  a wall end stops short of another wall's side
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt

MAX_GAP_FT = 8.0          # a double leaf door; beyond this a "gap" is an open plan
CONNECTED_FT = 0.005      # an end this close to a wall is connected, not a gap
CONE_DEGREES = 35.0       # the target must lie ahead of the wall's end, not beside it
PARALLEL_DEGREES = 8.0
SYMBOL_REACH_FT = 1.0     # a symbol within this of the gap supports it

KINDS = ("door-window", "collinear", "corner", "stops-short")


@dataclass(frozen=True)
class RoomGap:
    start: Pt
    end: Pt
    kind: str
    source_ids: tuple[str, ...] = ()

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)


def _axis(wall: WallSeg):
    n = wall.length_ft or 1.0
    return (wall.end[0] - wall.start[0]) / n, (wall.end[1] - wall.start[1]) / n


def _symbol_geometry(symbol):
    points = list(symbol.boundary)
    if len(set(points)) >= 2:
        return LineString(points)
    return Point(symbol.position).buffer(max(symbol.width_ft, symbol.depth_ft, 0.5) / 2)


def find_room_gaps(walls, symbols=(), max_gap_ft: float = MAX_GAP_FT,
                   connected_ft: float = CONNECTED_FT) -> tuple[RoomGap, ...]:
    """The gaps a room needs closed, each with its evidence kind.

    One candidate per dangling wall end, aimed at the nearest wall ahead of it;
    two ends facing each other yield one gap, not two.
    """
    if not math.isfinite(max_gap_ft) or max_gap_ft < 0:
        raise ValueError("max_gap_ft must be finite and nonnegative")
    walls = list(walls)
    if max_gap_ft == 0 or len(walls) < 2:
        return ()
    lines = [LineString((w.start, w.end)) for w in walls]
    tree = STRtree(lines)
    cone = math.cos(math.radians(CONE_DEGREES))
    parallel = math.cos(math.radians(PARALLEL_DEGREES))
    drawn = [(s, _symbol_geometry(s)) for s in symbols if s.kind in ("door", "window")]
    found: dict[tuple, RoomGap] = {}

    for i, wall in enumerate(walls):
        if wall.length_ft <= 1e-9 or wall.detector == "virtual-opening":
            continue
        ux, uy = _axis(wall)
        for end, (ox, oy) in ((wall.start, (-ux, -uy)), (wall.end, (ux, uy))):
            point = Point(end)
            near = [j for j in tree.query(point.buffer(max_gap_ft)) if j != i]
            if any(lines[j].distance(point) <= connected_ft for j in near):
                continue                                       # this end touches a wall
            best = None
            for j in sorted(near):
                if walls[j].detector == "virtual-opening":
                    continue
                d = lines[j].distance(point)
                if d > max_gap_ft:
                    continue
                q = lines[j].interpolate(lines[j].project(point))
                vx, vy = q.x - end[0], q.y - end[1]
                if (vx * ox + vy * oy) / d < cone:
                    continue
                if best is None or d < best[0] - 1e-12:
                    best = (d, (q.x, q.y), j)
            if best is None:
                continue
            d, target, j = best
            other = walls[j]
            vx, vy = _axis(other)
            aligned = abs(ux * vx + uy * vy) >= parallel
            at_end = min(math.dist(target, other.start), math.dist(target, other.end)) <= 0.05
            drift = abs((target[0] - end[0]) * uy - (target[1] - end[1]) * ux)
            if aligned and at_end and drift <= max(wall.thickness_ft, other.thickness_ft, 0.25) + 0.25:
                kind = "collinear"
            elif at_end:
                kind = "corner"
            else:
                kind = "stops-short"
            ids = set(wall.source_ids) | set(other.source_ids)
            edge = LineString((end, target)).buffer(SYMBOL_REACH_FT)
            for symbol, geometry in drawn:
                if edge.intersects(geometry):
                    kind = "door-window"
                    ids.add(symbol.id)
            key = tuple(sorted((tuple(round(c, 6) for c in end), tuple(round(c, 6) for c in target))))
            previous = found.get(key)
            if previous is None or KINDS.index(kind) < KINDS.index(previous.kind):
                found[key] = RoomGap(tuple(end), (target[0], target[1]), kind, tuple(sorted(ids)))
    return tuple(sorted(found.values(), key=lambda g: (g.start, g.end)))


def gap_edges(gaps) -> tuple[WallSeg, ...]:
    """The gaps as thickness-free virtual edges, for room topology only."""
    return tuple(WallSeg(g.start, g.end, 0.0, "room-gap", "virtual-opening", "default", g.source_ids)
                 for g in gaps if g.length_ft > 1e-9)


def gaps_on_boundary(boundary, gaps, tolerance_ft: float = 1e-6) -> int:
    """How many closed gaps form part of this room's boundary."""
    ring = LineString(boundary)
    count = 0
    for g in gaps:
        mid = Point((g.start[0] + g.end[0]) / 2, (g.start[1] + g.end[1]) / 2)
        if ring.distance(mid) <= tolerance_ft:
            count += 1
    return count
