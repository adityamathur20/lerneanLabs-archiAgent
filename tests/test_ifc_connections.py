import ifcopenshell

from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
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
        layer_decisions=(LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.95,
                                       "", "manual"),),
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


def test_connection_types_are_pinned_for_ring_corners(tmp_path):
    """The vacuous version of this test asserted membership in
    `_connection_type`'s complete codomain, so forcing every connection type
    to a constant "ATSTART" still passed it. Pin the actual pairs instead.

    Each ring wall runs from its lower-indexed corner to the next
    (`_ring()` walks bottom -> right -> top -> left), so at three of the
    four corners the earlier-indexed wall's END meets the later-indexed
    wall's START. The fourth corner, (0,0), closes the loop: wall 0 STARTS
    there and wall 3 (the last wall) ENDS there. Every corner is an L
    junction sitting exactly at both walls' endpoints, so ATPATH can never
    appear here.
    """
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    pairs = [(rel.RelatingConnectionType, rel.RelatedConnectionType)
             for rel in f.by_type("IfcRelConnectsPathElements")]
    assert set(pairs) == {("ATSTART", "ATEND"), ("ATEND", "ATSTART")}
    assert pairs.count(("ATSTART", "ATEND")) == 1
    assert pairs.count(("ATEND", "ATSTART")) == 3


def test_t_junction_connections_are_endpoint_typed(tmp_path):
    """ATPATH is unreachable here by construction, not by coincidence:
    `split_through_walls` converts every through-wall junction into a real
    graph node before `resolve_junctions` ever classifies it, so the stem of
    a T always lands exactly on an endpoint of both walls it touches and
    `_connection_type` finds distance 0 (ATSTART/ATEND) every time. This test
    pins that reality rather than pretending ATPATH is reachable -- do not
    contort the code to make ATPATH fire.
    """
    walls = _ring(10.0) + [_w((5.0, 0.0), (5.0, 10.0))]
    f = ifcopenshell.open(author_ifc(_model(walls), tmp_path / "m.ifc"))
    types = {rel.RelatingConnectionType for rel in
             f.by_type("IfcRelConnectsPathElements")}
    types |= {rel.RelatedConnectionType for rel in
              f.by_type("IfcRelConnectsPathElements")}
    assert types == {"ATSTART", "ATEND"}


def test_connections_reference_distinct_walls(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    for rel in f.by_type("IfcRelConnectsPathElements"):
        assert rel.RelatingElement != rel.RelatedElement
