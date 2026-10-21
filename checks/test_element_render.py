"""Numbered tiles of one floor, for the element stage to ask about each element by its number."""
from archiagent.classify.element_render import Item, assign, number_items, render, tiles
from archiagent.primitives import Primitive, PrimitiveSet


def covers(boxes, bounds):
    x0, y0, x1, y1 = bounds
    return all(any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)
               for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1))


def test_tiles_cover_the_floor_with_overlap():
    boxes = tiles((0, 0, 100, 60))
    assert 1 <= len(boxes) <= 8 and covers(boxes, (0, 0, 100, 60))
    if len(boxes) > 1:
        a, b = boxes[0], boxes[1]
        assert min(a[2], b[2]) > max(a[0], b[0]) or min(a[3], b[3]) > max(a[1], b[1])   # they overlap


def test_a_huge_floor_still_gets_at_most_max_tiles():
    boxes = tiles((0, 0, 400, 400), max_tiles=8)
    assert len(boxes) <= 8 and covers(boxes, (0, 0, 400, 400))


def test_a_small_floor_is_one_tile():
    assert len(tiles((0, 0, 30, 20))) == 1


def items():
    return [Item(0, "cand-b", "wall", ((50, 5), (60, 5)), False),
            Item(0, "cand-a", "wall", ((1, 1), (9, 1)), True),
            Item(0, "cand-c", "wall", ((95, 55), (99, 55)), False)]


def test_numbers_follow_id_order_and_start_at_one():
    numbered = number_items(items())
    assert [(i.number, i.id) for i in numbered] == [(1, "cand-a"), (2, "cand-b"), (3, "cand-c")]


def test_every_item_lands_in_exactly_one_tile():
    boxes = tiles((0, 0, 100, 60), tile_ft=40)
    placed = assign(number_items(items()), boxes)
    ids = [i.id for group in placed.values() for i in group]
    assert sorted(ids) == ["cand-a", "cand-b", "cand-c"]


def test_render_is_a_png_in_memory():
    ps = PrimitiveSet((Primitive("line", ((0, 0), (120, 0)), "W", None, None, "p1"),), (), 100, 100, "t.dxf", "s" * 64)
    png = render(ps, 12.0, (0, -5, 20, 5), number_items(items())[:1])
    assert png[:4] == b"\x89PNG"
