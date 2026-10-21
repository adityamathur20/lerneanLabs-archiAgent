"""Unexplained gaps between wall ends: a door, a window, an open passage, or a wall the detector missed."""
import pytest

from archiagent.classify.gap_stage import GAP_KINDS, GapStage, apply_gap_kinds
from archiagent.geometry.room_gaps import RoomGap
from archiagent.geometry.walls import WallSeg
from archiagent.llm.client import LLMSchemaError
from archiagent.primitives import PrimitiveSet
from archiagent.recognition import host_openings

PS = PrimitiveSet((), (), 100.0, 100.0, "t.dxf", "s" * 64)
WALLS = (WallSeg((0, 0), (10, 0), 0.75, "WALL", "paired-line", "measured", ("a",)),
         WallSeg((14, 0), (24, 0), 0.75, "WALL", "paired-line", "measured", ("b",)))
GAP = RoomGap((10, 0), (14, 0), "collinear", ("a", "b"))


class Client:
    def __init__(self, kind, fail=False):
        self.kind, self.fail, self.calls = kind, fail, 0

    def classify_json_vision(self, *, system, user, schema, images, max_tokens):
        self.calls += 1
        if self.fail:
            raise LLMSchemaError("bad")
        numbers = [int(l.split("|")[0].strip().lstrip("#")) for l in user.splitlines() if l.startswith("#")]
        return {"items": [{"n": n, "kind": self.kind, "confidence": 0.9, "reason": "r"} for n in numbers]
                + [{"n": 55, "kind": "door", "confidence": 1, "reason": "invented"}]}


def judged(kind, **kw):
    return GapStage(Client(kind, **kw), render=lambda *a, **k: b"\x89PNG")(PS, (GAP,), WALLS, 12.0)


def test_gap_kinds():
    assert GAP_KINDS == ("door", "window", "passage", "wall")


def test_a_window_answer_becomes_a_hosted_window():
    kinds, issues = judged("window")
    walls, symbols = apply_gap_kinds(WALLS, (GAP,), kinds)
    _, openings = host_openings(walls, symbols, 10.0)
    assert [o.kind for o in openings] == ["window"] and openings[0].width_ft == pytest.approx(4.0)


def test_a_wall_answer_bridges_the_gap_with_the_flanking_thickness():
    kinds, _ = judged("wall")
    walls, symbols = apply_gap_kinds(WALLS, (GAP,), kinds)
    added = walls[len(WALLS):]
    assert symbols == () and len(added) == 1
    assert (added[0].start, added[0].end, added[0].thickness_ft) == ((10, 0), (14, 0), 0.75)
    assert added[0].detector == "element-gap"


def test_a_passage_changes_nothing():
    kinds, _ = judged("passage")
    assert apply_gap_kinds(WALLS, (GAP,), kinds) == (WALLS, ())


def test_an_invented_number_is_ignored_and_a_failure_is_an_issue():
    kinds, _ = judged("door")
    assert set(kinds) == {0}
    kinds, issues = judged("door", fail=True)
    assert kinds == {} and [i.code for i in issues] == ["gap_stage_skipped"]


def test_a_low_confidence_answer_is_not_applied():
    assert apply_gap_kinds(WALLS, (GAP,), {0: ("wall", 0.5)}) == (WALLS, ())
