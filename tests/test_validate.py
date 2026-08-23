import pytest
from dataclasses import replace

from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import Space, detect_spaces
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
        scale=scale or _scale(),
        layer_decisions=(LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.95,
                                       "", "manual"),),
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


def test_unmatched_dimension_is_a_warning_not_an_error():
    """A dimension outside the residual gate must be surfaced, not dropped --
    but it is a `warn`, since we cannot tell whether it measures non-wall
    geometry (a balcony) or the scale is actually wrong."""
    scale = ScaleResult(units_per_foot=11.861, convention="clear",
                        residuals_in=(0.2, 0.5, 0.7), max_residual_in=0.7,
                        matched_count=3, total_dimensions=4,
                        unmatched_residuals_in=(9.5,), unmatched_count=1)
    issues = validate(_model(_ring(), scale=scale))
    matches = [i for i in issues if i.code == "dimension_outside_gate"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    assert "9.5" in matches[0].msg


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


def test_zero_length_wall_is_reported():
    """Defensive guard. `resolve_junctions` drops zero-length walls at entry, so
    this cannot arise through the normal pipeline — but Tasks 13 and 14 construct
    BuildingModel directly, and the validator must catch a degenerate wall from
    any assembly route."""
    model = _model(_ring())
    broken = replace(model, walls=model.walls + (
        WallSeg((2.0, 2.0), (2.0, 2.0), 4 / 12,
                "WALLS", "paired-line", "measured"),))
    codes = {i.code for i in validate(broken)}
    assert "zero_length_wall" in codes


def test_default_thickness_is_warned_not_silent():
    """Fallback thicknesses (4in interior / 8in exterior) must be visible in the
    issue list, never applied silently — provenance is what makes a wrong wall
    traceable to a wrong assumption."""
    walls = _ring() + [WallSeg((2.0, 2.0), (6.0, 2.0), 8 / 12,
                               "WALLS", "paired-line", "default")]
    issues = validate(_model(walls))
    defaults = [i for i in issues if i.code == "default_thickness"]
    assert len(defaults) == 1
    assert defaults[0].severity == "warn"
    assert defaults[0].entity.startswith("W")


def test_unclosed_space_boundary_is_reported():
    """Space.boundary must be a closed ring; Task 13 reads it to author IfcSpace."""
    model = _model(_ring())
    broken = replace(model, spaces=(Space(boundary=((0.0, 0.0), (10.0, 0.0),
                                                    (10.0, 10.0)),
                                          area_sqft=50.0),))
    codes = {i.code for i in validate(broken)}
    assert "unclosed_space" in codes
