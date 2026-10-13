"""A floor's own drawing: everything outside its box is removed, nothing else is touched."""
import ezdxf
import pytest

from archiagent.floor_cut import cut_floor
from archiagent.floors import Floor

GROUND = Floor("floor-01", "Ground floor", (-5.0, -5.0, 65.0, 45.0), 0.0, "assumed")
FIRST = Floor("floor-02", "First floor", (95.0, -5.0, 165.0, 45.0), 10.0, "assumed")
FLOORS = (GROUND, FIRST)


@pytest.fixture
def sheet(tmp_path):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 2          # feet
    doc.layers.add("WALL")
    doc.layers.add("DOOR")
    block = doc.blocks.new("DOOR_BLOCK")
    block.add_line((0, 0), (3, 0))
    msp = doc.modelspace()
    msp.add_line((0, 0), (60, 0), dxfattribs={"layer": "WALL"})               # ground
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 40), (0, 40)], dxfattribs={"layer": "WALL"})
    msp.add_blockref("DOOR_BLOCK", (10, 0), dxfattribs={"layer": "DOOR"})     # ground
    msp.add_line((100, 0), (160, 0), dxfattribs={"layer": "WALL"})            # first
    msp.add_blockref("DOOR_BLOCK", (110, 0), dxfattribs={"layer": "DOOR"})    # first
    msp.add_line((-10, 20), (170, 20), dxfattribs={"layer": "WALL"})          # spans both plans
    msp.add_text("TITLE BLOCK", dxfattribs={"insert": (500, 500), "height": 2})   # outside every floor
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    return path


def counts(path):
    msp = ezdxf.readfile(str(path)).modelspace()
    return {kind: len(msp.query(kind)) for kind in ("LINE", "LWPOLYLINE", "INSERT", "TEXT")}


def test_a_floor_keeps_only_its_own_entities(sheet, tmp_path):
    report = cut_floor(sheet, GROUND, FLOORS, tmp_path / "g.dxf")
    assert counts(tmp_path / "g.dxf") == {"LINE": 1, "LWPOLYLINE": 1, "INSERT": 1, "TEXT": 0}
    assert report.kept == 3


def test_an_entity_spanning_two_floors_belongs_to_neither_and_is_reported(sheet, tmp_path):
    first = cut_floor(sheet, FIRST, FLOORS, tmp_path / "f.dxf")
    assert counts(tmp_path / "f.dxf")["LINE"] == 1
    assert first.dropped_spanning == 1
    assert first.dropped_outside == 1            # the title block


def test_layers_blocks_and_units_are_carried_exactly(sheet, tmp_path):
    cut_floor(sheet, GROUND, FLOORS, tmp_path / "g.dxf")
    doc = ezdxf.readfile(str(tmp_path / "g.dxf"))
    assert doc.header["$INSUNITS"] == 2
    assert {"WALL", "DOOR"} <= {layer.dxf.name for layer in doc.layers}
    assert "DOOR_BLOCK" in doc.blocks


def test_the_cut_drawing_loads_in_the_normal_reader(sheet, tmp_path):
    from archiagent.ingest.dxf_vector import load_dxf
    cut_floor(sheet, GROUND, FLOORS, tmp_path / "g.dxf")
    ps, _ = load_dxf(tmp_path / "g.dxf", 1.0)
    assert len(ps.primitives) >= 2


def test_the_source_file_is_never_modified(sheet, tmp_path):
    before = sheet.read_bytes()
    cut_floor(sheet, GROUND, FLOORS, tmp_path / "g.dxf")
    assert sheet.read_bytes() == before


def test_the_floor_must_be_one_of_the_floors(sheet, tmp_path):
    stranger = Floor("floor-99", "Elsewhere", (0, 0, 1, 1), 0.0, "assumed")
    with pytest.raises(ValueError, match="floor-99"):
        cut_floor(sheet, stranger, FLOORS, tmp_path / "x.dxf")
