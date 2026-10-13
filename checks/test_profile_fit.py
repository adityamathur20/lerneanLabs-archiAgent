"""A wall that straddles an accepted wall profile is split at the profile's edge.

Every case ends on the same invariant the IFC guard enforces: each wall is
either covered by exactly one profile or clear of all of them.
"""
import math
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString

from archiagent.geometry.profile_fit import fit_walls_to_profiles
from archiagent.geometry.profiles import WallProfile
from archiagent.geometry.walls import WallSeg
from archiagent.ifc.profile_layout import wall_layout
from archiagent.semantic import Opening


def wall(a, b, thickness=0.75):
    return WallSeg(a, b, thickness, "WALL", "paired-line", "measured", ("src",))


def box(x0, y0, x1, y1, pid="p"):
    return WallProfile(pid, ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)))


def layout(walls, profiles):
    """The guard itself: raises when any wall is partly on a profile."""
    return wall_layout(SimpleNamespace(walls=tuple(walls), wall_profiles=tuple(profiles)))


def span(w):
    return sorted((w.start[0], w.end[0]))


def test_wall_clear_of_profiles_is_returned_untouched():
    walls = (wall((0, 0), (10, 0)),)
    fit = fit_walls_to_profiles(walls, (box(0, 5, 10, 6),))
    assert fit.walls == walls
    assert (fit.trimmed, fit.hosted, fit.dropped) == (0, 0, 0)


def test_wall_fully_inside_a_profile_is_returned_untouched():
    walls = (wall((1, 0), (9, 0)),)
    profiles = (box(0, -1, 10, 1),)
    fit = fit_walls_to_profiles(walls, profiles)
    assert fit.walls == walls
    assert layout(fit.walls, profiles)[2] == {0: 0}


def test_wall_running_past_a_profile_is_split_at_its_edge():
    profiles = (box(3.2, -0.5, 12, 0.5),)
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    _, _, mapping = layout(fit.walls, profiles)         # must not raise
    hosted = [fit.walls[i] for i in mapping]
    separate = [w for i, w in enumerate(fit.walls) if i not in mapping]
    assert len(hosted) == 1 and span(hosted[0]) == pytest.approx([3.2, 10.0])
    assert len(separate) == 1 and span(separate[0]) == pytest.approx([0.0, 3.2])
    assert fit.trimmed == 1 and fit.hosted == 1


def test_side_sliver_trims_the_thickness_and_drops_the_sliver():
    # 3% of the thickness lies on the profile, along the whole wall.
    profiles = (box(-1, 0.3525, 11, 2),)
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    layout(fit.walls, profiles)
    assert len(fit.walls) == 1
    kept = fit.walls[0]
    assert span(kept) == pytest.approx([0.0, 10.0])
    assert kept.thickness_ft == pytest.approx(0.375 + 0.3525)
    # The centreline moved with the face that was cut, not the other one.
    assert kept.start[1] == pytest.approx((-0.375 + 0.3525) / 2)
    assert fit.dropped == 1


def test_stub_shorter_than_a_quarter_foot_is_dropped():
    profiles = (box(0.1, -0.5, 12, 0.5),)
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    layout(fit.walls, profiles)
    assert len(fit.walls) == 1 and span(fit.walls[0]) == pytest.approx([0.1, 10.0])
    assert fit.dropped == 1


def test_two_profiles_along_one_wall_leave_three_free_pieces_and_two_hosted():
    profiles = (box(2, -0.5, 3, 0.5, "a"), box(6, -0.5, 7, 0.5, "b"))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    _, _, mapping = layout(fit.walls, profiles)
    assert len(fit.walls) == 5 and len(mapping) == 2
    free = sorted(span(w) for i, w in enumerate(fit.walls) if i not in mapping)
    assert free == [pytest.approx([0, 2]), pytest.approx([3, 6]), pytest.approx([7, 10])]


def test_ragged_overlap_drops_the_covered_part_instead_of_guessing():
    # A triangle clips the wall: the covered part is not a rectangle.
    triangle = WallProfile("tri", ((4, -1), (6, -1), (4, 1), (4, -1)))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), (triangle,))
    layout(fit.walls, (triangle,))
    assert fit.dropped >= 1


def test_oblique_wall_against_an_axis_profile_still_passes_the_guard():
    a = (0.0, 0.0)
    b = (10 * math.cos(math.radians(30)), 10 * math.sin(math.radians(30)))
    profiles = (box(3, -2, 8, 1.5),)
    fit = fit_walls_to_profiles((wall(a, b),), profiles)
    layout(fit.walls, profiles)


def test_opening_on_the_covered_part_keeps_a_wall_that_can_host_it():
    profiles = (box(3.2, -0.5, 12, 0.5),)
    door = Opening("o", "door", 0, (5.0, 0.0), (6.0, 0.0), 7.0, 0.0, "sym", ("src",))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles, openings=(door,))
    axis_covers = [LineString((w.start, w.end)).buffer(1e-5).covers(LineString((door.start, door.end)))
                   for w in fit.walls]
    assert sum(axis_covers) == 1


