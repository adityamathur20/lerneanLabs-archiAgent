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


# --- Task 2: classes become heights and IFC types ----------------------------------------------

from dataclasses import replace

from archiagent.geometry.wall_classes import (POOL_DEPTH_FT, WALL_CLASSES, base_offset_for, height_for,
                                              ifc_type_for)


@pytest.mark.parametrize("cls, h", [("railing", 3.5), ("parapet", 3.5), ("site_wall", 6.0), ("pool_edge", POOL_DEPTH_FT),
                                    ("exterior", 10.0), ("interior", 10.0), ("partition", 10.0), ("", 10.0)])
def test_height_for(cls, h):
    assert height_for(cls, 10.0) == h


def test_a_pool_edge_hangs_below_the_floor_and_everything_else_stands_on_it():
    assert base_offset_for("pool_edge") == -POOL_DEPTH_FT
    assert all(base_offset_for(c) == 0.0 for c in WALL_CLASSES if c != "pool_edge")


def test_ifc_types():
    assert ifc_type_for("railing") == ("IfcRailing", "GUARDRAIL")
    assert ifc_type_for("parapet") == ("IfcWallStandardCase", "PARAPET")
    assert ifc_type_for("site_wall") == ("IfcWallStandardCase", None)


def _classed(model, classes):
    walls = tuple(replace(w, wall_class=c) for w, c in zip(model.walls, classes + ("",) * len(model.walls)))
    return replace(model, walls=walls, openings=(), symbols=())


def test_railing_parapet_and_pool_edge_author_as_their_types_and_validate(tmp_path):
    import ifcopenshell
    from archiagent.ifc.author import author_ifc
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model
    model = _classed(fixture_model(), ("railing", "parapet", "pool_edge"))
    path = author_ifc(model, tmp_path / "t.ifc")
    report = validate_export(path, model)
    assert report["passed"], report["errors"]
    f = ifcopenshell.open(str(path))
    assert len(f.by_type("IfcRailing")) == 1
    assert [w for w in f.by_type("IfcWall") if w.PredefinedType == "PARAPET"]
    pool = [g for g in report["geometry"] if g["name"] == "W002"]
    floor = model.elevation_ft * 0.3048
    assert pool and pool[0]["bounds_m"][2] == pytest.approx(floor - POOL_DEPTH_FT * 0.3048)
    assert pool[0]["bounds_m"][5] == pytest.approx(floor)          # its top is the floor, it hangs below


def test_walls_of_different_class_are_not_one_run():
    from archiagent.ifc.wall_runs import build_runs
    from checks.test_semantic_ifc import fixture_model
    model = fixture_model()
    a = model.walls[0]
    b = replace(a, start=a.end, end=(a.end[0] + 5.0, a.end[1]), wall_class="parapet")
    assert len(build_runs(replace(model, walls=(a, b), junctions=(), openings=(), symbols=())).runs) == 2
