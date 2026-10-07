"""Scale comes from the drawing, or from one asserted wall, never from the header alone."""
import pytest

from archiagent.cli import _parser, _resolve_scale, _wall_length_measurements
from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet
from archiagent.scale.verify import Measurement, infer_associated_scale, scale_from_reviewed

BASE = ["--dxfFilePath", "plan.dxf", "--outputDir", "out"]


def parse(extra=()):
    return _parser().parse_args(BASE + list(extra))


def asserted(id, start, end, expected_ft, source="cli-wall-length"):
    return Measurement(id, start, end, expected_ft, "face", source, None)


def dim(id, dimlfac="0.0254", measured=120.0):
    entity = SourceEntity(id, "DIMENSION", "DIMS", metadata=(
        ("dimension_text_origin", "generated"), ("dimstyle_dimlfac", dimlfac)))
    native = NativeDimension(id, (0.0, 0.0), (measured, 0.0), measured,
                             "", "DIMS", "linear", (1.0, 0.0))
    return entity, native


def drawing(pairs=()):
    return PrimitiveSet((), (), 500.0, 500.0, "plan.dxf", "sha",
                        tuple(e for e, _ in pairs), tuple(d for _, d in pairs))


DIMENSIONED = drawing([dim("a"), dim("b"), dim("c")])   # implies 12 units/foot
BARE = drawing()


class Region:
    def __init__(self, units_per_foot):
        self.units_per_foot = units_per_foot


# --- scale_from_reviewed -----------------------------------------------------

def test_one_asserted_length_sets_the_scale():
    assert scale_from_reviewed((asserted("w1", (0, 0), (120, 0), 10.0),)) == pytest.approx(12.0)


def test_infer_associated_scale_still_refuses_a_single_span():
    # Regression pin: the PDF path is unchanged.
    one = asserted("w1", (0, 0), (120, 0), 10.0, source="reviewed-measurement")
    assert infer_associated_scale((one,)) is None


def test_native_and_ocr_sources_are_not_assertions():
    native = asserted("d1", (0, 0), (120, 0), 10.0, source="native-dimension")
    ocr = asserted("o1", (0, 0), (120, 0), 10.0, source="ocr-associated")
    assert scale_from_reviewed((native, ocr)) is None


def test_two_agreeing_assertions_give_the_same_scale():
    a = asserted("w1", (0, 0), (120, 0), 10.0)
    b = asserted("w2", (0, 50), (0, 290), 20.0)
    assert scale_from_reviewed((a, b)) == pytest.approx(12.0)


def test_two_disagreeing_assertions_are_refused():
    a = asserted("w1", (0, 0), (120, 0), 10.0)
    b = asserted("w2", (0, 50), (0, 290), 10.0)
    with pytest.raises(ValueError, match="disagree"):
        scale_from_reviewed((a, b))


def test_no_assertions_yields_none():
    assert scale_from_reviewed(()) is None


# --- the CLI helpers ---------------------------------------------------------

def test_a_wall_length_becomes_an_asserted_measurement():
    measurement, = _wall_length_measurements(parse(["--scale-from-wall", "0", "0", "120", "0", "10ft"]))
    assert measurement.start == (0.0, 0.0)
    assert measurement.end == (120.0, 0.0)
    assert measurement.expected_ft == pytest.approx(10.0)
    assert measurement.source == "cli-wall-length"


def test_lengths_in_any_unit_are_accepted():
    for value, feet in (("10'-6\"", 10.5), ("3.048m", 10.0), ("3048mm", 10.0), ("120in", 10.0)):
        args = parse(["--scale-from-wall", "0", "0", "120", "0", value])
        assert _wall_length_measurements(args)[0].expected_ft == pytest.approx(feet, rel=1e-3)


def test_an_unparseable_length_is_refused():
    with pytest.raises(ValueError, match="length"):
        _wall_length_measurements(parse(["--scale-from-wall", "0", "0", "120", "0", "ten"]))


