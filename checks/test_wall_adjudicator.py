"""Wall adjudication with a fake client: no provider, no network, no files."""
import pytest

from archiagent.classify.thumbnails import RenderUnavailable
from archiagent.classify.wall_adjudicator import WallAdjudicator, verdicts_from_reply
from archiagent.geometry.candidacy import ScoredCandidate
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from checks.candidacy_fixtures import source, wall


def candidate(n, length=6.0):
    return ScoredCandidate(f"cand-{n:012d}", wall((0, n), (length, n), ids=(f"f{n}",)), (), .5, "ambiguous")


class FakeClient:
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def classify_json_vision(self, *, system, user, schema, images, max_tokens):
        ids = [label.removeprefix("candidate ") for label, _ in images]
        self.calls.append(ids)
        if self.fail_on.intersection(ids):
            raise LLMUnavailable("boom")
        return {"candidates": [{"id": i, "verdict": "wall", "confidence": .9, "reason": "parapet"} for i in ids]}


def render(ps, w, upf):
    return b"\x89PNG fake"


def adjudicate(client, candidates, **kw):
    return WallAdjudicator(client, render=render, **kw)(source(), tuple(candidates), 1.0)


def test_candidates_are_sent_in_batches_with_one_labelled_image_each():
    client = FakeClient()
    verdicts, issues = adjudicate(client, [candidate(n) for n in range(6)], batch_size=4)
    assert sorted(len(c) for c in client.calls) == [2, 4]
    assert set(verdicts) == {candidate(n).id for n in range(6)}
    assert verdicts[candidate(0).id] == ("wall", .9, "parapet")
    assert issues == ()


def test_candidates_over_the_cap_are_reported_not_sent_longest_first():
    client = FakeClient()
    runs = [candidate(n, length=10 - n) for n in range(5)]
    verdicts, issues = adjudicate(client, runs, cap=3)
    assert set(verdicts) == {runs[0].id, runs[1].id, runs[2].id}
    assert {i.entity for i in issues} == {runs[3].id, runs[4].id}
    assert {i.code for i in issues} == {"wall_adjudication_skipped"}


def test_a_failed_batch_costs_only_its_own_candidates():
    runs = [candidate(n) for n in range(8)]
    verdicts, issues = adjudicate(FakeClient(fail_on={runs[0].id}), runs, batch_size=4)
    assert len(verdicts) == 4
    assert len(issues) == 4 and all("adjudication failed" in i.msg for i in issues)


def test_no_renderer_means_no_calls_and_every_candidate_reported():
    client = FakeClient()

    def unavailable(ps, w, upf):
        raise RenderUnavailable("install the vision extra")

    verdicts, issues = WallAdjudicator(client, render=unavailable)(source(), (candidate(1), candidate(2)), 1.0)
    assert verdicts == {} and client.calls == []
    assert len(issues) == 2


def test_reply_validation_ignores_unknown_ids_and_rejects_ambiguity():
    batch = (candidate(1),)
    cid = candidate(1).id
    ok = verdicts_from_reply({"candidates": [
        {"id": cid, "verdict": "not_wall", "confidence": 1.7, "reason": "hatch"},
        {"id": "cand-invented", "verdict": "wall", "confidence": .9, "reason": ""}]}, batch)
    assert ok == {cid: ("not_wall", 1.0, "hatch")}
    with pytest.raises(LLMSchemaError, match="more than once"):
        verdicts_from_reply({"candidates": [{"id": cid, "verdict": "wall", "confidence": .9, "reason": ""}] * 2}, batch)
    with pytest.raises(LLMSchemaError, match="verdict"):
        verdicts_from_reply({"candidates": [{"id": cid, "verdict": "maybe", "confidence": .9, "reason": ""}]}, batch)
    with pytest.raises(LLMSchemaError, match="candidates"):
        verdicts_from_reply({"layers": []}, batch)


def test_render_candidate_returns_png_bytes_and_writes_nothing(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from archiagent.classify.thumbnails import render_candidate
    from checks.candidacy_fixtures import house_faces
    monkeypatch.chdir(tmp_path)
    png = render_candidate(source(house_faces()), wall((0, 20), (30, 20), layer="furni", ids=("t0", "t1")), 1.0)
    assert png.startswith(b"\x89PNG")
    assert list(tmp_path.iterdir()) == []
