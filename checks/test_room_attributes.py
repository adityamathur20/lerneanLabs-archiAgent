"""What a room label says about the room, and what that means for the model.

"LIVING ROOM BELOW" on an upper floor is a void: no slab, and the thin line
bounding it is a railing, not a ceiling-high wall. Levels and clear heights
are stated in the label and were being ignored.
"""
from dataclasses import replace

import pytest

from archiagent.geometry.room_attributes import (RAILING_HEIGHT_FT, label_attributes, railing_wall_indices,
                                                 space_attributes)
from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import TextItem


def text(s, x, y):
    return TextItem(s, (x - 1, y - 1, x + 1, y + 1), "TEXT")


def square(x0, y0, x1, y1):
    return Space(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), abs((x1 - x0) * (y1 - y0)))


def wall(a, b, t=0.1):
    return WallSeg(a, b, t, "WALL", "paired-line", "measured")


# --- parsing --------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, level, clear, double, below", [
    ("MASTER BEDROOM 14'-3\" x 16'-3\" LVL + 17'-0\"", 17.0, None, False, False),
    ("LIVING AREA 22' x 19'-10\" Clear Ht. - 12' LVL + 4'-6\"", 4.5, 12.0, False, False),
    ("LIVING ROOM BELOW 14'-3\" x 15'-9\"", None, None, False, True),
    ("FOYER (DOUBLE HIGHT) 5'-0\" x 15'-3\"", None, None, True, False),
    ("VESTIBULE 11'-0\" x 9'-0\" (DOUBLE HEIGHT)", None, None, True, False),
    ("OPEN TO SKY", None, None, False, True),
])
def test_label_attributes(raw, level, clear, double, below):
    a = label_attributes(raw)
    assert (a.level_offset_ft, a.clear_height_ft, a.double_height, a.below) == (level, clear, double, below)


def test_a_plain_room_label_has_no_attributes():
    assert label_attributes("BEDROOM-1 17'-5\" x 12'-6\"") is None


def test_mtext_formatting_codes_do_not_hide_the_words():
    a = label_attributes("\\A1;\\pxqc;{\\fArial|b0;LIVING ROOM BELOW}\\P14'-3\"x15'-9\"")
    assert a is not None and a.below


# --- which room a label belongs to ----------------------------------------------------------


def test_a_label_belongs_to_the_room_that_contains_it_and_gives_it_a_name():
    spaces = (square(0, 0, 10, 10), square(10, 0, 20, 10))
    texts = (text("LIVING ROOM BELOW", 15, 5), text("HALL", 5, 5))
    out = space_attributes(spaces, texts, units_per_foot=1.0)
    assert out[0].label == "HALL" and out[0].below is False
    assert out[1].label == "LIVING ROOM BELOW" and out[1].below is True


def test_units_per_foot_scales_text_positions_into_feet():
    spaces = (square(0, 0, 10, 10),)
    out = space_attributes(spaces, (text("VOID BELOW", 60, 60),), units_per_foot=12.0)
    assert out[0].below is True


def test_a_room_without_a_label_has_none():
    assert space_attributes((square(0, 0, 10, 10),), (), units_per_foot=1.0) == (None,)


# --- railings -------------------------------------------------------------------------------


def test_a_thin_wall_on_a_void_edge_is_a_railing_and_a_thick_one_is_not():
    void = square(0, 0, 10, 10)
    walls = (wall((0, 0), (10, 0), 0.25),          # thin, on the void's edge
             wall((10, 0), (10, 10), 0.75),        # a real wall, on the edge
             wall((20, 0), (30, 0), 0.25))         # thin, nowhere near
    assert railing_wall_indices(walls, (void.boundary,)) == {0}


def test_railing_height_is_a_guard_rail_not_a_storey():
    assert 3.0 <= RAILING_HEIGHT_FT <= 4.0


# --- authoring --------------------------------------------------------------------------------


def test_a_railing_is_authored_at_its_own_height_and_validates(tmp_path):
    import ifcopenshell
    import ifcopenshell.util.element as util
    from archiagent.ifc.author import author_ifc
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    model = fixture_model()
    low = replace(model.walls[0], height_ft=RAILING_HEIGHT_FT)
    model = replace(model, walls=(low, *model.walls[1:]), openings=(), symbols=())
    path = author_ifc(model, tmp_path / "railing.ifc")
    report = validate_export(path, model)
    assert report["passed"], report["errors"]
    f = ifcopenshell.open(str(path))
    heights = {round(float(util.get_psets(w).get("ArchiAgent_Provenance", {}).get("HeightFt", 0)), 2)
               for w in f.by_type("IfcWall")}
    assert RAILING_HEIGHT_FT in heights and model.wall_height_ft in heights


