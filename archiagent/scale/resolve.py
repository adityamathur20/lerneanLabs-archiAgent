"""Recover the source-unit -> foot scale factor by consensus.

R1 (PLAN.md §2): every printed dimension must be reproduced within 2
inches. This module is where that is measured and enforced. Scale error
is proportional, so it dominates the error budget: over a 100ft span a
2in budget means the factor must be right to ~0.17%.

Clear-basis by construction: candidate runs are raw wall-layer segment
lengths, and those segments are the wall FACES. Printed room dimensions
are face-to-face. Like compares with like.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from archiagent.primitives import PrimitiveSet
from archiagent.scale.dimensions import DimensionText

MIN_RUN_UNITS = 10.0  # ignore hatch ticks and glyph fragments


class ScaleGateError(Exception):
    """Scale could not be resolved within the R1 accuracy gate."""


@dataclass(frozen=True)
class ScaleResult:
    units_per_foot: float
    convention: str  # always "clear" in v1
    residuals_in: tuple[float, ...]
    max_residual_in: float
    matched_count: int


def candidate_runs(ps: PrimitiveSet, layers: set[str]) -> tuple[float, ...]:
    """Lengths of axis-aligned segments on the given layers, in source units."""
    out: list[float] = []
    for p in ps.by_layer(layers):
        for (x0, y0), (x1, y1) in p.segments():
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx > 0.02 and dy > 0.02:
                continue  # not axis-aligned
            length = math.hypot(dx, dy)
            if length >= MIN_RUN_UNITS:
                out.append(length)
    return tuple(out)


def resolve_scale(dims: list[DimensionText] | tuple[DimensionText, ...],
                  runs: tuple[float, ...],
                  max_residual_in: float = 2.0,
                  min_matches: int = 3,
                  cluster_rel_tol: float = 0.01) -> ScaleResult:
    """Find the units-per-foot factor supported by the most dimensions."""
    if not dims or not runs:
        raise ScaleGateError("no dimensions or no candidate runs to match")

    candidates = sorted(run / d.feet for d in dims for run in runs if d.feet > 0)
    if not candidates:
        raise ScaleGateError("no scale candidates could be formed")

    # Largest cluster of mutually-close candidates wins.
    best_lo = best_hi = 0
    lo = 0
    for hi in range(len(candidates)):
        while candidates[hi] - candidates[lo] > cluster_rel_tol * candidates[hi]:
            lo += 1
        if hi - lo > best_hi - best_lo:
            best_lo, best_hi = lo, hi
    scale = statistics.median(candidates[best_lo:best_hi + 1])

    # Residual per dimension against its best-matching run.
    residuals: list[float] = []
    for d in dims:
        predicted = d.feet * scale
        nearest = min(runs, key=lambda r: abs(r - predicted))
        residuals.append(abs(nearest - predicted) / scale * 12.0)

    worst = max(residuals)
    if worst > max_residual_in:
        raise ScaleGateError(
            f"max residual {worst:.2f}in exceeds the {max_residual_in}in gate")

    matched = [r for r in residuals if r <= max_residual_in]
    if len(matched) < min_matches:
        raise ScaleGateError(
            f"only {len(matched)} dimensions matched within "
            f"{max_residual_in}in; need {min_matches}")

    return ScaleResult(
        units_per_foot=scale,
        convention="clear",
        residuals_in=tuple(round(r, 3) for r in sorted(matched)),
        max_residual_in=round(worst, 3),
        matched_count=len(matched),
    )
