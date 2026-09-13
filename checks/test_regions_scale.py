"""Reviewed crops and dimensions must never manufacture supporting evidence."""
import json
import math

import pytest

pytest.importorskip("shapely")
from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.regions import load_regions, propose_regions, select_region
from archiagent.scale.verify import (
    Measurement, infer_associated_scale, load_measurements,
    measurements_from_source, parse_explicit_length, verify_dimensions,
)
from archiagent.semantic import PlanRegion


def drawing(primitives=(), entities=(), dimensions=()):
    return PrimitiveSet(tuple(primitives), (), 200, 200, "fixture", "fixture",
                        tuple(entities), tuple(dimensions))


def wall(start, end, thickness=0.5):
    return WallSeg(start, end, thickness, "WALL", "paired-line", "measured")


def test_crop_retains_observed_edges_without_manufacturing_crop_border():
    p = Primitive("rect", ((0, 0), (10, 0), (10, 10), (0, 10)), "WALL", None, None,
                  "rect-1", "LWPOLYLINE", True)
    cropped = select_region(drawing([p]), PlanRegion("crop", (5, -1, 11, 11)))
    segments = [segment for primitive in cropped.primitives for segment in primitive.segments()]
    assert segments
    assert sum(math.dist(a, b) for a, b in segments) == pytest.approx(20)
    assert not any(a[0] == b[0] == 5 for a, b in segments)
    assert all(not primitive.closed for primitive in cropped.primitives)


def test_crop_inside_closed_ring_does_not_turn_crop_into_wall():
    p = Primitive("rect", ((0, 0), (10, 0), (10, 10), (0, 10)), "WALL", None, None)
    result = select_region(drawing([p]), PlanRegion("inside", (2, 2, 8, 8)))
    assert not result.primitives


def test_region_preserves_parent_provenance_and_translates_witnesses_only():
    source = SourceEntity("block", "INSERT", "DOOR", ((-10, -10), (100, 100)), center=(-10, -10))
    child = SourceEntity("leaf", "LINE", "DOOR", ((10, 10), (20, 10)), parent_id="block")
    primitive = Primitive("line", child.coords, "DOOR", None, None, "leaf", "LINE")
    dimension = NativeDimension("dim", (10, 10), (20, 15), 10, "10 ft", measurement_axis=(1, 0))
    result = select_region(drawing([primitive], [source, child], [dimension]),
                           PlanRegion("crop", (0, 0, 30, 30), origin=(10, 10)))
    assert {e.id for e in result.entities} == {"block", "leaf"}
    parent = next(e for e in result.entities if e.id == "block")
    assert dict(parent.metadata)["region_partial"] == "true"
    assert result.dimensions[0].start == (0, 0)
    assert result.dimensions[0].end == (10, 5)
    assert result.dimensions[0].measurement_axis == (1, 0)
    assert any(w.source_id == "block" for w in result.warnings)


@pytest.mark.parametrize("origin", [[0], [0, math.nan], [0, math.inf], [True, 0], "0,0"])
def test_reviewed_region_rejects_invalid_origin(tmp_path, origin):
    path = tmp_path / "regions.json"
    path.write_text(json.dumps([{"id": "floor", "bounds": [0, 0, 10, 10], "origin": origin}]))
    with pytest.raises(ValueError, match="origin"):
        load_regions(path)


@pytest.mark.parametrize("gap", [math.nan, math.inf, 0, -1, True])
def test_region_proposals_require_finite_positive_gap(gap):
    with pytest.raises(ValueError, match="finite"):
        propose_regions(drawing(), 12, gap)


def test_native_dimension_projection_is_distinct_from_diagonal():
    native = NativeDimension("dim", (0, 0), (120, 24), 120, "10 ft", measurement_axis=(1, 0))
    measurement, = measurements_from_source(drawing(dimensions=[native]))
    assert measurement.axis == (1, 0)
    walls = (wall((0, -1), (0, 5)), wall((10, -1), (10, 5)))
    from dataclasses import replace
    check, = verify_dimensions([replace(measurement, basis="centerline")], walls, 12)
    assert check.actual_ft == pytest.approx(10)
    assert check.status == "verified"


