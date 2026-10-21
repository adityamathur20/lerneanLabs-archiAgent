"""A wall's class (site wall, parapet, railing, pool edge ...) travels with it and decides its height and IFC type."""
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.profile_fit import fit_walls_to_profiles
from archiagent.geometry.profiles import WallProfile
from archiagent.geometry.walls import WallSeg


def box(x0, y0, x1, y1, pid="p"):
    return WallProfile(pid, ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)))


def test_wall_class_survives_junction_repair():
    a = WallSeg((0, 0), (10, 0), 0.75, "W", "paired-line", "measured", ("a",), wall_class="site_wall")
    b = WallSeg((5, -5), (5, 5), 0.75, "W", "paired-line", "measured", ("b",))
    graph = resolve_junctions((a, b), snap_in=0, extend_in=0, min_dangle_ft=0)
    assert {w.wall_class for w in graph.walls if "a" in w.source_ids} == {"site_wall"}


def test_wall_class_survives_profile_fit():
    w = WallSeg((0, 0), (10, 0), 0.75, "W", "paired-line", "measured", ("a",), wall_class="parapet")
    fit = fit_walls_to_profiles((w,), (box(3.2, -0.5, 12, 0.5),))
    assert {p.wall_class for p in fit.walls} == {"parapet"}


def test_a_generated_opening_host_inherits_the_class_of_the_wall_it_spans():
    from archiagent.recognition import host_openings
    from archiagent.semantic import SymbolInstance
    import json
    a = WallSeg((0, 0), (10, 0), 0.75, "W", "paired-line", "measured", ("a",), wall_class="site_wall")
    b = WallSeg((14, 0), (24, 0), 0.75, "W", "paired-line", "measured", ("b",), wall_class="site_wall")
    gate = SymbolInstance("g", "door", (12, 0), 4.0, 0.5, evidence="wall-gap-lines", boundary=((10, 0), (14, 0)),
                          properties=(("opening_start_ft", json.dumps([10, 0])), ("opening_end_ft", json.dumps([14, 0]))))
    walls, openings = host_openings((a, b), (gate,), 10.0)
    assert openings and walls[openings[0].host_wall_index].wall_class == "site_wall"
