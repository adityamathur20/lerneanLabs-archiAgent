"""DXF layer features: everything the DXF layer table and entity types know
that a PrimitiveSet cannot express.

ARC counts identify doors; MTEXT counts identify text layers; lineweight
separates walls (35-40) from furniture (9) and windows (5). None of this
survives into PrimitiveSet, so it is gathered here from the DXF directly.
"""

from __future__ import annotations

import collections
from dataclasses import replace
from pathlib import Path

import ezdxf

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.primitives import PrimitiveSet


def build_dxf_inventory(dxf_path: str | Path,
                        ps: PrimitiveSet) -> tuple[LayerStats, ...]:
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    mix: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    bbox: dict[str, list[float]] = {}
    total = 0
    for e in msp:
        total += 1
        lay = e.dxf.layer
        mix[lay][e.dxftype()] += 1

    for pr in ps.primitives:
        b = bbox.setdefault(pr.layer, [1e30, 1e30, -1e30, -1e30])
        for x, y in pr.coords:
            b[0] = min(b[0], x); b[1] = min(b[1], y)
            b[2] = max(b[2], x); b[3] = max(b[3], y)

    draw_area = max(ps.width * ps.height, 1e-9)
    base = {s.name: s for s in build_inventory(ps)}
    table = {l.dxf.name: l for l in doc.layers}

    out: list[LayerStats] = []
    for name in sorted(set(base) | set(mix)):
        s = base.get(name) or LayerStats(
            name=name, path_count=0, segment_count=0, axis_aligned_fraction=0.0,
            stroke_widths=(), dominant_colors=(), bbox=(0.0, 0.0, 0.0, 0.0),
            length_p10=0.0, length_p50=0.0, length_p90=0.0)
        lay = table.get(name)
        b = bbox.get(name)
        ratio = (((b[2] - b[0]) * (b[3] - b[1])) / draw_area) if b else 0.0
        out.append(replace(
            s,
            entity_mix=tuple(mix[name].most_common(5)),
            entity_share=(sum(mix[name].values()) / total) if total else 0.0,
            lineweight=(int(lay.dxf.lineweight) if lay is not None
                        and lay.dxf.hasattr("lineweight") else None),
            linetype=(str(lay.dxf.linetype) if lay is not None else ""),
            is_off=(bool(lay.is_off()) if lay is not None else False),
            is_frozen=(bool(lay.is_frozen()) if lay is not None else False),
            extent_ratio=min(ratio, 1.0),
        ))
    return tuple(out)
