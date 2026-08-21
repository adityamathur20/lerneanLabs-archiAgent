import pytest

from archiagent.scale.dimensions import DimensionText
from archiagent.scale.resolve import ScaleGateError, ScaleResult, resolve_scale


def _dim(feet: float) -> DimensionText:
    return DimensionText(text=f"{feet}'", feet=feet, center=(0.0, 0.0))


def test_recovers_an_exact_scale():
    scale = 11.861
    dims = [_dim(f) for f in (14.4167, 13.8333, 7.0, 3.0)]
    runs = tuple(f * scale for f in (14.4167, 13.8333, 7.0, 3.0))
    result = resolve_scale(dims, runs)
    assert result.units_per_foot == pytest.approx(scale, rel=1e-6)
    assert result.max_residual_in < 0.01
    assert result.matched_count == 4
    assert result.convention == "clear"


def test_ignores_distractor_runs():
    """Real drawings have thousands of runs that match nothing."""
    scale = 11.861
    dims = [_dim(f) for f in (14.4167, 13.8333, 7.0)]
    runs = tuple([f * scale for f in (14.4167, 13.8333, 7.0)]
                 + [3.1, 7.7, 101.3, 250.0, 999.0])
    result = resolve_scale(dims, runs)
    assert result.units_per_foot == pytest.approx(scale, rel=1e-6)


def test_tolerates_a_small_per_dimension_error():
    scale = 11.861
    feet = (14.4167, 13.8333, 7.0, 3.0)
    runs = tuple(f * scale + delta
                 for f, delta in zip(feet, (0.0, 0.2, -0.7, 0.1)))
    result = resolve_scale([_dim(f) for f in feet], runs)
    assert result.max_residual_in < 2.0


def test_raises_when_residual_exceeds_the_two_inch_gate():
    scale = 11.861
    feet = (14.4167, 13.8333, 7.0)
    # 40pt error on one dimension is ~3.4ft — far outside the gate
    runs = tuple(f * scale for f in (14.4167, 13.8333)) + (7.0 * scale + 40.0,)
    with pytest.raises(ScaleGateError, match="residual"):
        resolve_scale([_dim(f) for f in feet], runs)


def test_raises_when_too_few_dimensions_match():
    with pytest.raises(ScaleGateError, match="matched"):
        resolve_scale([_dim(14.4167), _dim(13.8333)],
                      (14.4167 * 11.861, 13.8333 * 11.861))


def test_resolves_the_real_drawing_within_the_gate(demolition_pdf):
    """Regression: synthetic fixtures have uniform candidate density and hid a
    selection bug that returned 10.68 pt/ft instead of ~11.67 on this drawing."""
    from archiagent.ingest.pdf_vector import load_pdf
    from archiagent.scale.dimensions import extract_dimensions
    from archiagent.scale.resolve import candidate_runs

    ps = load_pdf(demolition_pdf)
    result = resolve_scale(extract_dimensions(ps),
                           candidate_runs(ps, {"walll", "wall"}))

    assert 11.5 < result.units_per_foot < 11.9
    assert result.matched_count >= 15
    assert result.max_residual_in < 2.0
    assert result.convention == "clear"