def test_pieces_keep_their_source_evidence():
    profiles = (box(3.2, -0.5, 12, 0.5),)
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    assert {(w.source_layer, w.detector, w.source_ids) for w in fit.walls} == {
        ("WALL", "paired-line", ("src",))}


def test_touching_a_profile_edge_is_not_an_overlap():
    walls = (wall((0, 0), (10, 0)),)
    profiles = (box(-1, 0.375, 11, 2),)       # shares the wall's long face
    fit = fit_walls_to_profiles(walls, profiles)
    assert fit.walls == walls
    layout(fit.walls, profiles)


def test_guard_ignores_coordinate_noise_but_not_a_real_overlap():
    noise = (box(-1, 0.375 - 1e-9, 11, 2),)          # 1 nm onto the wall: rounding, not overlap
    layout((wall((0, 0), (10, 0)),), noise)
    real = (box(-1, 0.375 - 1e-3, 11, 2),)           # 1 mm
    with pytest.raises(ValueError, match="partially overlaps"):
        layout((wall((0, 0), (10, 0)),), real)


def test_a_wall_moved_by_junction_repair_still_counts_as_covered():
    # Junction repair may move an end by up to 1e-4 ft after the walls are fitted.
    walls = (wall((0, 0), (10, 0.0001)),)
    profiles = (box(-1, -0.375, 11, 0.375),)
    assert fit_walls_to_profiles(walls, profiles).walls == walls
    assert layout(walls, profiles)[2] == {0: 0}


def opening(a, b, oid="o"):
    return Opening(oid, "door", 0, a, b, 7.0, 0.0, "sym", ("src",))


def test_opening_host_spanning_jambs_is_trimmed_and_the_opening_narrowed_to_the_gap():
    # A generated host runs jamb to jamb; the jambs are accepted profiles.
    profiles = (box(-3, -0.5, 0.5, 0.5, "left"), box(3.5, -0.5, 7, 0.5, "right"))
    door = opening((0.0, 0.0), (4.0, 0.0))
    fit = fit_walls_to_profiles((wall((0, 0), (4, 0)),), profiles, openings=(door,))
    layout(fit.walls, profiles)
    (narrowed,) = fit.openings
    assert (*narrowed.start, *narrowed.end) == pytest.approx((0.5, 0.0, 3.5, 0.0))
    assert narrowed.id == "o" and narrowed.source_ids == ("src",)
    assert fit.lost_openings == ()
    assert any(LineString((w.start, w.end)).buffer(1e-5).covers(LineString((narrowed.start, narrowed.end)))
               for w in fit.walls)


def test_opening_no_piece_holds_half_of_is_reported_lost_not_shrunk_to_a_slit():
    profiles = (box(-1, -0.5, 1.5, 0.5, "a"), box(2.5, -0.5, 5, 0.5, "b"))   # pieces 1.5 / 1 / 1.5 ft
    door = opening((0.0, 0.0), (4.0, 0.0))
    fit = fit_walls_to_profiles((wall((0, 0), (4, 0)),), profiles, openings=(door,))
    assert fit.lost_openings == ("o",)
    assert fit.openings == (door,)      # kept as given: the pipeline drops it with its host


def test_opening_follows_a_wall_whose_thickness_was_trimmed():
    profiles = (box(-1, 0.3525, 11, 2),)
    door = opening((2.0, 0.0), (3.0, 0.0))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles, openings=(door,))
    (moved,) = fit.openings
    kept = fit.walls[0]
    assert moved.start[1] == pytest.approx(kept.start[1])
    assert LineString((kept.start, kept.end)).buffer(1e-5).covers(LineString((moved.start, moved.end)))
    assert fit.lost_openings == ()


def test_openings_on_untouched_walls_pass_through_identically():
    door = opening((2.0, 0.0), (3.0, 0.0))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), (box(0, 5, 10, 6),), openings=(door,))
    assert fit.openings == (door,)


def test_fitted_walls_author_to_a_valid_ifc(tmp_path):
    from dataclasses import replace
    from archiagent.ifc.author import author_ifc
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    profiles = (box(3.2, -0.5, 12, 0.5, "jamb"), box(-1, 0.3525, 2, 2, "return"))
    fit = fit_walls_to_profiles((wall((0, 0), (10, 0)),), profiles)
    model = replace(fixture_model(), walls=fit.walls, wall_profiles=profiles, openings=(), symbols=(),
                    junctions=(), unresolved=(), spaces=(), footprints=())
    path = author_ifc(model, tmp_path / "fitted.ifc")
    report = validate_export(path, model)
    assert report["passed"], report["errors"]
