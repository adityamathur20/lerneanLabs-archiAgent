import math

import ifcopenshell
import pytest

from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.ifc.author import author_ifc
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult

FT = 0.3048


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


@pytest.fixture
def model():
    graph = resolve_junctions(_ring())
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=ScaleResult(11.861, "clear", (0.2,), 0.2, 3),
        layer_decisions=(LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.95,
                                       "", "manual"),),
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def test_writes_a_valid_ifc4_file(model, tmp_path):
    out = author_ifc(model, tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcWall")) == 4
    assert len(f.by_type("IfcBuildingStorey")) == 1


def test_walls_are_parametric_extrusions_not_meshes(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    for wall in f.by_type("IfcWall"):
        item = wall.Representation.Representations[0].Items[0]
        assert item.is_a() == "IfcExtrudedAreaSolid"


def test_wall_height_matches_the_model(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    depth = f.by_type("IfcWall")[0].Representation.Representations[0].Items[0].Depth
    assert depth == pytest.approx(10.0 * FT)


def test_wall_is_placed_at_its_start_point_not_its_midpoint(model, tmp_path):
    """Regression for the ShapeBuilder corner-anchor trap that scattered
    49 walls in the spike. The first ring wall runs (0,0)->(10,0)."""
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    origins = {tuple(round(c, 4) for c in w.ObjectPlacement
                     .RelativePlacement.Location.Coordinates)
               for w in f.by_type("IfcWall")}
    assert (0.0, 0.0, 0.0) in origins


def test_wall_profile_is_centred_on_its_axis(model, tmp_path):
    """Regression for the corner-anchor trap. `ShapeBuilder.rectangle` anchors the
    profile at its CORNER, so without `position=(0, -t/2)` the wall's thickness
    sits wholly on one side of its centerline. Asserting the placement origin
    cannot catch that — the wall still starts in the right place, it is just the
    wrong shape about its axis."""
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    solid = f.by_type("IfcWall")[0].Representation.Representations[0].Items[0]
    ys = [p[1] for p in solid.SweptArea.OuterCurve.Points.CoordList]
    half = (4 / 12) * FT / 2.0
    assert min(ys) == pytest.approx(-half)
    assert max(ys) == pytest.approx(+half)


def test_every_wall_carries_its_own_provenance_pset(model, tmp_path):
    """Provenance must be traceable per wall: a wrong wall has to lead back to the
    layer and detector that produced it. File-wide set membership cannot show that
    each wall got its own pset."""
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    walls = f.by_type("IfcWall")
    assert len(walls) == 4
    expected = {"SourceLayer", "Detector", "ThicknessSource",
                "ThicknessIn", "ScaleUnitsPerFoot", "ScaleMaxResidualIn"}
    for wall in walls:
        psets = [r.RelatingPropertyDefinition
                 for r in f.by_type("IfcRelDefinesByProperties")
                 if wall in r.RelatedObjects]
        provenance = [p for p in psets if p.Name == "ArchiAgent_Provenance"]
        assert len(provenance) == 1
        assert {pr.Name for pr in provenance[0].HasProperties} == expected


def test_spaces_have_geometry(model, tmp_path):
    """Regression: IfcSpace was authored with no Representation at all, so it
    was invisible when the model was loaded in Blender. It must carry a real
    extruded solid built from Space.boundary."""
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    spaces = f.by_type("IfcSpace")
    assert spaces
    for sp in spaces:
        assert sp.Representation is not None
        item = sp.Representation.Representations[0].Items[0]
        assert item.is_a() == "IfcExtrudedAreaSolid"


def test_spaces_are_emitted_and_aggregated_to_the_storey(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    spaces = f.by_type("IfcSpace")
    assert len(spaces) == 1
    parents = {rel.RelatingObject.is_a()
               for rel in f.by_type("IfcRelAggregates")
               if spaces[0] in rel.RelatedObjects}
    assert "IfcBuildingStorey" in parents


def test_provenance_property_set_round_trips(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    values = {p.NominalValue.wrappedValue for p in f.by_type("IfcPropertySingleValue")}
    assert "WALLS" in values
    assert "paired-line" in values
    assert "measured" in values
