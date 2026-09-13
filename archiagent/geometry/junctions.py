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


def _nonnegative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def _wall_key(w):
    return (min(w.start, w.end), max(w.start, w.end), w.thickness_ft,
            w.source_layer, w.source_ids)


def cluster_endpoints(walls: list[WallSeg] | tuple[WallSeg, ...],
                      snap_in: float = 1.0
                      ) -> tuple[tuple[WallSeg, ...], tuple[Pt, ...]]:
    """Deterministic complete-link clusters; no transitive tolerance growth.

    Separated collinear ends are not drafting slop: joining them could seal
    an opening. A cluster also never collapses the two ends of one wall.
    """
    _nonnegative(snap_in, "snap_in")
    points = [(p, i, end) for i, w in enumerate(walls)
              for end, p in enumerate((w.start, w.end))]
    groups = []
    for item in sorted(points, key=lambda item: (item[0], _wall_key(walls[item[1]]), item[2])):
        p, i, _ = item
        def compatible(group):
            for q, j, _ in group:
                if i == j or math.dist(p, q) > snap_in / 12 + 1e-12:
                    return False
                if math.dist(p, q) > 1e-9:
                    di = _direction(walls[i].start, walls[i].end)
                    dj = _direction(walls[j].start, walls[j].end)
                    if abs(di[0] * dj[1] - di[1] * dj[0]) < 1e-7:
                        return False
            return True
        options = [g for g in groups if compatible(g)]
        if options:
            min(options, key=lambda g: (max(math.dist(p, v[0]) for v in g), g[0][0])).append(item)
        else:
            groups.append([item])
    assigned = {}
    for group in groups:
        unique = sorted({v[0] for v in group})
        center = tuple(math.fsum(p[k] for p in unique) / len(unique) for k in (0, 1))
        for _, i, end in group:
            assigned[i, end] = center
    out = tuple(replace(w, start=assigned[i, 0], end=assigned[i, 1])
                for i, w in enumerate(walls))
    return out, tuple(sorted(set(assigned.values())))


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
    """Choose the nearest eligible intersection from immutable source lines.

    The budget is a drafting-repair limit, not evidence that a gap is not a
    door. Collinear gaps are never bridged. Ties use coordinates, not order.
    """
    _nonnegative(extend_in, "extend_in")
    budget = extend_in / 12
    out = []
    for i, w in enumerate(walls):
        candidates = [[], []]
        for j, other in enumerate(walls):
            if i == j:
                continue
            hit = _line_intersection(w.start, w.end, other.start, other.end)
            if hit is None or _distance_to_segment(hit, other.start, other.end) > budget + 1e-9:
                continue
            for end, p in enumerate((w.start, w.end)):
                distance = math.dist(p, hit)
                opposite = w.end if end == 0 else w.start
                if distance <= budget + 1e-9 and math.dist(hit, opposite) > 1e-9:
                    candidates[end].append((distance, _nz(hit)))
        start = min(candidates[0])[1] if candidates[0] else w.start
        end = min(candidates[1])[1] if candidates[1] else w.end
        # Never invert or collapse short spans when both ends want one node.
        if _param_on(end, w.start, w.end) <= _param_on(start, w.start, w.end):
            start, end = w.start, w.end
        out.append(replace(w, start=start, end=end))
    return tuple(out)


@dataclass(frozen=True)
class EndpointAdjustment:
    source_ids: tuple[str, ...]
    original: Pt
    resolved: Pt
    distance_ft: float
    reason: str = "junction repair"


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
    snap_in: float = 0.0
    extend_in: float = 0.0
    adjustments: tuple[EndpointAdjustment, ...] = ()


def split_through_walls(walls: list[WallSeg] | tuple[WallSeg, ...],
                        nodes: list[Pt] | tuple[Pt, ...],
                        tol_in: float = 1.0) -> tuple[WallSeg, ...]:
    """Split any wall whose INTERIOR contains a node, so T and X junctions
    become real graph nodes rather than geometric coincidences."""
    _nonnegative(tol_in, "tol_in")
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

        interior = sorted(set(interior))
        cursor = w.start
        for _, n in interior:
            if math.dist(cursor, n) > 1e-9:
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
    for value, name in ((snap_in, "snap_in"), (extend_in, "extend_in"),
                        (min_dangle_ft, "min_dangle_ft")):
        _nonnegative(value, name)
    walls = sorted((replace(w, start=min(w.start, w.end), end=max(w.start, w.end))
                    for w in walls if w.length_ft > 1e-9), key=_wall_key)
    if not walls:
        return WallGraph((), (), (), snap_in, extend_in)
    snapped, _ = cluster_endpoints(walls, snap_in=snap_in)
    extended = extend_to_intersections(snapped, extend_in=extend_in)
    adjustments = tuple(EndpointAdjustment(old.source_ids, a, b, math.dist(a, b))
                        for old, new in zip(walls, extended)
                        for a, b in zip((old.start, old.end), (new.start, new.end))
                        if math.dist(a, b) > 1e-9)
    # Quantization is numerical normalization, not another drafting snap pass.
    extended = tuple(replace(w, start=tuple(round(v, 9) for v in w.start),
                             end=tuple(round(v, 9) for v in w.end)) for w in extended)
    nodes = {p for w in extended for p in (w.start, w.end)}
    for i, w in enumerate(extended):
        for other in extended[i + 1:]:
            hit = _line_intersection(w.start, w.end, other.start, other.end)
            if hit is not None and (1e-9 < _param_on(hit, w.start, w.end) < 1 - 1e-9
                                    and 1e-9 < _param_on(hit, other.start, other.end) < 1 - 1e-9):
                nodes.add(tuple(round(v, 9) for v in hit))
    final = split_through_walls(extended, tuple(sorted(nodes)), tol_in=1e-7)
    unique = {}
    for w in final:
        if w.length_ft > 1e-9:
            key = (min(w.start, w.end), max(w.start, w.end), round(w.thickness_ft, 9))
            if key in unique:
                prev = unique[key]
                unique[key] = replace(prev, source_ids=tuple(sorted(set(prev.source_ids + w.source_ids))))
            else:
                unique[key] = w
    final = tuple(sorted(unique.values(), key=_wall_key))

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

    return WallGraph(walls=kept, junctions=junctions, unresolved=unresolved,
                     snap_in=snap_in, extend_in=extend_in, adjustments=adjustments)
