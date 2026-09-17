"""Small authored-from-scratch models for wall join checks. No client data."""
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.walls import WallSeg
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult


def model_with(segments, wall_height_ft=10., **kwargs):
    """segments: [(start, end, thickness_ft), ...] in feet, already noded.

    Junction wall indices address `resolve_junctions`' canonical wall order, so
    the model keeps that order rather than the order segments were written in.
    """
    walls = tuple(WallSeg(a, b, t, "walls", "paired-line", "measured", (f"cad:{i}",))
                  for i, (a, b, t) in enumerate(segments))
    graph = resolve_junctions(walls, snap_in=0, extend_in=0, min_dangle_ft=0)
    assert len(graph.walls) == len(walls), "fixture segments must already be noded"
    values = dict(walls=graph.walls, junctions=graph.junctions, unresolved=graph.unresolved,
                  spaces=(), scale=ScaleResult(12., "clear", (), 0., 0), layer_decisions=(),
                  source_path="fixture.dxf", source_sha256="b" * 64,
                  wall_height_ft=wall_height_ft, region_id="plan-a", storey_name="Level 1",
                  elevation_ft=0., scale_verified=True, footprint_verified=True,
                  symbols_verified=True)
    values.update(kwargs)
    return BuildingModel(**values)


def spans(layout):
    """Each run as (start, end, thickness), sorted, for order-independent asserts."""
    return sorted((r.start, r.end, r.thickness_ft) for r in layout.runs)


def footprint(wall):
    """World-coordinate XY outline of a wall's Body, in feet."""
    import ifcopenshell.util.placement as placement
    import numpy
    from shapely.geometry import Polygon

    matrix = placement.get_local_placement(wall.ObjectPlacement)
    body = next(r for r in wall.Representation.Representations
                if r.RepresentationIdentifier == "Body")
    item = body.Items[0]
    while item.is_a("IfcBooleanResult"):
        item = item.FirstOperand
    profile = item.SweptArea
    if profile.is_a("IfcRectangleProfileDef"):
        x, y = profile.Position.Location.Coordinates if profile.Position else (0., 0.)
        points = [(x-profile.XDim/2, y-profile.YDim/2), (x+profile.XDim/2, y-profile.YDim/2),
                  (x+profile.XDim/2, y+profile.YDim/2), (x-profile.XDim/2, y+profile.YDim/2)]
    else:
        curve = profile.OuterCurve
        points = (list(curve.Points.CoordList) if curve.is_a("IfcIndexedPolyCurve")
                  else [p.Coordinates for p in curve.Points])
    world = [(matrix @ numpy.array([p[0], p[1], 0., 1.]))[:2] / .3048 for p in points]
    return Polygon(world)
