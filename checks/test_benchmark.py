"""Groundtruth scoring must never label unreviewed drawing areas as negatives."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from archiagent.benchmark import evaluate_reference, load_reference
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult
from archiagent.semantic import DimensionCheck, PlanRegion, SymbolInstance

SHA = "a" * 64


def model(*symbols):
    return BuildingModel((), (), (), (), ScaleResult(1., "associated", (), 0., 0), (),
                         "drawing.dxf", SHA, symbols=tuple(symbols), region_id="selected-plan")


def prediction(pid, kind, position, source_ids=()):
    return SymbolInstance(pid, kind, position, 1., 1., source_ids=source_ids)


def annotation(sid, kind, position, **extra):
    return {"id": sid, "kind": kind, "position": list(position), "status": "reviewed", **extra}


def reference(symbols, *, scopes=None, **extra):
    return {"schema_version": 1, "source_sha256": SHA, "region_id": "selected-plan",
            "bounds": [0, 0, 50, 50], "reviewer": {"id": "assistant-annotation", "role": "assistant"},
            "symbols": symbols, "scopes": scopes or [], **extra}


def scope(sid="doors", kinds=None, bounds=None, **extra):
    return {"id": sid, "kinds": kinds or ["door"], "bounds_ft": bounds or [0, 0, 20, 20],
            "complete": True, "status": "reviewed", **extra}


def test_partial_annotations_produce_observations_without_precision_or_false_positives():
    result = evaluate_reference(model(prediction("p1", "door", (2, 2)), prediction("unannotated", "door", (10, 10))),
                                reference([annotation("r1", "door", (2, 2))]))
    assert result["annotation_status"] == "partial"
    assert result["counts"]["matched_annotations"] == 1
    assert result["counts"]["tp"] == result["counts"]["fp"] == result["counts"]["fn"] == 0
    assert result["counts"]["precision"] is result["counts"]["recall"] is None
    assert result["precision_recall_available"] is False
    assert result["full_acceptance"] is False
    assert set(result["unscored_prediction_ids"]) == {"p1", "unannotated"}


def test_scoped_assignment_counts_duplicates_missing_and_ignores_other_areas_classes():
    groundtruth = reference([annotation("r1", "door", (2, 2)), annotation("r2", "door", (10, 10))], scopes=[scope()])
    result = evaluate_reference(model(prediction("p1", "door", (2, 2)),
                                      prediction("duplicate", "door", (2.01, 2)),
                                      prediction("outside", "door", (30, 30)),
                                      prediction("not-reviewed-class", "column", (5, 5))), groundtruth)
    assert result["per_class"]["door"] == {"tp": 1, "fp": 1, "fn": 1, "precision": .5, "recall": .5, "f1": .5}
    assert result["unmatched_prediction_ids"] == ["duplicate"]
    assert result["unmatched_reference_ids"] == ["r2"]
    assert set(result["unscored_prediction_ids"]) == {"outside", "not-reviewed-class"}
    assert result["reviewer_roles"] == ["assistant"]
    assert result["user_confirmed"] is False


def test_assignment_maximizes_one_to_one_matches_before_nearest_distance():
    groundtruth = reference([annotation("a", "door", (2, 2)), annotation("b", "door", (3, 2))],
                            scopes=[scope()], matching={"location_tolerance_in": 9.6})
    result = evaluate_reference(model(prediction("flexible", "door", (2.4, 2)),
                                      prediction("a-only", "door", (1.3, 2))), groundtruth)
    assert result["counts"]["tp"] == 2
    assert {(m["reference_id"], m["prediction_id"]) for m in result["matches"]} == {("a", "a-only"), ("b", "flexible")}


def test_source_link_match_does_not_hide_failed_localization_and_iou():
    groundtruth = reference([annotation("r", "door", (2, 2), source_ids=["CAD-1"],
                                       boundary=[[1,1],[3,1],[3,3],[1,3],[1,1]])],
                            scopes=[scope(bounds=[0,0,50,50])])
    result = evaluate_reference(model(prediction("p", "door", (30, 30), ("CAD-1", "CAD-2"))), groundtruth)
    match = result["matches"][0]
    assert match["match_basis"] == ["source-ids"]
    assert match["source_overlap"] == 1
    assert match["location_within_tolerance"] is False
    assert match["iou"] == 0
    assert match["iou_above_threshold"] is False
    assert result["full_acceptance"] is False


def test_iou_matches_extent_even_when_annotated_point_differs():
    groundtruth = reference([annotation("r", "door", (5.4, 5.4),
                                       boundary=[[4.5,4.5],[5.5,4.5],[5.5,5.5],[4.5,5.5],[4.5,4.5]])], scopes=[scope()])
    result = evaluate_reference(model(prediction("p", "door", (5, 5))), groundtruth)
    match = result["matches"][0]
    assert match["iou"] == pytest.approx(1.)
    assert match["location_within_tolerance"] is False
    assert match["iou_above_threshold"] is True


def test_overlap_is_not_double_counted_and_boundary_crossings_stay_unscored():
    groundtruth = reference([annotation("r", "door", (5, 5))],
                            scopes=[scope("one", bounds=[0,0,10,10]), scope("two", bounds=[1,1,9,9])])
    result = evaluate_reference(model(prediction("p", "door", (5, 5)), prediction("edge", "door", (9.8, 5))), groundtruth)
    assert result["counts"]["tp"] == 1
    assert result["counts"]["fp"] == 0
    assert result["unscored_prediction_ids"] == ["edge"]


def test_incomplete_or_draft_scope_never_enables_scores():
    groundtruth = reference([annotation("r", "door", (2, 2), status="draft")], scopes=[scope()], annotation_status="complete")
    result = evaluate_reference(model(prediction("p", "door", (2, 2))), groundtruth)
    assert result["precision_recall_available"] is False
    assert "draft annotations" in result["excluded_scopes"][0]["reason"]
    assert result["full_acceptance"] is False


def test_wrong_class_is_error_only_when_both_classes_are_reviewed():
    groundtruth = reference([annotation("r", "door", (2, 2))], scopes=[scope(kinds=["door", "window"])])
    result = evaluate_reference(model(prediction("p", "window", (2, 2))), groundtruth)
    assert result["per_class"]["door"]["fn"] == 1
    assert result["per_class"]["window"]["fp"] == 1


def test_reviewer_provenance_never_promotes_assistant_labels_to_user_confirmation():
    groundtruth = reference([annotation("r", "door", (2, 2), reviewer={"id": "assistant", "role": "assistant"},
                                       provenance={"method": "visual annotation"})],
                            scopes=[scope(reviewer={"id": "human", "role": "user"})], annotation_status="complete")
    result = evaluate_reference(model(prediction("p", "door", (2, 2))), groundtruth)
    assert result["reviewer_roles"] == ["assistant", "user"]
    assert result["user_confirmed"] is False
    assert result["reference_annotations"][0]["provenance"]["method"] == "visual annotation"


def test_load_reference_binds_source_window_and_region(tmp_path):
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(reference([])))
    source = SimpleNamespace(source_sha256=SHA)
    region = PlanRegion("selected-plan", (0,0,50,50), origin=(5, 10))
    loaded = load_reference(path, source, region)
    assert loaded["registration_origin"] == [5, 10]
    assert loaded["reference_path"] == str(path)
    with pytest.raises(ValueError, match="source_sha256"):
        load_reference(path, SimpleNamespace(source_sha256="b"*64), region)
    with pytest.raises(ValueError, match="bounds"):
        load_reference(path, source, replace(region, bounds=(0,0,60,50)))
    with pytest.raises(ValueError, match="region_id"):
        load_reference(path, source, replace(region, id="other"))
    with pytest.raises(ValueError, match="source hash"):
        evaluate_reference(replace(model(), source_sha256="b"*64), loaded)


def test_dimension_benchmark_requires_reviewed_final_model_measurement():
    groundtruth = reference([], dimensions=[{"id": "length", "expected_ft": 10., "status": "reviewed"}])
    check = DimensionCheck("length", (0,0), (10,0), 10., 10., 0., "model-face", "verified")
    result = evaluate_reference(replace(model(), dimension_checks=(check,)), groundtruth)
    assert result["dimensions"][0]["within_tolerance"] is True
    result = evaluate_reference(replace(model(), dimension_checks=(replace(check, basis="source-endpoints"),)), groundtruth)
    assert result["dimensions"][0]["verified_against_model"] is False
    assert result["dimensions"][0]["within_tolerance"] is None
