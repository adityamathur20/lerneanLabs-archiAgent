"""Explicit plan windows and conservative spatial region proposals.

Proposals are unclassified regions, never automatic storey assignments. Source
coordinates remain unchanged unless a reviewed registration origin is supplied.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

from shapely.geometry import LineString, box
from shapely.ops import unary_union

from archiagent.primitives import PrimitiveSet
from archiagent.semantic import PlanRegion


def _finite_values(value, count, name):
    if not isinstance(value, (tuple, list)) or len(value) != count or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value
    ):
        raise ValueError(f"{name} requires {count} finite numeric coordinates")
    return tuple(float(v) for v in value)


def load_regions(path: str | Path) -> tuple[PlanRegion, ...]:
    """Read reviewed windows; absent kind remains unclassified, never a plan.

    The CLI authors only kind='plan'. Other kinds remain useful inventory
    records but require an explicit plan review before model generation.
    """
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("regions", [])
    if not isinstance(data, list) or not data:
        raise ValueError("regions file requires a nonempty list of regions")
    parsed = []
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"].strip():
            raise ValueError("each region requires a nonempty string id")
        bounds = _finite_values(d.get("bounds"), 4, "region bounds")
        origin = _finite_values(d.get("origin", (0, 0)), 2, "region origin")
        elevation = d.get("elevation_ft")
        if elevation is not None and (not isinstance(elevation, (int, float)) or
                isinstance(elevation, bool) or not math.isfinite(elevation)):
            raise ValueError("region elevation_ft must be finite numeric feet")
        kind, name = d.get("kind", "unclassified"), d.get("name", "Unassigned plan")
        if not all(isinstance(v, str) and v.strip() for v in (kind, name)):
            raise ValueError("region kind and name must be nonempty strings")
        parsed.append(PlanRegion(d["id"], bounds, kind, name, elevation, origin, "reviewed",
                                 units_per_foot=d.get("units_per_foot")))
    regions = tuple(parsed)
    if len({r.id for r in regions}) != len(regions):
        raise ValueError("region IDs must be unique")
    return regions


def select_region(ps: PrimitiveSet, region: PlanRegion) -> PrimitiveSet:
    ox, oy = _finite_values(region.origin, 2, "region origin")
    window = box(*region.bounds)
    from archiagent.evidence import IngestWarning
    primitives = []
    partial_ids = set()
    for p in ps.primitives:
        if len(p.coords) < 2:
            continue
        closed = p.kind == "rect" or getattr(p, "closed", False)
        coords = p.coords + (p.coords[0],) if closed and p.coords[0] != p.coords[-1] else p.coords
        observed = LineString(coords)
        if window.covers(observed):
            primitives.append(p)
            continue
        clipped = observed.intersection(window)
        if not clipped.is_empty:
            partial_ids.add(p.source_id)
        for part in getattr(clipped, "geoms", (clipped,)):
            if part.geom_type == "LineString" and part.length > 0:
                # Never manufacture crop-edge walls by clipping a filled area.
                primitives.append(replace(p, coords=tuple(part.coords), kind="line", closed=False))
    x0, y0, x1, y1 = region.bounds
    def inside(p): return x0 <= p[0] <= x1 and y0 <= p[1] <= y1
    texts = tuple(t for t in ps.texts if inside(t.center()))
    selected_ids = {p.source_id for p in primitives}
    def relevant(e):
        return e.id in selected_ids or (e.center is not None and inside(e.center)) or any(inside(p) for p in e.coords)
    lookup = {e.id: e for e in ps.entities}
    keep = {e.id for e in ps.entities if relevant(e)}
    for sid in list(keep):
        seen = set()
        while sid in lookup and lookup[sid].parent_id and sid not in seen:
            seen.add(sid);sid = lookup[sid].parent_id;keep.add(sid)
    entities = []
    for e in ps.entities:
        if e.id not in keep: continue
        partial = e.id in partial_ids or any(not inside(p) for p in e.coords)
        if partial:
            partial_ids.add(e.id)
            e = replace(e, metadata=e.metadata + (("region_partial", "true"),))
        entities.append(e)
    entities = tuple(entities)
    dimensions = tuple(d for d in ps.dimensions if inside(d.start) and inside(d.end))
    def point(p): return (p[0]-ox, p[1]-oy)
    if ox or oy:
        primitives = [replace(p, coords=tuple(map(point, p.coords))) for p in primitives]
        texts = tuple(replace(t, bbox=(t.bbox[0]-ox,t.bbox[1]-oy,t.bbox[2]-ox,t.bbox[3]-oy)) for t in texts)
        entities = tuple(replace(e, coords=tuple(map(point,e.coords)),
                                 center=point(e.center) if e.center else None,
                                 holes=tuple(tuple(map(point,h)) for h in e.holes)) for e in entities)
        dimensions = tuple(replace(d,start=point(d.start),end=point(d.end),
                                   text_position=point(d.text_position) if d.text_position is not None else None)
                           for d in dimensions)
    return replace(ps,primitives=tuple(primitives),texts=texts,entities=entities,dimensions=dimensions,
                   width=x1-x0,height=y1-y0,
                   warnings=ps.warnings+tuple(IngestWarning("region_boundary_crossing",sid,"source entity crosses selected region; symbol/body recognition requires review") for sid in sorted(partial_ids)))


def propose_regions(ps: PrimitiveSet, units_per_foot: float, gap_ft: float = 4.0) -> tuple[PlanRegion,...]:
    """Spatial candidates only; titles/discipline/elevation need review.

    Annotation layers and drawing-spanning border primitives are excluded from
    proposals to reduce accidental joins. Explicit windows remain authoritative.
    """
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and
               math.isfinite(v) and v > 0 for v in (units_per_foot, gap_ft)):
        raise ValueError("positive finite scale and region gap are required")
    exclude = ("dim", "text", "grid", "sheet", "title", "anno", "format", "defpoint")
    distance = gap_ft*units_per_foot/2
    shapes = []
    for p in ps.primitives:
        if any(t in p.layer.lower() for t in exclude) or len(p.coords)<2:
            continue
        shape = LineString(p.coords)
        bx0,by0,bx1,by1=shape.bounds
        if bx1-bx0>ps.width*.9 or by1-by0>ps.height*.9:
            continue
        if shape.length >= units_per_foot:
            shapes.append(shape.buffer(distance, cap_style=2))
    if not shapes:
        return ()
    joined=unary_union(shapes)
    polys=sorted((g for g in getattr(joined,"geoms",(joined,)) if g.area>=25*units_per_foot**2),
                 key=lambda g:(-g.bounds[3],g.bounds[0]))
    return tuple(PlanRegion(f"region-{i+1:02d}",tuple(g.bounds),evidence="spatial-proposal") for i,g in enumerate(polys))
