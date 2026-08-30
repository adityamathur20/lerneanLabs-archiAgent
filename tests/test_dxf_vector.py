import ezdxf
import pytest
from archiagent.ingest.dxf_vector import load_dxf, units_from_header


def _doc(insunits=1):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_line((0, 0), (120, 0), dxfattribs={"layer": "WALL"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "FURN"})
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5)],
                       close=True, dxfattribs={"layer": "COL"})
    msp.add_text("KITCHEN", dxfattribs={"layer": "TEXT"}).set_placement((3, 3))
    return doc


def test_lines_and_polylines_become_primitives(tmp_path):
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, upf = load_dxf(p)
    kinds = {(pr.layer, pr.kind) for pr in ps.primitives}
    assert ("WALL", "line") in kinds
    assert ("FURN", "line") in kinds
    assert ("COL", "rect") in kinds        # closed polyline -> rect, so it closes
    assert upf == 12.0                     # INSUNITS 1 = inches


def test_live_text_is_carried_through(tmp_path):
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, _ = load_dxf(p)
    assert any(t.text == "KITCHEN" and t.layer == "TEXT" for t in ps.texts)


def test_coordinates_are_plain_floats(tmp_path):
    """ezdxf yields numpy floats; ifcopenshell rejects them downstream."""
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, _ = load_dxf(p)
    for pr in ps.primitives:
        for x, y in pr.coords:
            assert type(x) is float and type(y) is float


def test_missing_units_is_a_clear_error(tmp_path):
    from archiagent.ingest.dxf_vector import DxfUnitsError
    p = tmp_path / "t.dxf"
    _doc(insunits=0).saveas(p)
    with pytest.raises(DxfUnitsError, match="units-per-foot"):
        load_dxf(p)


def test_explicit_units_override_the_header(tmp_path):
    p = tmp_path / "t.dxf"
    _doc(insunits=2).saveas(p)          # header claims feet
    _, upf = load_dxf(p, units_per_foot=12.0)
    assert upf == 12.0                   # caller wins
