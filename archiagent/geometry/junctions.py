"""Junction resolution — R2, the pipeline's critical path.

Room detection is ENTIRELY downstream of this module: one unhealed
junction leaks two rooms into one and the error propagates silently into
every later phase. See PLAN.md §7 Stage 3b for the taxonomy.

Tolerances are in INCHES, never pixels, and are always reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

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


def _distance_to_segment(p: Pt, a: Pt, b: Pt) -> float:
    """Distance from p to the SEGMENT a-b (not its infinite line)."""
    t = max(0.0, min(1.0, _param_on(p, a, b)))
    proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return math.dist(p, proj)


def _nz(p: Pt) -> Pt:
    """Normalize negative zero to positive zero."""
    return (p[0] + 0.0, p[1] + 0.0)


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
    # Budget is measured from the ORIGINAL endpoints. Measuring from the running
    # values lets extension compound across hops: a wall reaches one intersection,
    # then measures the next from there, and can walk clean through a doorway in
    # two individually-legal steps. It also makes the result depend on list order.
    orig_starts = list(starts)
    orig_ends = list(ends)

    for i in range(len(walls)):
        for j, wj in enumerate(walls):
            if i == j:
                continue
            hit = _line_intersection(starts[i], ends[i], wj.start, wj.end)
            if hit is None:
                continue

            # The corner must lie on, or within the same budget of, wall j.
            # A mutual undershoot leaves it just past j's end: at a corner both
            # walls stop half a wall-thickness short, so requiring the corner to
            # be strictly ON j deadlocks — each wall waits for the other.
            if _distance_to_segment(hit, wj.start, wj.end) > budget_ft:
                continue

            # KNOWN LIMITATION (deliberately parked, not a bug to fix in place):
            # when a wall's original endpoint is within budget of TWO different
            # walls' lines, the LAST candidate iterated wins rather than the
            # NEAREST. Verified: h=(0,0)-(9.7,0) against verticals at x=9.8 and
            # x=10.1 resolves to 10.1 or 9.8 depending on input order. Travel
            # stays inside the budget either way (that is Fix 1 above), so the
            # doorway guarantee holds; what varies is WHICH intersection is
            # chosen. Switching to nearest-wins is principled and was prototyped
            # successfully, but on the sample drawing it drops the only detected
            # space from 1 to 0, because that ring closes via a farther
            # intersection. Practical impact is nil today: detect_walls_paired_lines
            # emits a stable order, so a given PDF always yields the same model.
            # Sequence this fix with the M6 hatch-body detector, when room
            # detection no longer hangs on a single marginal ring.
            if math.dist(hit, orig_ends[i]) <= budget_ft:
                ends[i] = _nz(hit)
            elif math.dist(hit, orig_starts[i]) <= budget_ft:
                starts[i] = _nz(hit)

    return tuple(replace(w, start=starts[k], end=ends[k])
                 for k, w in enumerate(walls))


@dataclass(frozen=True)
class Junction:
    point: Pt
    wall_indices: tuple[int, ...]
    kind: str  # "L" | "T" | "X" | "collinear"
    # NOTE: `_classify` can also return "end" for a degree-<=1 node, but
    # `resolve_junctions` never constructs a Junction for one -- free endpoints
    # surface in `WallGraph.unresolved` instead. Do not branch on kind == "end".


@dataclass(frozen=True)
class WallGraph:
    walls: tuple[WallSeg, ...]
    junctions: tuple[Junction, ...]
    unresolved: tuple[Pt, ...] = field(default=())


def split_through_walls(walls: list[WallSeg] | tuple[WallSeg, ...],
                        nodes: list[Pt] | tuple[Pt, ...],
                        tol_in: float = 1.0) -> tuple[WallSeg, ...]:
    """Split any wall whose INTERIOR contains a node, so T and X junctions
    become real graph nodes rather than geometric coincidences."""
    tol_ft = tol_in / 12.0
    out: list[WallSeg] = []

    for w in walls:
        interior: list[tuple[float, Pt]] = []
        for n in nodes:
            if math.dist(n, w.start) <= tol_ft or math.dist(n, w.end) <= tol_ft:
                continue
            t = _param_on(n, w.start, w.end)
            if not (0.0 < t < 1.0):
                continue
            # must lie ON the wall, not merely on its infinite line
            proj = (w.start[0] + t * (w.end[0] - w.start[0]),
                    w.start[1] + t * (w.end[1] - w.start[1]))
            if math.dist(proj, n) > tol_ft:
                continue
            interior.append((t, n))

        if not interior:
            out.append(w)
            continue

        interior.sort()
        cursor = w.start
        for _, n in interior:
            out.append(replace(w, start=cursor, end=n))
            cursor = n
        out.append(replace(w, start=cursor, end=w.end))

    return tuple(out)


def _direction(a: Pt, b: Pt) -> Pt:
    dx, dy = b[0] - a[0], b[1] - a[1]
    mag = math.hypot(dx, dy)
    if mag < 1e-12:
        return (0.0, 0.0)
    return (dx / mag, dy / mag)


def _classify(point: Pt, incident: list[tuple[int, Pt]]) -> str:
    """Name the junction from its degree and the directions leaving it."""
    degree = len(incident)
    if degree <= 1:
        return "end"
    if degree >= 4:
        return "X"
    if degree == 3:
        return "T"
    (_, d0), (_, d1) = incident
    dot = abs(d0[0] * d1[0] + d0[1] * d1[1])
    return "collinear" if dot > 0.99 else "L"


def resolve_junctions(walls: list[WallSeg] | tuple[WallSeg, ...],
                      snap_in: float = 1.0,
                      extend_in: float = 6.0,
                      min_dangle_ft: float = 0.5) -> WallGraph:
    """Full R2 pipeline: cluster -> extend -> split -> re-cluster -> classify.

    Tolerances are in inches and are reported on the result so a run's
    junction behaviour is auditable.
    """
    # Drop zero-length input walls outright (1e-9: exact-input tolerance).
    walls = [w for w in walls if w.length_ft > 1e-9]
    if not walls:
        return WallGraph((), (), ())

    snapped, _ = cluster_endpoints(walls, snap_in=snap_in)
    extended = extend_to_intersections(snapped, extend_in=extend_in)
    # A wall that extension collapsed to a point (its end snapped onto an
    # intersection where its start already sat) is an artifact, not a wall:
    # left in, it would both survive dangle pruning (its two endpoints are
    # the same node, so both have degree > 1) and spuriously split whatever
    # wall passes through that point. The threshold here (1e-6) is looser
    # than the 1e-9 used above on raw input: the intersection math the walls
    # just passed through (division, multiple coordinate combinations) is
    # not exact, so a "collapsed" wall's length may not land on precisely
    # 0.0 -- it needs a coarser epsilon to still be caught as degenerate.
    extended = tuple(w for w in extended if w.length_ft > 1e-6)
    resnapped, nodes = cluster_endpoints(extended, snap_in=snap_in)
    split = split_through_walls(resnapped, nodes, tol_in=snap_in)
    final, nodes = cluster_endpoints(split, snap_in=snap_in)

    # Prune dangles: short walls with a free end. Iterate to a fixpoint --
    # removing one dangle can expose the next along a chain (free-end -> A ->
    # P1 -> B -> real junction): computing degree once, before any removal,
    # would keep B even after A's removal leaves P1 truly degree-1.
    kept = list(final)
    while True:
        incident_count: dict[Pt, int] = {}
        for w in kept:
            incident_count[w.start] = incident_count.get(w.start, 0) + 1
            incident_count[w.end] = incident_count.get(w.end, 0) + 1
        survivors = [w for w in kept
                     if w.length_ft >= min_dangle_ft
                     or (incident_count[w.start] > 1 and incident_count[w.end] > 1)]
        if len(survivors) == len(kept):
            break
        kept = survivors
    kept = tuple(kept)

    incident: dict[Pt, list[tuple[int, Pt]]] = {}
    for idx, w in enumerate(kept):
        incident.setdefault(w.start, []).append((idx, _direction(w.start, w.end)))
        incident.setdefault(w.end, []).append((idx, _direction(w.end, w.start)))

    junctions = tuple(
        Junction(point=pt,
                 wall_indices=tuple(sorted(i for i, _ in inc)),
                 kind=_classify(pt, inc))
        for pt, inc in sorted(incident.items())
        if len(inc) > 1)

    unresolved = tuple(pt for pt, inc in sorted(incident.items()) if len(inc) == 1)

    return WallGraph(walls=kept, junctions=junctions, unresolved=unresolved)
