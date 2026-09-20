"""Geometry-first wall candidacy. Authored-from-scratch geometry; no client data."""
import pytest

from archiagent.classify.layers import LayerDecision, candidate_layers
from archiagent.classify.roles import Role
from dataclasses import replace

from archiagent.geometry.candidacy import (WEIGHTS, band, build_context, candidate_signals, combine,
                                           reconcile_symbols, score_candidates, select_walls, yields_to_walls)
from archiagent.model import Issue
from archiagent.primitives import Primitive
from archiagent.semantic import SymbolInstance
from checks.candidacy_fixtures import CLASSIFICATION, house, projection, shower, showers, source, square, wall


def decision(layer, role, confidence):
    return LayerDecision(layer, role, confidence, "", "llm")


def test_every_layer_is_a_candidate_except_confident_never_wall_roles():
    classification = (decision("WALL", Role.WALL_PARTITION, .9), decision("furni", Role.FURNITURE, .9),
                      decision("PROJECTION", Role.ANNOTATION, .45), decision("STAIR", Role.STAIR, .8),
                      decision("DIM", Role.DIMENSION, .95), decision("TEXT", Role.TEXT_LABEL, .9),
                      decision("SHEET", Role.TITLE_BLOCK, .9), decision("AXIS", Role.GRID, .8))
    names = {"WALL", "furni", "PROJECTION", "STAIR", "DIM", "TEXT", "SHEET", "AXIS", "UNLISTED"}
    assert candidate_layers(classification, names) == {"WALL", "furni", "PROJECTION", "STAIR", "UNLISTED"}


@pytest.mark.parametrize("confidence,kept", [(.69, True), (.70, False), (.95, False)])
def test_a_never_wall_role_excludes_only_at_or_above_the_confidence_floor(confidence, kept):
    assert ("DIM" in candidate_layers((decision("DIM", Role.DIMENSION, confidence),), {"DIM"})) is kept


def test_layer_names_match_case_insensitively():
    assert candidate_layers((decision("dim", Role.DIMENSION, .9),), {"DIM", "Wall"}) == {"Wall"}


def signals_of(w, ps=None, classification=()):
    return dict(candidate_signals(w, build_context(ps or source(), classification, 1.0)))


def test_a_small_nested_outline_with_a_centre_circle_reads_as_a_glyph():
    entities, primitives, walls = shower(0, 0, 0)
    ps = source(primitives, entities)
    assert signals_of(walls[0], ps)["nested_outline"] == -1.0
    assert signals_of(walls[0], ps)["glyph"] == -1.0
    # pairing may record only one face; siblings in the same block instance still count
    one_face = replace(walls[0], source_ids=walls[0].source_ids[:1])
    assert signals_of(one_face, ps)["nested_outline"] == -1.0


def test_nested_closed_outlines_at_building_scale_are_not_glyphs():
    # a perimeter wall drawn as two closed polylines 9in apart is how real envelopes are drawn
    outer = Primitive("line", square(0, 0, 30), "WALL", None, None, "outer", "LWPOLYLINE", True)
    inner = Primitive("line", square(.75, .75, 28.5), "WALL", None, None, "inner", "LWPOLYLINE", True)
    ring = Primitive("curve", ((15, 15), (15.1, 15), (15.1, 15.1)), "WALL", None, None, "c", "CIRCLE", True)
    signals = signals_of(wall((.375, .375), (29.625, .375), ids=("outer", "inner")), source((outer, inner, ring)))
    assert signals["nested_outline"] == 0.0
    assert signals["glyph"] == 0.0


def test_short_runs_from_a_repeated_block_are_penalised_but_long_ones_are_not():
    entities, primitives, walls = showers()
    ps = source(primitives, entities)
    long_run = replace(walls[0], end=(walls[0].start[0] + 12, walls[0].start[1]))
    assert signals_of(walls[0], ps)["instancing"] == -1.0
    assert signals_of(long_run, ps)["instancing"] == 0.0


def test_a_block_inserted_fewer_than_three_times_is_not_a_repeated_symbol():
    entities, primitives, walls = showers(count=2)
    assert signals_of(walls[0], source(primitives, entities))["instancing"] == 0.0


