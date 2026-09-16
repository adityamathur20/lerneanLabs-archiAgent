"""Reopen exported IFC and verify schema, semantics and actual tessellated solids.

Volume/bounds tolerances below address geometry-kernel precision; they are not
source-drawing acceptance tolerances. This does not certify BIM compliance or
establish drawing accuracy: source and interpretation gates remain separate.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path

import numpy

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.shape
import ifcopenshell.validate
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from archiagent.ifc.author import FT, SLAB_THICKNESS_FT, is_rectangular
from archiagent.ifc.profile_layout import host_name, profile_material_slices, wall_layout
from archiagent.ifc.wall_runs import build_runs, run_footprints
from archiagent.validate import GEOMETRY_EPS_FT

PHYSICAL_CLASSES = ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcColumn", "IfcBeam")
VOLUME_ABS_TOL_M3 = 1e-8
VOLUME_REL_TOL = 1e-5
BOUNDS_TOL_M = 1e-5
AREA_TOL_M2 = 1e-6


def _scope(model):
    return (model.region_id, model.source_path, model.source_sha256)


def _provenance(product):
    return ifcopenshell.util.element.get_pset(product, "ArchiAgent_Provenance") or {}


def _product_scope(product):
    props = _provenance(product)
    return tuple(props.get(k, "") for k in ("RegionId", "SourcePath", "SourceSHA256"))


def _key(product):
    cls = next((name for name in PHYSICAL_CLASSES if product.is_a(name)), product.is_a())
    return (cls, product.Name or "", _product_scope(product))


def _bounds(points, z0, z1):
    return tuple(v * FT for v in (min(p[0] for p in points), min(p[1] for p in points), z0,
                                  max(p[0] for p in points), max(p[1] for p in points), z1))


def _run_corners(start, end, thickness):
    length = math.dist(start, end)
    nx = -(end[1]-start[1])/length * thickness/2
    ny = (end[0]-start[0])/length * thickness/2
    return tuple((p[0]+sign*nx, p[1]+sign*ny) for p in (start, end) for sign in (-1, 1))


def _expectations(models):
    expected = {}
    counts = Counter({cls: 0 for cls in PHYSICAL_CLASSES})
    counts.update({"IfcProject": 1, "IfcSite": 1, "IfcBuilding": 1,
                   "IfcBuildingStorey": len(models), "IfcSpace": 0,
                   "IfcOpeningElement": 0, "IfcRelVoidsElement": 0,
                   "IfcRelFillsElement": 0})
    opening_records = {}

    def add(cls, name, model, volume_ft3, bounds):
        key = (cls, name, _scope(model))
        if key in expected:
            raise ValueError(f"nonunique expected object: {key}")
        expected[key] = {"volume_m3": volume_ft3 * FT**3, "bounds_m": bounds}
        counts[cls] += 1

    for model in models:
        z = model.elevation_ft if model.elevation_ft is not None else 0.0
        profiles, polygons, mapping = wall_layout(model)
        for i, profile in enumerate(profiles):
            slices = profile_material_slices(model, i, polygons, mapping)
            volume = sum((hi-lo)*poly.area for lo, hi, poly in slices)
            bounds = (min(poly.bounds[0] for _, _, poly in slices),
                      min(poly.bounds[1] for _, _, poly in slices), z+min(lo for lo, _, _ in slices),
                      max(poly.bounds[2] for _, _, poly in slices),
                      max(poly.bounds[3] for _, _, poly in slices), z+max(hi for _, hi, _ in slices))
            add("IfcWall", f"WP:{profile.id}", model, volume, tuple(v*FT for v in bounds))
        # Walls are authored as runs whose joints are trimmed, so the expected
        # solid is the trimmed footprint, less the material its voids remove.
        layout = build_runs(model)
        shapes = run_footprints(layout, model)
        for index, wall_run in enumerate(layout.runs):
            length = wall_run.length_ft
            ux = (wall_run.end[0]-wall_run.start[0])/length
            uy = (wall_run.end[1]-wall_run.start[1])/length
            # The run is trimmed where it stops against another wall, so a void
            # reaching past the trim removes material that is no longer there.
            shape = shapes[index]
            spans = [(p[0]-wall_run.start[0])*ux+(p[1]-wall_run.start[1])*uy
                     for p in shape.exterior.coords] if not shape.is_empty else [0.0, length]
            lo, hi = max(0.0, min(spans)), min(length, max(spans))
            cuts = []
            for op in model.openings:
                if op.host_wall_index not in wall_run.members:
                    continue
                a,b = sorted((p[0]-wall_run.start[0])*ux+(p[1]-wall_run.start[1])*uy
                             for p in (op.start,op.end))
                # A void flush with the host ends within model tolerance cuts
                # through them; projection residue must not survive as a sliver.
                a = lo if a <= lo+GEOMETRY_EPS_FT else a
                b = hi if b >= hi-GEOMETRY_EPS_FT else b
                a, b = max(a, lo), min(b, hi)
                if b > a:
                    cuts.append(box(a, op.sill_ft, b, op.sill_ft+op.height_ft))
            section = box(lo,0,hi,model.wall_height_ft).difference(unary_union(cuts))
            if section.is_empty:
                raise ValueError(f"wall run {wall_run.id} has no material after opening subtraction")
            _,zlo,_,zhi = section.bounds
            void_area = box(lo,0,hi,model.wall_height_ft).area - section.area
            x0,y0,x1,y1 = shape.bounds
            add("IfcWall", wall_run.id, model,
                shape.area*model.wall_height_ft - void_area*wall_run.thickness_ft,
                (x0*FT, y0*FT, (z+zlo)*FT, x1*FT, y1*FT, (z+zhi)*FT))
        for i, footprint in enumerate(model.footprints):
            area = Polygon(footprint.boundary, footprint.holes).area
            add("IfcSlab",f"Floor{i:03d}",model,area*SLAB_THICKNESS_FT,
                _bounds(footprint.boundary,z-SLAB_THICKNESS_FT,z))
        counts["IfcSpace"] += len(model.spaces)
        for op in model.openings:
            counts["IfcOpeningElement"] += 1
            counts["IfcRelVoidsElement"] += 1
            opening_records[(op.id,_scope(model))] = (op, model)
            if op.kind not in {"door","window"}:
                continue
            counts["IfcRelFillsElement"] += 1
            host = model.walls[op.host_wall_index]
            thickness = min(host.thickness_ft,.15)
            add("IfcDoor" if op.kind=="door" else "IfcWindow",op.symbol_id or op.id,model,
                op.width_ft*thickness*op.height_ft,
                _bounds(_run_corners(op.start,op.end,thickness),z+op.sill_ft,z+op.sill_ft+op.height_ft))
        for symbol in model.symbols:
            if symbol.kind not in {"column","beam"} or symbol.height_ft is None:
                continue
            props = dict(symbol.properties)
            if symbol.kind=="beam" and "base_height_ft" not in props:
                continue
            base = float(props.get("base_height_ft","0"))
            if symbol.boundary:
                points = symbol.boundary
                area = Polygon(points).area
            else:
                c,s = math.cos(symbol.rotation_rad), math.sin(symbol.rotation_rad)
                points = tuple((symbol.position[0]+x*c-y*s,symbol.position[1]+x*s+y*c)
                               for x in (-symbol.width_ft/2,symbol.width_ft/2)
                               for y in (-symbol.depth_ft/2,symbol.depth_ft/2))
                area = symbol.width_ft*symbol.depth_ft
            add("IfcColumn" if symbol.kind=="column" else "IfcBeam",symbol.id,model,
                area*symbol.height_ft,_bounds(points,z+base,z+base+symbol.height_ft))
    return expected, counts, opening_records


def _json_safe(value):
    if value is None or isinstance(value,(str,bool,int)):
        return value
    if isinstance(value,float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value,dict):
        return {str(k):_json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _check_joins(f, models, error):
    """Verify the parametric wall structure, and that solids tile the model walls.

    Joined walls are only as good as their joints: the authored solids must
    cover the model's wall area exactly once, and each wall must keep the axis,
    layer set and profile a BIM tool needs to re-join it.
    """
    from shapely.strtree import STRtree

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    for model in models:
        layout = build_runs(model)
        shapes = run_footprints(layout, model)
        scope = _scope(model)
        by_name = {p.Name: p for p in f.by_type("IfcWall") if _product_scope(p) == scope}
        footprints = {}
        for index, wall_run in enumerate(layout.runs):
            product = by_name.get(wall_run.id)
            if product is None:
                continue
            label = f"{product.is_a()} #{product.id()} {product.Name}"
            if not product.is_a("IfcWallStandardCase"):
                error("wall_class_mismatch", label, "a straight wall run must be IfcWallStandardCase")
            representations = product.Representation.Representations if product.Representation else ()
            identifiers = {r.RepresentationIdentifier for r in representations}
            usage = ifcopenshell.util.element.get_material(product)
            thickness = wall_run.thickness_ft*FT
            if (not usage or not usage.is_a("IfcMaterialLayerSetUsage") or {"Axis", "Body"}-identifiers
                    or abs(sum(l.LayerThickness for l in usage.ForLayerSet.MaterialLayers)-thickness) > BOUNDS_TOL_M
                    or abs(usage.OffsetFromReferenceLine+thickness/2) > BOUNDS_TOL_M):
                error("wall_parametric_data_missing", label,
                      "run needs Axis and Body representations and a layer set centred on its axis")
            body = next((r for r in representations if r.RepresentationIdentifier == "Body"), None)
            if body is None or not body.Items:
                continue
            # A trimmed outline is still a rectangle unless the joint is angled.
            # The same predicate authoring uses, so the two cannot disagree.
            shape = shapes[index]
            if (is_rectangular(shape.area, shape.minimum_rotated_rectangle.area)
                    and not body.Items[0].SweptArea.is_a("IfcRectangleProfileDef")):
                error("wall_profile_not_parametric", label,
                      "a rectangular run must use IfcRectangleProfileDef")
            try:
                solid = ifcopenshell.geom.create_shape(settings, product)
                verts, faces = list(solid.geometry.verts), list(solid.geometry.faces)
                triangles = [Polygon([(verts[i*3]/FT, verts[i*3+1]/FT) for i in faces[t:t+3]])
                             for t in range(0, len(faces), 3)]
                footprints[wall_run.id] = unary_union([t for t in triangles if t.is_valid and t.area > 0])
            except Exception as exc:
                error("solid_geometry_failed", label, exc)
        # A junction that could not be joined may overlap or leave a gap there.
        widest = max((r.thickness_ft for r in layout.runs), default=0.)
        allowance = unary_union([box(p[0]-widest, p[1]-widest, p[0]+widest, p[1]+widest)
                                 for p in layout.untrimmed])
        names = sorted(footprints)
        index_of = {wall_run.id: index for index, wall_run in enumerate(layout.runs)}
        tree = STRtree([footprints[name] for name in names])
        for position, name in enumerate(names):
            for other in sorted(int(i) for i in tree.query(footprints[name])):
                if other <= position:
                    continue
                # A model without junctions predicts overlapping corners, and the
                # file matching that prediction is not an authoring fault.
                predicted = shapes[index_of[name]].intersection(shapes[index_of[names[other]]])
                overlap = (footprints[name].intersection(footprints[names[other]])
                           .difference(allowance).difference(predicted))
                if overlap.area*FT*FT > AREA_TOL_M2:
                    error("wall_overlap", f"{name}/{names[other]}",
                          f"walls share {overlap.area*FT*FT:.3g}m2 outside any untrimmed junction")
        if layout.runs:
            ideal = unary_union([shapes[i] for i in range(len(layout.runs))])
            missed = unary_union(list(footprints.values())).symmetric_difference(ideal).difference(allowance)
            if missed.area*FT*FT > AREA_TOL_M2:
                error("wall_coverage_mismatch", scope[0] or "model",
                      f"authored walls differ from the model wall area by {missed.area*FT*FT:.3g}m2")

    for relationship in f.by_type("IfcRelConnectsPathElements"):
        label = f"IfcRelConnectsPathElements #{relationship.id()}"
        geometry = relationship.ConnectionGeometry
        if geometry is None or not geometry.is_a("IfcConnectionPointGeometry"):
            error("connection_geometry_mismatch", label, "connection must carry its junction point")
            continue
        points = []
        for element, point in ((relationship.RelatingElement, geometry.PointOnRelatingElement),
                               (relationship.RelatedElement, geometry.PointOnRelatedElement)):
            if point is None:
                error("connection_geometry_mismatch", label,
                      "both connected walls need the junction point")
                break
            matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
            values = list(point.Coordinates)+[0.0, 0.0, 0.0]
            points.append((matrix @ numpy.array([values[0], values[1], values[2], 1.0]))[:3])
        else:
            if math.dist(points[0][:2], points[1][:2]) > BOUNDS_TOL_M:
                error("connection_geometry_mismatch", label,
                      "the two connection points do not coincide in world coordinates")


def validate_export(path, models):
    """Return JSON-safe exported-file QC, including real opening subtraction.

    `models` is the sequence passed to author_building; a single BuildingModel
    is also accepted. Errors fail QC, even if pre-export model checks passed.
    """
    if hasattr(models,"walls"):
        models = (models,)
    else:
        models = tuple(models)
    report = {"passed": False, "file": str(Path(path)), "schema": None,
              "errors": [], "warnings": [], "counts": {}, "geometry": [],
              "schema_validation": {"completed": False, "express_rules": True, "statements": []},
              "tolerances": {"volume_abs_m3": VOLUME_ABS_TOL_M3,
                             "volume_relative": VOLUME_REL_TOL,"bounds_m": BOUNDS_TOL_M}}

    def error(code, entity, message):
        report["errors"].append({"code":code,"entity":str(entity),"message":str(message)})

    try:
        f = ifcopenshell.open(str(path))
        report["schema"] = f.schema
    except Exception as exc:
        error("ifc_read_failed",str(path),exc)
        return report
    if f.schema != "IFC4":
        error("unexpected_schema","file",f"expected IFC4, found {f.schema}")
    # Census all physical IFC elements, so new/unsupported classes cannot hide
    # behind the explicit expectation list below. Openings are subtraction
    # features, but must also retain their authored representation.
    physical = [p for p in f.by_type("IfcElement") if not p.is_a("IfcFeatureElement")]
    represented = 0
    for product in (*f.by_type("IfcElement"), *f.by_type("IfcSpace")):
        has_rep = bool(product.Representation and any(
            rep.Items for rep in product.Representation.Representations))
        if not has_rep:
            error("missing_representation", product.id(),
                  f"{product.is_a()} {product.Name} lacks a nonempty representation")
        if product in physical:
            represented += int(has_rep)
            if not any(product.is_a(cls) for cls in PHYSICAL_CLASSES):
                error("unsupported_physical_element", product.id(),
                      f"{product.is_a()} has no geometry expectation; extend validation before acceptance")
    report["coverage"] = {"physical_elements": len(physical), "represented_physical_elements": represented,
                          "missing_physical_representations": len(physical)-represented,
                          "represented_geometry_checked": 0}
    guids = [root.GlobalId for root in f.by_type("IfcRoot")]
    if len(guids) != len(set(guids)):
        error("duplicate_global_id", "IfcRoot", "GlobalIds must be unique within an IFC file")
    try:
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f,logger,express_rules=True)
        report["schema_validation"]["completed"] = True
        report["schema_validation"]["statements"] = _json_safe(logger.statements)
        for statement in logger.statements:
            if str(statement.get("level","error")).lower() in {"error","critical"}:
                error("ifc_schema_violation",statement.get("instance","schema"),statement.get("message",statement))
    except Exception as exc:
        error("ifc_schema_validation_failed","schema",exc)
    try:
        expected, counts, opening_records = _expectations(models)
    except Exception as exc:
        error("invalid_expected_model","model",exc)
        return report
    for cls, expected_count in counts.items():
        actual = len(f.by_type(cls))
        report["counts"][cls] = {"expected":expected_count,"actual":actual}
        if actual != expected_count:
            error("entity_count_mismatch",cls,f"expected {expected_count}, found {actual}")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords",True)
    settings.set("disable-opening-subtractions",False)
    matched = set()
    for cls in PHYSICAL_CLASSES:
        for product in f.by_type(cls):
            prior_errors = len(report["errors"])
            key = _key(product)
            label = f"{product.is_a()} #{product.id()} {product.Name}"
            expectation = expected.get(key)
            if expectation is None:
                error("unexpected_product",label,"no corresponding source model object/provenance")
            elif key in matched:
                error("duplicate_product",label,"more than one IFC product matches this model object")
            matched.add(key)
            containers = tuple(product.ContainedInStructure)
            if len(containers)!=1:
                error("invalid_containment",label,"physical element must have exactly one storey container")
            elif (not containers[0].RelatingStructure.is_a("IfcBuildingStorey")
                  or _product_scope(containers[0].RelatingStructure)!=_product_scope(product)):
                error("wrong_storey",label,"container storey does not match object source region")
            record = {"ifc_id":product.id(),"global_id":product.GlobalId,"class":cls,
                      "name":product.Name,"region_id":_product_scope(product)[0],
                      "volume_m3":None,"bounds_m":None,"expected":expectation,"passed":False}
            try:
                shape = ifcopenshell.geom.create_shape(settings,product)
                verts = list(shape.geometry.verts)
                if not verts or len(verts)%3 or not all(math.isfinite(v) for v in verts):
                    raise ValueError("triangulated coordinates must be nonempty and finite")
                volume = float(ifcopenshell.util.shape.get_volume(shape.geometry))
                if not math.isfinite(volume) or volume<=0:
                    raise ValueError("triangulated solid has nonpositive or nonfinite volume")
                bounds = tuple(min(verts[i::3]) for i in range(3))+tuple(max(verts[i::3]) for i in range(3))
                record.update(volume_m3=volume,bounds_m=bounds)
                if expectation:
                    wanted = expectation["volume_m3"]
                    if not math.isclose(volume,wanted,rel_tol=VOLUME_REL_TOL,abs_tol=VOLUME_ABS_TOL_M3):
                        error("solid_volume_mismatch",label,f"actual {volume:.10g}m3 versus model {wanted:.10g}m3 (including voids)")
                    if any(abs(a-b)>BOUNDS_TOL_M for a,b in zip(bounds,expectation["bounds_m"])):
                        error("solid_bounds_mismatch",label,f"world bounds {bounds} differ from model {expectation['bounds_m']}")
            except Exception as exc:
                error("solid_geometry_failed",label,exc)
            record["passed"] = expectation is not None and len(report["errors"])==prior_errors
            report["geometry"].append(record)
            report["coverage"]["represented_geometry_checked"] += int(record["volume_m3"] is not None)
    for key in expected.keys()-matched:
        error("missing_product",key,"model object is absent from IFC")
    seen_openings = set()
    for product in f.by_type("IfcOpeningElement"):
        key = (product.Name or "",_product_scope(product))
        label = f"IfcOpeningElement #{product.id()} {product.Name}"
        if key not in opening_records or key in seen_openings:
            error("unexpected_opening",label,"opening is duplicated or lacks matching model provenance")
            continue
        seen_openings.add(key)
        op,model = opening_records[key]
        voids = tuple(product.VoidsElements)
        if len(voids)!=1:
            error("invalid_void_relationship",label,"opening must void exactly one host wall")
        else:
            host = voids[0].RelatingBuildingElement
            if _key(host)!=("IfcWall",host_name(model, op.host_wall_index),_scope(model)):
                error("wrong_void_host",label,"opening voids a different wall from its semantic host")
        fillings = tuple(product.HasFillings)
        expected_fill = op.kind in {"door","window"}
        if len(fillings)!=(1 if expected_fill else 0):
            error("invalid_fill_relationship",label,"opening has an incorrect number of fillings")
        elif expected_fill:
            filling = fillings[0].RelatedBuildingElement
            wanted = ("IfcDoor" if op.kind=="door" else "IfcWindow",op.symbol_id or op.id,_scope(model))
            if _key(filling)!=wanted:
                error("wrong_opening_filling",label,"filling type/instance differs from the model")
    for product in (*f.by_type("IfcDoor"),*f.by_type("IfcWindow")):
        if len(product.FillsVoids)!=1:
            error("unhosted_filling",product.id(),"door/window must fill exactly one opening")
    for space in f.by_type("IfcSpace"):
        parents = tuple(space.Decomposes)
        if (len(parents)!=1 or not parents[0].RelatingObject.is_a("IfcBuildingStorey")
                or _product_scope(parents[0].RelatingObject)!=_product_scope(space)):
            error("invalid_space_storey",space.id(),"space must aggregate into its source storey")
    _check_joins(f, models, error)
    report["passed"] = not report["errors"]
    return _json_safe(report)
