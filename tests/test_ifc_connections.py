import ifcopenshell
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.ifc.author import author_ifc
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _model(walls):
    graph = resolve_junctions(walls)
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=ScaleResult(11.861, "clear", (0.2,), 0.2, 3),
        layer_roles={"WALLS": "wall_structural"},
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def test_every_junction_emits_a_connection_relationship(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    rels = f.by_type("IfcRelConnectsPathElements")
    assert len(rels) == 4


def test_connection_types_are_at_start_or_at_end_for_corners(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    for rel in f.by_type("IfcRelConnectsPathElements"):
        assert rel.RelatingConnectionType in ("ATSTART", "ATEND", "ATPATH")
        assert rel.RelatedConnectionType in ("ATSTART", "ATEND", "ATPATH")


def test_t_junction_uses_atpath_on_the_through_wall(tmp_path):
    walls = _ring(10.0) + [_w((5.0, 0.0), (5.0, 10.0))]
    f = ifcopenshell.open(author_ifc(_model(walls), tmp_path / "m.ifc"))
    types = {rel.RelatingConnectionType for rel in
             f.by_type("IfcRelConnectsPathElements")}
    types |= {rel.RelatedConnectionType for rel in
              f.by_type("IfcRelConnectsPathElements")}
    assert types <= {"ATSTART", "ATEND", "ATPATH"}


def test_connections_reference_distinct_walls(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    for rel in f.by_type("IfcRelConnectsPathElements"):
        assert rel.RelatingElement != rel.RelatedElement
