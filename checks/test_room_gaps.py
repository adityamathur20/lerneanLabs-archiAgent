"""Room detection closes door-sized gaps between wall ends, and says it did."""
from dataclasses import replace
from types import SimpleNamespace

from archiagent.geometry.room_gaps import RoomGap, find_room_gaps, gap_edges, gaps_on_boundary
from archiagent.geometry.spaces import detect_spaces, room_graph_with_openings
from archiagent.pipeline import extract_from_dxf
from archiagent.semantic import SymbolInstance
from checks.candidacy_fixtures import CLASSIFICATION, faces, source, wall


def room_with_bottom_gap(gap=3.0):
    """A 10 x 10 ft room whose bottom wall stops `gap` ft short of the middle."""
    left = (10 - gap) / 2
    return [wall((0, 0), (left, 0), .5), wall((10 - left, 0), (10, 0), .5),
            wall((10, 0), (10, 10), .5), wall((10, 10), (0, 10), .5), wall((0, 10), (0, 0), .5)]


def door_symbol(boundary):
    return SymbolInstance("door-1", "door", (5, 0), 3, 0, 0.0, "unknown", ("src-door",), "layer-and-geometry",
                          .9, tuple(boundary))


def test_two_collinear_facing_ends_make_one_gap():
    gaps = find_room_gaps(room_with_bottom_gap(3.0))
    assert len(gaps) == 1                                  # both ends meet in one edge, not two
    assert gaps[0].kind == "collinear"
    assert abs(gaps[0].length_ft - 3.0) < 1e-9


def test_a_gap_wider_than_the_limit_stays_open():
    assert find_room_gaps(room_with_bottom_gap(9.0), max_gap_ft=8.0) == ()
    assert len(find_room_gaps(room_with_bottom_gap(9.0), max_gap_ft=10.0)) == 1


def test_a_closed_room_has_no_gaps():
    walls = room_with_bottom_gap(0.0)
    assert find_room_gaps([wall((0, 0), (10, 0), .5), *walls[2:]]) == ()


def test_a_wall_end_facing_the_end_of_a_perpendicular_wall_is_a_corner_gap():
    walls = [wall((0, 0), (10, 0)), wall((0, 10), (0, 0)), wall((10, 10), (10, 0)),
             wall((0, 10), (3, 10))]                          # the top wall stops 7 ft short of the right wall
    gaps = find_room_gaps(walls, max_gap_ft=8.0)
    assert [(g.kind, round(g.length_ft, 6)) for g in gaps] == [("corner", 7.0)]


def test_a_wall_ending_short_of_the_side_of_another_is_stops_short():
    walls = [wall((0, 0), (10, 0)), wall((5, 6), (5, 2))]     # the vertical wall stops 2 ft above the long one
    gaps = find_room_gaps(walls)
    assert [g.kind for g in gaps] == ["stops-short"]
    assert abs(gaps[0].length_ft - 2.0) < 1e-9


def test_ends_that_nearly_meet_at_a_corner_are_a_corner_gap():
    walls = [wall((0, 0), (5, 0)), wall((5.8, 0.4), (5.8, 6))]
    gaps = find_room_gaps(walls)
    assert [g.kind for g in gaps] == ["corner"]


def test_a_door_symbol_on_the_gap_is_the_evidence_and_wins_over_geometry():
    symbol = door_symbol(((3.5, -1.0), (6.5, -1.0), (6.5, 1.0), (3.5, 1.0)))
    gaps = find_room_gaps(room_with_bottom_gap(3.0), symbols=(symbol,))
    assert [g.kind for g in gaps] == ["door-window"]
    assert "door-1" in gaps[0].source_ids


def test_a_wall_alongside_the_end_but_not_ahead_of_it_is_not_a_target():
    walls = [wall((0, 0), (5, 0)), wall((0, 1), (5, 1))]      # parallel and 1 ft apart: nothing lies ahead
    assert find_room_gaps(walls) == ()


def test_the_gap_becomes_a_virtual_edge_with_no_thickness_and_closes_the_room():
    walls = room_with_bottom_gap(3.0)
    assert detect_spaces(room_graph_with_openings(walls, ()), clear_boundary=True) == ()
    gaps = find_room_gaps(walls)
    edges = gap_edges(gaps)
    assert all(e.thickness_ft == 0.0 and e.detector == "virtual-opening" for e in edges)
    closed = detect_spaces(room_graph_with_openings(walls, (), gaps=gaps), clear_boundary=True)
    assert len(closed) == 1
    assert len(walls) == 5                                 # no wall was added or moved


