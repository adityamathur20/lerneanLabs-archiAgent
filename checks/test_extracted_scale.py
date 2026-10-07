"""The drawing's own dimensions give a scale, when they agree and are trustworthy."""
import pytest

from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet
from archiagent.scale.extracted import ExtractedScale, extracted_scale


def dim(id, length_units, text, origin="generated", dimlfac="1"):
    """One horizontal native dimension, with the metadata ingestion records."""
    entity = SourceEntity(id, "DIMENSION", "DIMS", metadata=(
        ("dimension_text_origin", origin), ("dimstyle_dimlfac", dimlfac)))
    native = NativeDimension(id, (0.0, 0.0), (length_units, 0.0), length_units,
                             text, "DIMS", "linear", (1.0, 0.0))
    return entity, native


def drawing(pairs):
    return PrimitiveSet((), (), 500.0, 500.0, "dims.dxf", "sha-dims",
                        tuple(e for e, _ in pairs), tuple(d for _, d in pairs))


def test_two_agreeing_generated_dimensions_give_the_scale():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\"")])
    result = extracted_scale(ps)
    assert isinstance(result, ExtractedScale)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.support == 2
    assert result.basis == "native-dimension"
    assert len(result.spans) == 2


def test_a_single_dimension_cannot_agree_with_anything():
    assert extracted_scale(drawing([dim("a", 120.0, "10'-0\"")])) is None


def test_an_explicit_override_is_not_used_while_a_generated_pair_exists():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 100.0, "50'-0\"", origin="explicit-override")])
    result = extracted_scale(ps)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.support == 2
    assert any(rid == "c" for rid, _ in result.rejected)


def test_overrides_still_calibrate_when_nothing_was_generated():
    ps = drawing([dim("a", 120.0, "10'-0\"", origin="explicit-override"),
                  dim("b", 240.0, "20'-0\"", origin="explicit-override")])
    result = extracted_scale(ps)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.basis == "native-dimension-override"


def test_a_scaled_dimstyle_is_excluded_and_explained():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 120.0, "10'-0\"", dimlfac="25.4")])
    rejected = dict(extracted_scale(ps).rejected)
    assert "c" in rejected
    assert "dimlfac" in rejected["c"]


def test_dimensions_that_disagree_yield_nothing():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "5'-0\"")])
    assert extracted_scale(ps) is None


def test_unparseable_dimension_text_is_rejected_with_a_reason():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 120.0, "SEE PLAN")])
    rejected = dict(extracted_scale(ps).rejected)
    assert "c" in rejected


def test_a_drawing_with_no_dimensions_yields_nothing():
    assert extracted_scale(drawing([])) is None


def test_an_outlier_does_not_drag_the_median_but_is_recorded():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 360.0, "30'-0\""), dim("d", 999.0, "10'-0\"")])
    result = extracted_scale(ps)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.support == 3
    assert "d" in dict(result.rejected)


def test_the_tolerance_must_be_positive():
    with pytest.raises(ValueError):
        extracted_scale(drawing([]), tolerance_in=0)


def test_the_pdf_scale_path_is_untouched():
    # Regression pin: infer_associated_scale still refuses a single span.
    from archiagent.scale.verify import Measurement, infer_associated_scale
    one = Measurement("m1", (0, 0), (120, 0), 10.0, "face", "reviewed-measurement", None)
    assert infer_associated_scale((one,)) is None


if __name__ == "__main__":
    pytest.main([__file__])
