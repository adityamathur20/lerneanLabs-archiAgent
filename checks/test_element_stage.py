"""The wall element stage: every accepted or ambiguous wall, numbered on tiles, judged by id."""
import pytest

from archiagent.classify.element_stage import WallElementStage, parse_reply
from archiagent.geometry.candidacy import ScoredCandidate
from archiagent.geometry.walls import WallSeg
from archiagent.llm.client import LLMSchemaError
from archiagent.primitives import PrimitiveSet


def cand(cid, x, band, y=0.0):
    w = WallSeg((x, y), (x + 8, y), 0.75, "WALL", "paired-line", "measured", (cid,))
    return ScoredCandidate(cid, w, (), 0.5, band)


PS = PrimitiveSet((), (), 100.0, 100.0, "t.dxf", "s" * 64)


class Client:
    """Answers every number it was sent, with the class it is told to."""

    def __init__(self, answer=None, fail=False):
        self.calls, self.answer, self.fail = [], answer or {}, fail

    def classify_json_vision(self, *, system, user, schema, images, max_tokens):
        self.calls.append(user)
        if self.fail:
            raise LLMSchemaError("bad reply")
        numbers = [int(line.split("|")[0].strip().lstrip("#")) for line in user.splitlines() if line.startswith("#")]
        return {"items": [{"n": n, "verdict": "wall", "class": self.answer.get(n, "interior"),
                           "confidence": 0.9, "reason": "r"} for n in numbers] + [
                          {"n": 999, "verdict": "wall", "class": "exterior", "confidence": 1, "reason": "invented"}]}


def stage(client, **kw):
    return WallElementStage(client, render=lambda *a, **k: b"\x89PNG", context="12 units per foot", **kw)


def test_an_empty_floor_makes_no_call():
    client = Client()
    verdicts, issues = stage(client)(PS, (), 12.0)
    assert (verdicts, issues, client.calls) == ({}, (), [])


def test_rejected_candidates_are_not_asked_about():
    client = Client()
    s = stage(client)
    verdicts, _ = s(PS, (cand("a", 0, "accept"), cand("b", 10, "ambiguous"), cand("c", 20, "reject")), 12.0)
    assert set(verdicts) == {"a", "b"} and set(s.classes) == {"a", "b"}


def test_an_invented_number_never_reaches_a_wall():
    s = stage(Client())
    verdicts, _ = s(PS, (cand("a", 0, "ambiguous"),), 12.0)
    assert set(verdicts) == {"a"}


def test_classes_come_back_per_id():
    s = stage(Client(answer={1: "site_wall"}))
    s(PS, (cand("a", 0, "accept"), cand("b", 10, "accept")), 12.0)
    assert s.classes["a"][0] == "site_wall" and s.classes["b"][0] == "interior"


def test_a_failed_tile_is_an_issue_not_an_exception():
    verdicts, issues = stage(Client(fail=True))(PS, (cand("a", 0, "ambiguous"),), 12.0)
    assert verdicts == {} and [i.code for i in issues] == ["element_stage_skipped"]


def test_one_call_per_tile_and_the_context_is_sent():
    client = Client()
    far = [cand(f"x{i}", i * 60.0, "ambiguous", y=i * 60.0) for i in range(3)]
    stage(client)(PS, tuple(far), 12.0)
    assert 1 <= len(client.calls) <= 3 and all("12 units per foot" in c for c in client.calls)


