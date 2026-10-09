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
