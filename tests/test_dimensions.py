import pytest

from archiagent.scale.dimensions import DimensionText, extract_dimensions, parse_dimension
from archiagent.primitives import PrimitiveSet, TextItem


@pytest.mark.parametrize("text,feet", [
    ("14'-5\"", 14 + 5 / 12),
    ("13'-10\"", 13 + 10 / 12),
    ("7'-0\"", 7.0),
    ("3'-0\"", 3.0),
    ("11'6\"", 11.5),
    ("9'4\"", 9 + 4 / 12),
    ("4'6'", 4.5),          # typo in the source drawing; must still parse
    ("14'-5\"X", 14 + 5 / 12),  # trailing X from "14'-5"X 13'-10""
    ("12'", 12.0),
])
def test_parses_feet_inches(text, feet):
    assert parse_dimension(text) == pytest.approx(feet)


@pytest.mark.parametrize("text", ["BEDROOM", "TOILET", "", "X", "WIDE", "-1", ":-"])
def test_rejects_non_dimensions(text):
    assert parse_dimension(text) is None


def test_rejects_implausible_magnitudes():
    assert parse_dimension("0'-0\"") is None
    assert parse_dimension("9999'") is None


def test_extract_dimensions_keeps_position():
    ps = PrimitiveSet(
        primitives=(),
        texts=(TextItem("BEDROOM", (0.0, 0.0, 10.0, 5.0), ""),
               TextItem("14'-5\"X", (0.0, 10.0, 20.0, 15.0), "")),
        width=100.0, height=100.0, source_path="x", source_sha256="y")
    dims = extract_dimensions(ps)
    assert len(dims) == 1
    assert dims[0].feet == pytest.approx(14 + 5 / 12)
    assert dims[0].center == (10.0, 12.5)


# --- fractional inches -------------------------------------------------
# CAD emits Unicode vulgar fractions; other exporters emit ASCII "1/2".
# The ground-floor plan's overall chain is 18'-7½" + 6'-4½" + 15'-0" + 10'-0",
# which sums to exactly the printed 50'-0" -- so rejecting fractions loses
# 2 of 5 dimensions and breaks that self-check.

@pytest.mark.parametrize("text,feet", [
    ("18'-7½\"", 18 + 7.5 / 12),      # 18'-7½"  -> 18.625
    ("6'-4½\"", 6 + 4.5 / 12),        # 6'-4½"   ->  6.375
    ("7'-½\"", 7 + 0.5 / 12),         # fraction with no whole inches
    ("3'-2¼\"", 3 + 2.25 / 12),       # ¼
    ("3'-2¾\"", 3 + 2.75 / 12),       # ¾
    ("3'-2⅛\"", 3 + 2.125 / 12),      # ⅛
    ("3'-2⅜\"", 3 + 2.375 / 12),      # ⅜
    ("3'-2⅝\"", 3 + 2.625 / 12),      # ⅝
    ("3'-2⅞\"", 3 + 2.875 / 12),      # ⅞
    ("7'-1/2\"", 7 + 0.5 / 12),            # ASCII fraction, hyphenated
    ("7' 1/2\"", 7 + 0.5 / 12),            # ASCII fraction, spaced
    ("7'-11 1/2\"", 7 + 11.5 / 12),        # whole inches + ASCII fraction
    ("11'-11¾\"", 11 + 11.75 / 12),   # just under the 12in rollover
])
def test_parses_fractional_inches(text, feet):
    assert parse_dimension(text) == pytest.approx(feet)


@pytest.mark.parametrize("text", [
    "3'-12½\"",   # 12½ inches -- should have been written as 4'-½"
    "3'-13\"",         # plain out-of-range inches, unchanged behaviour
    "3'-2/0\"",        # zero denominator
])
def test_rejects_out_of_range_or_malformed_inches(text):
    assert parse_dimension(text) is None


def test_mounting_heights_are_still_rejected():
    """The electrical plan carries +102", +18", +48" etc -- fixture mounting
    heights above floor, NOT plan dimensions. Feeding them to scale resolution
    would corrupt the dominant error term."""
    for text in ('+102"', '+18"', '+48"', '+66"'):
        assert parse_dimension(text) is None


def test_ground_floor_chain_sums_to_its_printed_overall():
    """The self-check the fractions unlock: a dimension chain reproduces the
    overall it spans. Independent evidence that a scale is right."""
    chain = ["18'-7½\"", "6'-4½\"", "15'-0\"", "10'-0\""]
    total = sum(parse_dimension(t) for t in chain)
    assert total == pytest.approx(parse_dimension("50'-0\""))
