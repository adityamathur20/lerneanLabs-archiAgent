"""Stages 0-8 wired together: PDF in, IFC out."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.classify.layers import (WALL_CONFIDENCE_FLOOR, WALL_ROLES,
                                        LayerClassifier, layers_for_roles)
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces, space_boundary_graph
from archiagent.geometry.walls import (detect_walls_paired_lines,
                                       reject_ladder_runs)
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import load_pdf
from archiagent.model import BuildingModel, Issue
from archiagent.primitives import PrimitiveSet
from archiagent.scale.dimensions import extract_dimensions
from archiagent.scale.resolve import ScaleResult, candidate_runs, resolve_scale
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
    wall_layers = layers_for_roles(classification, WALL_ROLES,
                                   min_confidence=WALL_CONFIDENCE_FLOOR)
    if not wall_layers:
        _no_wall_layers(classification)

    scale = resolve_scale(extract_dimensions(ps), candidate_runs(ps, wall_layers))
    walls = detect_walls_paired_lines(ps, wall_layers, scale.units_per_foot)
    walls, treads = reject_ladder_runs(walls)

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
    return replace(model, issues=validate(model) + _tread_issues(treads))


def extract_from_dxf(ps: PrimitiveSet, classifier: LayerClassifier, *,
                     units_per_foot: float,
                     wall_height_ft: float = 10.0) -> BuildingModel:
    """DXF path: units are known, so scale resolution is skipped entirely.

    The PDF path infers units_per_foot from printed dimension text and gates
    it on R1. A DXF declares its units, so there is nothing to infer and no
    residual to gate -- ScaleResult records the supplied value with an empty
    residual set.

    Mirrors extract_from_primitives, minus resolve_scale/candidate_runs/
    extract_dimensions. `stats` is not a parameter here (unlike
    extract_from_primitives): the caller -- the CLI -- pairs this with a
    classifier that already carries a precomputed Classification, built
    against the fuller build_dxf_inventory stats, since this function only
    has `ps` and cannot build that DXF-aware inventory itself.
    """
    stats = build_inventory(ps)
    classification = classifier.classify(stats)
    wall_layers = layers_for_roles(classification, WALL_ROLES,
                                   min_confidence=WALL_CONFIDENCE_FLOOR)
    if not wall_layers:
        _no_wall_layers(classification)

    scale = ScaleResult(units_per_foot=units_per_foot, convention="clear",
                        residuals_in=(), max_residual_in=0.0,
                        matched_count=0, total_dimensions=0)
    walls = detect_walls_paired_lines(ps, wall_layers, scale.units_per_foot)
    walls, treads = reject_ladder_runs(walls)

    # See extract_from_primitives for why this graph split exists: real
    # walls (with real doorway gaps) here, a door-width-bridged graph for
    # room detection.
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
    return replace(model, issues=validate(model) + _tread_issues(treads))


def extract(pdf_path: str | Path, classifier: LayerClassifier, page: int = 0,
            wall_height_ft: float = 10.0) -> BuildingModel:
    return extract_from_primitives(load_pdf(pdf_path, page=page), classifier,
                                   wall_height_ft=wall_height_ft)


def run_pipeline(pdf_path: str | Path, classifier: LayerClassifier,
                 out_ifc: str | Path, page: int = 0
                 ) -> tuple[Path, tuple[Issue, ...]]:
    model = extract(pdf_path, classifier, page=page)
    return author_ifc(model, out_ifc), model.issues


def _tread_issues(treads: tuple) -> tuple[Issue, ...]:
    """Report rejected tread runs. A silent drop would look like a detector
    bug the next time someone counts walls."""
    if not treads:
        return ()
    by_layer: dict[str, int] = {}
    for w in treads:
        by_layer[w.source_layer] = by_layer.get(w.source_layer, 0) + 1
    return tuple(
        Issue("info", layer, "tread_run_rejected",
              f"{n} evenly-spaced parallel runs on layer {layer!r} were "
              "dropped as stair treads or hatch, not walls")
        for layer, n in sorted(by_layer.items()))


def _no_wall_layers(classification) -> None:
    """Raise, distinguishing "nothing looked like a wall" from "something did
    but we did not believe it". They are different problems: the first wants a
    different drawing or --walls, the second wants --vision or a better model."""
    unsure = sorted(
        (d for d in classification
         if d.role in WALL_ROLES and d.confidence < WALL_CONFIDENCE_FLOOR),
        key=lambda d: -d.confidence)
    if unsure:
        named = ", ".join(f"{d.layer!r} ({d.confidence:.0%})" for d in unsure[:5])
        raise ValueError(
            f"{len(unsure)} layer(s) were classified as walls but none reached "
            f"the {WALL_CONFIDENCE_FLOOR:.0%} confidence floor: {named}. "
            "Name them with --walls to use them anyway, or re-run without "
            "--no_vision so the image stage can confirm them.")
    raise ValueError(
        "no layers were classified as walls. If a classification was "
        "served from the cache it may be responsible; re-run with "
        "--no-cache to force a fresh call.")
