import ezdxf
from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.ingest.dxf_vector import load_dxf


def _doc(tmp_path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL", color=7, lineweight=35)
    doc.layers.add("FURN", color=3, lineweight=9)
    doc.layers.add("GRID", color=8, lineweight=5)
    msp = doc.modelspace()
    for i in range(10):
        msp.add_line((0, i), (100, i), dxfattribs={"layer": "WALL"})
    msp.add_arc((5, 5), 2, 0, 90, dxfattribs={"layer": "FURN"})
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "GRID"})
    doc.layers.get("GRID").freeze()
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def test_entity_mix_and_share(tmp_path):
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert dict(stats["WALL"].entity_mix)["LINE"] == 10
    assert dict(stats["FURN"].entity_mix)["ARC"] == 1
    assert stats["WALL"].entity_share > stats["FURN"].entity_share
    assert abs(sum(s.entity_share for s in stats.values()) - 1.0) < 1e-6


def test_layer_table_attributes_are_carried(tmp_path):
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert stats["WALL"].lineweight == 35
    assert stats["FURN"].lineweight == 9
    assert stats["GRID"].is_frozen is True
    assert stats["WALL"].is_frozen is False


def test_arc_only_layer_is_visible_in_the_mix(tmp_path):
    """ARC-heavy layers are doors. The classifier cannot see that unless the
    entity mix records entity types the PrimitiveSet drops."""
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert "ARC" in dict(stats["FURN"].entity_mix)
