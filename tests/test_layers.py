import pytest

from archiagent.classify.layers import (WALL_ROLES, Classification,
                                        layers_for_roles)
from archiagent.classify.roles import Role


def test_role_values_are_stable_strings():
    assert Role.WALL_STRUCTURAL.value == "wall_structural"
    assert Role.BEAM_OVERHEAD.value == "beam_overhead"
    assert Role("ignore") is Role.IGNORE


def test_wall_roles_include_structural_and_partition_but_not_beams():
    assert Role.WALL_STRUCTURAL in WALL_ROLES
    assert Role.WALL_PARTITION in WALL_ROLES
    assert Role.BEAM_OVERHEAD not in WALL_ROLES


def test_layers_for_roles_selects_matching_layers():
    c: Classification = {
        "WALLS": (Role.WALL_STRUCTURAL, 0.98),
        "walll": (Role.WALL_PARTITION, 0.9),
        "BEAM": (Role.BEAM_OVERHEAD, 0.95),
        "0": (Role.IGNORE, 0.5),
    }
    assert layers_for_roles(c, WALL_ROLES) == {"WALLS", "walll"}
    assert layers_for_roles(c, {Role.BEAM_OVERHEAD}) == {"BEAM"}


def test_layers_for_roles_honours_a_confidence_floor():
    c: Classification = {
        "WALLS": (Role.WALL_STRUCTURAL, 0.98),
        "maybe": (Role.WALL_STRUCTURAL, 0.30),
    }
    assert layers_for_roles(c, WALL_ROLES, min_confidence=0.5) == {"WALLS"}
