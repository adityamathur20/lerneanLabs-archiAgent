"""Room labels state two dimensions at once, and a dimstyle factor can state the unit.

`14'-9"x12'-4½"` is the commonest dimension-bearing text in this corpus and
parses as nothing at all unless it is split first. Separately, a generated
dimension carries no text, but its dimstyle linear factor still relates drawing
units to displayed units, which pins the drawing's unit where it is unambiguous.
"""
import pytest

from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet, TextItem
from archiagent.scale.dimensions import extract_dimensions, parse_dimension
from archiagent.scale.extracted import dimstyle_units_per_foot, extracted_scale


def text(value, x=0.0, y=0.0):
    return TextItem(value, (x, y, x, y), "TEXT", f"t-{value}")


def drawing(values):
    return PrimitiveSet((), tuple(text(v, i * 10.0, 0.0) for i, v in enumerate(values)),
                        500.0, 500.0, "texts.dxf", "sha-texts")


class RoomLabelPairs:
    """Grouping marker; pytest collects the functions below directly."""


def test_a_room_label_pair_yields_both_dimensions():
    found = extract_dimensions(drawing(["14'-9\"x12'-4½\""]))
    assert sorted(d.feet for d in found) == [12.375, 14.75]


def test_the_multiplication_sign_separates_as_well_as_the_letter():
    found = extract_dimensions(drawing(["10'-10½\"×9'-0\""]))
    assert sorted(d.feet for d in found) == [9.0, 10.875]


def test_an_uppercase_separator_works():
    found = extract_dimensions(drawing(["9'-7½\"X9'-6\""]))
    assert sorted(d.feet for d in found) == [9.5, 9.625]


def test_both_halves_keep_the_original_text_for_provenance():
    found = extract_dimensions(drawing(["5'-0\"x5'-0\""]))
    assert {d.text for d in found} == {"5'-0\"x5'-0\""}
    assert len(found) == 2


def test_a_lone_dimension_still_yields_exactly_one():
    found = extract_dimensions(drawing(["17'-1\""]))
    assert [d.feet for d in found] == [pytest.approx(17 + 1 / 12)]


def test_a_trailing_separator_is_not_a_second_dimension():
    # parse_dimension already strips a trailing X; splitting must not
    # turn "12'-0\"X" into a pair with an empty half.
    found = extract_dimensions(drawing(["12'-0\"X"]))
    assert [d.feet for d in found] == [12.0]


def test_text_that_is_not_a_dimension_is_still_ignored():
    assert extract_dimensions(drawing(["BEDROOM", "KITCHEN", ""])) == ()


def test_half_a_pair_being_unparseable_keeps_the_other_half():
    found = extract_dimensions(drawing(["14'-9\"x SEE PLAN"]))
    assert [d.feet for d in found] == [14.75]


def test_parse_dimension_itself_is_unchanged():
    # Other callers depend on it returning one value or None; pair handling
    # belongs to extract_dimensions, not here.
    assert parse_dimension("14'-9\"") == 14.75
    assert parse_dimension("14'-9\"x12'-4½\"") is None


def dim(id, dimlfac, measured=120.0):
    entity = SourceEntity(id, "DIMENSION", "DIMS", metadata=(
        ("dimension_text_origin", "generated"), ("dimstyle_dimlfac", str(dimlfac))))
    native = NativeDimension(id, (0.0, 0.0), (measured, 0.0), measured,
                             "", "DIMS", "linear", (1.0, 0.0))
    return entity, native


def dimensioned(pairs):
    return PrimitiveSet((), (), 500.0, 500.0, "dims.dxf", "sha",
                        tuple(e for e, _ in pairs), tuple(d for _, d in pairs))


def test_an_inch_drawing_displayed_in_metres_is_recognised():
    # Aiims Road: 424 dimensions, all dimlfac=0.0254, all text empty.
    # 0.0254 is inches/metres, so the drawing unit is the inch.
    assert dimstyle_units_per_foot(0.0254) == pytest.approx(12.0)


def test_an_inch_drawing_displayed_in_millimetres_is_recognised():
    assert dimstyle_units_per_foot(25.4) == pytest.approx(12.0)


def test_a_millimetre_drawing_displayed_in_inches_is_recognised():
    assert dimstyle_units_per_foot(1 / 25.4) == pytest.approx(304.8)


def test_an_inch_drawing_displayed_in_feet_is_recognised():
    assert dimstyle_units_per_foot(1 / 12) == pytest.approx(12.0)


def test_a_neutral_factor_says_nothing():
    # dimlfac 1 means no conversion, which is true of every unit displayed
    # as itself. It identifies nothing and must not guess.
    assert dimstyle_units_per_foot(1.0) is None


def test_an_unrecognisable_factor_says_nothing():
    assert dimstyle_units_per_foot(3.7) is None
    assert dimstyle_units_per_foot(0.0) is None
    assert dimstyle_units_per_foot(-25.4) is None


def test_extraction_falls_back_to_the_dimstyle_factor():
    ps = dimensioned([dim("a", 0.0254), dim("b", 0.0254), dim("c", 0.0254)])
    result = extracted_scale(ps)
    assert result is not None
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.basis == "dimstyle-factor"
    assert result.support == 3


def test_disagreeing_dimstyle_factors_are_refused():
    ps = dimensioned([dim("a", 0.0254), dim("b", 25.4), dim("c", 1 / 25.4)])
    assert extracted_scale(ps) is None


def test_a_neutral_factor_does_not_produce_a_scale():
    ps = dimensioned([dim("a", 1.0), dim("b", 1.0)])
    assert extracted_scale(ps) is None


if __name__ == "__main__":
    pytest.main([__file__])
