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
