import pytest

from archiagent.ingest.pdf_vector import NoLayersError, load_pdf


def test_loads_layers_from_real_drawing(demolition_pdf):
    ps = load_pdf(demolition_pdf)
    names = {n.lower() for n in ps.layer_names()}
    assert "walll" in names   # 3 L's: typo in the source CAD file, 187 line paths
    assert "furniture" in names
    assert len(ps.primitives) > 1000


def test_extracts_live_text_with_positions(demolition_pdf):
    ps = load_pdf(demolition_pdf)
    strings = [t.text for t in ps.texts]
    assert "BEDROOM" in strings
    assert any("14'-5" in s for s in strings)


def test_y_axis_is_flipped_upward(demolition_pdf):
    """BEDROOM-1 is drawn near the TOP of the page. After the flip its y
    must be in the upper half, i.e. greater than half the page height."""
    ps = load_pdf(demolition_pdf)
    bedroom = next(t for t in ps.texts if t.text == "BEDROOM")
    assert bedroom.center()[1] > ps.height / 2


def test_raises_when_pdf_has_no_layers(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line(pymupdf.Point(0, 0), pymupdf.Point(100, 100))
    flat = tmp_path / "flat.pdf"
    doc.save(flat)
    doc.close()

    with pytest.raises(NoLayersError):
        load_pdf(flat)