def test_default_native_text_never_independently_verifies_scale():
    native = NativeDimension("dim", (0, 0), (120, 24), 120, "<>", measurement_axis=(1, 0))
    references = measurements_from_source(drawing(dimensions=[native]))
    assert infer_associated_scale(references) is None
    check, = verify_dimensions(references, [wall((0, 0), (10, 0))], 12)
    assert check.status == "unverified"
    assert check.expected_ft == pytest.approx(10)


def test_associated_scale_requires_independent_spans_and_checks_consistency():
    a = Measurement("a", (0, 0), (120, 24), 10, axis=(1, 0))
    repeated = Measurement("repeat", (120, 24), (0, 0), 10, axis=(-1, 0))
    assert infer_associated_scale([a, repeated]) is None
    b = Measurement("b", (0, 40), (60, 50), 5, axis=(1, 0))
    assert infer_associated_scale([a, b]) == pytest.approx(12)
    wrong = Measurement("wrong", (0, 50), (60, 50), 10)
    with pytest.raises(ValueError, match="disagree"):
        infer_associated_scale([a, wrong])


def test_reviewed_projection_axis_and_origin(tmp_path):
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps([{"id": "width", "region_id": "floor", "start": [100, 100],
        "end": [220, 124], "expected_ft": 10, "measurement_axis": [4, 0], "basis": "centerline"}]))
    reference, = load_measurements(path, PlanRegion("floor", (90, 90, 230, 130), origin=(100, 100)))
    assert reference.start == (0, 0)
    assert reference.end == (120, 24)
    assert reference.axis == (1, 0)


@pytest.mark.parametrize("axis", [[0, 0], [math.nan, 0], [math.inf, 0], [1], [True, 0], "x"])
def test_reviewed_projection_rejects_invalid_axis(tmp_path, axis):
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps([{"id": "a", "start": [0, 0], "end": [1, 0],
                                "expected_ft": 1, "measurement_axis": axis}]))
    with pytest.raises(ValueError, match="measurement_axis"):
        load_measurements(path)


def test_reviewed_axis_alias_is_not_silently_ignored(tmp_path):
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps([{"id": "a", "start": [0, 0], "end": [1, 0],
                                "expected_ft": 1, "axis": [1, 0]}]))
    with pytest.raises(ValueError, match="use measurement_axis"):
        load_measurements(path)


def test_reviewed_measurement_cannot_reference_another_named_region(tmp_path):
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps([{"id": "a", "region_id": "floor", "start": [20, 20],
                                "end": [30, 20], "expected_ft": 10}]))
    with pytest.raises(ValueError, match="outside"):
        load_measurements(path, PlanRegion("floor", (0, 0, 10, 10)))


def test_final_model_error_and_missing_association_are_distinct():
    reference = Measurement("width", (0, 0), (120, 0), 10, basis="centerline")
    changed = [wall((.16, -1), (.16, 1)), wall((9.84, -1), (9.84, 1))]
    check, = verify_dimensions([reference], changed, 12)
    assert check.status == "failed"
    assert check.error_in == pytest.approx(3.84)
    missing, = verify_dimensions([reference], [wall((50, 0), (60, 0))], 12)
    assert missing.status == "unverified"


def test_wrong_scale_cannot_pass_with_zero_error():
    reference = Measurement("width", (0, 0), (120, 0), 10, basis="centerline")
    check, = verify_dimensions([reference], [wall((0, 0), (120, 0))], 1)
    assert check.status == "failed"
    assert check.error_in == pytest.approx(1320)


@pytest.mark.parametrize("value", [0, math.nan, math.inf, -1, True])
def test_dimension_tolerance_must_be_positive_finite(value):
    with pytest.raises(ValueError, match="tolerance"):
        verify_dimensions([], [], 12, value)
    with pytest.raises(ValueError, match="tolerance"):
        infer_associated_scale([], value)


def test_nonphysical_or_overflowing_explicit_lengths_remain_unverified():
    assert parse_explicit_length("0 mm") is None
    assert parse_explicit_length("9" * 500 + " m") is None


def test_perpendicular_axis_cannot_define_zero_measurement():
    with pytest.raises(ValueError, match="positive measured span"):
        Measurement("zero", (0, 0), (1, 0), 1, axis=(0, 1))
