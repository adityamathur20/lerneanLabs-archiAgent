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
from ezdxf.bbox import extents

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.primitives import PrimitiveSet


def build_dxf_inventory(dxf_path: str | Path,
                        ps: PrimitiveSet) -> tuple[LayerStats, ...]:
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    mix: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    total = 0
    for e in msp:
        total += 1
        lay = e.dxf.layer
        mix[lay][e.dxftype()] += 1

    draw_area = max(ps.width * ps.height, 1e-9)
    base = {s.name: s for s in build_inventory(ps)}
    table = {l.dxf.name: l for l in doc.layers}

    out: list[LayerStats] = []
    for name in sorted(set(base) | set(mix) | set(table)):
        s = base.get(name) or LayerStats(
            name=name, path_count=0, segment_count=0, axis_aligned_fraction=0.0,
            stroke_widths=(), dominant_colors=(), bbox=(0.0, 0.0, 0.0, 0.0),
            length_p10=0.0, length_p50=0.0, length_p90=0.0)
        lay = table.get(name)

        # Compute extent_ratio from DXF entities in this layer
        entities_in_layer = [e for e in msp if e.dxf.layer == name]
        ratio = 0.0
        if entities_in_layer:
            try:
                bbox_result = extents(entities_in_layer)
                # has_data is the documented way to check for valid extents.
                # Note: Vec3.__bool__ returns False for origin vectors, so truthiness
                # checks fail for geometry starting at (0,0,0) — use has_data instead.
                if bbox_result.has_data:
                    min_pt, max_pt = bbox_result.extmin, bbox_result.extmax
                    area = ((max_pt[0] - min_pt[0]) * (max_pt[1] - min_pt[1]))
                    ratio = area / draw_area
            except Exception:
                # Non-fatal: if bbox cannot be computed, fall back to 0.0
                ratio = 0.0

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
