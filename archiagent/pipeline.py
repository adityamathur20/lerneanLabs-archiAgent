"""Stages 0-8 wired together: PDF in, IFC out."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import (WALL_ROLES, LayerClassifier,
                                        layers_for_roles)
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces, space_boundary_graph
from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import load_pdf
from archiagent.model import BuildingModel, Issue
from archiagent.scale.dimensions import extract_dimensions
from archiagent.scale.resolve import candidate_runs, resolve_scale
from archiagent.validate import validate


def extract(pdf_path: str | Path, classifier: LayerClassifier, page: int = 0,
            wall_height_ft: float = 10.0) -> BuildingModel:
    ps = load_pdf(pdf_path, page=page)
    classification = classifier.classify(build_inventory(ps))
    wall_layers = layers_for_roles(classification, WALL_ROLES)
    if not wall_layers:
        raise ValueError("no layers were classified as walls")

    scale = resolve_scale(extract_dimensions(ps), candidate_runs(ps, wall_layers))
    walls = detect_walls_paired_lines(ps, wall_layers, scale.units_per_foot)

    # The wall graph is a forest: every room has a door, and a door is a
    # real gap that resolve_junctions correctly refuses to close beyond its
    # 6in drafting-slop budget. Real walls (with real doorway gaps) go into
    # the model; room detection uses a SEPARATE, door-width-bridged graph
    # (Task 11's space_boundary_graph) purely for polygonization.
    graph = resolve_junctions(walls)
    spaces = detect_spaces(space_boundary_graph(walls))

    model = BuildingModel(
        walls=graph.walls,
        junctions=graph.junctions,
        unresolved=graph.unresolved,
        spaces=spaces,
        scale=scale,
        layer_roles={name: role.value for name, (role, _) in classification.items()},
        source_path=str(pdf_path),
        source_sha256=ps.source_sha256,
        wall_height_ft=wall_height_ft,
    )
    return replace(model, issues=validate(model))


def run_pipeline(pdf_path: str | Path, classifier: LayerClassifier,
                 out_ifc: str | Path, page: int = 0
                 ) -> tuple[Path, tuple[Issue, ...]]:
    model = extract(pdf_path, classifier, page=page)
    return author_ifc(model, out_ifc), model.issues
