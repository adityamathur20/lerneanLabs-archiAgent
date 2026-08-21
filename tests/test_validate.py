import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.model import BuildingModel, Issue
from archiagent.scale.resolve import ScaleResult
from archiagent.validate import validate


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def _scale():
    return ScaleResult(units_per_foot=11.861, convention="clear",
                       residuals_in=(0.2, 0.5, 0.7), max_residual_in=0.7,
                       matched_count=3)


def _model(walls, scale=None):
    graph = resolve_junctions(walls)
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=scale or _scale(), layer_roles={"WALLS": "wall_structural"},
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def test_clean_ring_has_no_issues():
    assert validate(_model(_ring())) == ()


def test_unresolved_junction_is_an_error():
    issues = validate(_model(_ring()[:3]))
    codes = {i.code for i in issues}
    assert "unresolved_junction" in codes
    assert any(i.severity == "error" for i in issues)


def test_scale_above_the_gate_is_an_error():
    bad = ScaleResult(units_per_foot=11.861, convention="clear",
                      residuals_in=(0.2, 3.4), max_residual_in=3.4,
                      matched_count=2)
    codes = {i.code for i in validate(_model(_ring(), scale=bad))}
    assert "scale_gate_failed" in codes


def test_zero_thickness_wall_is_reported():
    walls = _ring() + [WallSeg((2.0, 2.0), (4.0, 2.0), 0.0,
                               "WALLS", "paired-line", "measured")]
    codes = {i.code for i in validate(_model(walls))}
    assert "zero_thickness_wall" in codes


def test_model_with_no_spaces_is_reported():
    codes = {i.code for i in validate(_model(_ring()[:2]))}
    assert "no_spaces_detected" in codes


def test_envelope_is_the_bounding_box_of_all_walls():
    model = _model(_ring(10.0))
    assert model.envelope() == (0.0, 0.0, 10.0, 10.0)