def test_collinear_walls_of_different_height_are_not_one_run():
    from checks.test_semantic_ifc import fixture_model
    from archiagent.ifc.wall_runs import build_runs
    model = fixture_model()
    a = model.walls[0]
    b = replace(a, start=a.end, end=(a.end[0] + 5.0, a.end[1]), height_ft=RAILING_HEIGHT_FT)
    straight = replace(model, walls=(a, replace(b, height_ft=None)), junctions=(), openings=(), symbols=())
    # the two meet end to end; with equal heights they may share a run, with different heights never
    mixed = replace(straight, walls=(a, b))
    assert len(build_runs(mixed).runs) == 2


# --- a void that is not a closed room ----------------------------------------------------------


def _ps(texts, *lines):
    from archiagent.primitives import Primitive, PrimitiveSet
    prims = tuple(Primitive("line", ((x0, y0), (x1, y1)), "ANY", None, None, f"l{i}")
                  for i, (x0, y0, x1, y1) in enumerate(lines))
    return PrimitiveSet(prims, tuple(texts), 100.0, 100.0, "t.dxf", "s" * 64)


def _box(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


def test_label_dimensions_are_read_in_feet():
    from archiagent.geometry.room_attributes import label_dimensions_ft
    assert label_dimensions_ft("LIVING ROOM BELOW 14'-3\" x 15'-9\"") == pytest.approx((14.25, 15.75))
    assert label_dimensions_ft("DRESS 8'-0\"x7'-3\"") == pytest.approx((8.0, 7.25))
    assert label_dimensions_ft("LIVING ROOM BELOW") is None


def test_a_below_label_finds_the_rectangle_its_stated_size_fits():
    from archiagent.geometry.room_attributes import below_label_regions
    # 14'-3" x 15'-9" drawn with a 3 in error on one side, among other lines
    lines = _box(0, 0, 14.25, 15.5) + _box(-6, -6, 30, 30)
    ps = _ps([text("LIVING ROOM BELOW 14'-3\" x 15'-9\"", 7, 7.5)], *lines)
    (ring,) = below_label_regions(ps, 1.0)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((0, 0, 14.25, 15.5))


def test_a_below_label_whose_size_fits_nothing_makes_no_void():
    from archiagent.geometry.room_attributes import below_label_regions
    ps = _ps([text("LIVING ROOM BELOW 14'-3\" x 15'-9\"", 7, 7.5)], *_box(0, 0, 40, 40))
    assert below_label_regions(ps, 1.0) == ()


def test_a_below_label_without_a_stated_size_makes_no_void():
    from archiagent.geometry.room_attributes import below_label_regions
    ps = _ps([text("LIVING ROOM BELOW", 7, 7.5)], *_box(0, 0, 14.25, 15.75))
    assert below_label_regions(ps, 1.0) == ()


def test_a_size_in_its_own_text_item_next_to_the_label_is_used():
    from archiagent.geometry.room_attributes import below_label_regions
    lines = _box(0, 0, 14.25, 15.75)
    ps = _ps([text("LIVING ROOM BELOW", 7, 8.5), text("14'-3\" x 15'-9\"", 7, 7.0)], *lines)
    assert len(below_label_regions(ps, 1.0)) == 1


def test_a_size_text_far_away_is_not_used():
    from archiagent.geometry.room_attributes import below_label_regions
    ps = _ps([text("LIVING ROOM BELOW", 7, 8.5), text("14'-3\" x 15'-9\"", 7, 40.0)], *_box(0, 0, 14.25, 15.75))
    assert below_label_regions(ps, 1.0) == ()


def test_a_railing_whose_centreline_is_half_a_thickness_inside_the_void_edge_still_counts():
    void = square(0, 0, 10, 10)
    near = wall((10.17, 0), (10.17, 10), 0.33)          # a 4 in wall whose face is the void's edge
    far = wall((11.5, 0), (11.5, 10), 0.33)
    assert railing_wall_indices((near, far), (void.boundary,)) == {0}


def test_a_railing_that_runs_a_little_past_the_void_edge_still_counts():
    void = square(0, 0, 10, 10)
    long = wall((10.17, -0.8), (10.17, 10.0), 0.33)      # ends 0.8 ft beyond the edge
    short_overlap = wall((10.17, 9.5), (10.17, 25.0), 0.33)   # only touches it
    assert railing_wall_indices((long, short_overlap), (void.boundary,)) == {0}
