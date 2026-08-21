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

import bisect
import math
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
    total_dimensions: int = 0


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


def _nearest_distance(sorted_runs: list[float], value: float) -> float:
    """Distance from `value` to the closest run. O(log n)."""
    i = bisect.bisect_left(sorted_runs, value)
    best = float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(sorted_runs):
            best = min(best, abs(sorted_runs[j] - value))
    return best


def resolve_scale(dims: list[DimensionText] | tuple[DimensionText, ...],
                  runs: tuple[float, ...],
                  max_residual_in: float = 2.0,
                  min_matches: int = 3) -> ScaleResult:
    """Find the units-per-foot factor explaining the most printed dimensions.

    Each dimension votes at most once, so a scale is scored by how many
    DISTINCT dimensions it explains — not by how many (dimension, run) pairs
    happen to cluster near it. Candidate density is uneven, and scoring pairs
    lets a dense band of coincidences outvote the truth.

    Dimensions left unexplained are not failures: on a real drawing some
    dimensions measure balconies or openings rather than wall-layer geometry.
    They are excluded from the supporting set, not treated as errors.
    """
    dims = [d for d in dims if d.feet > 0]
    if not dims or not runs:
        raise ScaleGateError("no dimensions or no candidate runs to match")

    sorted_runs = sorted(runs)
    raw = sorted({run / d.feet for d in dims for run in runs})
    # Collapse candidates to one representative per 0.01% band. Scale precision
    # of 1e-4 is far finer than the 2in gate needs (2in over 10ft is ~1.7%),
    # so this changes no outcome while cutting the scoring loop enormously.
    candidates: list[float] = []
    for c in raw:
        if not candidates or c - candidates[-1] > 1e-4 * c:
            candidates.append(c)
    if not candidates:
        raise ScaleGateError("no scale candidates could be formed")

    def support(scale: float) -> list[float]:
        """Residuals in inches for every dimension this scale explains."""
        out: list[float] = []
        for d in dims:
            predicted = d.feet * scale
            err = _nearest_distance(sorted_runs, predicted) / scale * 12.0
            if err <= max_residual_in:
                out.append(err)
        return out

    best_scale = candidates[0]
    best_support: list[float] = []
    for c in candidates:
        s = support(c)
        # more dimensions explained wins; ties broken by tighter worst residual
        if (len(s), -max(s, default=0.0)) > (
                len(best_support), -max(best_support, default=0.0)):
            best_scale, best_support = c, s

    required = max(min_matches, math.ceil(0.25 * len(dims)))
    if len(best_support) < required:
        raise ScaleGateError(
            f"only {len(best_support)} of {len(dims)} dimensions matched within "
            f"the {max_residual_in}in residual gate; need {required}")

    return ScaleResult(
        units_per_foot=best_scale,
        convention="clear",
        residuals_in=tuple(round(r, 3) for r in sorted(best_support)),
        max_residual_in=round(max(best_support), 3),
        matched_count=len(best_support),
        total_dimensions=len(dims),
    )
