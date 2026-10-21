"""One floor drawn as a few overlapping tiles, each element numbered, for the element stage.

The model is asked about elements by number and never answers with coordinates, so
the picture only has to make each numbered element recognisable in its context.
Rendered in memory and never written to disk: it is a picture of a client drawing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from archiagent.classify.thumbnails import RENDER_UNAVAILABLE_MSG, RenderUnavailable
from archiagent.primitives import Pt

#: Colour per element kind; settled elements are drawn thin and pale.
COLOURS = {"wall": "#d62728", "region": "#1f77b4", "gap": "#2ca02c"}
SETTLED = "#7aa6c2"


@dataclass(frozen=True)
class Item:
    number: int
    id: str
    kind: str                 # "wall" | "region" | "gap"
    coords: tuple[Pt, ...]    # feet
    settled: bool             # drawn for context, not asked about


def number_items(items) -> tuple[Item, ...]:
    """Numbers 1..n in id order, so the same candidates get the same numbers every run."""
    return tuple(replace(item, number=n) for n, item in enumerate(sorted(items, key=lambda i: i.id), 1))


def _grid(lo: float, hi: float, size: float, overlap: float) -> list[tuple[float, float]]:
    span = hi - lo
    if span <= size:
        return [(lo, hi)]
    step = size * (1 - overlap)
    count = math.ceil((span - size) / step) + 1
    step = (span - size) / (count - 1)
    return [(lo + k * step, lo + k * step + size) for k in range(count)]


def tiles(bounds, tile_ft: float = 40.0, overlap: float = 0.15, max_tiles: int = 8):
    """Overlapping square tiles covering `bounds`; tile size grows until at most `max_tiles` fit."""
    x0, y0, x1, y1 = bounds
    size = tile_ft
    while True:
        xs, ys = _grid(x0, x1, size, overlap), _grid(y0, y1, size, overlap)
        if len(xs) * len(ys) <= max_tiles:
            return tuple((a, c, b, d) for c, d in ys for a, b in xs)
        size *= 1.25


def _mid(coords):
    xs, ys = [p[0] for p in coords], [p[1] for p in coords]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def assign(items, tile_boxes) -> dict[int, tuple[Item, ...]]:
    """Each item to the first tile containing its midpoint (or the nearest tile)."""
    out: dict[int, list[Item]] = {k: [] for k in range(len(tile_boxes))}
    for item in items:
        mx, my = _mid(item.coords)
        inside = [k for k, (a, b, c, d) in enumerate(tile_boxes) if a <= mx <= c and b <= my <= d]
        if inside:
            k = inside[0]
        else:
            k = min(range(len(tile_boxes)), key=lambda t: math.dist(
                (mx, my), ((tile_boxes[t][0] + tile_boxes[t][2]) / 2, (tile_boxes[t][1] + tile_boxes[t][3]) / 2)))
        out[k].append(item)
    return {k: tuple(v) for k, v in out.items() if v}


def render(ps, units_per_foot: float, box, items, size_px: int = 1400) -> bytes:
    """PNG of one tile: the drawing in grey, settled items pale, asked items coloured and numbered."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        raise RenderUnavailable(RENDER_UNAVAILABLE_MSG) from exc
    import io

    x0, y0, x1, y1 = box
    dpi = 100
    fig, ax = plt.subplots(figsize=(size_px / dpi, size_px / dpi), dpi=dpi)
    try:
        for p in ps.primitives:
            pts = [(x / units_per_foot, y / units_per_foot) for x, y in p.coords]
            if len(pts) < 2:
                continue
            if p.closed:
                pts.append(pts[0])
            px, py = zip(*pts)
            if max(px) < x0 or min(px) > x1 or max(py) < y0 or min(py) > y1:
                continue
            ax.plot(px, py, color="#9a9a9a", linewidth=0.5)
        for item in items:
            px, py = zip(*item.coords)
            if item.settled:
                ax.plot(px, py, color=SETTLED, linewidth=2, alpha=0.6)
                continue
            colour = COLOURS.get(item.kind, "#d62728")
            ax.plot(px, py, color=colour, linewidth=3.5, alpha=0.7, solid_capstyle="butt")
            mx, my = _mid(item.coords)
            ax.text(mx, my, str(item.number), color="white", fontsize=9, ha="center", va="center",
                    bbox={"boxstyle": "round,pad=0.15", "fc": colour, "ec": "none"})
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.axis("off")
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight")
        return buffer.getvalue()
    finally:
        plt.close(fig)
