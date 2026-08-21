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
    assert len(result.residuals_in) == result.matched_count
    assert result.total_dimensions == 24
    assert result.convention == "clear"
    # Every dimension is accounted for, matched or not -- none are dropped.
    assert result.matched_count + result.unmatched_count == result.total_dimensions
    assert len(result.unmatched_residuals_in) == result.unmatched_count
    assert result.unmatched_count == 3
    assert all(r > 2.0 for r in result.unmatched_residuals_in)


def test_unmatched_dimensions_are_reported_not_dropped():
    """A dimension that measures non-wall geometry (e.g. a balcony) must still
    surface its residual, not vanish silently from the result."""
    scale = 11.861
    feet = (14.4167, 13.8333, 7.0, 3.0)
    runs = tuple(f * scale for f in feet)
    # A fifth dimension with no matching run at all -- far outside the gate.
    dims = [_dim(f) for f in feet] + [_dim(50.0)]
    result = resolve_scale(dims, runs)
    assert result.matched_count == 4
    assert result.unmatched_count == 1
    assert len(result.unmatched_residuals_in) == 1
    assert result.unmatched_residuals_in[0] > 2.0
    assert result.matched_count + result.unmatched_count == result.total_dimensions


def test_dense_distractor_band_does_not_outvote_the_true_scale():
    """Regression for the index-span selection bug: a dense band of coincidental
    candidates must not beat the scale that explains the most dimensions.
    Reproduces the real-drawing failure without client data."""
    scale = 12.0
    feet = [3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0,
            12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]
    true_runs = [f * scale for f in feet]
    distractors = [40.0 + i * 0.05 for i in range(180)]   # tightly packed band
    result = resolve_scale([_dim(f) for f in feet],
                           tuple(true_runs + distractors))
    assert result.units_per_foot == pytest.approx(scale, rel=1e-6)
    assert result.matched_count == 20
    assert result.total_dimensions == 20
