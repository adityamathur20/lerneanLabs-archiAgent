"""Paired-line wall detection.

A wall drawn in CAD is two parallel lines a wall-thickness apart. Pairing
them recovers both the centerline and the TRUE thickness — no guessing.
Validated in PLAN.md §4: 187 raw segments -> 49 walls, median measured
thickness 4.0in, matching the drawing's partition walls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from archiagent.primitives import Pt, PrimitiveSet

AXIS_TOL_FT = 0.02  # feet; below this a segment counts as axis-aligned


@dataclass(frozen=True)
class WallSeg:
    start: Pt
    end: Pt
    thickness_ft: float
    source_layer: str
    detector: str  # "paired-line" | "hatch-body"
    thickness_source: str  # "measured" | "default"
    source_ids: tuple[str, ...] = ()

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def is_horizontal(self) -> bool:
        return abs(self.end[1] - self.start[1]) < abs(self.end[0] - self.start[0])


def _validate_pairing(units_per_foot, min_t_in, max_t_in, min_len_ft):
    if not all(math.isfinite(v) and v > 0 for v in
               (units_per_foot, min_t_in, max_t_in, min_len_ft)):
        raise ValueError("scale, thickness and minimum length must be finite and positive")
    if max_t_in < min_t_in:
        raise ValueError("max_t_in must be at least min_t_in")


def _interval_pairs(family, min_t_ft, max_t_ft, min_len_ft):
    """Sweep face endpoints; only consume the overlapping interval of a face.

    Each entry is (lo, hi, offset, source_ids). Tiny numerical differences
    are normalized, never drafting gaps. Collinear fragments can therefore
    support one wall without consuming the unused span of a long face.
    """
    cuts = sorted({round(v, 9) for f in family for v in f[:2]})
    runs = {}
    for lo, hi in zip(cuts, cuts[1:]):
        if hi - lo < 1e-9:
            continue
        mid = (lo + hi) / 2
        active = {}
        for a, b, off, ids in family:
            if a - 1e-9 <= mid <= b + 1e-9:
                active.setdefault(round(off, 9), set()).update(ids)
        offsets = sorted(active)
        candidates = sorted((b - a, a, b) for i, a in enumerate(offsets)
                            for b in offsets[i + 1:]
                            if min_t_ft - 1e-9 <= b - a <= max_t_ft + 1e-9)
        used = set()
        for thickness, a, b in candidates:
            if a in used or b in used:
                continue
            used.update((a, b))
            ids = active[a] | active[b]
            segments = runs.setdefault((a, b), [])
            if segments and abs(segments[-1][1] - lo) < 1e-8:
                segments[-1][1] = hi
                segments[-1][2].update(ids)
            else:
                segments.append([lo, hi, set(ids)])
    for (a, b), segments in sorted(runs.items()):
        for lo, hi, ids in segments:
            if hi - lo + 1e-9 >= max(min_len_ft, b - a):
                yield lo, hi, (a + b) / 2, b - a, tuple(sorted(ids))


def _pair_family(family, min_t_ft, max_t_ft, min_len_ft):
    """Compatibility wrapper for axis-family callers."""
    out = []
    for layer in sorted({f[3] for f in family}):
        entries = [(a, b, off, ()) for a, b, off, lyr in family if lyr == layer]
        out.extend((a, b, c, t, layer)
                   for a, b, c, t, _ in _interval_pairs(entries, min_t_ft, max_t_ft, min_len_ft))
    return out


def detect_walls_paired_lines(ps: PrimitiveSet, wall_layers: set[str],
                              units_per_foot: float,
                              min_t_in: float = 2.0, max_t_in: float = 24.0,
                              min_len_ft: float = 1.0) -> tuple[WallSeg, ...]:
    """Reconstruct straight wall faces in their local frame, at any angle.

    Pairing is layer-local and requires parallel source evidence. It does
    not infer missing faces or reinterpret curved polylines as straight walls.
    """
    _validate_pairing(units_per_foot, min_t_in, max_t_in, min_len_ft)
    segments = []
    for p in ps.by_layer(wall_layers):
        if p.kind in {"curve", "fill"}:
            continue  # curved/filled bodies need their own source-aware detector
        for raw_a, raw_b in p.segments():
            a = tuple(v / units_per_foot for v in raw_a)
            b = tuple(v / units_per_foot for v in raw_b)
            length = math.dist(a, b)
            if length < 1e-9:
                continue
            if b < a:
                a, b = b, a
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            source = getattr(p, "source_id", "")
            segments.append((p.layer, ux, uy, a, b, (source,) if source else ()))
    families = []
    for layer, ux, uy, a, b, ids in sorted(segments):
        family = next((g for g in families
                       if g[0] == layer and abs(g[1] * uy - g[2] * ux) < 1e-7), None)
        if family is None:
            family = [layer, ux, uy, []]
            families.append(family)
        _, dx, dy, entries = family
        lo, hi = sorted((a[0] * dx + a[1] * dy, b[0] * dx + b[1] * dy))
        off = ((a[1] + b[1]) * dx - (a[0] + b[0]) * dy) / 2
        entries.append((lo, hi, off, ids))
    walls = []
    for layer, dx, dy, entries in families:
        for lo, hi, off, thickness, ids in _interval_pairs(
                entries, min_t_in / 12, max_t_in / 12, min_len_ft):
            a = (round(lo * dx - off * dy, 9), round(lo * dy + off * dx, 9))
            b = (round(hi * dx - off * dy, 9), round(hi * dy + off * dx, 9))
            walls.append(WallSeg(min(a, b), max(a, b), thickness, layer,
                                 "paired-line", "measured", ids))
    return tuple(sorted(walls, key=lambda w: (w.start, w.end, w.source_layer, w.thickness_ft)))


def combine_wall_hypotheses(*groups: tuple[WallSeg, ...]) -> tuple[WallSeg, ...]:
    """Merge duplicate/overlapping collinear hypotheses without filling gaps.

    Only equal-thickness, equal-layer centerlines can be combined. Provenance
    is unioned; competing thicknesses remain explicit competing hypotheses.
    """
    families = []
    for w in sorted((w for group in groups for w in group),
                    key=lambda w: (min(w.start, w.end), max(w.start, w.end),
                                   w.source_layer, w.thickness_ft, w.detector, w.source_ids)):
        if w.length_ft <= 1e-9:
            continue
        a, b = sorted((w.start, w.end))
        dx, dy = (b[0]-a[0])/w.length_ft, (b[1]-a[1])/w.length_ft
        family = next((g for g in families
                       if g[0].source_layer == w.source_layer
                       and abs(g[0].thickness_ft-w.thickness_ft) < 1e-7
                       and abs(g[1]*dy-g[2]*dx) < 1e-7
                       and abs(a[1]*g[1]-a[0]*g[2]-g[3]) < 1e-7), None)
        if family is None:
            family = [w, dx, dy, a[1]*dx-a[0]*dy, []]
            families.append(family)
        _, dx, dy, _, intervals = family
        lo, hi = sorted((a[0]*dx+a[1]*dy, b[0]*dx+b[1]*dy))
        intervals.append((lo, hi, w))
    out = []
    for template, dx, dy, off, intervals in families:
        runs = []
        for lo, hi, w in sorted(intervals, key=lambda v: (v[0], v[1], v[2].detector, v[2].source_ids)):
            if runs and lo <= runs[-1][1] + 1e-9:
                runs[-1][1] = max(runs[-1][1], hi)
                runs[-1][2].update(w.source_ids)
                runs[-1][3].add(w.detector)
            else:
                runs.append([lo, hi, set(w.source_ids), {w.detector}])
        for lo, hi, ids, detectors in runs:
            a = (round(lo*dx-off*dy, 9), round(lo*dy+off*dx, 9))
            b = (round(hi*dx-off*dy, 9), round(hi*dy+off*dx, 9))
            out.append(WallSeg(min(a, b), max(a, b), template.thickness_ft,
                               template.source_layer,
                               next(iter(detectors)) if len(detectors) == 1 else "combined-evidence",
                               template.thickness_source, tuple(sorted(ids))))
    return tuple(sorted(out, key=lambda w: (w.start, w.end, w.source_layer, w.thickness_ft)))


def detect_walls_filled_bodies(ps: PrimitiveSet, wall_layers: set[str],
                               units_per_foot: float,
                               min_t_in: float = 2.0, max_t_in: float = 24.0,
                               min_len_ft: float = 1.0) -> tuple[WallSeg, ...]:
    """Accept thin rectangular filled bodies, rejecting holes/complex outlines.

    Rectangle coverage must be within one part per million. This deliberately
    excludes L-shaped networks and curved bodies requiring polygonal walls;
    it never approximates them with one bounding rectangle.
    """
    from shapely.geometry import Polygon

    _validate_pairing(units_per_foot, min_t_in, max_t_in, min_len_ft)
    entities = {e.id: e for e in getattr(ps, "entities", ())}
    candidates = []
    for p in ps.by_layer(wall_layers):
        if p.kind != "fill":
            continue
        entity = entities.get(getattr(p, "source_id", ""))
        if entity is not None and entity.holes:
            continue
        candidates.append((p.coords, p.layer, getattr(p, "source_id", "")))
    wanted = {layer.casefold() for layer in wall_layers}
    for e in entities.values():
        if e.kind in {"HATCH", "SOLID", "TRACE", "PDF_FILL"} and e.closed and not e.holes and e.layer.casefold() in wanted:
            candidates.append((e.coords, e.layer, e.id))
    out = []
    for coords, layer, source_id in candidates:
        if len(coords) < 3:
            continue
        poly = Polygon([(x/units_per_foot, y/units_per_foot) for x, y in coords])
        if not poly.is_valid or poly.area <= 1e-12:
            continue
        rectangle = poly.minimum_rotated_rectangle
        if rectangle.area <= 0 or abs(poly.area/rectangle.area-1) > 1e-6:
            continue
        corners = list(rectangle.exterior.coords)[:4]
        edges = [(math.dist(a,b), a,b) for a,b in zip(corners, corners[1:]+corners[:1])]
        thickness = min(edge[0] for edge in edges)
        length = max(edge[0] for edge in edges)
        if not (min_t_in/12-1e-9 <= thickness <= max_t_in/12+1e-9 and length >= max(min_len_ft, thickness)):
            continue
        short_edges = sorted(edges, key=lambda edge: edge[0])[:2]
        endpoints = sorted(((a[0]+b[0])/2, (a[1]+b[1])/2) for _,a,b in short_edges)
        # Equal-sided polygons have no supported principal wall axis.
        if abs(length-thickness) < 1e-9:
            continue
        out.append(WallSeg(endpoints[0], endpoints[1], thickness, layer,
                           "hatch-body", "measured", (source_id,) if source_id else ()))
    return combine_wall_hypotheses(tuple(out))


# Stair treads and hatch strokes are runs of evenly-spaced parallel lines.
# _pair_family cannot tell them from walls: a tread pitch of ~10in sits
# squarely inside the 2-24in thickness window, so every adjacent pair of
# treads becomes a "wall". Measured on PLAN.dxf, 6 of 62 walls came from a
# layer named sSTAIR.
#
# The discriminator is SPACING, not thickness. Real parallel walls -- a row
# of rooms, a corridor -- sit feet apart. Treads sit inches apart, and they
# are evenly spaced because that is what makes a staircase climbable.
LADDER_MIN_RUN = 4       # a stair has many treads; two parallel walls are normal
LADDER_MAX_SPACING_IN = 30.0   # measured: Floor Plan.dxf's treads pair at 22in,
                               # not the ~11in tread going, because _pair_family
                               # pairs a tread's two drawn edges across the step
LADDER_SPACING_CV = 0.15  # stdev/mean; treads are uniform by construction


def _overlaps(a: WallSeg, b: WallSeg, horizontal: bool) -> bool:
    """Do two parallel walls cover the same stretch of their shared axis?"""
    i = 0 if horizontal else 1
    a_lo, a_hi = sorted((a.start[i], a.end[i]))
    b_lo, b_hi = sorted((b.start[i], b.end[i]))
    overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
    shorter = min(a_hi - a_lo, b_hi - b_lo)
    return shorter > 0 and overlap > 0.5 * shorter


def reject_ladder_runs(walls: tuple[WallSeg, ...] | list[WallSeg]
                       ) -> tuple[tuple[WallSeg, ...], tuple[WallSeg, ...]]:
    """Split walls into (kept, rejected), dropping stair/hatch tread runs.

    Deliberately separate from detect_walls_paired_lines so existing callers
    and their tests are untouched, and so the rule can be tested on its own.
    """
    kept: list[WallSeg] = []
    rejected: list[WallSeg] = []
    max_gap_ft = LADDER_MAX_SPACING_IN / 12.0

    for horizontal in (True, False):
        family = [w for w in walls if w.is_horizontal is horizontal]
        # perpendicular offset: y for a horizontal wall, x for a vertical one
        off = (lambda w: w.start[1]) if horizontal else (lambda w: w.start[0])
        span_i = 0 if horizontal else 1

        # Group by the stretch of the shared axis the wall covers, BEFORE
        # looking for even spacing. A sheet holding several drawings has
        # unrelated walls at similar offsets all over it; sorting globally
        # interleaves them with the treads and destroys the very regularity
        # the rule looks for. Treads of one stair share a span -- that is
        # what makes them one stair.
        groups: dict[tuple[int, int], list[WallSeg]] = {}
        for w in family:
            lo, hi = sorted((w.start[span_i], w.end[span_i]))
            key = (round(lo), round(hi))          # 1 ft buckets
            groups.setdefault(key, []).append(w)

        for group in groups.values():
            group.sort(key=off)
            run: list[WallSeg] = [group[0]]
            gaps: list[float] = []

            def flush(run=run, gaps=gaps):
                if len(run) >= LADDER_MIN_RUN and gaps:
                    mean = sum(gaps) / len(gaps)
                    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
                    if mean and (var ** 0.5) / mean < LADDER_SPACING_CV:
                        rejected.extend(run)
                        return
                kept.extend(run)

            for a, b in zip(group, group[1:]):
                gap = off(b) - off(a)
                if 0 < gap <= max_gap_ft:
                    run.append(b)
                    gaps.append(gap)
                else:
                    flush(run, gaps)
                    run, gaps = [b], []
            flush(run, gaps)

    # Preserve the caller's wall order. detect_spaces is order-sensitive:
    # reordering the same 805 walls changed the room count from 27 to 18.
    # That is a defect in space detection, but this function must not be the
    # thing that triggers it.
    dropped = {id(w) for w in rejected}
    kept_in_order = tuple(w for w in walls if id(w) not in dropped)
    return kept_in_order, tuple(rejected)
