"""Semantic IFC4 authoring in SI with source-linked objects and real voids.

Wall sweeps remain separate solids at joints; path connections describe their
relationship but do not trim physical overlaps. No storey heights are inferred.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.util.element
import ifcopenshell.util.shape_builder

from archiagent.ifc.wall_runs import build_runs
from archiagent.model import BuildingModel
from archiagent.ifc.appearance import APPEARANCE_BASIS, PRESENTATION_PALETTE, PresentationStyles

FT = 0.3048
SLAB_THICKNESS_FT = 0.5
run = ifcopenshell.api.run


def author_ifc(model: BuildingModel, out_path: str | Path) -> Path:
    return author_building((model,), out_path)


def author_building(models: tuple[BuildingModel, ...] | list[BuildingModel],
                    out_path: str | Path) -> Path:
    """Combine explicitly registered plans; never invent inter-storey spacing.

    A single draft plan may have an assumed zero elevation. Multiple plans
    require supplied elevations and unique region IDs to avoid accidental
    stacking or duplicate storeys.
    """
    models = tuple(models)
    if not models:
        raise ValueError("at least one plan model is required")
    if len(models) > 1:
        if any(m.elevation_ft is None or not m.region_id for m in models):
            raise ValueError("multiple plans require explicit elevations and region IDs")
        if len({m.region_id for m in models}) != len(models):
            raise ValueError("duplicate plan region IDs")
    # Reject malformed geometry before calling native geometry code; acceptance
    # warnings/errors such as uncertain scale still permit a marked draft IFC.
    from archiagent.validate import validate
    fatal_codes = {"nonfinite_geometry", "invalid_wall_height", "zero_thickness_wall",
                   "zero_length_wall", "invalid_polygon", "unclosed_space",
                   "unclosed_footprint", "invalid_opening", "invalid_opening_host",
                   "opening_outside_host", "overlapping_openings", "invalid_symbol",
                   "invalid_junction_index", "invalid_junction", "invalid_scale",
                   "invalid_wall_profile", "unclosed_wall_profile"}
    failures = [i for m in models for i in validate(m) if i.code in fatal_codes]
    if failures:
        raise ValueError("unsafe IFC geometry: " + "; ".join(i.msg for i in failures))
    from archiagent.ifc.profile_layout import wall_layout
    for model in models:
        wall_layout(model)  # Reject ambiguous overlap before native authoring.

    f = ifcopenshell.file(schema="IFC4")
    project = run("root.create_entity", f, ifc_class="IfcProject",
                  name=f"ArchiAgent — {Path(models[0].source_path).stem}")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    ctx = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model",
               context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    # Joint regeneration reads the wall axis from the Plan/Axis context.
    plan = run("context.add_context", f, context_type="Plan")
    axis = run("context.add_context", f, context_type="Plan",
               context_identifier="Axis", target_view="GRAPH_VIEW", parent=plan)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    run("aggregate.assign_object", f, products=[site], relating_object=project)
    run("aggregate.assign_object", f, products=[building], relating_object=site)
    presentation = PresentationStyles(f)
    layer_sets = {}  # shared by every storey, like the presentation styles
    for model in models:
        _author_plan(f, body, axis, building, model, presentation, layer_sets)
    from archiagent.ifc.identity import assign_stable_ids
    assign_stable_ids(f, models)
    # No successful export may conceal an empty represented physical element.
    for product in (*f.by_type("IfcElement"), *f.by_type("IfcSpace")):
        if not product.Representation or not any(rep.Items for rep in product.Representation.Representations):
            raise ValueError(f"missing IFC representation: {product.is_a()} {product.Name}")
    out_path = Path(out_path)
    f.write(str(out_path))
    return out_path


def _palette_class(ifc_class):
    """Wall subtypes share the IfcWall presentation preset."""
    return "IfcWall" if ifc_class.startswith("IfcWall") else ifc_class


def _author_plan(f, body, axis, building, model, presentation, layer_sets):
    sb = ifcopenshell.util.shape_builder.ShapeBuilder(f)
    z = (model.elevation_ft if model.elevation_ft is not None else 0.0) * FT
    height_m = model.wall_height_ft * FT
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey",
                 name=model.storey_name)
    storey.Elevation = z
    run("aggregate.assign_object", f, products=[storey], relating_object=building)

    def placement(x=0.0, y=0.0, elevation=z, angle=0.0):
        return f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint((float(x), float(y), float(elevation))),
            f.createIfcDirection((0.0, 0.0, 1.0)),
            f.createIfcDirection((math.cos(angle), math.sin(angle), 0.0))))

    storey.ObjectPlacement = placement()

    def provenance(product, extra=None):
        values = {
            "SourcePath": model.source_path, "SourceSHA256": model.source_sha256,
            "RegionId": model.region_id, "ScaleVerified": model.scale_verified,
            "FootprintVerified": model.footprint_verified,
            "SymbolsVerified": model.symbols_verified,
            "SourceRegionBoundsJSON": json.dumps(model.source_region_bounds),
            "SourceOriginJSON": json.dumps(model.source_origin),
            "ScaleUnitsPerFoot": model.scale.units_per_foot,
        }
        values.update(extra or {})
        if _palette_class(product.is_a()) in PRESENTATION_PALETTE:
            values["AppearanceBasis"] = APPEARANCE_BASIS
            values["AppearancePreset"] = PRESENTATION_PALETTE[_palette_class(product.is_a())][0]
        pset = run("pset.add_pset", f, product=product, name="ArchiAgent_Provenance")
        run("pset.edit_pset", f, pset=pset, properties=values)

    from archiagent.validate import validate
    report_issues = tuple(dict.fromkeys((*model.issues, *validate(model))))
    provenance(storey, {
        "ExportStatus": "draft" if any(i.severity == "error" for i in report_issues) else "geometry-checked",
        "ElevationAssumed": model.elevation_ft is None,
        "AssumptionsJSON": json.dumps([asdict(a) for a in model.assumptions]),
        "DimensionChecksJSON": json.dumps([asdict(d) for d in model.dimension_checks]),
        "IssuesJSON": json.dumps([asdict(i) for i in report_issues]),
        "SymbolsJSON": json.dumps([asdict(s) for s in model.symbols]),
        "EndpointAdjustmentsJSON": json.dumps([asdict(a) for a in model.endpoint_adjustments]),
        "RepairSnapIn": model.repair_snap_in,
        "RepairExtendIn": model.repair_extend_in,
        "JointGeometry": "Separate sweeps; physical joint overlaps are not trimmed",
    })

    def entity(cls, name, solid, loc, *, space=False, predefined_type=None):
        kwargs = {"ifc_class": cls, "name": name}
        if predefined_type:
            kwargs["predefined_type"] = predefined_type
        obj = run("root.create_entity", f, **kwargs)
        if space:
            run("aggregate.assign_object", f, products=[obj], relating_object=storey)
        elif cls != "IfcOpeningElement":
            run("spatial.assign_container", f, products=[obj], relating_structure=storey)
        run("geometry.assign_representation", f, product=obj,
            representation=sb.get_representation(body, [solid]))
        presentation.assign(_palette_class(cls), solid)
        obj.ObjectPlacement = loc
        return obj

    def rectangle_solid(length, thickness, height):
        # A parametric profile: BIM tools read the wall's length and thickness
        # from it, and joint regeneration rewrites it only where it must.
        profile = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                                  Position=f.createIfcAxis2Placement2D(
                                      f.createIfcCartesianPoint((length / 2, 0.0)), None),
                                  XDim=length, YDim=thickness)
        return sb.extrude(profile, magnitude=height, extrusion_vector=(0.0, 0.0, 1.0))

    def polygon_solid(boundary, holes, height, down=False):
        def curve(ring, clockwise=False):
            ring = list(ring[:-1] if ring[0] == ring[-1] else ring)
            signed_area = sum(a[0] * b[1] - b[0] * a[1]
                              for a, b in zip(ring, ring[1:] + ring[:1]))
            if (signed_area < 0) != clockwise:
                ring.reverse()
            return sb.polyline([(p[0] * FT, p[1] * FT) for p in ring], closed=True)
        outer = curve(boundary)
        if holes:
            profile = f.create_entity("IfcArbitraryProfileDefWithVoids",
                                      ProfileType="AREA", OuterCurve=outer,
                                      InnerCurves=[curve(h, clockwise=True) for h in holes])
        else:
            profile = f.create_entity("IfcArbitraryClosedProfileDef",
                                      ProfileType="AREA", OuterCurve=outer)
        return sb.extrude(profile, magnitude=height,
                          extrusion_vector=(0.0, 0.0, -1.0 if down else 1.0))

    from archiagent.ifc.profile_layout import wall_layout
    profiles, _, profile_mapping = wall_layout(model)
    profile_entities = {}
    for i, profile in enumerate(profiles):
        wall = entity("IfcWall", f"WP:{profile.id}",
                      polygon_solid(profile.boundary, profile.holes, height_m), placement())
        provenance(wall, {"SourceLayer": profile.source_layer, "Detector": profile.detector,
                          "SourceIds": json.dumps(profile.source_ids), "ProfileId": profile.id,
                          "ReviewStatus": profile.review_status, "HeightFt": model.wall_height_ft,
                          "RepresentationBasis": "Accepted source wall-face polygon",
                          "HeightEvidence": "See storey assumptions; height is not independently verified"})
        profile_entities[i] = wall
    def layer_set(thickness_m, priority):
        """One layer set per thickness and priority; priority decides an L corner."""
        key = (round(thickness_m, 9), priority)
        if key not in layer_sets:
            label = PRESENTATION_PALETTE["IfcWall"][0]
            if "material" not in layer_sets:
                layer_sets["material"] = run("material.add_material", f, name=label)
            material_set = run("material.add_material_set", f,
                               name=f"{label} {thickness_m*1000:.0f}mm P{priority}",
                               set_type="IfcMaterialLayerSet")
            layer = run("material.add_layer", f, layer_set=material_set,
                        material=layer_sets["material"])
            run("material.edit_layer", f, layer=layer,
                attributes={"LayerThickness": thickness_m, "Priority": priority})
            layer_sets[key] = material_set
        return layer_sets[key]

    wall_entities = {idx: profile_entities[profile_index]
                     for idx, profile_index in profile_mapping.items()}
    layout = build_runs(model)
    for wall_run in layout.runs:
        angle = math.atan2(wall_run.end[1] - wall_run.start[1], wall_run.end[0] - wall_run.start[0])
        length_m, thickness_m = wall_run.length_ft * FT, wall_run.thickness_ft * FT
        wall = entity("IfcWallStandardCase", wall_run.id,
                      rectangle_solid(length_m, thickness_m, height_m),
                      placement(wall_run.start[0] * FT, wall_run.start[1] * FT, angle=angle))
        run("geometry.assign_representation", f, product=wall,
            representation=f.createIfcShapeRepresentation(
                axis, "Axis", "Curve2D",
                [f.createIfcPolyline([f.createIfcCartesianPoint((0.0, 0.0)),
                                      f.createIfcCartesianPoint((length_m, 0.0))])]))
        run("material.assign_material", f, products=[wall],
            type="IfcMaterialLayerSetUsage", material=layer_set(thickness_m, wall_run.priority))
        ifcopenshell.util.element.get_material(wall).OffsetFromReferenceLine = -thickness_m / 2
        member = model.walls[wall_run.members[0]]
        provenance(wall, {"SourceLayer": wall_run.source_layer, "Detector": member.detector,
                          "SourceIds": json.dumps(list(wall_run.source_ids)),
                          "ModelWallIndices": json.dumps(list(wall_run.members)),
                          "ThicknessSource": member.thickness_source,
                          "HeightFt": model.wall_height_ft,
                          "HeightEvidence": "See storey assumptions; height is not independently verified",
                          "ThicknessIn": wall_run.thickness_ft * 12,
                          "ScaleMaxResidualIn": model.scale.max_residual_in})
        for i in wall_run.members:
            wall_entities[i] = wall

    # One connection per wall end: connect_path drops an existing connection at
    # an end it reuses, which is why a through wall is authored as one run.
    relationships = []
    for connection in layout.connections:
        relating = wall_entities[layout.runs[connection.relating].members[0]]
        related = wall_entities[layout.runs[connection.related].members[0]]
        relationships.append((connection, run(
            "geometry.connect_path", f, relating_element=relating, related_element=related,
            relating_connection=connection.relating_type,
            related_connection=connection.related_type)))

    # Regeneration rebuilds a connected wall's body from its axis, layer set and
    # connections; that is what trims the joint. It replaces the styled item.
    joined = {index for connection in layout.connections
              for index in (connection.relating, connection.related)}
    for index in sorted(joined):
        wall = wall_entities[layout.runs[index].members[0]]
        run("geometry.regenerate_wall_representation", f, wall=wall)
        regenerated = next(r for r in wall.Representation.Representations
                           if r.RepresentationIdentifier == "Body")
        presentation.assign("IfcWall", regenerated.Items[0])

    for op in model.openings:
        host = model.walls[op.host_wall_index]
        angle = math.atan2(op.end[1] - op.start[1], op.end[0] - op.start[0])
        loc = lambda: placement(op.start[0] * FT, op.start[1] * FT,
                                z + op.sill_ft * FT, angle)
        void = entity("IfcOpeningElement", op.id,
                      rectangle_solid(op.width_ft * FT, (host.thickness_ft + 0.02) * FT,
                                      op.height_ft * FT), loc(), predefined_type="OPENING")
        run("feature.add_feature", f, feature=void, element=wall_entities[op.host_wall_index])
        info = {"SourceIds": json.dumps(op.source_ids), "SymbolId": op.symbol_id,
                "Evidence": op.evidence, "Subtype": op.subtype,
                "HeightAssumed": op.assumed_height, "HostWallIndex": op.host_wall_index}
        provenance(void, info)
        if op.kind in {"door", "window"}:
            filling = entity("IfcDoor" if op.kind == "door" else "IfcWindow", op.symbol_id or op.id,
                             rectangle_solid(op.width_ft * FT, min(host.thickness_ft, 0.15) * FT,
                                             op.height_ft * FT), loc())
            filling.OverallWidth = op.width_ft * FT
            filling.OverallHeight = op.height_ft * FT
            run("feature.add_filling", f, opening=void, element=filling)
            provenance(filling, {**info, "Representation": "Simplified rectangular infill; no frame detail"})

    for i, space in enumerate(model.spaces):
        obj = entity("IfcSpace", f"S{i:03d}",
                     polygon_solid(space.boundary, space.holes, height_m), placement(), space=True)
        provenance(obj, {"AreaSqft": space.area_sqft, "BoundaryBasis": "Interpreted room boundary"})

    # Structural floors require a footprint. Room detection is deliberately
    # independent: missing footprints are reported rather than faked by rooms.
    for i, footprint in enumerate(model.footprints):
        slab = entity("IfcSlab", f"Floor{i:03d}",
                      polygon_solid(footprint.boundary, footprint.holes,
                                    SLAB_THICKNESS_FT * FT, down=True),
                      placement(), predefined_type="FLOOR")
        provenance(slab, {"AreaSqft": footprint.area_sqft,
                          "ThicknessAssumed": True, "ThicknessFt": SLAB_THICKNESS_FT,
                          "BoundaryBasis": "Interpreted footprint", "HoleCount": len(footprint.holes)})

    for symbol in model.symbols:
        if symbol.kind not in {"column", "beam"} or symbol.height_ft is None:
            continue
        props = dict(symbol.properties)
        if symbol.kind == "beam" and "base_height_ft" not in props:
            continue  # a plan projection cannot establish a beam's elevation
        base = float(props.get("base_height_ft", "0"))
        if symbol.boundary:
            solid = polygon_solid(symbol.boundary, (), symbol.height_ft * FT)
            loc = placement(elevation=z + base * FT)
        else:
            solid = rectangle_solid(symbol.width_ft * FT, symbol.depth_ft * FT,
                                    symbol.height_ft * FT)
            # Symbol position is the footprint centre, unlike the wall start.
            x = symbol.position[0] - symbol.width_ft / 2 * math.cos(symbol.rotation_rad)
            y = symbol.position[1] - symbol.width_ft / 2 * math.sin(symbol.rotation_rad)
            loc = placement(x * FT, y * FT, z + base * FT, symbol.rotation_rad)
        obj = entity("IfcColumn" if symbol.kind == "column" else "IfcBeam", symbol.id, solid, loc)
        provenance(obj, {"SourceIds": json.dumps(symbol.source_ids), "Evidence": symbol.evidence,
                         "Subtype": symbol.subtype, "Confidence": symbol.confidence})