def test_layer_role_is_a_graded_signal_not_a_gate():
    classification = CLASSIFICATION + (LayerDecision("MISC", Role.IGNORE, .6, "", "llm"),)
    role = lambda layer: signals_of(wall((0, 0), (10, 0), layer=layer), classification=classification)["layer_role"]
    assert role("WALL") == pytest.approx(.9)
    assert role("wall") == pytest.approx(.9)
    assert role("furni") == pytest.approx(-.45)
    assert role("MISC") == 0.0
    assert role("UNKNOWN") == 0.0


@pytest.mark.parametrize("length,expected", [(1.0, -1.0), (1.5, -1.0), (4.75, 0.0), (8.0, 1.0), (30.0, 1.0)])
def test_length_ramps_from_glyph_scale_to_wall_scale(length, expected):
    assert signals_of(wall((0, 0), (length, 0)))["length"] == pytest.approx(expected)


def bands(walls, ps=None):
    ctx = build_context(ps or source(), CLASSIFICATION, 1.0)
    return {c.wall: c.band for c in score_candidates(walls, ctx)}


def test_a_boundary_drawn_on_a_furniture_layer_is_accepted():
    assert set(bands(house()).values()) == {"accept"}


def test_every_side_of_every_shower_glyph_is_rejected_even_on_a_wall_layer():
    entities, primitives, glyphs = showers()
    result = bands(house(glyphs), source(primitives, entities))
    assert {result[w] for w in glyphs} == {"reject"}
    assert {result[w] for w in house()} == {"accept"}


def test_a_short_projection_run_touching_the_house_is_ambiguous():
    run = projection()
    assert bands(house((run,)))[run] == "ambiguous"


def test_score_is_the_weighted_mean_mapped_to_zero_one():
    assert combine(tuple((name, 1.0) for name in WEIGHTS)) == pytest.approx(1.0)
    assert combine(tuple((name, -1.0) for name in WEIGHTS)) == pytest.approx(0.0)
    assert combine(tuple((name, 0.0) for name in WEIGHTS)) == pytest.approx(0.5)


@pytest.mark.parametrize("score,expected", [(.65, "accept"), (.649, "ambiguous"), (.35, "reject"), (.351, "ambiguous")])
def test_thresholds_are_decisive_at_the_boundary(score, expected):
    assert band(score) == expected


def test_candidate_ids_are_stable_and_unique():
    ctx = build_context(source(), CLASSIFICATION, 1.0)
    first = [c.id for c in score_candidates(house(), ctx)]
    assert first == [c.id for c in score_candidates(house(), ctx)]
    assert len(set(first)) == len(first)


STAIR_CLASSIFICATION = CLASSIFICATION + (LayerDecision("STAIR", Role.STAIR, .8, "", "llm"),)


def by_wall(decisions):
    return {d.wall: d for d in decisions}


def test_select_walls_keeps_the_boundary_drops_the_glyphs_and_records_every_run():
    entities, primitives, glyphs = showers()
    accepted, decisions, _ = select_walls(source(primitives, entities), house(glyphs), CLASSIFICATION, 1.0)
    assert accepted == house()
    assert len(decisions) == len(house(glyphs))
    glyph_decisions = [by_wall(decisions)[w] for w in glyphs]
    assert {d.verdict for d in glyph_decisions} == {"reject"}
    assert {d.verdict_source for d in glyph_decisions} == {"deterministic"}
    assert "nested_outline" in glyph_decisions[0].reason


def test_an_ambiguous_run_keeps_the_outcome_it_had_before_candidacy():
    stub = wall((30, 10), (33, 10), ids=("s0", "s1"))    # on WALL: the old gate accepted it
    run = projection()                                     # on PROJECTION: never paired before
    accepted, decisions, issues = select_walls(source(), house((stub, run)), CLASSIFICATION, 1.0)
    assert by_wall(decisions)[stub].verdict_source == by_wall(decisions)[run].verdict_source == "ambiguous-default"
    assert stub in accepted and run not in accepted
    assert [i.code for i in issues if i.severity == "warn"] == ["wall_candidate_unresolved"]
    assert [i.code for i in issues if i.severity == "info"] == ["wall_candidates_kept_by_default"]


