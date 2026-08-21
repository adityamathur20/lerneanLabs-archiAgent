"""Junction resolution — R2, the pipeline's critical path.

Room detection is ENTIRELY downstream of this module: one unhealed
junction leaks two rooms into one and the error propagates silently into
every later phase. See PLAN.md §7 Stage 3b for the taxonomy.

Tolerances are in INCHES, never pixels, and are always reported.
"""

from __future__ import annotations

import math
from dataclasses import replace

from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[max(ri, rj)] = min(ri, rj)


def cluster_endpoints(walls: list[WallSeg] | tuple[WallSeg, ...],
                      snap_in: float = 1.0
                      ) -> tuple[tuple[WallSeg, ...], tuple[Pt, ...]]:
    """Snap near-miss endpoints onto shared nodes.

    Returns the walls with endpoints moved to their cluster centroid, and
    the sorted list of distinct nodes.
    """
    walls = list(walls)
    if not walls:
        return (), ()

    snap_ft = snap_in / 12.0
    points: list[Pt] = []
    for w in walls:
        points.append(w.start)
        points.append(w.end)

    uf = _UnionFind(len(points))
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if math.dist(points[i], points[j]) <= snap_ft:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(len(points)):
        groups.setdefault(uf.find(idx), []).append(idx)

    centroid: dict[int, Pt] = {}
    for root, members in groups.items():
        cx = sum(points[m][0] for m in members) / len(members)
        cy = sum(points[m][1] for m in members) / len(members)
        centroid[root] = (cx, cy)

    out: list[WallSeg] = []
    for k, w in enumerate(walls):
        out.append(replace(w,
                           start=centroid[uf.find(2 * k)],
                           end=centroid[uf.find(2 * k + 1)]))

    return tuple(out), tuple(sorted(set(centroid.values())))


def _line_intersection(a0: Pt, a1: Pt, b0: Pt, b1: Pt) -> Pt | None:
    """Intersection of two INFINITE lines, or None if parallel."""
    x1, y1 = a0
    x2, y2 = a1
    x3, y3 = b0
    x4, y4 = b1
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-12:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
    return (px, py)


def _param_on(p: Pt, a: Pt, b: Pt) -> float:
    """Where p falls along a->b: 0 at a, 1 at b, outside [0,1] beyond the ends."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom < 1e-18:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denom


def extend_to_intersections(walls: list[WallSeg] | tuple[WallSeg, ...],
                            extend_in: float = 6.0) -> tuple[WallSeg, ...]:
    """Close undershoots and trim overshoots at wall intersections.

    A gap within `extend_in` is sloppy drafting and is closed. A gap
    beyond it is a DOORWAY and is left alone — sealing doorways would
    merge rooms and look like success while being badly wrong.
    """
    walls = list(walls)
    budget_ft = extend_in / 12.0
    starts = [w.start for w in walls]
    ends = [w.end for w in walls]

    for i, wi in enumerate(walls):
        for j, wj in enumerate(walls):
            if i == j:
                continue
            hit = _line_intersection(starts[i], ends[i], wj.start, wj.end)
            if hit is None:
                continue

            # The intersection must lie on (or within a hair of) wall j's body,
            # otherwise these two walls do not actually meet.
            tj = _param_on(hit, wj.start, wj.end)
            if not (-1e-9 <= tj <= 1.0 + 1e-9):
                continue

            if math.dist(hit, ends[i]) <= budget_ft:
                ends[i] = hit
            elif math.dist(hit, starts[i]) <= budget_ft:
                starts[i] = hit

    return tuple(replace(w, start=starts[k], end=ends[k])
                 for k, w in enumerate(walls))
