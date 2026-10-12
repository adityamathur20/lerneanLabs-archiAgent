"""A declared thickness set replaces the set candidacy would infer."""
from archiagent.geometry.candidacy import (MODE_TOLERANCE_FT, build_context,
                                           score_candidates)
from checks.candidacy_fixtures import CLASSIFICATION, source, wall


def signals_for(walls, **kwargs):
    ctx = build_context(source(), CLASSIFICATION, 1.0, **kwargs)
    return {c.wall.thickness_ft: dict(c.signals)["thickness"]
            for c in score_candidates(walls, ctx)}


def test_a_declared_set_replaces_the_inferred_one():
    # Six-inch junk carries most of the length, so inference would make 6in the
    # mode and penalise the real 4in and 8in walls. The real walls stay under
    # MODE_SHARE (10%) of the total run length, or inference would adopt them.
    walls = [wall((0, 0), (45, 0), 0.5), wall((0, 5), (45, 5), 0.5),
             wall((0, 10), (10, 10), 4 / 12), wall((0, 15), (10, 15), 8 / 12)]
    inferred = signals_for(walls)
    assert inferred[4 / 12] == -0.5
    assert inferred[0.5] == 1.0

    declared = signals_for(walls, declared_thickness_ft=(4 / 12, 8 / 12),
                           thickness_tolerance_ft=0.5 / 12)
    assert declared[4 / 12] == 1.0
    assert declared[8 / 12] == 1.0
    assert declared[0.5] == -0.5


def test_the_declared_tolerance_is_honoured():
    inside = signals_for([wall((0, 0), (10, 0), 4.4 / 12)],
                         declared_thickness_ft=(4 / 12,),
                         thickness_tolerance_ft=0.5 / 12)
    assert inside[4.4 / 12] == 1.0
    outside = signals_for([wall((0, 0), (10, 0), 4.6 / 12)],
                          declared_thickness_ft=(4 / 12,),
                          thickness_tolerance_ft=0.5 / 12)
    assert outside[4.6 / 12] == -0.5


def test_inference_keeps_its_original_one_inch_tolerance():
    ctx = build_context(source(), CLASSIFICATION, 1.0)
    assert ctx.thickness_tolerance_ft == MODE_TOLERANCE_FT
    assert ctx.declared_thickness_ft == ()
    assert ctx.thickness_exhaustive is False


from archiagent.geometry.candidacy import ACCEPT_FLOOR
from checks.candidacy_fixtures import house


def banded(walls, **kwargs):
    ctx = build_context(source(), CLASSIFICATION, 1.0, **kwargs)
    return {c.wall.thickness_ft: (c.band, c.score) for c in score_candidates(walls, ctx)}


def test_an_exhaustive_set_vetoes_a_thickness_it_does_not_contain():
    # The house is a closed, connected 9in rectangle, so it scores well.
    walls = house()
    declared = {"declared_thickness_ft": (4 / 12, 8 / 12),
                "thickness_tolerance_ft": 0.5 / 12}

    scored = banded(walls, **declared)
    assert any(score >= ACCEPT_FLOOR for _, score in scored.values())

    vetoed = banded(walls, **declared, thickness_exhaustive=True)
    assert {band for band, _ in vetoed.values()} == {"reject"}
    # The pre-veto score is retained so a reviewer can see what was removed.
    assert vetoed[0.75][1] == scored[0.75][1]


def test_without_the_exhaustive_flag_the_scored_band_stands():
    walls = house()
    scored = banded(walls, declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12)
    assert "reject" not in {band for band, _ in scored.values()}


def test_an_exhaustive_set_still_accepts_a_declared_thickness():
    walls = [wall(a, b, 8 / 12) for a, b in
             (((0, 20), (30, 20)), ((30, 0), (30, 20)), ((0, 0), (30, 0)), ((0, 0), (0, 20)))]
    kept = banded(walls, declared_thickness_ft=(4 / 12, 8 / 12),
                  thickness_tolerance_ft=0.5 / 12, thickness_exhaustive=True)
    assert "reject" not in {band for band, _ in kept.values()}