def test_coincident_points_are_refused():
    with pytest.raises(ValueError):
        _wall_length_measurements(parse(["--scale-from-wall", "5", "5", "5", "5", "10ft"]))


def test_the_flag_repeats():
    args = parse(["--scale-from-wall", "0", "0", "120", "0", "10ft",
                  "--scale-from-wall", "0", "0", "0", "240", "20ft"])
    assert len(_wall_length_measurements(args)) == 2


# --- the ladder --------------------------------------------------------------

def test_a_bare_drawing_with_no_scale_input_is_refused():
    with pytest.raises(ValueError) as caught:
        _resolve_scale(parse(), BARE, True, None, 1.0)
    message = str(caught.value)
    assert "--scale-from-wall" in message
    assert "--trust-extracted-scale" in message


def test_the_refusal_reports_the_extracted_estimate_and_the_header():
    with pytest.raises(ValueError) as caught:
        _resolve_scale(parse(), DIMENSIONED, True, None, 1.0)
    message = str(caught.value)
    assert "12" in message
    assert "header" in message


def test_trusting_the_extracted_scale_accepts_it():
    scale, rung, _ = _resolve_scale(parse(["--trust-extracted-scale"]), DIMENSIONED, True, None, 1.0)
    assert scale == pytest.approx(12.0)
    assert rung == "extracted"


def test_trusting_extraction_with_nothing_extractable_is_refused():
    with pytest.raises(ValueError, match="no scale could be read"):
        _resolve_scale(parse(["--trust-extracted-scale"]), BARE, True, None, 1.0)


def test_one_assertion_sets_the_scale():
    args = parse(["--scale-from-wall", "0", "0", "120", "0", "10ft"])
    scale, rung, _ = _resolve_scale(args, BARE, True, None, 1.0)
    assert scale == pytest.approx(12.0)
    assert rung == "asserted"


def test_an_assertion_agreeing_with_extraction_issues_nothing():
    args = parse(["--scale-from-wall", "0", "0", "120", "0", "10ft"])
    _, rung, issues = _resolve_scale(args, DIMENSIONED, True, None, 1.0)
    assert rung == "asserted"
    assert issues == ()


def test_an_assertion_overrides_a_disagreeing_extraction_with_a_warning():
    args = parse(["--scale-from-wall", "0", "0", "254", "0", "10ft"])
    scale, rung, issues = _resolve_scale(args, DIMENSIONED, True, None, 1.0)
    assert scale == pytest.approx(25.4)
    assert rung == "asserted"
    warning, = [i for i in issues if i.code == "asserted_scale_overrides_extracted"]
    assert "25.4" in warning.msg and "12" in warning.msg


def test_a_unit_sized_disagreement_is_named_as_such():
    # 25.4 against 12 is not a unit factor; 12 against 1 is.
    args = parse(["--scale-from-wall", "0", "0", "12", "0", "12ft"])
    _, _, issues = _resolve_scale(args, DIMENSIONED, True, None, 1.0)
    warning, = [i for i in issues if i.code == "asserted_scale_overrides_extracted"]
    assert "12" in warning.msg


def test_a_reviewed_region_scale_wins_without_any_assertion():
    scale, rung, _ = _resolve_scale(parse(), BARE, True, Region(304.8), 1.0)
    assert scale == pytest.approx(304.8)
    assert rung == "region"


def test_a_pdf_is_not_subject_to_the_dxf_requirement():
    scale, rung, _ = _resolve_scale(parse(), BARE, False, None, 72.0)
    assert scale == pytest.approx(72.0)
    assert rung == "pdf"


# --- the removed flag --------------------------------------------------------

def test_the_units_per_foot_flag_is_gone():
    with pytest.raises(SystemExit):
        _parser().parse_args(BASE + ["--units-per-foot", "12"])


def test_the_python_api_still_takes_a_scale():
    import inspect

    from archiagent.pipeline import extract_from_dxf
    assert "units_per_foot" in inspect.signature(extract_from_dxf).parameters


if __name__ == "__main__":
    pytest.main([__file__])
