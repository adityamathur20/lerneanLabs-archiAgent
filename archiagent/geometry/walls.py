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

AXIS_TOL = 0.02


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
            b_lo, b_hi, b_off, _ = family[j]
            thickness = abs(b_off - a_off)
            if thickness > max_t_ft:
                break  # sorted by offset, so no later j can be closer
            if thickness < min_t_ft:
                continue
            overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
            if overlap < min_len_ft:
                continue
            if best is None or overlap > best[0]:
                best = (overlap, j, thickness,
                        max(a_lo, b_lo), min(a_hi, b_hi), (a_off + b_off) / 2.0)
        if best is not None:
            _, j, thickness, lo, hi, center = best
            used.add(i)
            used.add(j)
            out.append((lo, hi, center, thickness, a_layer))
    return out


def detect_walls_paired_lines(ps: PrimitiveSet, wall_layers: set[str],
                              units_per_foot: float,
                              min_t_in: float = 2.0, max_t_in: float = 24.0,
                              min_len_ft: float = 1.0) -> tuple[WallSeg, ...]:
    horizontal: list[tuple[float, float, float, str]] = []
    vertical: list[tuple[float, float, float, str]] = []

    for p in ps.by_layer(wall_layers):
        for (x0, y0), (x1, y1) in p.segments():
            fx0, fy0 = x0 / units_per_foot, y0 / units_per_foot
            fx1, fy1 = x1 / units_per_foot, y1 / units_per_foot
            if abs(fy1 - fy0) < AXIS_TOL and abs(fx1 - fx0) >= min_len_ft:
                horizontal.append((min(fx0, fx1), max(fx0, fx1),
                                   (fy0 + fy1) / 2.0, p.layer))
            elif abs(fx1 - fx0) < AXIS_TOL and abs(fy1 - fy0) >= min_len_ft:
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

    return tuple(walls)
