"""OCR labels may qualify native spans only through explicit bounded evidence."""
from dataclasses import replace
import math

import pytest

pytest.importorskip("shapely")
from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet, TextItem
from archiagent.regions import select_region
from archiagent.scale.verify import associate_dimension_text, measurements_from_source
from archiagent.semantic import PlanRegion


def dimension(**overrides):
    return replace(NativeDimension("dim", (0, 0), (120, 0), 120,
        text_position=(60, 24), measurement_axis=(1, 0)), **overrides)


def ocr(text="10'-0\"", position=(60, 24), confidence="0.97", sid="ocr-1", **metadata):
    x, y = position
    item = TextItem(text, (x-2, y-1, x+2, y+1), "", sid)
    entity = SourceEntity(sid, "OCR_TEXT", "", center=position,
        metadata=(("confidence", confidence), ("engine", "fixture"), *metadata.items()))
    return item, entity


def drawing(dimensions=None, observations=None):
    pairs = observations if observations is not None else [ocr()]
    return PrimitiveSet((), tuple(pair[0] for pair in pairs), 200, 100,
        "fixture", "fixture", tuple(pair[1] for pair in pairs),
        tuple(dimensions if dimensions is not None else [dimension()]))


def test_unique_high_confidence_full_label_retains_ocr_provenance():
    ps = drawing()
    associated, = associate_dimension_text(ps, 12)
    assert associated.text == "10'-0\""
    assert associated.text_source_ids == ("ocr-1",)
    assert associated.text_evidence == "ocr-associated"
    assert associated.start == ps.dimensions[0].start
    assert associated.text_position == (60, 24)
    assert ps.dimensions[0].text == ""  # source is immutable
    measurement, = measurements_from_source(replace(ps, dimensions=(associated,)))
    assert measurement.expected_ft == 10
    assert measurement.source == "ocr-associated"


@pytest.mark.parametrize("text", ["120", "10", "ROOM 10", "10' X", "10'-", "<>" , "0 mm"])
def test_unparsed_or_incomplete_labels_are_not_guessed(text):
    ps = drawing(observations=[ocr(text=text)])
    assert associate_dimension_text(ps, 12) == ps.dimensions


@pytest.mark.parametrize("confidence", ["0.84", "nan", "inf", "1.2", "-1", "unknown"])
def test_missing_or_low_quality_confidence_is_not_accepted(confidence):
    ps = drawing(observations=[ocr(confidence=confidence)])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_missing_confidence_does_not_default_to_trusted():
    text, entity = ocr()
    ps = drawing(observations=[(text, replace(entity, metadata=(("engine", "fixture"),)))])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_native_labels_and_deliberately_suppressed_labels_are_preserved():
    for text in ("8 ft", " "):
        ps = drawing(dimensions=[dimension(text=text)])
        assert associate_dimension_text(ps, 12) == ps.dimensions


def test_multiple_nearby_labels_remain_ambiguous_even_when_values_match():
    ps = drawing(observations=[ocr(), ocr(position=(63, 24), sid="ocr-2")])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_split_inches_token_blocks_partial_feet_label_without_concatenation():
    ps = drawing(observations=[ocr(text="10'", position=(59, 24)),
        ocr(text='3"', position=(63, 24), confidence="0.2", sid="inches-fragment")])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_one_ocr_label_cannot_qualify_two_native_dimensions():
    ps = drawing(dimensions=[dimension(), dimension(id="second", text_position=(62, 24))])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_no_native_text_anchor_means_no_association():
    ps = drawing(dimensions=[dimension(text_position=None)])
    assert associate_dimension_text(ps, 12) == ps.dimensions


def test_association_radius_is_physical_inches_not_raw_source_units():
    ps = drawing(observations=[ocr(position=(67, 24))])
    assert associate_dimension_text(ps, 12) == ps.dimensions  # seven inches
    assert associate_dimension_text(ps, 12, max_text_distance_in=8)[0].text == "10'-0\""


def test_region_origin_translates_ocr_text_anchor_and_witnesses_together():
    native = dimension(start=(100, 100), end=(220, 100), text_position=(160, 124))
    ps = drawing(dimensions=[native], observations=[ocr(position=(160, 124))])
    region = PlanRegion("crop", (90, 90, 230, 140), origin=(100, 100))
    local = select_region(ps, region)
    assert local.dimensions[0].text_position == (60, 24)
    assert local.texts[0].center() == (60, 24)
    assert associate_dimension_text(local, 12)[0].text_source_ids == ("ocr-1",)


@pytest.mark.parametrize("distance", [0, -1, math.nan, math.inf, 12.1, True])
def test_ocr_association_distance_is_finite_positive_and_capped(distance):
    with pytest.raises(ValueError, match="distance"):
        associate_dimension_text(drawing(), 12, distance)


def test_native_dimension_anchor_comes_from_saved_dxf_text_midpoint(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from archiagent.ingest.dxf_vector import load_dxf
    doc = ezdxf.new()
    doc.units = 1
    native = doc.modelspace().add_linear_dim(base=(0, 24), p1=(0, 0), p2=(120, 0))
    native.render()
    native.dimension.dxf.text_midpoint = (61, 27, 0)
    path = tmp_path/"dimension-anchor.dxf"
    doc.saveas(path)
    ps, _ = load_dxf(path)
    assert ps.dimensions[0].text_position == pytest.approx((61, 27))
    assert ps.dimensions[0].start == pytest.approx((0, 0))
    assert ps.dimensions[0].end == pytest.approx((120, 0))
