"""The feedback file: a template from a run, and a summary of what a person filled in."""
import json

import pytest

from archiagent import feedback
from archiagent.cli import main


def manifest(tmp_path):
    model = {
        "region_id": "floor-01", "storey_name": "Ground", "wall_height_ft": 10.0, "scale": {"units_per_foot": 12.0},
        "walls": [{"start": [0, 0], "end": [10, 0], "thickness_ft": 0.75, "source_layer": "WALL",
                   "detector": "paired-line", "source_ids": ["a"]},
                  {"start": [0, 5], "end": [10, 5], "thickness_ft": 0.25, "source_layer": "FURN",
                   "detector": "paired-line", "source_ids": ["b"], "height_ft": 3.5}],
        "openings": [{"id": "o1", "kind": "window", "start": [2, 0], "end": [4, 0], "symbol_id": "s1"}],
        "spaces": [{"boundary": [[0, 0], [10, 0], [10, 5], [0, 5]], "area_sqft": 50.0, "label": "HALL"}],
        "symbols": [{"id": "d1", "kind": "door", "position": [1, 0], "width_ft": 3.0, "evidence": "library"},
                    {"id": "f1", "kind": "furniture", "position": [5, 2], "width_ft": 2.0}],
    }
    path = tmp_path / "plan.interpretation.json"
    path.write_text(json.dumps({"source_sha256": "x" * 64, "models": [model]}))
    return path


def test_the_template_lists_every_element_with_a_blank_verdict(tmp_path):
    out = tmp_path / "plan.feedback.json"
    t = feedback.write_template(manifest(tmp_path), out)
    els = t["models"][0]["elements"]
    assert {e["id"] for e in els} == {"wall:0", "wall:1", "opening:o1", "space:0", "symbol:d1"}   # furniture left out
    assert all(e["verdict"] is None for e in els)
    assert next(e for e in els if e["id"] == "wall:1")["height_ft"] == 3.5
    assert json.loads(out.read_text())["models"][0]["missing"] == []


def test_the_summary_gives_precision_recall_and_reasons(tmp_path):
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.feedback.json")
    for e in t["models"][0]["elements"]:
        if e["id"] == "wall:0":
            e["verdict"] = "correct"
        elif e["id"] == "wall:1":
            e.update(verdict="wrong", reason="should be railing")
        elif e["id"] == "opening:o1":
            e["verdict"] = "correct"
    t["models"][0]["missing"] = [{"kind": "window", "at_ft": [8, 0], "note": ""},
                                 {"kind": "window", "at_ft": [9, 5], "note": ""}]
    path = tmp_path / "filled.json"
    path.write_text(json.dumps(t))
    s = feedback.summarize([path])["kinds"]
    assert s["wall"]["precision"] == 0.5 and s["wall"]["wrong_reasons"] == {"should be railing": 1}
    assert s["window"]["correct"] == 1 and s["window"]["missing"] == 2
    assert s["window"]["recall_estimate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["space"]["unreviewed"] == 1 and s["space"]["precision"] is None


def test_a_misspelt_verdict_is_an_error_not_a_silent_skip(tmp_path):
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.feedback.json")
    t["models"][0]["elements"][0]["verdict"] = "right"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(t))
    with pytest.raises(ValueError, match="right"):
        feedback.summarize([path])


def test_the_command_line_writes_and_summarises(tmp_path, capsys):
    m = manifest(tmp_path)
    assert main(["--feedback-template", str(m), "--outputDir", str(tmp_path / "out")]) == 0
    written = tmp_path / "out" / "plan.feedback.json"
    assert written.is_file()
    assert main(["--feedback-summary", str(written)]) == 0
    assert "kinds" in capsys.readouterr().out


# --- a labelled 2D sheet that matches the JSON ------------------------------------------------


def test_every_element_gets_a_short_label_by_kind(tmp_path):
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.feedback.json")
    labels = {e["id"]: e["label"] for e in t["models"][0]["elements"]}
    assert labels == {"wall:0": "W1", "wall:1": "W2", "opening:o1": "WN1", "space:0": "R1", "symbol:d1": "D1"}


