"""Regions: what each closed space is (room, void, pool, terrace ...), and what that does to walls and slabs."""
import pytest

from archiagent.classify.region_stage import REGION_CLASSES, RegionStage
from archiagent.geometry.region_effects import apply_regions
from archiagent.geometry.room_attributes import label_attributes
from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.llm.client import LLMSchemaError
from archiagent.primitives import PrimitiveSet

PS = PrimitiveSet((), (), 100.0, 100.0, "t.dxf", "s" * 64)


def square(x0, y0, x1, y1, label=""):
    return Space(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), abs((x1 - x0) * (y1 - y0)), label=label)


def wall(a, b, t=0.75):
    return WallSeg(a, b, t, "WALL", "paired-line", "measured", ("w",))


@pytest.mark.parametrize("text", ["SWIMMING POOL 15'0\"x 13'-0\"", "POOL", "Swimming Pool"])
def test_a_pool_label_is_read(text):
    assert label_attributes(text).pool


def test_a_word_containing_pool_is_not_a_pool():
    assert label_attributes("CARPOOLING AREA") is None


def test_a_pool_lowers_every_wall_along_its_edge_and_has_no_slab():
    pool = square(0, 0, 10, 8)
    walls = (wall((0, 0), (10, 0)), wall((10, 0), (10, 8), 0.33), wall((20, 0), (30, 0)))
    effects = apply_regions(walls, (pool,), {0: "pool"})
    assert [w.wall_class for w in effects.walls] == ["pool_edge", "pool_edge", ""]
    assert effects.voids == (pool.boundary,)


def test_a_void_makes_thin_edges_railings_and_leaves_thick_walls_alone():
    void = square(0, 0, 10, 10)
    walls = (wall((0, 0), (10, 0), 0.25), wall((10, 0), (10, 10), 0.75))
    effects = apply_regions(walls, (void,), {0: "void"})
    assert [w.wall_class for w in effects.walls] == ["railing", ""]
    assert effects.voids == (void.boundary,)


def test_ordinary_rooms_change_nothing():
    walls = (wall((0, 0), (10, 0)),)
    effects = apply_regions(walls, (square(0, 0, 10, 10),), {0: "room"})
    assert effects.walls == walls and effects.voids == ()


class Client:
    def __init__(self, cls="room", fail=False):
        self.cls, self.fail, self.calls = cls, fail, []

    def classify_json_vision(self, *, system, user, schema, images, max_tokens):
        self.calls.append(user)
        if self.fail:
            raise LLMSchemaError("bad")
        numbers = [int(l.split("|")[0].strip().lstrip("#")) for l in user.splitlines() if l.startswith("#")]
        return {"items": [{"n": n, "class": self.cls, "confidence": 0.9, "reason": "r"} for n in numbers]
                + [{"n": 77, "class": "pool", "confidence": 1, "reason": "invented"}]}


def test_region_stage_classes_by_space_index_and_ignores_invented_numbers():
    stage = RegionStage(Client("terrace"), render=lambda *a, **k: b"\x89PNG")
    classes, issues = stage(PS, (square(0, 0, 10, 10, "OPEN TERRACE"), square(20, 0, 30, 10)), 12.0)
    assert set(classes) == {0, 1} and classes[0][0] == "terrace" and issues == ()


def test_region_stage_failure_is_an_issue():
    classes, issues = RegionStage(Client(fail=True), render=lambda *a, **k: b"\x89PNG")(PS, (square(0, 0, 10, 10),), 12.0)
    assert classes == {} and [i.code for i in issues] == ["region_stage_skipped"]


def test_region_classes():
    assert set(REGION_CLASSES) == {"room", "balcony", "terrace", "void", "double_height", "shaft", "stair",
                                   "pool", "garden", "parking", "not_a_room"}


def test_a_pool_that_is_not_a_closed_room_is_found_from_its_stated_size():
    from archiagent.geometry.room_attributes import pool_label_regions
    from archiagent.primitives import Primitive, TextItem
    lines = [(0, 0, 15, 0), (15, 0, 15, 13), (15, 13, 0, 13), (0, 13, 0, 0)]
    prims = tuple(Primitive("line", ((a, b), (c, d)), "ANY", None, None, f"l{i}") for i, (a, b, c, d) in enumerate(lines))
    ps = PrimitiveSet(prims, (TextItem("SWIMMING POOL 15'0\"x 13'-0\"", (6, 6, 8, 7), "T"),), 100, 100, "t.dxf", "s" * 64)
    (ring,) = pool_label_regions(ps, 1.0)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((0, 0, 15, 13))


def test_a_long_wall_that_only_passes_the_pool_is_not_a_pool_edge():
    pool = square(0, 0, 10, 8)
    boundary_wall = wall((-15, 0), (40, 0))          # 55 ft compound wall, 10 ft of it along the pool
    effects = apply_regions((boundary_wall,), (pool,), {0: "pool"})
    assert effects.walls[0].wall_class == ""
