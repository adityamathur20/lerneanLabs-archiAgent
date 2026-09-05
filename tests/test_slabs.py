"""Every detected room gets a floor.

The authored model used to contain IfcWall and IfcSpace and nothing else, so
a model loaded into Blender had no floor to stand on.
"""
import ifcopenshell

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import SLAB_THICKNESS_FT, author_ifc
from archiagent.ingest.dxf_vector import load_dxf
from archiagent.pipeline import extract_from_dxf


def _room_dxf(tmp_path):
    """Four walls enclosing one room, faces 8in apart at 12 units/ft."""
    import ezdxf
    doc = ezdxf.new(); doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for y in (0, 8, 240, 248):
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "W"})
    for x in (0, 8, 240, 248):
        msp.add_line((x, 0), (x, 248), dxfattribs={"layer": "W"})
    p = tmp_path / "room.dxf"; doc.saveas(p)
    return p


def _model(tmp_path):
    ps, upf = load_dxf(_room_dxf(tmp_path))
    return extract_from_dxf(ps, StubClassifier({"W": (Role.WALL_STRUCTURAL, 1.0)}),
                            units_per_foot=upf)


def test_one_slab_per_room(tmp_path):
    model = _model(tmp_path)
    out = author_ifc(model, tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    assert len(f.by_type("IfcSlab")) == len(model.spaces)
    assert len(f.by_type("IfcSlab")) > 0, "fixture produced no rooms to floor"


def test_slabs_are_floors_not_roofs(tmp_path):
    out = author_ifc(_model(tmp_path), tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    assert {s.PredefinedType for s in f.by_type("IfcSlab")} == {"FLOOR"}


def test_a_slab_carries_solid_geometry(tmp_path):
    out = author_ifc(_model(tmp_path), tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    for slab in f.by_type("IfcSlab"):
        item = slab.Representation.Representations[0].Items[0]
        assert item.is_a() == "IfcExtrudedAreaSolid"
        assert item.Depth > 0


def test_the_slab_hangs_below_storey_level(tmp_path):
    """Extruded downward, so it does not swallow the walls standing on it."""
    out = author_ifc(_model(tmp_path), tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    item = f.by_type("IfcSlab")[0].Representation.Representations[0].Items[0]
    assert item.ExtrudedDirection.DirectionRatios[2] == -1.0


def test_a_model_with_no_rooms_authors_no_slabs(tmp_path):
    """Two parallel walls enclose nothing. No room, no floor -- and no crash."""
    import ezdxf
    doc = ezdxf.new(); doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for y in (0, 8):
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "W"})
    p = tmp_path / "open.dxf"; doc.saveas(p)
    ps, upf = load_dxf(p)
    model = extract_from_dxf(ps, StubClassifier({"W": (Role.WALL_STRUCTURAL, 1.0)}),
                             units_per_foot=upf)
    out = author_ifc(model, tmp_path / "m.ifc")
    assert model.spaces == ()
    assert ifcopenshell.open(out).by_type("IfcSlab") == []
