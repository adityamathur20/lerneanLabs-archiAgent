"""Headless IFC4 authoring via ifcopenshell.api — no Blender required.

Walls are authored parametrically (axis + profile + extrusion), never as
meshes: an IfcFacetedBrep blob has no parametric meaning and would gut
Phase 3, where MEP needs real entities to attach to.

Verified API traps for ifcopenshell 0.8.5 (PLAN.md §4):
  - void.* was renamed feature.*
  - ShapeBuilder.rectangle anchors at the CORNER, so the profile must be
    offset by -thickness/2 and the object placed at the wall's start
  - IfcSpace uses aggregate.assign_object, not spatial.assign_container
"""

from __future__ import annotations

import math
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.guid
import ifcopenshell.util.shape_builder

from archiagent.model import BuildingModel

FT = 0.3048  # metres per foot; IFC is authored in SI

run = ifcopenshell.api.run


def author_ifc(model: BuildingModel, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    f = ifcopenshell.file(schema="IFC4")

    project = run("root.create_entity", f, ifc_class="IfcProject",
                  name=f"ArchiAgent — {Path(model.source_path).stem}")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    ctx = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model",
               context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)

    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey",
                 name="Ground Floor")
    run("aggregate.assign_object", f, products=[site], relating_object=project)
    run("aggregate.assign_object", f, products=[building], relating_object=site)
    run("aggregate.assign_object", f, products=[storey], relating_object=building)

    sb = ifcopenshell.util.shape_builder.ShapeBuilder(f)
    height_m = model.wall_height_ft * FT

    # idx -> IFC wall entity, for IfcRelConnectsPathElements below. Walls of
    # zero length are skipped, so this map has gaps relative to model.walls;
    # junction code below must tolerate a missing index.
    wall_entities: dict[int, object] = {}

    for idx, w in enumerate(model.walls):
        length_m = w.length_ft * FT
        if length_m <= 0.0:
            continue
        thickness_m = w.thickness_ft * FT

        wall = run("root.create_entity", f, ifc_class="IfcWall",
                   name=f"W{idx:03d}")
        run("spatial.assign_container", f, products=[wall],
            relating_structure=storey)

        # TRAP: rectangle() anchors at the corner. Offset by -t/2 so the
        # wall's own axis is its centerline, then place the object at p0.
        profile = sb.rectangle(size=(length_m, thickness_m),
                               position=(0.0, -thickness_m / 2.0))
        solid = sb.extrude(profile, magnitude=height_m,
                           extrusion_vector=(0.0, 0.0, 1.0))
        run("geometry.assign_representation", f, product=wall,
            representation=sb.get_representation(body, [solid]))

        angle = math.atan2(w.end[1] - w.start[1], w.end[0] - w.start[0])
        wall.ObjectPlacement = f.createIfcLocalPlacement(
            None,
            f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(
                    (w.start[0] * FT, w.start[1] * FT, 0.0)),
                f.createIfcDirection((0.0, 0.0, 1.0)),
                f.createIfcDirection((math.cos(angle), math.sin(angle), 0.0))))

        pset = run("pset.add_pset", f, product=wall,
                   name="ArchiAgent_Provenance")
        run("pset.edit_pset", f, pset=pset, properties={
            "SourceLayer": w.source_layer,
            "Detector": w.detector,
            "ThicknessSource": w.thickness_source,
            "ThicknessIn": round(w.thickness_ft * 12.0, 3),
            "ScaleUnitsPerFoot": round(model.scale.units_per_foot, 6),
            "ScaleMaxResidualIn": model.scale.max_residual_in,
        })

        wall_entities[idx] = wall

    def _connection_type(wall_index: int, point) -> str:
        """ATSTART / ATEND if the junction is at a wall's end, else ATPATH."""
        w = model.walls[wall_index]
        if math.dist(point, w.start) < 1e-6:
            return "ATSTART"
        if math.dist(point, w.end) < 1e-6:
            return "ATEND"
        return "ATPATH"

    for junction in model.junctions:
        indices = [i for i in junction.wall_indices if i in wall_entities]
        for a, b in zip(indices, indices[1:]):
            f.create_entity(
                "IfcRelConnectsPathElements",
                GlobalId=ifcopenshell.guid.new(),
                RelatingElement=wall_entities[a],
                RelatedElement=wall_entities[b],
                RelatingPriorities=[],
                RelatedPriorities=[],
                RelatingConnectionType=_connection_type(a, junction.point),
                RelatedConnectionType=_connection_type(b, junction.point),
            )

    for i, space in enumerate(model.spaces):
        sp = run("root.create_entity", f, ifc_class="IfcSpace",
                 name=f"S{i:03d}")
        # TRAP: IfcSpace decomposes the storey; it is not "contained" in it.
        run("aggregate.assign_object", f, products=[sp],
            relating_object=storey)

    f.write(str(out_path))
    return out_path
