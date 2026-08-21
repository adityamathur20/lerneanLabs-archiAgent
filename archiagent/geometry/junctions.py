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
