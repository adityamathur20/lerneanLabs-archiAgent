"""Candidacy bands fitted from owner feedback, never from too little of it."""
import pytest

from archiagent.calibrate import MIN_DRAWINGS, MIN_LABELLED, fit_bands
from archiagent.geometry import candidacy


def labelled(n_wall=40, n_not=40):
    walls = [(0.62 + 0.3 * i / n_wall, True) for i in range(n_wall)]
    others = [(0.10 + 0.3 * i / n_not, False) for i in range(n_not)]
    return walls + others


def test_separable_scores_give_a_band_between_them():
    reject_ceiling, accept_floor = fit_bands(labelled(), drawings=6)
    assert 0.40 <= reject_ceiling <= accept_floor <= 0.62


def test_accepts_stay_precise():
    data = labelled() + [(0.9, False)] * 1        # one confident mistake in 41 high scores
    _, floor = fit_bands(data, drawings=6)
    above = [w for s, w in data if s >= floor]
    assert sum(above) / len(above) >= 0.95


@pytest.mark.parametrize("data, drawings", [(labelled(10, 10), 6), (labelled(), 3)])
def test_too_little_feedback_refuses(data, drawings):
    with pytest.raises(ValueError, match="feedback"):
        fit_bands(data, drawings=drawings)


def test_limits_are_the_specs():
    assert (MIN_LABELLED, MIN_DRAWINGS) == (50, 5)


def test_candidacy_uses_calibrated_bands():
    assert candidacy.band(0.6) == "ambiguous"
    assert candidacy.band(0.6, accept_floor=0.55, reject_ceiling=0.3) == "accept"


def test_a_feedback_file_written_from_another_folder_still_finds_its_manifest(tmp_path):
    import json
    from archiagent.feedback import resolve_manifest
    run = tmp_path / "testRuns" / "batch" / "Plan" / "floor-01"
    run.mkdir(parents=True)
    (run / "floor-01.interpretation.json").write_text("{}")
    fb = tmp_path / "testRuns" / "batch" / "feedback" / "Plan-floor-01.feedback.json"
    fb.parent.mkdir(parents=True)
    fb.write_text("{}")
    found = resolve_manifest(fb, "../testRuns/batch/Plan/floor-01/floor-01.interpretation.json")
    assert found == (run / "floor-01.interpretation.json").resolve()


def test_floors_cut_from_one_sheet_count_as_one_drawing(tmp_path):
    from archiagent.calibrate import drawing_key
    a = {"source_path": "/runs/Plan/floors/floor-01.dxf", "source_sha256": "a"}
    b = {"source_path": "/runs/Plan/floors/floor-02.dxf", "source_sha256": "b"}
    c = {"source_path": "/runs/Other/whole.dxf", "source_sha256": "c"}
    assert drawing_key(a) == drawing_key(b) != drawing_key(c)


def test_new_templates_record_an_absolute_manifest_path(tmp_path):
    import json
    from checks.test_feedback import manifest
    from archiagent import feedback
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.json")
    from pathlib import Path
    assert Path(t["source"]["manifest"]).is_absolute()
