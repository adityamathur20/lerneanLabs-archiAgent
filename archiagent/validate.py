"""Stage 7 validators.

Nothing is silently corrected. Every problem becomes an Issue naming the
entity, so a human or the correction loop can act on it.
"""

from __future__ import annotations

from archiagent.model import BuildingModel, Issue

# Inclusive boundary: a residual of exactly 2.0in passes. This matches
# resolve_scale, which admits a dimension to its supporting set with
# `err <= max_residual_in` -- so the resolver can legitimately return exactly
# 2.0 and the validator must not then contradict it.
MAX_RESIDUAL_IN = 2.0


def validate(model: BuildingModel) -> tuple[Issue, ...]:
    issues: list[Issue] = []

    if model.scale.max_residual_in > MAX_RESIDUAL_IN:
        issues.append(Issue(
            "error", "scale", "scale_gate_failed",
            f"max residual {model.scale.max_residual_in}in exceeds "
            f"the {MAX_RESIDUAL_IN}in gate (R1)"))

    for pt in model.unresolved:
        issues.append(Issue(
            "error", f"node@{pt[0]:.2f},{pt[1]:.2f}", "unresolved_junction",
            "wall endpoint connects to nothing; rooms downstream may leak"))

    for idx, w in enumerate(model.walls):
        if w.thickness_ft <= 0.0:
            issues.append(Issue(
                "error", f"W{idx:03d}", "zero_thickness_wall",
                "wall has zero or negative thickness"))
        if w.length_ft <= 0.0:
            issues.append(Issue(
                "error", f"W{idx:03d}", "zero_length_wall",
                "wall has zero length"))
        if w.thickness_source == "default":
            issues.append(Issue(
                "warn", f"W{idx:03d}", "default_thickness",
                "thickness could not be measured; a default was applied"))

    if model.walls and not model.spaces:
        issues.append(Issue(
            "error", "model", "no_spaces_detected",
            "walls exist but no closed room was found; check junctions"))

    for i, space in enumerate(model.spaces):
        if space.boundary and space.boundary[0] != space.boundary[-1]:
            issues.append(Issue(
                "error", f"S{i:03d}", "unclosed_space",
                "space boundary is not a closed ring"))

    return tuple(issues)
