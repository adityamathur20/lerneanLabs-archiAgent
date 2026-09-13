"""Geometry and acceptance checks; uncertainty cannot masquerade as accuracy."""
from __future__ import annotations

import math
from shapely.geometry import Polygon

from archiagent.model import BuildingModel, Issue

MAX_RESIDUAL_IN = 2.0
GEOMETRY_EPS_FT = 1e-5


def validate(model: BuildingModel) -> tuple[Issue, ...]:
    issues: list[Issue] = []

    def add(severity, entity, code, message):
        issues.append(Issue(severity, entity, code, message))

    def finite(values, entity):
        if not all(math.isfinite(v) for v in values):
            add("error", entity, "nonfinite_geometry", "coordinates and dimensions must be finite")
            return False
        return True

    if not model.walls and not model.wall_profiles:
        add("error", "model", "empty_wall_model", "no walls were reconstructed")
    if not finite((model.scale.units_per_foot, model.scale.max_residual_in,
                   *model.scale.residuals_in, *model.scale.unmatched_residuals_in), "scale"):
        pass
    elif model.scale.units_per_foot <= 0:
        add("error", "scale", "invalid_scale", "units per foot must be positive")
    if not model.scale_verified:
        add("error", "scale", "scale_unverified",
            "scale lacks verified source endpoint evidence; a zero residual alone is not proof")
    if model.scale.max_residual_in > MAX_RESIDUAL_IN:
        add("error", "scale", "scale_gate_failed",
            f"max source residual {model.scale.max_residual_in}in exceeds {MAX_RESIDUAL_IN}in")
    for residual in model.scale.unmatched_residuals_in:
        add("warn", "scale", "dimension_outside_gate",
            f"source dimension unmatched by scale consensus ({residual:.1f}in)")

    if not model.dimension_checks:
        add("error", "dimensions", "model_dimensions_unverified",
            "no source dimensions have been checked against reconstructed geometry")
    for check in model.dimension_checks:
        if check.actual_ft is None or check.error_in is None:
            add("error", check.id, "dimension_unverified", check.message or "no reconstructed measurement")
            continue
        if not finite((*check.start, *check.end, check.expected_ft,
                       check.actual_ft, check.error_in), check.id):
            continue
        if check.expected_ft <= 0 or check.actual_ft <= 0:
            add("error", check.id, "invalid_dimension", "dimension lengths must be positive")
        error = abs(check.actual_ft - check.expected_ft) * 12
        if abs(error - check.error_in) > 1e-5:
            add("error", check.id, "dimension_residual_inconsistent",
                "recorded error does not match expected and actual lengths")
        if error > MAX_RESIDUAL_IN + 1e-8:
            add("error", check.id, "model_dimension_gate_failed",
                f"final model differs by {error:.3f}in; maximum is {MAX_RESIDUAL_IN}in")
        if check.status != "verified" or not any(
                marker in check.basis.lower() for marker in ("model", "reconstructed", "wall")):
            add("error", check.id, "dimension_unverified",
                "measurement must refer to reconstructed geometry, not only source endpoints")

    if not math.isfinite(model.wall_height_ft) or model.wall_height_ft <= 0:
        add("error", "model", "invalid_wall_height", "wall height must be finite and positive")
    if model.elevation_ft is None:
        add("warn", "storey", "assumed_elevation", "draft storey elevation is assumed to be 0ft")
    else:
        finite((model.elevation_ft,), "storey")
    if not any("height" in a.property.lower() for a in model.assumptions):
        add("warn", "model", "wall_height_evidence_missing",
            "wall height has no recorded source or assumption")
    for a in model.assumptions:
        add("warn", a.entity, "assumption", f"{a.property}={a.value}: {a.reason}")
    if model.footprints:
        add("warn", "floors", "assumed_slab_thickness", "IFC floor thickness assumes 0.5ft")

    for d in model.layer_decisions:
        if d.source == "default":
            add("warn", d.layer, "layer_unclassified", "layer defaulted to ignore without a classification")
    for pt in model.unresolved:
        finite(pt, "junction")
        add("error", f"node@{pt[0]:.2f},{pt[1]:.2f}", "unresolved_junction",
            "wall endpoint connects to nothing; resolve or review this free end")
    for idx, w in enumerate(model.walls):
        entity = f"W{idx:03d}"
        finite((*w.start, *w.end, w.thickness_ft, w.length_ft), entity)
        if w.thickness_ft <= 0:
            add("error", entity, "zero_thickness_wall", "wall thickness must be positive")
        if w.length_ft <= 0:
            add("error", entity, "zero_length_wall", "wall length must be positive")
        if w.thickness_source == "default":
            add("warn", entity, "default_thickness", "wall thickness is assumed")
    if model.junctions:
        add("warn", "junctions", "joint_solids_untrimmed",
            "IFC path relationships connect separate sweeps; physical overlaps are not trimmed")
    for j in model.junctions:
        finite(j.point, "junction")
        if any(not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(model.walls)
               for i in j.wall_indices):
            add("error", "junction", "invalid_junction_index", "junction refers to a missing wall")
            continue
        if len(j.wall_indices) < 2 or len(set(j.wall_indices)) != len(j.wall_indices):
            add("error", "junction", "invalid_junction", "junction must connect distinct wall indices")
        for index in j.wall_indices:
            wall = model.walls[index]
            if wall.length_ft <= 0 or not math.isfinite(wall.length_ft):
                continue
            ux = (wall.end[0] - wall.start[0]) / wall.length_ft
            uy = (wall.end[1] - wall.start[1]) / wall.length_ft
            dx, dy = j.point[0] - wall.start[0], j.point[1] - wall.start[1]
            along, offset = dx * ux + dy * uy, abs(dx * uy - dy * ux)
            if offset > GEOMETRY_EPS_FT or along < -GEOMETRY_EPS_FT or along > wall.length_ft + GEOMETRY_EPS_FT:
                add("error", "junction", "invalid_junction", "junction point must lie on every connected wall")

    if model.walls and not model.spaces:
        add("warn" if model.footprints else "error", "model", "no_spaces_detected",
            "no closed rooms were detected; floor generation uses the independent footprint")
    if model.footprints and not model.footprint_verified:
        add("error", "model", "footprint_unverified", "closed-cell footprint candidates require exterior-source review")
    if not model.footprints:
        add("error", "model", "missing_footprint", "no verified exterior contour exists for a structural floor")

    def check_polygon(boundary, holes, entity, area=None, kind="space"):
        rings = (boundary, *holes)
        if any(len(ring) < 4 for ring in rings):
            add("error", entity, "invalid_polygon", "polygon rings need at least three points plus closure")
            return
        if any(not finite((v for p in ring for v in p), entity) for ring in rings):
            return
        if any(ring[0] != ring[-1] for ring in rings):
            add("error", entity, f"unclosed_{kind}", "polygon rings must be explicitly closed")
            return
        polygon = Polygon(boundary, holes)
        if not polygon.is_valid or polygon.area <= 0:
            add("error", entity, "invalid_polygon", "polygon is degenerate or its holes intersect/exceed its exterior")
        if area is not None and (not math.isfinite(area) or
                not math.isclose(area, polygon.area, abs_tol=1e-4, rel_tol=1e-6)):
            add("error", entity, "polygon_area_mismatch", "recorded area disagrees with polygon including holes")

    for kind, spaces in (("space", model.spaces), ("footprint", model.footprints)):
        for i, sp in enumerate(spaces):
            check_polygon(sp.boundary, sp.holes, f"{kind}{i}", sp.area_sqft, kind)

    profile_ids = [p.id for p in model.wall_profiles]
    if len(set(profile_ids)) != len(profile_ids) or any(not i for i in profile_ids):
        add("error", "wall_profiles", "invalid_wall_profile", "wall profile IDs must be nonempty and unique")
    for profile in model.wall_profiles:
        check_polygon(profile.boundary, profile.holes, profile.id, kind="wall_profile")
        if profile.review_status not in {"accepted_by_rule", "accepted_after_review"}:
            add("error", profile.id, "invalid_wall_profile", "wall-face profiles require an explicit accepted interpretation")

    if model.symbols_verified is not True:
        add("error", "symbols", "symbol_interpretation_unverified",
            "symbol coverage and interpretations require source review; an empty detection set does not prove symbols are absent")
    symbol_ids = [s.id for s in model.symbols]
    if len(set(symbol_ids)) != len(symbol_ids):
        add("error", "symbols", "duplicate_symbol_id", "symbol instance IDs must be unique")
    symbols_by_id = {s.id: s for s in model.symbols}
    hosted = {op.symbol_id for op in model.openings}
    for symbol in model.symbols:
        if dict(symbol.properties).get("opening_type_ambiguous") == "true":
            add("error", symbol.id, "opening_type_ambiguous",
                "nearby sill text conflicts with the inferred door type; review source context before hosting")
        finite((*symbol.position, symbol.width_ft, symbol.depth_ft,
                symbol.rotation_rad, symbol.confidence), symbol.id)
        if symbol.boundary:
            finite((v for p in symbol.boundary for v in p), symbol.id)
        if symbol.width_ft <= 0 or symbol.depth_ft < 0 or not 0 <= symbol.confidence <= 1:
            add("error", symbol.id, "invalid_symbol", "invalid symbol footprint or confidence")
        if symbol.height_ft is not None and (
                not math.isfinite(symbol.height_ft) or symbol.height_ft <= 0):
            add("error", symbol.id, "invalid_symbol", "symbol height must be finite and positive")
        if symbol.kind in {"door", "window"} and symbol.id not in hosted:
            add("error", symbol.id, "unhosted_opening_symbol", "door/window has no resolved wall opening")
        if symbol.kind in {"column", "beam"}:
            if symbol.height_ft is None:
                add("warn", symbol.id, "symbol_height_missing", "typed structural solid omitted: height unknown")
            if not symbol.boundary and symbol.depth_ft <= 0:
                add("error", symbol.id, "invalid_symbol", "structural solid needs positive depth")
            if symbol.boundary:
                check_polygon(symbol.boundary, (), symbol.id)
            props = dict(symbol.properties)
            if symbol.kind == "beam" and "base_height_ft" not in props:
                add("warn", symbol.id, "beam_elevation_missing", "beam solid omitted: base height unknown")
            if "base_height_ft" in props:
                try:
                    base = float(props["base_height_ft"])
                    if not math.isfinite(base):
                        raise ValueError
                except (TypeError, ValueError):
                    add("error", symbol.id, "invalid_symbol", "base height must be a finite number")
        if symbol.evidence == "inferred" or symbol.confidence < 0.8:
            add("warn", symbol.id, "symbol_requires_review", "symbol interpretation remains uncertain")

    opening_ids = [op.id for op in model.openings]
    if len(set(opening_ids)) != len(opening_ids):
        add("error", "openings", "invalid_opening", "opening IDs must be unique")
    filled_ids = [op.symbol_id for op in model.openings if op.symbol_id]
    if len(set(filled_ids)) != len(filled_ids):
        add("error", "openings", "invalid_opening", "one symbol instance cannot fill multiple openings")
    intervals = {}
    for op in model.openings:
        if not finite((*op.start, *op.end, op.height_ft, op.sill_ft), op.id):
            continue
        if op.kind not in {"door", "window", "opening"}:
            add("error", op.id, "invalid_opening", "opening type must be door, window or opening")
        symbol = symbols_by_id.get(op.symbol_id)
        if symbol is not None and op.kind != symbol.kind:
            add("error", op.id, "invalid_opening", "opening kind disagrees with its symbol instance")
        if op.width_ft <= 0 or op.height_ft <= 0 or op.sill_ft < 0:
            add("error", op.id, "invalid_opening", "opening needs positive width/height and nonnegative sill")
        if (not isinstance(op.host_wall_index, int) or isinstance(op.host_wall_index, bool)
                or op.host_wall_index < 0 or op.host_wall_index >= len(model.walls)):
            add("error", op.id, "invalid_opening_host", "opening refers to a missing host wall")
            continue
        if op.symbol_id and op.symbol_id not in symbol_ids:
            add("error", op.id, "invalid_opening", "opening refers to a missing symbol instance")
        host = model.walls[op.host_wall_index]
        if host.length_ft <= 0 or not math.isfinite(host.length_ft):
            continue
        ux = (host.end[0] - host.start[0]) / host.length_ft
        uy = (host.end[1] - host.start[1]) / host.length_ft
        positions = []
        for p in (op.start, op.end):
            dx, dy = p[0] - host.start[0], p[1] - host.start[1]
            along, offset = dx * ux + dy * uy, abs(dx * uy - dy * ux)
            positions.append(along)
            if (offset > GEOMETRY_EPS_FT or along < -GEOMETRY_EPS_FT or
                    along > host.length_ft + GEOMETRY_EPS_FT):
                add("error", op.id, "opening_outside_host", "opening endpoints must lie on the host axis within its span")
        if op.sill_ft + op.height_ft > model.wall_height_ft + GEOMETRY_EPS_FT:
            add("error", op.id, "opening_outside_host", "opening extends above host wall")
        lo, hi = sorted(positions)
        for a, b, sill, top in intervals.setdefault(op.host_wall_index, []):
            if min(hi, b) > max(lo, a) + GEOMETRY_EPS_FT and min(
                    op.sill_ft + op.height_ft, top) > max(op.sill_ft, sill) + GEOMETRY_EPS_FT:
                add("error", op.id, "overlapping_openings", "two voids overlap in the same host wall")
        intervals[op.host_wall_index].append((lo, hi, op.sill_ft, op.sill_ft + op.height_ft))
        if op.assumed_height:
            add("warn", op.id, "assumed_opening_height", "opening height or sill uses assumed vertical dimensions")

    return tuple(issues)