def test_a_confident_adjudication_settles_an_ambiguous_run_and_a_weak_one_does_not():
    run = projection()
    confident = lambda ps, candidates, upf: ({c.id: ("wall", .9, "parapet") for c in candidates}, ())
    weak = lambda ps, candidates, upf: ({c.id: ("wall", .5, "unsure") for c in candidates}, ())
    accepted, decisions, _ = select_walls(source(), house((run,)), CLASSIFICATION, 1.0, adjudicator=confident)
    assert run in accepted and by_wall(decisions)[run].verdict_source == "adjudicated"
    accepted, _, _ = select_walls(source(), house((run,)), CLASSIFICATION, 1.0, adjudicator=weak)
    assert run not in accepted


def test_only_ambiguous_runs_reach_the_adjudicator_and_its_issues_are_kept():
    seen = []

    def adjudicator(ps, candidates, upf):
        seen.extend(c.wall for c in candidates)
        return {}, (Issue("warn", "x", "wall_adjudication_skipped", "test"),)

    _, _, issues = select_walls(source(), house((projection(),)), CLASSIFICATION, 1.0, adjudicator=adjudicator)
    assert seen == [projection()]
    assert "wall_adjudication_skipped" in {i.code for i in issues}


def test_ladder_runs_are_rejected_only_on_newly_admitted_layers():
    treads = tuple(wall((50, y), (54, y), .8, layer="STAIR", ids=(f"s{y}",)) for y in (0, 2, 4, 6, 8))
    _, decisions, _ = select_walls(source(), treads, STAIR_CLASSIFICATION, 1.0)
    assert {d.verdict_source for d in decisions} == {"ladder-run"}
    on_wall_layer = tuple(replace(w, source_layer="WALL") for w in treads)
    _, decisions, _ = select_walls(source(), on_wall_layer, STAIR_CLASSIFICATION, 1.0)
    assert "ladder-run" not in {d.verdict_source for d in decisions}


def test_only_layer_inferred_nonstructural_symbols_yield_to_walls():
    symbol = lambda kind, evidence: SymbolInstance("s", kind, (0, 0), 1., 1., evidence=evidence)
    assert yields_to_walls(symbol("furniture", "layer-and-geometry"))
    assert yields_to_walls(symbol("stair", "layer-and-geometry"))
    assert not yields_to_walls(symbol("furniture", "block-metadata"))
    assert not yields_to_walls(symbol("door", "layer-and-geometry"))
    assert not yields_to_walls(symbol("column", "layer-and-geometry"))


def test_a_symbol_that_loses_lines_to_a_wall_is_split_not_dropped():
    sofa_line = Primitive("line", ((5, 5), (11, 5), (11, 8), (5, 8)), "furni", None, None,
                          "sofa", "LWPOLYLINE", True)
    mixed = SymbolInstance("furniture-a", "furniture", (15, 20), 30., 3., source_ids=("sofa", "t0"),
                           evidence="layer-and-geometry")          # wall line t0 + a sofa
    consumed = SymbolInstance("furniture-b", "furniture", (15, 0), 30., 0., source_ids=("b0",),
                              evidence="layer-and-geometry")       # nothing but a wall line
    untouched = SymbolInstance("door-c", "door", (2, 2), 3., .2, source_ids=("d",), evidence="block-metadata")
    kept, issues = reconcile_symbols((mixed, consumed, untouched), house(), source((sofa_line,)),
                                     CLASSIFICATION, 1.0)
    assert kept[0] == untouched
    assert [(s.kind, s.source_ids, s.evidence) for s in kept[1:]] == [
        ("furniture", ("sofa",), "layer-and-geometry")]
    assert kept[1].width_ft == pytest.approx(6.0)
    assert {(i.entity, i.code) for i in issues} == {("furniture-a", "symbol_split_by_wall"),
                                                   ("furniture-b", "symbol_superseded_by_wall")}
