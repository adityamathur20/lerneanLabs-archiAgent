"""The escalation ceiling is a cost/coverage dial, not a fixed constant.

Measured on Floor Plan.dxf (39 layers): a cap of 6 dropped 17 candidates,
among them WALLS and RCC WALL -- real wall layers the vision stage never got
to look at. Each escalated layer costs one rendered image in the request, so
the right ceiling depends on the drawing and the budget.
"""
from archiagent.classify.escalate import (ESCALATION_CAP, escalation_candidates,
                                          select_for_escalation)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=1.0, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 1, 1),
                      length_p10=0.0, length_p50=1.0, length_p90=1.0,
                      entity_share=share)


def _many(n):
    d = tuple(LayerDecision(f"L{i:02}", Role.FURNITURE, 0.5, "", "llm")
              for i in range(n))
    s = tuple(_s(f"L{i:02}", (n - i) / 100) for i in range(n))
    return d, s


def test_the_default_ceiling_is_twenty():
    assert ESCALATION_CAP == 20


def test_the_default_is_applied_when_no_cap_is_given():
    d, s = _many(30)
    assert len(select_for_escalation(d, s)) == ESCALATION_CAP


def test_an_explicit_cap_overrides_the_default():
    d, s = _many(30)
    assert len(select_for_escalation(d, s, cap=5)) == 5
    assert len(select_for_escalation(d, s, cap=25)) == 25


def test_the_cap_keeps_the_largest_layers():
    """Whatever the ceiling, it must keep the layers holding most of the
    drawing -- those are the ones a wrong answer costs most on."""
    d, s = _many(30)
    picked = [n for n, _ in select_for_escalation(d, s, cap=3)]
    assert picked == ["L00", "L01", "L02"]


def test_a_cap_above_the_candidate_count_takes_everything():
    d, s = _many(4)
    assert len(select_for_escalation(d, s, cap=99)) == 4


def test_the_uncapped_list_is_unaffected_by_the_cap():
    """escalation_candidates stays uncapped so the caller can always report
    what the ceiling dropped."""
    d, s = _many(30)
    assert len(escalation_candidates(d, s)) == 30
