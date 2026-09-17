"""Evaluate source-bound annotations without treating unreviewed areas as truth.

Reference JSON (symbol geometry and scope bounds are registered MODEL FEET):

    {
      "schema_version": 1,
      "source_sha256": "<64 hexadecimal characters>",
      "region_id": "selected-plan",
      "bounds": [0, 0, 1200, 1800],
      "coordinate_system": "model-feet",
      "annotation_status": "partial",
      "reviewer": {"id": "reviewer-name", "role": "assistant"},
      "symbols": [{"id": "door-1", "kind": "door", "position": [5, 8],
                   "boundary": [[4,7],[6,7],[6,9],[4,9],[4,7]],
                   "source_ids": ["CAD-HANDLE"], "status": "reviewed"}],
      "scopes": [{"id": "door-zone", "bounds_ft": [0,0,10,12],
                  "kinds": ["door"], "complete": true, "status": "reviewed"}]
    }

`bounds` is the original SOURCE-coordinate region window; `region_id`, source
hash and region window must match the selected input. `symbols` may use position
or a valid closed boundary (the latter supplies a centroid). Individual source
IDs are optional and never interpreted as layers. `matching` optionally supplies
location_tolerance_in (2), min_iou (0.5), and min_source_overlap (1.0).

Only complete, explicitly reviewed bbox/class scopes enable precision/recall.
Objects crossing a scope edge remain unscored unless another complete scope
contains them. Partial annotation sets still produce diagnostic one-to-one
matches, but predictions outside reviewed scopes are never false positives.
Assistant review is labelled as such; it is never promoted to user confirmation.
Even annotation_status='complete' describes declared scopes, not BIM acceptance.

Optional `dimensions` records require id, expected_ft and status='reviewed';
model_check_id defaults to id. They compare against associated final-model
DimensionChecks, not arbitrary nearby lengths. Wall-coverage metrics are not
implemented. No benchmark result changes model acceptance flags.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re

import networkx as nx
from shapely.geometry import Point, Polygon, box

REVIEW_ROLES = {"user", "assistant", "external", "unknown"}
STATUSES = {"draft", "reviewed"}


def _number(value, label, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError(f"{label} must be a finite {'positive ' if positive else ''}number")
    return float(value)


def _coordinates(value, count, label):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f"{label} requires {count} numeric coordinates")
    result = [_number(v, label) for v in value]
    if count == 4 and (result[0] >= result[2] or result[1] >= result[3]):
        raise ValueError(f"{label} must have positive width and height")
    return result


def _reviewer(value):
    if value is None:
        value = {"role": "unknown"}
    if (not isinstance(value, dict) or not isinstance(value.get("role", "unknown"), str)
            or value.get("role", "unknown") not in REVIEW_ROLES):
        raise ValueError("reviewer must be an object with role user, assistant, external or unknown")
    result = deepcopy(value)
    result.setdefault("role", "unknown")
    for key in ("id", "name"):
        if key in result and not isinstance(result[key], str):
            raise ValueError(f"reviewer.{key} must be a string")
    return result


def _records(data, field, reviewer, default_status):
    records = data.get(field, [])
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError(f"reference.{field} must be a list of objects")
    seen = set()
    normalized = []
    for original in records:
        record = deepcopy(original)
        rid = record.get("id")
        if not isinstance(rid, str) or not rid.strip() or rid in seen:
            raise ValueError(f"reference.{field} IDs must be nonempty unique strings")
        seen.add(rid)
        record["status"] = record.get("status", default_status)
        if not isinstance(record["status"], str) or record["status"] not in STATUSES:
            raise ValueError(f"reference.{field} status must be draft or reviewed")
        record["reviewer"] = _reviewer(record.get("reviewer", reviewer))
        normalized.append(record)
    return normalized


def _normalize_reference(data):
    if not isinstance(data, dict):
        raise ValueError("reference must be a JSON object")
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("reference must contain finite JSON values") from exc
    result = deepcopy(data)
    if result.get("schema_version", 1) != 1:
        raise ValueError("unsupported reference schema_version")
    result["schema_version"] = 1
    sha = result.get("source_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise ValueError("reference source_sha256 is required and must be a SHA-256 hex digest")
    result["source_sha256"] = sha.lower()
    if not isinstance(result.get("region_id"), str) or not result["region_id"].strip():
        raise ValueError("reference region_id must be a nonempty string")
    result["bounds"] = _coordinates(result.get("bounds"), 4, "reference.bounds")
    if result.get("coordinate_system", "model-feet") != "model-feet":
        raise ValueError("reference symbols and scopes must use registered model-feet")
    result["coordinate_system"] = "model-feet"
    result["annotation_status"] = result.get("annotation_status", "partial")
    if not isinstance(result["annotation_status"], str) or result["annotation_status"] not in {"partial", "complete"}:
        raise ValueError("annotation_status must be partial or complete")
    result["reviewer"] = _reviewer(result.get("reviewer"))
    default_status = result.get("status", "draft")
    if not isinstance(default_status, str) or default_status not in STATUSES:
        raise ValueError("reference status must be draft or reviewed")
    result["status"] = default_status
    settings = result.get("matching", {})
    if not isinstance(settings, dict):
        raise ValueError("reference.matching must be an object")
    result["matching"] = {
        "location_tolerance_in": _number(settings.get("location_tolerance_in", 2.0), "location_tolerance_in", positive=True),
        "min_iou": _number(settings.get("min_iou", .5), "min_iou", positive=True),
        "min_source_overlap": _number(settings.get("min_source_overlap", 1.0), "min_source_overlap", positive=True),
    }
    if result["matching"]["min_iou"] > 1 or result["matching"]["min_source_overlap"] > 1:
        raise ValueError("IoU/source-overlap thresholds must be at most 1")
    result["symbols"] = _records(result, "symbols", result["reviewer"], default_status)
    for symbol in result["symbols"]:
        if not isinstance(symbol.get("kind"), str) or not symbol["kind"].strip():
            raise ValueError("reference symbol.kind must be a nonempty string")
        boundary = symbol.get("boundary")
        if boundary is not None:
            if not isinstance(boundary, list) or len(boundary) < 4:
                raise ValueError("reference symbol.boundary requires a closed polygon ring")
            boundary = [_coordinates(p, 2, "symbol.boundary") for p in boundary]
            polygon = Polygon(boundary)
            if boundary[0] != boundary[-1] or not polygon.is_valid or polygon.area <= 0:
                raise ValueError("reference symbol.boundary must be closed, valid and positive-area")
            symbol["boundary"] = boundary
            if "position" not in symbol:
                symbol["position"] = [polygon.centroid.x, polygon.centroid.y]
        symbol["position"] = _coordinates(symbol.get("position"), 2, "symbol.position")
        source_ids = symbol.get("source_ids", [])
        if not isinstance(source_ids, list) or any(not isinstance(s, str) or not s for s in source_ids):
            raise ValueError("reference symbol.source_ids must be a list of nonempty strings")
        symbol["source_ids"] = sorted(set(source_ids))
    result["scopes"] = _records(result, "scopes", result["reviewer"], default_status)
    for scope in result["scopes"]:
        scope["bounds_ft"] = _coordinates(scope.get("bounds_ft"), 4, "scope.bounds_ft")
        kinds = scope.get("kinds")
        if not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) or not k for k in kinds):
            raise ValueError("scope.kinds must explicitly name one or more symbol classes")
        scope["kinds"] = sorted(set(kinds))
        scope["complete"] = scope.get("complete", False)
        if not isinstance(scope["complete"], bool):
            raise ValueError("scope.complete must be a boolean")
    result["dimensions"] = _records(result, "dimensions", result["reviewer"], default_status)
    for dimension in result["dimensions"]:
        dimension["expected_ft"] = _number(dimension.get("expected_ft"), "dimension.expected_ft", positive=True)
        dimension["model_check_id"] = dimension.get("model_check_id", dimension["id"])
        if not isinstance(dimension["model_check_id"], str) or not dimension["model_check_id"]:
            raise ValueError("dimension.model_check_id must be a nonempty string")
    return result


def load_reference(path, source, region):
    """Load and bind one reference to a PrimitiveSet and optional PlanRegion.

    With no explicit region, region_id must be 'whole-drawing'. A selected
    PlanRegion requires an identical source window, including after registration.
    """
    reference = _normalize_reference(json.loads(Path(path).read_text()))
    if reference["source_sha256"] != source.source_sha256.lower():
        raise ValueError("reference source_sha256 does not match the input drawing")
    wanted_id = region.id if region is not None else "whole-drawing"
    if reference["region_id"] != wanted_id:
        raise ValueError(f"reference region_id must match {wanted_id!r}")
    if region is not None and any(abs(a-b) > 1e-6 for a, b in zip(reference["bounds"], region.bounds)):
        raise ValueError("reference bounds do not match the selected source-coordinate region")
    reference["reference_path"] = str(Path(path))
    reference["registration_origin"] = list(region.origin) if region is not None else [0., 0.]
    return reference


def _reference_geometry(symbol):
    return Polygon(symbol["boundary"]) if symbol.get("boundary") else Point(symbol["position"])


def _prediction(symbol):
    if not all(math.isfinite(v) for v in (*symbol.position, symbol.width_ft, symbol.depth_ft, symbol.rotation_rad)):
        raise ValueError(f"prediction {symbol.id!r} has nonfinite geometry")
    geometry = None
    if len(symbol.boundary) >= 4:
        candidate = Polygon(symbol.boundary)
        if candidate.is_valid and candidate.area > 0:
            geometry = candidate
    if geometry is None and symbol.width_ft > 0 and symbol.depth_ft > 0:
        c, s = math.cos(symbol.rotation_rad), math.sin(symbol.rotation_rad)
        points = [(symbol.position[0]+x*c-y*s, symbol.position[1]+x*s+y*c)
                  for x,y in ((-symbol.width_ft/2,-symbol.depth_ft/2), (symbol.width_ft/2,-symbol.depth_ft/2),
                              (symbol.width_ft/2,symbol.depth_ft/2), (-symbol.width_ft/2,symbol.depth_ft/2))]
        geometry = Polygon(points)
    if geometry is None:
        geometry = Point(symbol.position)
    return {"id": symbol.id, "kind": symbol.kind, "position": tuple(symbol.position),
            "source_ids": set(symbol.source_ids), "geometry": geometry}


def _in_scope(kind, geometry, scope):
    return kind in scope["kinds"] and box(*scope["bounds_ft"]).covers(geometry)


def _pair(reference, prediction, settings):
    if reference["kind"] != prediction["kind"]:
        return None
    distance_in = math.dist(reference["position"], prediction["position"]) * 12
    ref_shape = _reference_geometry(reference)
    pred_shape = prediction["geometry"]
    iou = None
    if ref_shape.geom_type == pred_shape.geom_type == "Polygon":
        union = ref_shape.union(pred_shape).area
        iou = ref_shape.intersection(pred_shape).area / union if union > 0 else 0.
    rids, pids = set(reference["source_ids"]), prediction["source_ids"]
    overlap = len(rids & pids) / min(len(rids), len(pids)) if rids and pids else None
    source_match = overlap is not None and overlap >= settings["min_source_overlap"]
    location_match = distance_in <= settings["location_tolerance_in"] + 1e-9
    iou_match = iou is not None and iou >= settings["min_iou"]
    if not (source_match or location_match or iou_match):
        return None
    return {"reference_id": reference["id"], "prediction_id": prediction["id"], "kind": reference["kind"],
            "location_error_in": distance_in, "location_within_tolerance": location_match,
            "iou": iou, "iou_above_threshold": iou_match if iou is not None else None,
            "source_overlap": overlap, "source_id_match": source_match,
            "match_basis": [name for name, passed in (("source-ids", source_match), ("location", location_match), ("iou", iou_match)) if passed],
            "reviewer": deepcopy(reference["reviewer"]), "annotation_status": reference["status"]}


def _match(references, predictions, settings, reference_scopes=None, prediction_scopes=None):
    """Maximum-cardinality one-to-one assignment, then highest evidence score."""
    graph = nx.Graph()
    pairs = {}
    for ref in sorted(references, key=lambda r: r["id"]):
        for pred in sorted(predictions, key=lambda p: p["id"]):
            if reference_scopes is not None and not reference_scopes[ref["id"]].intersection(prediction_scopes[pred["id"]]):
                continue
            pair = _pair(ref, pred, settings)
            if pair is None:
                continue
            key = (ref["id"], pred["id"])
            pairs[key] = pair
            quality = (4 * float(pair["source_id_match"]) + (pair["iou"] or 0.) +
                       1 / (1 + pair["location_error_in"]))
            graph.add_edge(("r", key[0]), ("p", key[1]), weight=round(quality * 1_000_000))
    result = []
    for a, b in nx.max_weight_matching(graph, maxcardinality=True):
        rid, pid = (a[1], b[1]) if a[0] == "r" else (b[1], a[1])
        result.append(pairs[(rid, pid)])
    return sorted(result, key=lambda p: (p["kind"], p["reference_id"], p["prediction_id"]))


def _scores(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall) if precision + recall else 0.) if precision is not None and recall is not None else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate_reference(model, reference):
    """Return scoped benchmark evidence; never grant full drawing acceptance."""
    reference = _normalize_reference(reference)
    if reference["source_sha256"] != model.source_sha256.lower() or reference["region_id"] != model.region_id:
        raise ValueError("reference does not match model source hash and region ID")
    predictions = [_prediction(s) for s in model.symbols]
    if len({p["id"] for p in predictions}) != len(predictions):
        raise ValueError("prediction IDs must be unique for one-to-one evaluation")
    annotations = reference["symbols"]
    reviewed = [s for s in annotations if s["status"] == "reviewed"]
    scopes, excluded_scopes = [], []
    for scope in reference["scopes"]:
        reason = None
        if not scope["complete"] or scope["status"] != "reviewed":
            reason = "scope completeness is not explicitly reviewed"
        elif any(s["status"] != "reviewed" and _in_scope(s["kind"], _reference_geometry(s), scope) for s in annotations):
            reason = "scope contains draft annotations, so completeness is unresolved"
        if reason:
            excluded_scopes.append({"id": scope["id"], "reason": reason})
        else:
            scopes.append(scope)
    ref_scopes = {s["id"]: {scope["id"] for scope in scopes if _in_scope(s["kind"], _reference_geometry(s), scope)} for s in reviewed}
    pred_scopes = {s["id"]: {scope["id"] for scope in scopes if _in_scope(s["kind"], s["geometry"], scope)} for s in predictions}
    scored_refs = [s for s in reviewed if ref_scopes[s["id"]]]
    scored_preds = [s for s in predictions if pred_scopes[s["id"]]]
    matches = _match(scored_refs, scored_preds, reference["matching"], ref_scopes, pred_scopes)
    observations = _match(reviewed, predictions, reference["matching"])
    matched_refs, matched_preds = {p["reference_id"] for p in matches}, {p["prediction_id"] for p in matches}
    false_negatives = sorted(s["id"] for s in scored_refs if s["id"] not in matched_refs)
    false_positives = sorted(s["id"] for s in scored_preds if s["id"] not in matched_preds)
    per_class = {}
    for kind in sorted({kind for scope in scopes for kind in scope["kinds"]}):
        tp = sum(p["kind"] == kind for p in matches)
        fp = sum(p["kind"] == kind and p["id"] not in matched_preds for p in scored_preds)
        fn = sum(p["kind"] == kind and p["id"] not in matched_refs for p in scored_refs)
        per_class[kind] = _scores(tp, fp, fn)
    reviewer_roles = sorted({r["reviewer"]["role"] for r in (*scopes, *scored_refs)})
    check_lookup = {c.id: c for c in model.dimension_checks}
    dimension_results = []
    for d in reference["dimensions"]:
        check = check_lookup.get(d["model_check_id"])
        actual = check.actual_ft if check is not None else None
        verified_basis = (check is not None and check.status in {"verified", "failed"} and
                          any(marker in check.basis.lower() for marker in ("model", "reconstructed", "wall")))
        usable = d["status"] == "reviewed" and verified_basis and actual is not None and math.isfinite(actual)
        error = abs(actual - d["expected_ft"]) * 12 if usable else None
        dimension_results.append({"id": d["id"], "model_check_id": d["model_check_id"],
                                  "expected_ft": d["expected_ft"], "actual_ft": actual if actual is not None and math.isfinite(actual) else None,
                                  "error_in": error, "reviewer": deepcopy(d["reviewer"]), "status": d["status"],
                                  "verified_against_model": usable,
                                  "within_tolerance": error <= 2.0 + 1e-8 if error is not None else None})
    metadata = {key: deepcopy(reference[key]) for key in (
        "schema_version", "source_sha256", "region_id", "bounds", "coordinate_system",
        "annotation_status", "reviewer", "status", "matching")}
    for key in ("reference_path", "registration_origin", "provenance"):
        if key in reference:
            metadata[key] = deepcopy(reference[key])
    return {
        "reference": metadata,
        "evaluation_scope": "only explicitly complete reviewed bbox/class scopes",
        "detection_match_policy": "same class AND (source-ID overlap OR location tolerance OR polygon IoU); geometry flags are reported separately",
        "annotation_status": reference["annotation_status"],
        "precision_recall_available": bool(scopes),
        "full_acceptance": False,
        "acceptance_note": "Scoped reference measurements do not establish whole-drawing or BIM acceptance",
        "reviewer_roles": reviewer_roles,
        "user_confirmed": bool(scopes) and reviewer_roles == ["user"],
        "counts": {**_scores(len(matches), len(false_positives), len(false_negatives)),
                   "reference_symbols": len(annotations), "predicted_symbols": len(predictions),
                   "scored_reference_symbols": len(scored_refs), "scored_predictions": len(scored_preds),
                   "matched_annotations": len(observations),
                   "localized_matches": sum(m["location_within_tolerance"] for m in matches),
                   "iou_tested_matches": sum(m["iou"] is not None for m in matches),
                   "iou_passed_matches": sum(m["iou_above_threshold"] is True for m in matches),
                   "source_linked_matches": sum(m["source_id_match"] for m in matches)},
        "per_class": per_class,
        "matches": matches,
        "annotation_matches": observations,
        "unmatched_reference_ids": false_negatives,
        "unmatched_prediction_ids": false_positives,
        "unscored_reference_ids": sorted(s["id"] for s in annotations if not ref_scopes.get(s["id"])),
        "unscored_prediction_ids": sorted(s["id"] for s in predictions if not pred_scopes[s["id"]]),
        "scored_scopes": deepcopy(scopes), "excluded_scopes": excluded_scopes,
        "reference_annotations": deepcopy(annotations),
        "dimensions": dimension_results,
        "walls": {"evaluated": False, "reason": "wall source coverage is not implemented"},
    }
