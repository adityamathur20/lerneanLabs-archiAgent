"""Geometry-first wall candidacy. Authored-from-scratch geometry; no client data."""
import pytest

from archiagent.classify.layers import LayerDecision, candidate_layers
from archiagent.classify.roles import Role


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
