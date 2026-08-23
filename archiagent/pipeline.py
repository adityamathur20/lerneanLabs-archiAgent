"""Stages 0-8 wired together: PDF in, IFC out."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.classify.layers import (WALL_ROLES, LayerClassifier,
                                        layers_for_roles)
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces, space_boundary_graph
from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import load_pdf
from archiagent.model import BuildingModel, Issue
from archiagent.primitives import PrimitiveSet
from archiagent.scale.dimensions import extract_dimensions
from archiagent.scale.resolve import candidate_runs, resolve_scale
from archiagent.validate import validate


def extract_from_primitives(ps: PrimitiveSet, classifier: LayerClassifier,
                            wall_height_ft: float = 10.0,
                            stats: tuple[LayerStats, ...] | None = None
                            ) -> BuildingModel:
    """Stages 1-8 over an ALREADY-LOADED PrimitiveSet.

    Split out from `extract` so a caller that needs the layer inventory
    before classifying -- the CLI, which checks `--walls` names against the
    real layer names -- can build it once and hand both it and the parsed
    PDF straight in. load_pdf is the expensive step; nothing should do it
    twice on the authoring path.
    """
    if stats is None:
        stats = build_inventory(ps)
    classification = classifier.classify(stats)
    wall_layers = layers_for_roles(classification, WALL_ROLES)
    if not wall_layers:
        raise ValueError(
            "no layers were classified as walls. If a classification was "
            "served from the cache it may be responsible; re-run with "
            "--no-cache to force a fresh call.")

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
        layer_decisions=classification,
        source_path=ps.source_path,
        source_sha256=ps.source_sha256,
        wall_height_ft=wall_height_ft,
    )
    return replace(model, issues=validate(model))


def extract(pdf_path: str | Path, classifier: LayerClassifier, page: int = 0,
            wall_height_ft: float = 10.0) -> BuildingModel:
    return extract_from_primitives(load_pdf(pdf_path, page=page), classifier,
                                   wall_height_ft=wall_height_ft)


def run_pipeline(pdf_path: str | Path, classifier: LayerClassifier,
                 out_ifc: str | Path, page: int = 0
                 ) -> tuple[Path, tuple[Issue, ...]]:
    model = extract(pdf_path, classifier, page=page)
    return author_ifc(model, out_ifc), model.issues
