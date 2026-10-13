"""Floors are found as plan clusters named by their captions; elevation is optional."""
import pytest

from archiagent.floors import Floor, detect_floors
from archiagent.primitives import Primitive, PrimitiveSet, TextItem


def plan(x, y, w=60, h=40):
    """An outline with internal walls, in feet (units_per_foot = 1)."""
    lines = [((x, y), (x + w, y)), ((x + w, y), (x + w, y + h)), ((x + w, y + h), (x, y + h)), ((x, y + h), (x, y))]
    for k in range(1, 8):
        lines.append(((x + k * w / 8, y), (x + k * w / 8, y + h * (0.5 if k % 2 else 1))))
        lines.append(((x, y + k * h / 8), (x + w * (0.5 if k % 2 else 1), y + k * h / 8)))
    return [Primitive("line", pts, "WALL", None, None, f"p{x}-{y}-{i}", "LINE") for i, pts in enumerate(lines)]


def text(s, x, y, height=2.0, width=None):
    width = width if width is not None else 0.8 * len(s)
    return TextItem(s, (x - width / 2, y - height / 2, x + width / 2, y + height / 2), "TEXT", f"t-{s}-{x}-{y}")


def drawing(primitives, texts):
    return PrimitiveSet(tuple(primitives), tuple(texts), 1000.0, 1000.0, "f.dxf", "c" * 64)


def stacked_three():
    prims = plan(0, 0) + plan(0, 70) + plan(0, 140)
    texts = [text("GROUND FLOOR REVISED", 30, -4), text("FIRST  FLOOR REVISED", 30, 66),
             text("TERRACE  FLOOR REVISED", 30, 136)]
    return drawing(prims, texts)


def test_three_stacked_plans_are_named_and_ordered_from_the_ground_up():
    floors = detect_floors(stacked_three(), 1.0, wall_height_ft=10.0)
    assert [f.name for f in floors] == ["Ground floor", "First floor", "Terrace"]
    # each box surrounds its plan with a little margin, and boxes do not overlap
    for floor, y in zip(floors, (0, 70, 140)):
        x0, y0, x1, y1 = floor.bounds
        assert y - 10 <= y0 <= y and y + 40 <= y1 <= y + 50
        assert x0 <= 0 and x1 >= 60
    assert [f.elevation_ft for f in floors] == [0.0, 10.0, 20.0]
    assert {f.elevation_source for f in floors} == {"assumed"}
    assert floors[0].label_text == "GROUND FLOOR REVISED"
    assert all(f.confidence >= 0.8 for f in floors)


def test_the_wall_height_sets_the_assumed_spacing():
    floors = detect_floors(stacked_three(), 1.0, wall_height_ft=12.5)
    assert [f.elevation_ft for f in floors] == [0.0, 12.5, 25.0]


def test_a_note_inside_a_plan_does_not_win_over_the_caption():
    prims = plan(0, 0) + plan(100, 0)
    texts = [text("GROUND FLOOR PLAN", 30, -3), text("UP TO SECOND  FLOOR", 30, 20, 3.0),
             text("FIRST FLOOR PLAN", 130, -3)]
    floors = detect_floors(drawing(prims, texts), 1.0)
    assert [f.name for f in floors] == ["Ground floor", "First floor"]
    assert floors[0].label_text == "GROUND FLOOR PLAN"


def test_a_single_plan_is_not_a_multi_floor_drawing():
    assert detect_floors(drawing(plan(0, 0), [text("GROUND FLOOR PLAN", 30, -3)]), 1.0) == ()


def test_a_plan_without_a_caption_stays_listed_as_unnamed():
    prims = plan(0, 0) + plan(100, 0)
    floors = detect_floors(drawing(prims, [text("GROUND FLOOR PLAN", 30, -3)]), 1.0)
    assert [f.name for f in floors] == ["Ground floor", "Unnamed plan 2"]
    assert floors[1].label_text == "" and floors[1].confidence < 0.5


def test_a_level_marker_with_a_unit_sets_that_floors_elevation():
    d = stacked_three()
    d = drawing(d.primitives, d.texts + (text("+3.6m FIRST FFL", 80, 70),))
    floors = {f.name: f for f in detect_floors(d, 1.0, wall_height_ft=10.0)}
    assert floors["First floor"].elevation_source == "level-marker"
    assert floors["First floor"].elevation_ft == pytest.approx(3.6 * 3.28084, rel=1e-4)
    assert floors["Ground floor"].elevation_source == "assumed"
    # the next floor is spaced from the marked one
    assert floors["Terrace"].elevation_ft == pytest.approx(3.6 * 3.28084 + 10.0, rel=1e-4)


def test_a_level_marker_without_a_unit_is_ignored_because_the_unit_would_be_a_guess():
    d = stacked_three()
    d = drawing(d.primitives, d.texts + (text("+3.6 FIRST FFL", 80, 70),))
    floors = {f.name: f for f in detect_floors(d, 1.0)}
    assert floors["First floor"].elevation_source == "assumed"
    assert floors["First floor"].elevation_ft == 10.0


