"""Geometry-first wall candidacy. Authored-from-scratch geometry; no client data."""
import pytest

from archiagent.classify.layers import LayerDecision, candidate_layers
from archiagent.classify.roles import Role
from dataclasses import replace

from archiagent.geometry.candidacy import WEIGHTS, band, build_context, candidate_signals, combine, score_candidates
from archiagent.primitives import Primitive
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
