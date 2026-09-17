"""DXF ingestion retaining source evidence alongside flattened geometry.

Header units are a declaration, not a verified scale. Curves are tessellated
with a maximum chord deviation of 0.001 foot at the selected working scale;
their analytic source geometry is retained for interpretation and review.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from pathlib import Path

import ezdxf
from ezdxf import path as dxf_path

from archiagent.evidence import IngestWarning, NativeDimension, SourceEntity
from archiagent.primitives import Primitive, PrimitiveSet, TextItem


class DxfUnitsError(RuntimeError):
    """Units could not be resolved or the supplied scale is invalid."""


INSUNITS_PER_FOOT: dict[int, float] = {
    1: 12.0, 2: 1.0, 4: 304.8, 5: 30.48, 6: 0.3048,
}


def units_from_header(doc) -> float | None:
    return INSUNITS_PER_FOOT.get(doc.header.get("$INSUNITS", 0))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _xy(v) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def load_dxf(path: str | Path,
             units_per_foot: float | None = None) -> tuple[PrimitiveSet, float]:
    path = Path(path)
    doc = ezdxf.readfile(str(path))
    declared = units_from_header(doc)
    upf = units_per_foot if units_per_foot is not None else declared
    if upf is None or not math.isfinite(upf) or upf <= 0:
        raise DxfUnitsError(
            f"{path.name}: supply a finite positive --units-per-foot "
            "(12 for inches, 1 for feet, 304.8 for mm).")
    tolerance = 0.001 * upf
    prims: list[Primitive] = []
    texts: list[TextItem] = []
    entities: list[SourceEntity] = []
    dimensions: list[NativeDimension] = []
    warnings: list[IngestWarning] = []

    def warn(code, sid, message):
        warnings.append(IngestWarning(code, sid, message))

    def flatten(e):
        p = dxf_path.make_path(e)
        return tuple(_xy(v) for v in p.flattening(distance=tolerance))

    def visit(e, sid, inherited_layer="", parent_id="", depth=0):
        kind = e.dxftype()
        layer = e.dxf.get("layer", "0")
        if layer == "0" and inherited_layer:
            layer = inherited_layer
        # Includes original OCS/extrusion and geometric parameters, even for
        # unsupported types; bounds alone cannot recover these observations.
        base = SourceEntity(sid, kind, layer, parent_id=parent_id,
                            metadata=(("dxf_attributes", repr(e.dxf.all_existing_dxf_attribs())),))
        index = len(entities)
        entities.append(base)
        if depth > 32:
            warn("block_depth_limit", sid, "Nested block depth exceeds 32; geometry omitted.")
            return
        try:
            if kind == "INSERT":
                attrs = tuple((str(a.dxf.tag), str(a.dxf.text)) for a in e.attribs)
                metadata = tuple((name, str(e.dxf.get(name, default))) for name, default in
                                 (("rotation", 0), ("xscale", 1), ("yscale", 1), ("zscale", 1)))
                base = replace(base, center=_xy(e.dxf.insert), block_name=e.dxf.name,
                               attributes=attrs, metadata=base.metadata + metadata)
                start = len(prims)
                inserts = list(e.multi_insert()) if e.mcount > 1 else [e]
                for row, ins in enumerate(inserts):
                    def skipped(entity, reason):
                        warn("block_entity_skipped", sid, f"{entity.dxftype()}: {reason}")
                    for n, child in enumerate(ins.virtual_entities(skipped_entity_callback=skipped)):
                        visit(child, f"{sid}/{row}/{n}:{child.dxftype()}", layer, sid, depth + 1)
                    for n, attr in enumerate(ins.attribs):
                        visit(attr, f"{sid}/{row}/attr/{n}", layer, sid, depth + 1)
                points = [pt for p in prims[start:] for pt in p.coords]
                if points:
                    xs, ys = zip(*points)
                    base = replace(base, coords=((min(xs), min(ys)), (max(xs), min(ys)),
                                                (max(xs), max(ys)), (min(xs), max(ys))))
            elif kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                text = e.plain_text() if kind == "MTEXT" else str(e.dxf.text)
                x, y = _xy(e.dxf.insert)
                texts.append(TextItem(text, (x, y, x, y), layer, sid))
                base = replace(base, center=(x, y), metadata=base.metadata + (("text", text),))
            elif kind == "DIMENSION":
                dimtype = int(e.dxf.dimtype) & 7
                base = replace(base, metadata=base.metadata + (("dimension_type", str(dimtype)),
                                               ("text", str(e.dxf.get("text", "")))))
                # Formatting factors are hints, never independent physical
                # measurements: default rendered text derives from geometry.
                hints = ()
                try:
                    style = doc.dimstyles.get(e.dxf.get("dimstyle", "Standard"))
                    hint_names = ("dimlunit", "dimlfac", "dimpost", "dimalt", "dimaltf", "dimapost")
                    hints = tuple(("dimstyle_" + name, str(style.dxf.get(name, "")))
                                  for name in hint_names)
                    overrides = e.override().dimstyle_attribs
                    hints += tuple(("dimoverride_" + name, str(overrides[name]))
                                   for name in hint_names if name in overrides)
                except (ezdxf.DXFError, AttributeError, KeyError) as exc:
                    warn("dimension_style_unavailable", sid, str(exc))
                base = replace(base, metadata=base.metadata + hints +
                    (("dimension_text_origin", "explicit-override" if e.dxf.get("text", "") not in ("", "<>", " ") else "generated"),))
                if dimtype in (0, 1):
                    a, b = _xy(e.dxf.defpoint2), _xy(e.dxf.defpoint3)
                    text_midpoint = e.dxf.all_existing_dxf_attribs().get("text_midpoint")
                    text_position = _xy(e.ocs().to_wcs(text_midpoint)) if text_midpoint is not None else None
                    if text_position is not None and not all(math.isfinite(v) for v in text_position):
                        warn("invalid_dimension_text_position", sid, "Non-finite native dimension text midpoint ignored.")
                        text_position = None
                    if dimtype == 0:
                        angle = math.radians(float(e.dxf.get("angle", 0)))
                        direction = math.cos(angle), math.sin(angle)
                    else:
                        length = math.dist(a, b)
                        direction = ((b[0]-a[0])/length, (b[1]-a[1])/length) if length else (1., 0.)
                    measured = abs((b[0]-a[0])*direction[0] + (b[1]-a[1])*direction[1])
                    dimensions.append(NativeDimension(sid, a, b, measured,
                        str(e.dxf.get("text", "")), layer, "aligned" if dimtype == 1 else "linear",
                        direction, text_position))
                    base = replace(base, coords=(a, b), center=text_position)
                else:
                    warn("unsupported_dimension", sid, f"Dimension type {dimtype} retained but not used for scale.")
            elif kind == "HATCH":
                # ezdxf retains OCS, bulges and curved edge definitions while
                # converting each boundary path into world coordinates.
                loops = [tuple(_xy(v) for v in p.flattening(distance=tolerance))
                         for p in dxf_path.from_hatch(e)]
                loops = [p for p in loops if len(p) >= 3]
                if loops:
                    # Use nesting parity, not path order or winding: DXF
                    # boundary ordering is not guaranteed.
                    def inside(pt, ring):
                        x, y = pt
                        hit = False
                        for a, b in zip(ring, ring[1:] + ring[:1]):
                            if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                                hit = not hit
                        return hit
                    depths = [sum(inside(loop[0], other) for j, other in enumerate(loops) if j != i)
                              for i, loop in enumerate(loops)]
                    for n, loop in enumerate(loops):
                        holes = tuple(other for j, other in enumerate(loops)
                                      if depths[j] == depths[n] + 1 and inside(other[0], loop))
                        if depths[n] % 2 == 0:
                            loopid = f"{sid}/boundary/{n}"
                            entities.append(SourceEntity(loopid, "HATCH_BOUNDARY", layer,
                                loop, closed=True, holes=holes, parent_id=sid))
                        prims.append(Primitive("fill", loop, layer, None, None, sid, kind, True))
                    base = replace(base, coords=loops[0], closed=True,
                                   holes=tuple(p for p, d in zip(loops, depths) if d % 2 == 1),
                                   metadata=base.metadata + (("boundary_count", str(len(loops))),
                                             ("pattern_name", str(e.dxf.pattern_name))))
                else:
                    warn("empty_hatch", sid, "No usable hatch boundaries.")
            elif kind in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "SOLID", "TRACE", "3DFACE"}:
                coords = flatten(e)
                closed = kind in {"CIRCLE", "SOLID", "TRACE", "3DFACE"} or bool(
                    getattr(e, "closed", getattr(e, "is_closed", False)))
                curve = kind in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"} or (
                    kind in {"LWPOLYLINE", "POLYLINE"} and bool(e.has_arc))
                base = replace(base, coords=coords, closed=closed,
                               metadata=base.metadata + (("tessellation_tolerance", str(tolerance)),))
                if kind in {"ARC", "CIRCLE"}:
                    center = e.ocs().to_wcs(e.dxf.center)
                    base = replace(base, center=_xy(center), radius=float(e.dxf.radius),
                        start_angle=float(e.dxf.start_angle) if kind == "ARC" else 0.,
                        end_angle=float(e.dxf.end_angle) if kind == "ARC" else 360.)
                if kind == "LWPOLYLINE":
                    vertices = [tuple(float(v) for v in point) for point in e.get_points("xyseb")]
                    base = replace(base, metadata=base.metadata + (("vertices_xyseb", repr(vertices)),))
                elif kind == "POLYLINE":
                    base = replace(base, metadata=base.metadata + (("vertices", repr([
                        v.dxf.all_existing_dxf_attribs() for v in e.vertices])),))
                elif kind == "SPLINE":
                    base = replace(base, metadata=base.metadata + tuple((key, repr(list(getattr(e, key))))
                        for key in ("control_points", "fit_points", "knots", "weights")))
                if any(not math.isfinite(v) for pt in coords for v in pt):
                    raise ValueError("Non-finite drawing coordinates")
                if len(coords) >= 2:
                    prims.append(Primitive("curve" if curve else "line", coords, layer,
                                           None, None, sid, kind, closed))
                else:
                    warn("empty_geometry", sid, f"{kind} produced fewer than two points.")
                # DXFNamespace.get still rejects attributes unsupported by an
                # entity type even when a default is supplied (LINE has no
                # elevation, and CIRCLE has no start_angle/end_angle).
                existing = e.dxf.all_existing_dxf_attribs()
                elevations = []
                for name in ("elevation", "start", "end", "center"):
                    value = existing.get(name)
                    if isinstance(value, (int, float)):
                        elevations.append(float(value))
                    elif value is not None and hasattr(value, "z"):
                        elevations.append(float(value.z))
                if any(abs(v) > tolerance for v in elevations):
                    warn("projected_3d", sid, "Nonzero elevation projected into drawing XY plane.")
            else:
                warn("unsupported_entity", sid, f"{kind} retained as evidence; no geometry converter.")
            entities[index] = base
        except (ValueError, TypeError, AttributeError, ezdxf.DXFError, NotImplementedError) as exc:
            entities[index] = base
            warn("entity_conversion_failed", sid, f"{kind}: {exc}")

    for n, e in enumerate(doc.modelspace()):
        visit(e, str(e.dxf.get("handle", "")) or f"model/{n}:{e.dxftype()}")
    points = [pt for p in prims for pt in p.coords]
    xs, ys = zip(*points) if points else ((0.0,), (0.0,))
    return PrimitiveSet(tuple(prims), tuple(texts), max(xs)-min(xs), max(ys)-min(ys),
                        str(path), _sha256(path), tuple(entities), tuple(dimensions),
                        tuple(warnings), declared), float(upf)
