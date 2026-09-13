"""Source evidence to a reviewable semantic building model and IFC."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.classify.layers import (WALL_CONFIDENCE_FLOOR, WALL_ROLES,
                                        LayerClassifier, layers_for_roles)
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import (detect_spaces, space_boundary_graph,
                                        room_graph_with_openings)
from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import load_pdf
from archiagent.model import BuildingModel, Issue
from archiagent.primitives import PrimitiveSet
from archiagent.scale.dimensions import extract_dimensions
from archiagent.scale.resolve import ScaleResult, candidate_runs, resolve_scale
from archiagent.validate import validate


def _assemble(ps, classification, scale, wall_height_ft, *, region=None,
              measurements=(), review=None):
    import math
    from archiagent.recognition import (recognize_symbols, exclude_symbol_geometry,
                                        host_openings, remap_openings)
    from archiagent.geometry.walls import detect_walls_filled_bodies, combine_wall_hypotheses
    from archiagent.geometry.profiles import (detect_wall_profiles, reviewed_wall_profiles,
                                              detect_exploded_hatch_profiles)
    from archiagent.geometry.spaces import detect_footprints, Space
    from archiagent.semantic import Assumption, SymbolInstance
    from archiagent.scale.verify import measurements_from_source, verify_dimensions
    from shapely.geometry import Polygon
    if not math.isfinite(wall_height_ft) or wall_height_ft <= 0:
        raise ValueError("wall height must be finite and positive")
    review = review or {}
    wall_layers = layers_for_roles(classification, WALL_ROLES,
                                   min_confidence=WALL_CONFIDENCE_FLOOR)
    if not wall_layers and not review.get("wall_profiles"):
        _no_wall_layers(classification)
    symbols = recognize_symbols(ps, scale.units_per_foot, classification)
    if review.get("templates"):
        from archiagent.classify.templates import match_templates
        records = review["templates"]
        if not isinstance(records, (list, tuple)) or any(not isinstance(t, dict) for t in records):
            raise ValueError("templates must be a list of objects")
        if any(t.get("matcher", "vector") not in ("vector", "raster") for t in records):
            raise ValueError("template matcher must be vector or raster")
        template_symbols = match_templates(ps, scale.units_per_foot,
                                           [t for t in records if t.get("matcher", "vector") == "vector"])
        raster_records = [t for t in records if t.get("matcher") == "raster"]
        if raster_records:
            from archiagent.classify.raster_templates import match_raster_templates
            template_symbols += match_raster_templates(ps, scale.units_per_foot, raster_records)
        claimed = {sid for s in template_symbols for sid in s.source_ids}
        symbols = tuple(s for s in symbols if not claimed.intersection(s.source_ids)) + template_symbols
    assumptions = [Assumption("model", "wall_height_ft", str(wall_height_ft),
                              "configured height; verify against elevations/sections")]
    if "symbols" in review:
        # Explicit instance records replace inferred hypotheses; source geometry
        # is never changed by an undocumented bbox mask.
        symbols = tuple(SymbolInstance(**{**v, "position": tuple(v["position"]),
                                           "source_ids": tuple(v.get("source_ids", ())),
                                           "boundary": tuple(tuple(p) for p in v.get("boundary", ())),
                                           "properties": tuple(tuple(p) for p in v.get("properties", ()))})
                        for v in review["symbols"])
    for s in symbols:
        if s.kind == "column" and s.height_ft is None:
            assumptions.append(Assumption(s.id, "height_ft", str(wall_height_ft),
                                          "column height provisionally follows configured wall height"))
    symbols = tuple(replace(s, height_ft=wall_height_ft) if s.kind == "column" and s.height_ft is None else s
                    for s in symbols)
    from archiagent.recognition import contextualize_openings
    # Reviewed instance records are frozen decisions, not fresh hypotheses.
    if "symbols" not in review:
        symbols = contextualize_openings(ps, symbols, scale.units_per_foot)
    wall_ps = exclude_symbol_geometry(ps, symbols)
    profile_warnings = ()
    if "wall_profiles" in review:
        # Explicit records replace hypotheses, including an explicit empty list.
        wall_profiles = reviewed_wall_profiles(review["wall_profiles"])
    else:
        wall_profiles, profile_warnings = detect_wall_profiles(wall_ps, wall_layers,
                                                               scale.units_per_foot)
        config = review.get("exploded_hatch_profiles")
        if config:
            if not isinstance(config, dict):
                raise ValueError("exploded_hatch_profiles must be a configuration object")
            additional = detect_exploded_hatch_profiles(
                wall_ps, set(config.get("boundary_layers", wall_layers)),
                set(config.get("hatch_layers", ())), scale.units_per_foot,
                min_strokes=config.get("min_strokes", 3),
                max_thickness_in=config.get("max_thickness_in", 24.0),
                max_segments=config.get("max_segments", 20000),
                max_cells=config.get("max_cells", 10000),
                max_intersections=config.get("max_intersections", 100000))
            wall_profiles = tuple(sorted((*wall_profiles, *additional), key=lambda p: p.id))
    paired = detect_walls_paired_lines(wall_ps, wall_layers, scale.units_per_foot)
    filled = detect_walls_filled_bodies(wall_ps, wall_layers, scale.units_per_foot)
    walls = combine_wall_hypotheses(paired, filled)
    # Instance exclusions replace the old global stair-spacing deletion rule.
    graph = resolve_junctions(walls, min_dangle_ft=0)
    repair_snap_in, repair_extend_in = graph.snap_in, graph.extend_in
    hosts, openings = host_openings(graph.walls, symbols, wall_height_ft)
    first_adjustments = graph.adjustments
    graph = resolve_junctions(hosts, snap_in=0, extend_in=0, min_dangle_ft=0)
    graph = replace(graph, adjustments=first_adjustments)
    proposed_openings = openings
    openings = remap_openings(openings, graph.walls)
    failed_ids = {o.id for o in proposed_openings} - {o.id for o in openings}
    if failed_ids:
        failed_sources = {sid for o in proposed_openings if o.id in failed_ids for sid in o.source_ids}
        safe_walls = tuple(w for w in graph.walls if not (w.detector == "opening-host" and failed_sources.intersection(w.source_ids)))
        graph = resolve_junctions(safe_walls, snap_in=0, extend_in=0, min_dangle_ft=0)
        graph = replace(graph, adjustments=first_adjustments)
        openings = remap_openings(openings, graph.walls)
    for o in openings:
        assumptions.append(Assumption(o.id, "opening_height_ft", str(o.height_ft),
                                      "provisional head/sill; verify opening schedule"))
    voids = tuple(tuple(tuple(p) for p in ring) for ring in review.get("voids", ()))
    boundary_graph = space_boundary_graph(graph.walls)
    spaces = detect_spaces(room_graph_with_openings(graph.walls, openings),
                           clear_boundary=True, voids=voids)
    footprints = detect_footprints(boundary_graph, voids=voids)
    footprint_verified = False
    if "footprints" in review:
        footprints = []
        for record in review["footprints"]:
            polygon = Polygon(record["boundary"], record.get("holes", ()))
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError("reviewed footprint must be a valid polygon with positive area")
            if voids:
                from shapely.ops import unary_union
                polygon = polygon.difference(unary_union([Polygon(r) for r in voids]))
            for part in getattr(polygon, "geoms", (polygon,)):
                if part.geom_type == "Polygon" and part.area > 0:
                    footprints.append(Space(tuple(part.exterior.coords), part.area,
                                            tuple(tuple(r.coords) for r in part.interiors)))
        footprints = tuple(footprints)
        footprint_verified = bool(footprints) and review.get("footprint_verified", False)
    by_id = {m.id: m for m in measurements_from_source(ps)}
    by_id.update({m.id: m for m in measurements})
    checks = verify_dimensions(tuple(by_id.values()), graph.walls, scale.units_per_foot)
    source_points = [p for primitive in ps.primitives for p in primitive.coords]
    source_bounds = region.bounds if region else (
        (min(p[0] for p in source_points), min(p[1] for p in source_points),
         max(p[0] for p in source_points), max(p[1] for p in source_points))
        if source_points else ())
    verified = [c for c in checks if c.status == "verified"]
    source_spans = {tuple(sorted((c.start,c.end))) for c in verified}
    scale_verified = len(source_spans) >= 2 and len(verified) == len(checks)
    residuals = tuple(c.error_in for c in checks if c.error_in is not None)
    scale = replace(scale, residuals_in=residuals, max_residual_in=max(residuals, default=0.0),
                    matched_count=len(verified), total_dimensions=len(checks),
                    unmatched_count=len(checks)-len(verified))
    model = BuildingModel(walls=graph.walls, junctions=graph.junctions,
                          unresolved=graph.unresolved, spaces=spaces, scale=scale,
                          layer_decisions=classification, source_path=ps.source_path,
                          source_sha256=ps.source_sha256, wall_height_ft=wall_height_ft,
                          symbols=symbols, openings=openings, footprints=footprints,
                          dimension_checks=checks, assumptions=tuple(assumptions),
                          region_id=region.id if region else "", storey_name=region.name if region else "Unassigned plan",
                          elevation_ft=region.elevation_ft if region else None,
                          scale_verified=scale_verified, footprint_verified=footprint_verified,
                          symbols_verified=review.get("symbols_verified", False),
                          source_region_bounds=source_bounds,
                          source_origin=region.origin if region else (0.0, 0.0),
                          endpoint_adjustments=graph.adjustments,
                          wall_profiles=wall_profiles,
                          repair_snap_in=repair_snap_in, repair_extend_in=repair_extend_in)
    ingest_issues = tuple(Issue("warn", w.source_id, w.code, w.message)
                          for w in (*ps.warnings, *profile_warnings))
    geometry_issues = ()
    if graph.adjustments:
        geometry_issues = (Issue("info", "junctions", "geometry_adjusted",
                                 f"{len(graph.adjustments)} endpoint adjustments; snap={repair_snap_in}in extend={repair_extend_in}in"),)
    if ps.declared_units_per_foot and not math.isclose(ps.declared_units_per_foot,scale.units_per_foot):
        ingest_issues += (Issue("warn","scale","declared_units_overridden",
                               f"header={ps.declared_units_per_foot} units/ft, using {scale.units_per_foot}; dimension checks determine acceptance"),)
    return replace(model, issues=validate(model)+ingest_issues+geometry_issues)


def extract_from_primitives(ps: PrimitiveSet, classifier: LayerClassifier,
                            wall_height_ft: float = 10.0,
                            stats: tuple[LayerStats, ...] | None = None,
                            *, units_per_foot=None, region=None, measurements=(), review=None) -> BuildingModel:
    from archiagent.scale.verify import infer_associated_scale, measurements_from_source
    classification = classifier.classify(stats if stats is not None else build_inventory(ps))
    associated = (*measurements_from_source(ps), *measurements)
    upf = units_per_foot if units_per_foot is not None else infer_associated_scale(associated)
    if upf is None:
        # Legacy numeric consensus remains a DRAFT calibration fallback only.
        layers = layers_for_roles(classification, WALL_ROLES, WALL_CONFIDENCE_FLOOR)
        scale = resolve_scale(extract_dimensions(ps), candidate_runs(ps,layers))
    else:
        scale = ScaleResult(upf,"associated",(),0.0,0)
    return _assemble(ps,classification,scale,wall_height_ft,region=region,
                     measurements=measurements,review=review)


def extract_from_dxf(ps: PrimitiveSet, classifier: LayerClassifier, *,
                     units_per_foot: float, wall_height_ft: float = 10.0,
                     region=None, measurements=(), review=None) -> BuildingModel:
    scale = ScaleResult(units_per_foot,"source-units-unverified",(),0.0,0)
    return _assemble(ps,classifier.classify(build_inventory(ps)),scale,wall_height_ft,
                     region=region,measurements=measurements,review=review)


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