def test_the_image_budget_caps_calls():
    client = Client()
    many = [cand(f"x{i:02d}", (i % 10) * 50.0, "ambiguous", y=(i // 10) * 50.0) for i in range(100)]
    stage(client, max_images=4)(PS, tuple(many), 12.0)
    assert len(client.calls) <= 4


def test_parse_reply_rejects_unknown_classes():
    with pytest.raises(LLMSchemaError):
        parse_reply({"items": [{"n": 1, "verdict": "wall", "class": "spaceship", "confidence": 1, "reason": ""}]}, {1: "a"})


# --- Task 5: wired into select_walls; reconcile; replay ------------------------------------------

from archiagent.geometry import candidacy
from archiagent.geometry.candidacy import ScoredCandidate as _SC, select_walls


class StubStage:
    """A stage whose answers are fixed per candidate index: (verdict, confidence, class)."""
    wants_all = True

    def __init__(self, answers):
        self.answers, self.calls, self.classes = answers, 0, {}

    def __call__(self, ps, candidates, upf):
        self.calls += 1
        verdicts = {}
        for c in candidates:
            v, conf, cls = self.answers.get(c.band, ("wall", 0.9, "interior"))
            verdicts[c.id] = (v, conf, "stub")
            self.classes[c.id] = (cls, conf)
        return verdicts, ()


def _select(monkeypatch, bands, stage):
    walls = tuple(WallSeg((i * 10.0, 0), (i * 10.0 + 8, 0), 0.75, "WALL", "paired-line", "measured", (f"s{i}",))
                  for i in range(len(bands)))
    by_wall = {w: b for w, b in zip(walls, bands)}
    monkeypatch.setattr(candidacy, "score_candidates", lambda ws, ctx: tuple(
        _SC(candidacy.candidate_id(w), w, (), 0.5, by_wall[w]) for w in ws))
    monkeypatch.setattr(candidacy, "build_context", lambda *a, **k: None)
    from archiagent.classify.layers import LayerDecision
    from archiagent.classify.roles import Role
    cls = (LayerDecision("WALL", Role.WALL_PARTITION, 0.9, "r", "llm"),)
    return select_walls(PS, walls, cls, 12.0, adjudicator=stage)


def test_an_accepted_wall_gets_its_class(monkeypatch):
    accepted, decisions, issues = _select(monkeypatch, ["accept"], StubStage({"accept": ("wall", 0.9, "site_wall")}))
    assert [w.wall_class for w in accepted] == ["site_wall"]
    assert any(i.code == "element_stage_summary" for i in issues)


def test_a_careless_reply_cannot_empty_a_floor(monkeypatch):
    accepted, _, _ = _select(monkeypatch, ["accept", "accept"], StubStage({"accept": ("not_wall", 0.8, "not_a_wall")}))
    assert len(accepted) == 2 and all(w.wall_class == "" for w in accepted)


def test_a_confident_reply_overturns_an_accept(monkeypatch):
    accepted, decisions, _ = _select(monkeypatch, ["accept"], StubStage({"accept": ("not_wall", 0.9, "not_a_wall")}))
    assert accepted == () and decisions[0].verdict_source == "element-stage"


def test_an_ambiguous_wall_is_decided_by_the_stage(monkeypatch):
    accepted, decisions, _ = _select(monkeypatch, ["ambiguous"], StubStage({"ambiguous": ("wall", 0.75, "parapet")}))
    assert [w.wall_class for w in accepted] == ["parapet"] and decisions[0].verdict_source == "element-stage"


def test_a_low_confidence_class_is_not_applied(monkeypatch):
    accepted, _, _ = _select(monkeypatch, ["accept"], StubStage({"accept": ("wall", 0.5, "pool_edge")}))
    assert [w.wall_class for w in accepted] == [""]


def test_classes_survive_a_frozen_manifest(tmp_path):
    from archiagent.interpretation import make_manifest
    from archiagent.interpretation import _decode
    from archiagent.model import BuildingModel
    from checks.test_semantic_ifc import fixture_model
    from dataclasses import replace as _r
    m = fixture_model()
    m = _r(m, walls=(_r(m.walls[0], wall_class="site_wall"), *m.walls[1:]))
    doc = make_manifest((m,))
    back = _decode(doc["models"][0], BuildingModel, "m")
    assert back.walls[0].wall_class == "site_wall"
