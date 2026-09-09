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

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def is_horizontal(self) -> bool:
        return abs(self.end[1] - self.start[1]) < abs(self.end[0] - self.start[0])


def _pair_family(family: list[tuple[float, float, float, str]],
                 min_t_ft: float, max_t_ft: float,
                 min_len_ft: float) -> list[tuple[float, float, float, float, str]]:
    """Pair (lo, hi, offset, layer) entries into (lo, hi, center, thickness, layer).

    `offset` is the perpendicular coordinate; `lo`/`hi` bound the run along
    the parallel axis.
    """
    order = sorted(range(len(family)), key=lambda i: family[i][2])
    used: set[int] = set()
    out: list[tuple[float, float, float, float, str]] = []

    for pos, i in enumerate(order):
        if i in used:
            continue
        a_lo, a_hi, a_off, a_layer = family[i]
        best = None
        for j in order[pos + 1:]:
            if j in used:
                continue
            b_lo, b_hi, b_off, b_layer = family[j]
            if b_layer != a_layer:
                # A wall is built from ONE layer's own geometry. Pairing
                # across layers invents walls that exist on neither: on
                # PLAN.dxf it paired stair lines with wall lines, so a layer
                # named sSTAIR yielded 5 walls when its own geometry yields
                # 2. It also makes layer classification meaningless -- you
                # could classify every layer correctly and still get stair
                # walls, because the pairing reaches across into layers you
                # never approved.
                continue
            thickness = abs(b_off - a_off)
            if thickness > max_t_ft:
                break  # sorted by offset, so no later j can be farther
            if thickness < min_t_ft:
                continue
            overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
            if overlap < min_len_ft:
                continue
            if overlap < thickness:
                # A wall is never thicker than it is long. Two short stubs
                # a couple of feet apart are unrelated geometry, not a wall.
                continue
            # nearest offset wins; overlap only breaks ties
            key = (thickness, -overlap)
            if best is None or key < best[0]:
                best = (key, j, thickness,
                        max(a_lo, b_lo), min(a_hi, b_hi), (a_off + b_off) / 2.0)
        if best is not None:
            key, j, thickness, lo, hi, center = best
            used.add(i)
            used.add(j)
            out.append((lo, hi, center, thickness, a_layer))
    return out


def detect_walls_paired_lines(ps: PrimitiveSet, wall_layers: set[str],
                              units_per_foot: float,
                              min_t_in: float = 2.0, max_t_in: float = 24.0,
                              min_len_ft: float = 1.0) -> tuple[WallSeg, ...]:
    if units_per_foot <= 0:
        raise ValueError("units_per_foot must be positive")

    horizontal: list[tuple[float, float, float, str]] = []
    vertical: list[tuple[float, float, float, str]] = []

    for p in ps.by_layer(wall_layers):
        for (x0, y0), (x1, y1) in p.segments():
            fx0, fy0 = x0 / units_per_foot, y0 / units_per_foot
            fx1, fy1 = x1 / units_per_foot, y1 / units_per_foot
            if abs(fy1 - fy0) < AXIS_TOL_FT and abs(fx1 - fx0) >= min_len_ft:
                horizontal.append((min(fx0, fx1), max(fx0, fx1),
                                   (fy0 + fy1) / 2.0, p.layer))
            elif abs(fx1 - fx0) < AXIS_TOL_FT and abs(fy1 - fy0) >= min_len_ft:
                vertical.append((min(fy0, fy1), max(fy0, fy1),
                                 (fx0 + fx1) / 2.0, p.layer))

    min_t_ft, max_t_ft = min_t_in / 12.0, max_t_in / 12.0
    walls: list[WallSeg] = []

    for lo, hi, center, thickness, layer in _pair_family(
            horizontal, min_t_ft, max_t_ft, min_len_ft):
        walls.append(WallSeg((lo, center), (hi, center), thickness,
                             layer, "paired-line", "measured"))

    for lo, hi, center, thickness, layer in _pair_family(
            vertical, min_t_ft, max_t_ft, min_len_ft):
        walls.append(WallSeg((center, lo), (center, hi), thickness,
                             layer, "paired-line", "measured"))

    # Two runs of the same geometry can pair identically (and the same geometry
    # can appear on more than one layer). Duplicates inflate junction degree and
    # misclassify corners -- a duplicated L reads as an X -- and would emit
    # duplicate IfcWall entities downstream.
    seen: set = set()
    unique: list[WallSeg] = []
    for w in walls:
        a = (round(w.start[0], 4), round(w.start[1], 4))
        b = (round(w.end[0], 4), round(w.end[1], 4))
        key = (min(a, b), max(a, b), round(w.thickness_ft, 4))
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)
    return tuple(unique)


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
