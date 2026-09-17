"""Generated evidence fixtures: provenance, transforms, curves and dimensions."""
import math

import pytest

ez = pytest.importorskip("ezdxf")
from archiagent.ingest.dxf_vector import DxfUnitsError, load_dxf
from archiagent.primitives import Primitive


def save(doc, tmp_path):
    path = tmp_path / "evidence.dxf"
    doc.saveas(path)
    return path


def test_closed_path_does_not_duplicate_closing_segment():
    p = Primitive("rect", ((0, 0), (1, 0), (1, 1), (0, 0)), "", None, None)
    assert len(p.segments()) == 3
    assert all(a != b for a, b in p.segments())


def test_blocks_curves_hatches_dimensions_survive(tmp_path):
    doc = ez.new()
    doc.units = 1
    block = doc.blocks.new("DOOR_36")
    block.add_line((0, 0), (36, 0))
    block.add_arc((0, 0), 36, 0, 90)
    block.add_attdef("TYPE", (0, 0))
    insert = doc.modelspace().add_blockref("DOOR_36", (100, 200),
        dxfattribs={"layer": "A-DOOR", "rotation": 90})
    insert.add_auto_attribs({"TYPE": "D1"})
    doc.modelspace().add_lwpolyline([(0, 0, 1), (10, 0, 0)], format="xyb")
    doc.modelspace().add_circle((150, 150), 5)
    hatch = doc.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (100, 0), (100, 100), (0, 100)], is_closed=True)
    hatch.paths.add_polyline_path([(10, 10), (20, 10), (20, 20), (10, 20)], is_closed=True)
    doc.modelspace().add_linear_dim(base=(0, 120), p1=(0, 0), p2=(100, 0),
                                   text="8'-4\"").render()
    doc.modelspace().add_point((1, 2))
    ps, upf = load_dxf(save(doc, tmp_path))
    assert upf == ps.declared_units_per_foot == 12
    door = next(e for e in ps.entities if e.kind == "INSERT")
    assert door.block_name == "DOOR_36"
    assert ("TYPE", "D1") in door.attributes
    assert len(door.coords) == 4
    arc = next(e for e in ps.entities if e.kind == "ARC")
    assert arc.layer == "A-DOOR" and arc.parent_id == door.id
    assert arc.center == pytest.approx((100, 200))
    assert arc.radius == 36
    assert len(arc.coords) > 5
    circle = next(e for e in ps.entities if e.kind == "CIRCLE")
    assert circle.start_angle == 0 and circle.end_angle == 360
    assert circle.closed and circle.radius == 5
    arc_primitive = next(p for p in ps.primitives if p.source_id == arc.id)
    assert arc_primitive.coords[0] == pytest.approx((100, 236))
    assert any(p.kind == "curve" and p.entity_type == "LWPOLYLINE" for p in ps.primitives)
    boundary = next(e for e in ps.entities if e.kind == "HATCH_BOUNDARY" and e.holes)
    assert len(boundary.holes) == 1
    assert len(ps.dimensions) == 1
    dimension = ps.dimensions[0]
    assert math.dist(dimension.start, dimension.end) == pytest.approx(100)
    assert dimension.start == pytest.approx((0, 0))
    assert dimension.end == pytest.approx((100, 0))
    assert dimension.measurement_axis == pytest.approx((1, 0))
    assert dimension.text == "8'-4\""
    assert any(w.code == "unsupported_entity" and "POINT" in w.message for w in ps.warnings)
    assert not any(w.code == "entity_conversion_failed" for w in ps.warnings)
    again, _ = load_dxf(tmp_path / "evidence.dxf")
    assert [e.id for e in again.entities] == [e.id for e in ps.entities]


@pytest.mark.parametrize("units", [0, -1, math.nan, math.inf])
def test_invalid_override_is_not_replaced_by_header(tmp_path, units):
    doc = ez.new()
    doc.units = 1
    with pytest.raises(DxfUnitsError):
        load_dxf(save(doc, tmp_path), units)