def test_ids_are_unique_and_stable():
    a = [f.id for f in detect_floors(stacked_three(), 1.0)]
    assert a == [f.id for f in detect_floors(stacked_three(), 1.0)]
    assert len(set(a)) == 3


def test_fragments_too_small_to_be_a_plan_are_not_floors():
    prims = plan(0, 0) + plan(100, 0) + plan(300, 300, w=6, h=4)
    floors = detect_floors(drawing(prims, [text("GROUND FLOOR PLAN", 30, -3), text("FIRST FLOOR PLAN", 130, -3)]), 1.0)
    assert len(floors) == 2


def test_a_typical_floor_caption_means_one_plan_stands_for_many_floors_so_none_are_split():
    prims = plan(0, 0) + plan(100, 0)
    texts = [text("TYPICAL FLOOR PLAN", 30, -3), text("TERRACE FLOOR PLAN", 130, -3)]
    assert detect_floors(drawing(prims, texts), 1.0) == ()
    prims = plan(0, 0) + plan(100, 0)
    texts = [text("1st FLOOR TO 5th FLOOR PLAN", 30, -3), text("TERRACE FLOOR PLAN", 130, -3)]
    assert detect_floors(drawing(prims, texts), 1.0) == ()


def test_two_plans_captioned_alike_do_not_both_become_that_floor():
    prims = plan(0, 0) + plan(100, 0) + plan(200, 0)
    texts = [text("GROUND FLOOR PLAN", 30, -3), text("FIRST FLOOR PLAN", 130, -3),
             text("FIRST FLOOR PLAN", 230, -3)]
    floors = detect_floors(drawing(prims, texts), 1.0)
    assert [f.name for f in floors].count("First floor") == 1
    assert len(floors) == 3 and floors[-1].name.startswith("Unnamed plan")


def test_small_uncaptioned_clusters_are_details_not_floors():
    prims = plan(0, 0) + plan(100, 0) + plan(200, 0, w=30, h=20)
    texts = [text("GROUND FLOOR PLAN", 30, -3), text("FIRST FLOOR PLAN", 130, -3)]
    assert [f.name for f in detect_floors(drawing(prims, texts), 1.0)] == ["Ground floor", "First floor"]


def test_an_uncaptioned_plan_as_big_as_the_others_stays_for_the_user_to_name():
    prims = plan(0, 0) + plan(100, 0) + plan(200, 0)
    texts = [text("GROUND FLOOR PLAN", 30, -3), text("TERRACE FLOOR", 130, -3)]
    assert [f.name for f in detect_floors(drawing(prims, texts), 1.0)] == ["Ground floor", "Terrace", "Unnamed plan 3"]


# -- level markers and the wall height they imply ---------------------------------------------

from archiagent.floors import implied_wall_height, level_markers


def marked(*labels):
    return drawing([], [text(s, 0, 0) for s in labels])


def test_markers_with_a_unit_are_read_in_feet():
    found = level_markers(marked("+3.6m FIRST FFL"))
    assert [(m.word, round(m.elevation_ft, 3)) for m in found] == [("first", 11.811)]


def test_a_unitless_marker_takes_the_unit_the_others_in_the_set_carry():
    # Aiims Road writes "+3.6 FIRST FFL" and only the last one says M.
    found = level_markers(marked("+3.6 FIRST FFL", "+6.6 SECOND FFL", "+18.6 M TERRACE FFL"))
    assert [m.word for m in found] == ["first", "second", "terrace"]
    assert round(found[0].elevation_ft, 2) == round(3.6 * 3.28084, 2)


def test_unitless_markers_alone_are_not_guessed():
    assert level_markers(marked("+3.6 FIRST FFL", "+6.6 SECOND FFL")) == ()


def test_the_height_is_the_spacing_the_markers_agree_on():
    found = level_markers(marked("+0.6 M STILT FFL", "+3.6 FIRST FFL", "+6.6 SECOND FFL", "+9.6 THIRD FFL"))
    assert implied_wall_height(found) == pytest.approx(3.0 * 3.28084, rel=1e-4)


def test_markers_that_disagree_on_spacing_give_no_height():
    found = level_markers(marked("+0.6 M STILT FFL", "+3.6 FIRST FFL", "+9.6 SECOND FFL"))
    assert implied_wall_height(found) is None


def test_one_marker_is_not_a_spacing():
    assert implied_wall_height(level_markers(marked("+3.6m FIRST FFL"))) is None


def test_a_spacing_outside_one_to_fifty_feet_is_not_offered():
    found = level_markers(marked("+0.0 M GROUND FFL", "+0.1 M FIRST FFL"))
    assert implied_wall_height(found) is None