def test_the_scoring_weights_and_normalisation_are_unchanged():
    # Pinned deliberately. combine() divides by sum(WEIGHTS.values()), so
    # changing any single weight rescales every score and re-bands every
    # candidate in every drawing against floors tuned to this normalisation.
    # The declared-thickness veto exists precisely to avoid touching these.
    from archiagent.geometry.candidacy import REJECT_CEILING, WEIGHTS
    assert WEIGHTS == {"layer_role": 1.0, "length": 1.0, "connectivity": 2.0,
                       "closure": 2.0, "thickness": 0.5, "instancing": 1.5,
                       "glyph": 1.5, "nested_outline": 2.5}
    assert sum(WEIGHTS.values()) == 12.0
    assert (ACCEPT_FLOOR, REJECT_CEILING) == (0.65, 0.35)


import pytest

from archiagent.geometry.candidacy import select_walls


# Adjudicator is a plain Callable[[PrimitiveSet, tuple[ScoredCandidate, ...], float], ...]
# returning (verdicts, issues) -- not an object with a method.
def test_a_vetoed_candidate_is_never_sent_to_the_adjudicator():
    seen = []

    def recorder(ps, candidates, units_per_foot):
        seen.extend(candidates)
        return {}, ()

    select_walls(source(), house(), CLASSIFICATION, 1.0, adjudicator=recorder,
                 declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12,
                 thickness_exhaustive=True)
    assert seen == []


def test_select_walls_passes_the_declared_set_through():
    # CandidateDecision carries `verdict` ("accept" | "reject"), not `band`.
    kept, decisions, _ = select_walls(
        source(), house(), CLASSIFICATION, 1.0,
        declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12,
        thickness_exhaustive=True)
    assert kept == ()
    assert {d.verdict for d in decisions} == {"reject"}
    assert all(d.verdict_source == "deterministic" for d in decisions)


from archiagent.geometry.candidacy import thickness_scale_check


def test_a_uniform_factor_is_reported_when_nothing_matches():
    # Authored in inches but read as feet: every thickness is 12x too large.
    walls = [wall((0, 0), (10, 0), 4.0), wall((0, 5), (10, 5), 8.0)]
    factor = thickness_scale_check(walls, (4 / 12, 8 / 12), 0.5 / 12)
    assert factor == pytest.approx(12.0, rel=0.02)


def test_no_factor_is_reported_when_thicknesses_already_fit():
    walls = [wall((0, 0), (10, 0), 4 / 12), wall((0, 5), (10, 5), 8 / 12)]
    assert thickness_scale_check(walls, (4 / 12, 8 / 12), 0.5 / 12) is None


def test_no_factor_is_reported_without_a_declared_set():
    assert thickness_scale_check([wall((0, 0), (10, 0), 4.0)], (), 0.5 / 12) is None


from types import SimpleNamespace

from archiagent.pipeline import extract_from_dxf
from checks.candidacy_fixtures import house_faces


def run_house(**kwargs):
    ps = source(house_faces())
    return extract_from_dxf(ps, SimpleNamespace(classify=lambda stats: CLASSIFICATION),
                            units_per_foot=1.0, **kwargs)


def issue_codes(model):
    return {i.code for i in model.issues}


def test_the_pipeline_warns_when_a_declared_set_fits_only_after_a_rescale():
    # The 9in house read as if it were 12x thinner: a declared 0.75in set fits
    # only after dividing the observed thickness by 12.
    model = run_house(declared_thickness_ft=(0.75 / 12,), thickness_tolerance_ft=0.5 / 12)
    warning = next(i for i in model.issues if i.code == "declared_thickness_scale_mismatch")
    assert warning.severity == "warn"
    assert "12" in warning.msg
    assert "units_per_foot" not in warning.msg     # the flag no longer exists


def test_the_pipeline_is_silent_when_the_declared_set_fits():
    model = run_house(declared_thickness_ft=(0.75,), thickness_tolerance_ft=0.5 / 12)
    assert "declared_thickness_scale_mismatch" not in issue_codes(model)


def test_the_pipeline_is_silent_without_a_declared_set():
    assert "declared_thickness_scale_mismatch" not in issue_codes(run_house())