def test_nested_block_transforms_and_layer_zero(tmp_path):
    doc = ez.new()
    doc.units = 2
    inner = doc.blocks.new("INNER")
    inner.add_line((0, 0), (2, 0))
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (5, 0))
    doc.modelspace().add_blockref("OUTER", (10, 20),
        dxfattribs={"layer": "A-WALL", "rotation": 90, "xscale": 2, "yscale": 2})
    ps, _ = load_dxf(save(doc, tmp_path))
    line = next(p for p in ps.primitives if p.entity_type == "LINE")
    assert line.layer == "A-WALL"
    assert line.coords[0] == pytest.approx((10, 30))
    assert line.coords[-1] == pytest.approx((10, 34))
    assert not ps.warnings


def test_rotated_dimension_keeps_witness_points_and_projected_span(tmp_path):
    doc = ez.new()
    doc.units = 1
    doc.modelspace().add_linear_dim(base=(0, 50), p1=(0, 0), p2=(100, 20),
                                   angle=0, text="<>", override={"dimlfac": 2}).render()
    ps, _ = load_dxf(save(doc, tmp_path))
    dimension = ps.dimensions[0]
    assert dimension.start == pytest.approx((0, 0))
    assert dimension.end == pytest.approx((100, 20))
    assert dimension.measurement_axis == pytest.approx((1, 0))
    assert dimension.measured_source_units == pytest.approx(100)
    assert dimension.text == "<>"
    source = next(e for e in ps.entities if e.kind == "DIMENSION")
    hints = dict(source.metadata)
    assert hints["dimension_text_origin"] == "generated"
    assert float(hints["dimoverride_dimlfac"]) == 2


def test_flattened_pdf_preserves_cubic_fill_and_text(tmp_path):
    pdf = pytest.importorskip("pymupdf")
    from archiagent.ingest.pdf_vector import load_pdf
    path = tmp_path / "flat.pdf"
    doc = pdf.open()
    page = doc.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_bezier((10, 10), (50, 0), (50, 100), (100, 100))
    shape.finish(color=(0, 0, 0), closePath=False)
    shape.commit()
    page.draw_rect((10, 110, 100, 160), fill=(0.5, 0.5, 0.5))
    page.insert_text((20, 190), "GROUND FLOOR")
    doc.save(path)
    doc.close()
    ps = load_pdf(path)
    assert any(w.code == "flattened_pdf" for w in ps.warnings)
    cubic = next(e for e in ps.entities if e.kind == "PDF_CUBIC")
    assert cubic.coords[0] == (10, 190)
    assert cubic.coords[-1] == (100, 100)
    assert any(p.kind == "curve" and len(p.coords) > 4 for p in ps.primitives)
    assert any(p.kind == "fill" and p.closed for p in ps.primitives)
    assert "GROUND" in [t.text for t in ps.texts]
    assert all(t.source_id for t in ps.texts)


def test_raster_pdf_has_explicit_diagnostic(tmp_path):
    pdf = pytest.importorskip("pymupdf")
    from archiagent.ingest.pdf_vector import RasterOnlyPdfError, load_pdf
    path = tmp_path / "blank.pdf"
    doc = pdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    with pytest.raises(RasterOnlyPdfError, match="raster recognition and scale calibration"):
        load_pdf(path)


def test_pdf_even_odd_fill_retains_void(tmp_path):
    pdf = pytest.importorskip("pymupdf")
    from archiagent.ingest.pdf_vector import load_pdf
    path = tmp_path / "void.pdf"
    doc = pdf.open()
    page = doc.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_rect((10, 10, 190, 190))
    shape.draw_rect((50, 50, 100, 100))
    shape.finish(fill=(0.5, 0.5, 0.5), even_odd=True)
    shape.commit()
    doc.save(path)
    doc.close()
    ps = load_pdf(path)
    outer = next(e for e in ps.entities if e.kind == "PDF_FILL" and e.holes)
    assert len(outer.holes) == 1
    assert any(dict(e.metadata).get("is_hole") == "True" for e in ps.entities)
