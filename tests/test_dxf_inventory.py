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


def test_empty_layer_appears_in_output(tmp_path):
    """A layer in the DXF layer table with no entities should still appear
    in the output, carrying its table attributes."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL", lineweight=35)
    doc.layers.add("EMPTY", lineweight=30)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 100), dxfattribs={"layer": "WALL"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)

    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}

    # EMPTY layer should appear even though it has no entities
    assert "EMPTY" in stats
    assert stats["EMPTY"].lineweight == 30
    assert stats["EMPTY"].entity_share == 0.0


def test_arc_only_layer_gets_extent_ratio(tmp_path):
    """ARC-only layers should have a computed extent_ratio from their entities,
    not forced to 0.0 just because PrimitiveSet drops ARCs."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    doc.layers.add("ARCS")
    msp = doc.modelspace()
    # Add an ARC to the drawing
    msp.add_arc((50, 50), 20, 0, 90, dxfattribs={"layer": "ARCS"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)

    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}

    # ARC layer should have extent_ratio > 0, not 0.0
    assert stats["ARCS"].extent_ratio > 0.0
    assert "ARC" in dict(stats["ARCS"].entity_mix)


def test_arc_extent_ratio_scales_with_size(tmp_path):
    """Smaller ARC layers in large drawings should have smaller extent_ratio
    than the same arc in a small drawing."""
    doc1 = ezdxf.new()
    doc1.header["$INSUNITS"] = 1
    doc1.layers.add("ARCS")
    doc1.layers.add("REF")  # Reference layer to define drawing bounds
    msp1 = doc1.modelspace()
    # Large drawing bounds (100x100) with small arc
    msp1.add_line((0, 0), (100, 100), dxfattribs={"layer": "REF"})
    msp1.add_arc((50, 50), 5, 0, 90, dxfattribs={"layer": "ARCS"})
    p1 = tmp_path / "large_drawing.dxf"
    doc1.saveas(p1)

    doc2 = ezdxf.new()
    doc2.header["$INSUNITS"] = 1
    doc2.layers.add("ARCS")
    doc2.layers.add("REF")
    msp2 = doc2.modelspace()
    # Small drawing bounds (20x20) with same size arc
    msp2.add_line((0, 0), (20, 20), dxfattribs={"layer": "REF"})
    msp2.add_arc((10, 10), 5, 0, 90, dxfattribs={"layer": "ARCS"})
    p2 = tmp_path / "small_drawing.dxf"
    doc2.saveas(p2)

    ps1, _ = load_dxf(p1)
    ps2, _ = load_dxf(p2)
    stats1 = {s.name: s for s in build_dxf_inventory(p1, ps1)}
    stats2 = {s.name: s for s in build_dxf_inventory(p2, ps2)}

    # Both should have extent_ratio > 0
    assert stats1["ARCS"].extent_ratio > 0.0
    assert stats2["ARCS"].extent_ratio > 0.0
    # Arc in large drawing should have smaller ratio than in small drawing
    assert stats1["ARCS"].extent_ratio < stats2["ARCS"].extent_ratio
