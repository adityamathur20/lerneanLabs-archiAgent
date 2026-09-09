"""Summarise each CAD layer into compact statistics.

This is the ONLY thing the layer classifier (Task 4) sees. It must be
small enough to prompt with and discriminative enough to classify on:
walls are long and axis-aligned, hatch is short and diagonal, dimension
layers are thin and numerous.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass

from archiagent.primitives import PrimitiveSet

AXIS_TOL_UNITS = 0.02  # source units; below this a segment counts as axis-aligned


@dataclass(frozen=True)
class LayerStats:
    name: str
    path_count: int
    segment_count: int
    axis_aligned_fraction: float
    stroke_widths: tuple[float, ...]
    dominant_colors: tuple[tuple[float, float, float], ...]
    bbox: tuple[float, float, float, float]
    length_p10: float
    length_p50: float
    length_p90: float
    # DXF-only. The PDF front-end leaves these at their defaults.
    entity_mix: tuple[tuple[str, int], ...] = ()   # ("LINE", 812), top 5
    entity_share: float = 0.0                      # fraction of all entities
    lineweight: int | None = None                  # DXF units, -3 = default
    linetype: str = ""
    is_off: bool = False
    is_frozen: bool = False
    extent_ratio: float = 0.0                      # layer bbox area / drawing bbox area


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def build_inventory(ps: PrimitiveSet) -> tuple[LayerStats, ...]:
    by_layer: dict[str, list] = collections.defaultdict(list)
    for p in ps.primitives:
        by_layer[p.layer].append(p)

    out: list[LayerStats] = []
    for name, prims in sorted(by_layer.items()):
        lengths: list[float] = []
        axis = 0
        total = 0
        widths: collections.Counter = collections.Counter()
        colors: collections.Counter = collections.Counter()
        xs: list[float] = []
        ys: list[float] = []

        for p in prims:
            if p.stroke_width is not None:
                widths[round(p.stroke_width, 2)] += 1
            if p.color is not None:
                colors[p.color] += 1
            for (x0, y0), (x1, y1) in p.segments():
                total += 1
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                if dx < AXIS_TOL_UNITS or dy < AXIS_TOL_UNITS:
                    axis += 1
                lengths.append(math.hypot(dx, dy))
                xs.extend((x0, x1))
                ys.extend((y0, y1))

        lengths.sort()
        out.append(LayerStats(
            name=name,
            path_count=len(prims),
            segment_count=total,
            axis_aligned_fraction=(axis / total) if total else 0.0,
            stroke_widths=tuple(w for w, _ in widths.most_common(4)),
            dominant_colors=tuple(c for c, _ in colors.most_common(3)),
            bbox=(min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0),
            length_p10=_percentile(lengths, 0.10),
            length_p50=_percentile(lengths, 0.50),
            length_p90=_percentile(lengths, 0.90),
        ))
    return tuple(out)
