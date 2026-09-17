"""Conservative mapping of indexed wall hosts onto accepted wall-face profiles."""
from __future__ import annotations

from shapely.geometry import LineString, Polygon

PROFILE_TOL_FT = 1e-7


def wall_layout(model):
    profiles = tuple(getattr(model, "wall_profiles", ()))
    polygons = [Polygon(p.boundary, p.holes) for p in profiles]
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError("duplicate wall profile IDs")
    for i, poly in enumerate(polygons):
        if not poly.is_valid or poly.is_empty or poly.area <= 0:
            raise ValueError(f"invalid wall profile {profiles[i].id}")
        for other in polygons[:i]:
            if poly.intersection(other).area > PROFILE_TOL_FT**2:
                raise ValueError("accepted wall profiles overlap; resolve profiles before IFC export")
    mapping = {}
    for index, wall in enumerate(model.walls):
        footprint = LineString((wall.start, wall.end)).buffer(wall.thickness_ft/2, cap_style=2)
        matches = [i for i, poly in enumerate(polygons) if poly.buffer(PROFILE_TOL_FT).covers(footprint)]
        if len(matches) == 1:
            mapping[index] = matches[0]
        elif any(footprint.intersection(poly).area > PROFILE_TOL_FT**2 for poly in polygons):
            raise ValueError(f"wall {index} partially overlaps accepted wall profiles; review host mapping")
    return profiles, polygons, mapping


def host_names(model):
    """Every wall index mapped to its authored IFC wall name, in one pass.

    Resolving names one at a time rebuilds the profile layout and the run
    layout per call, which is quadratic in a drawing's walls once a caller
    loops over openings.
    """
    from archiagent.ifc.wall_runs import build_runs

    profiles, _, mapping = wall_layout(model)
    layout = build_runs(model)
    names = {index: f"WP:{profiles[profile].id}" for index, profile in mapping.items()}
    for index, run in layout.run_of_wall.items():
        names.setdefault(index, layout.runs[run].id)
    return names


def host_name(model, wall_index):
    profiles, _, mapping = wall_layout(model)
    if wall_index in mapping:
        return f"WP:{profiles[mapping[wall_index]].id}"
    from archiagent.ifc.wall_runs import build_runs
    layout = build_runs(model)
    return layout.runs[layout.run_of_wall[wall_index]].id


def opening_footprint(model, opening):
    host = model.walls[opening.host_wall_index]
    return LineString((opening.start, opening.end)).buffer((host.thickness_ft+.02)/2, cap_style=2)


def profile_material_slices(model, profile_index, polygons, mapping):
    """Exact horizontal slices, including overlapping opening void unions."""
    openings = [op for op in model.openings if mapping.get(op.host_wall_index) == profile_index]
    levels = sorted({0.0, model.wall_height_ft,
                     *(v for op in openings for v in (op.sill_ft, op.sill_ft+op.height_ft))})
    pieces = []
    for lower, upper in zip(levels, levels[1:]):
        polygon = polygons[profile_index]
        middle = (lower+upper)/2
        for opening in openings:
            if opening.sill_ft < middle < opening.sill_ft+opening.height_ft:
                polygon = polygon.difference(opening_footprint(model, opening))
        if not polygon.is_empty and upper > lower:
            pieces.append((lower, upper, polygon))
    if not pieces:
        raise ValueError("wall profile has no material after opening subtraction")
    return pieces