def test_the_sheet_prints_every_label_the_json_uses(tmp_path):
    out = tmp_path / "f.feedback.svg"
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.feedback.json")
    feedback.write_sheet(manifest(tmp_path), out)
    svg = out.read_text()
    assert svg.startswith("<svg")
    for e in t["models"][0]["elements"]:
        assert f">{e['label']}<" in svg


def test_the_command_line_writes_the_sheet_beside_the_json(tmp_path):
    m = manifest(tmp_path)
    assert main(["--feedback-template", str(m), "--outputDir", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "plan.feedback.svg").is_file()


# --- feedback as reconcile precedence on re-run -----------------------------------------------

from archiagent.feedback import apply_feedback, decisions_for
from archiagent.geometry.walls import WallSeg


def _filled(tmp_path, sha="x" * 64, verdicts=()):
    t = feedback.write_template(manifest(tmp_path), tmp_path / "f.feedback.json")
    by_label = {e["label"]: e for e in t["models"][0]["elements"]}
    for label, verdict, reason, note in verdicts:
        by_label[label].update(verdict=verdict, reason=reason, note=note)
    t["source"]["source_sha256"] = sha
    path = tmp_path / "filled.feedback.json"
    path.write_text(json.dumps(t))
    return path


def test_a_wall_marked_not_a_wall_is_rejected_on_re_run(tmp_path):
    path = _filled(tmp_path, verdicts=[("W1", "wrong", "not a wall", "")])
    d = decisions_for([path], "x" * 64)
    walls = (WallSeg((0, 0), (10, 0), 0.75, "WALL", "paired-line", "measured", ("a",)),
             WallSeg((0, 5), (10, 5), 0.25, "FURN", "paired-line", "measured", ("b",)))
    kept, notes = apply_feedback(walls, d)
    assert [w.source_ids for w in kept] == [("b",)] and notes


def test_reasons_and_notes_set_a_class(tmp_path):
    path = _filled(tmp_path, verdicts=[("W1", "wrong", "not a wall", "this is a railing"),
                                       ("W2", "wrong", "wrong height", "height should be in -Z axis")])
    d = decisions_for([path], "x" * 64)
    walls = (WallSeg((0, 0), (10, 0), 0.75, "WALL", "paired-line", "measured", ("a",)),
             WallSeg((0, 5), (10, 5), 0.25, "FURN", "paired-line", "measured", ("b",)))
    kept, _ = apply_feedback(walls, d)
    assert [w.wall_class for w in kept] == ["railing", "pool_edge"]


def test_feedback_for_another_drawing_is_ignored(tmp_path):
    path = _filled(tmp_path, sha="y" * 64, verdicts=[("W1", "wrong", "not a wall", "")])
    assert decisions_for([path], "x" * 64).reject_source_ids == frozenset()


def test_an_empty_hash_is_resolved_through_the_manifest_the_file_names(tmp_path):
    path = _filled(tmp_path, sha="", verdicts=[("W1", "wrong", "not a wall", "")])
    m = json.loads((tmp_path / "plan.interpretation.json").read_text())
    m["models"][0]["source_sha256"] = "z" * 64
    (tmp_path / "plan.interpretation.json").write_text(json.dumps(m))
    assert decisions_for([path], "z" * 64).reject_source_ids == frozenset({"a"})


def test_profile_walls_are_listed_and_drawn_too(tmp_path):
    m = json.loads(manifest(tmp_path).read_text())
    m["models"][0]["wall_profiles"] = [{"id": "p1", "boundary": [[0, 0], [3, 0], [3, 1], [0, 1], [0, 0]], "holes": [],
                                        "source_layer": "HATCH", "source_ids": ["h1"]}]
    path = tmp_path / "with-profile.interpretation.json"
    path.write_text(json.dumps(m))
    t = feedback.write_template(path, tmp_path / "p.feedback.json")
    wp = [e for e in t["models"][0]["elements"] if e["kind"] == "profile_wall"]
    assert [e["label"] for e in wp] == ["WP1"]
    feedback.write_sheet(path, tmp_path / "p.svg")
    assert ">WP1<" in (tmp_path / "p.svg").read_text()