def test_a_space_knows_how_many_assumed_gaps_bound_it():
    walls = room_with_bottom_gap(3.0)
    gaps = find_room_gaps(walls)
    space = detect_spaces(room_graph_with_openings(walls, (), gaps=gaps), clear_boundary=True)[0]
    assert gaps_on_boundary(space.boundary, gaps) == 1


# -- through the pipeline ---------------------------------------------------------------------

def run_room(gap_ft, **kwargs):
    left = (10 - gap_ft) / 2
    drawn = (faces((0, 0), (left, 0), .5, "WALL", "bl") + faces((10 - left, 0), (10, 0), .5, "WALL", "br")
             + faces((10, 0), (10, 10), .5, "WALL", "r") + faces((10, 10), (0, 10), .5, "WALL", "t")
             + faces((0, 10), (0, 0), .5, "WALL", "l"))
    return extract_from_dxf(source(drawn), SimpleNamespace(classify=lambda stats: CLASSIFICATION),
                            units_per_foot=1.0, **kwargs)


def test_the_pipeline_closes_a_doorway_sized_gap_and_flags_it():
    model = run_room(3.0)
    assert len(model.spaces) == 1
    assert len(model.room_gaps) == 1
    issue = next(i for i in model.issues if i.code == "room_gaps_assumed")
    assert issue.severity == "info"
    assert "1" in issue.msg


def test_zero_switches_it_off_and_nothing_else_changes():
    off = run_room(3.0, room_gap_ft=0.0)
    on = run_room(3.0)
    assert off.spaces == () and off.room_gaps == ()
    assert [i for i in off.issues if i.code == "room_gaps_assumed"] == []
    assert on.walls == off.walls                           # only the rooms differ


def test_a_gap_over_the_limit_is_not_closed_by_the_pipeline():
    model = run_room(9.0)
    assert model.spaces == () and model.room_gaps == ()


# -- command line ------------------------------------------------------------------------------

import math

import pytest

from archiagent.cli import _parser, _validate_options

BASE = ["--dxfFilePath", "plan.dxf", "--outputDir", "out"]


def parse(*extra):
    return _parser().parse_args(BASE + list(extra))


def test_the_default_is_the_documented_limit():
    assert parse().room_gap_ft == 8.0


def test_zero_is_accepted_and_means_off():
    _validate_options(parse("--room-gap-ft", "0"))


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "40.5"])
def test_a_limit_that_is_negative_not_finite_or_beyond_forty_feet_is_refused(bad):
    with pytest.raises(ValueError, match="--room-gap-ft"):
        _validate_options(parse("--room-gap-ft", bad))


def test_a_replay_cannot_change_it(tmp_path):
    from archiagent.cli import main
    assert main(["--replay-manifest", "m.json", "--dxfFilePath", "p.dxf", "--outputDir", str(tmp_path / "o"),
                 "--room-gap-ft", "4"]) != 0


# -- the IFC says which rooms rest on an assumed closure -------------------------------------

def test_an_ifc_space_records_how_many_assumed_gaps_bound_it(tmp_path):
    import ifcopenshell
    import ifcopenshell.util.element
    from archiagent.ifc.author import author_ifc
    out = author_ifc(run_room(3.0), tmp_path / "room.ifc")
    space = ifcopenshell.open(str(out)).by_type("IfcSpace")[0]
    pset = ifcopenshell.util.element.get_psets(space)["ArchiAgent_Provenance"]
    assert pset["ClosedGaps"] == 1


def test_a_room_with_no_assumed_gap_has_no_such_property(tmp_path):
    import ifcopenshell
    import ifcopenshell.util.element
    from archiagent.ifc.author import author_ifc
    out = author_ifc(run_room(0.0), tmp_path / "room.ifc")
    space = ifcopenshell.open(str(out)).by_type("IfcSpace")[0]
    assert "ClosedGaps" not in ifcopenshell.util.element.get_psets(space)["ArchiAgent_Provenance"]
