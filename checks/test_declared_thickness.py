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
@pytest.mark.xfail(raises=TypeError, strict=True,
                   reason="select_walls gains the declared-set keywords in Task 3")
def test_a_vetoed_candidate_is_never_sent_to_the_adjudicator():
    seen = []

    def recorder(ps, candidates, units_per_foot):
        seen.extend(candidates)
        return {}, ()

    select_walls(source(), house(), CLASSIFICATION, 1.0, adjudicator=recorder,
                 declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12,
                 thickness_exhaustive=True)
    assert seen == []
