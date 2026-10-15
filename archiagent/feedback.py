"""Element-level feedback: which of the model's elements were right, and what was missing.

`write_template` lists every element a run created, each with a blank verdict, in a
JSON file a person annotates. `summarize` turns the filled files into precision by
element kind, a recall estimate from the "missing" list, and the reasons given for
wrong elements. That is the measurement everything else (prompts, thresholds, the
symbol library) is improved against.

An element is identified by what the run itself calls it plus where it is, so a
note can be matched in the viewer or the overlay: `wall:<index>` is the wall's index
in the manifest (and in the IFC's ModelWallIndices).
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

VERDICTS = ("correct", "wrong", "unsure")
KINDS = ("wall", "door", "window", "opening", "space", "column", "stair", "beam", "void", "railing")
REASONS = {
    "wall": ["not a wall", "wrong position", "wrong thickness", "wrong height", "should be railing",
             "site or boundary wall", "duplicate", "other"],
    "door": ["not a door", "wrong position", "wrong width", "wrong host wall", "other"],
    "window": ["not a window", "wrong position", "wrong width", "other"],
    "opening": ["not an opening", "wrong position", "wrong width", "other"],
    "space": ["not a room", "merged rooms", "wrong label", "should be void", "other"],
    "column": ["not a column", "wrong size", "other"],
    "stair": ["not a stair", "other"],
    "beam": ["not a beam", "other"],
}
_SYMBOL_KINDS = {"door", "window", "column", "stair", "beam"}


def _mid(a, b):
    return [round((a[0] + b[0]) / 2, 2), round((a[1] + b[1]) / 2, 2)]


def _centroid(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return [round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)]


def elements(model: dict) -> list[dict]:
    out = []
    candidate_source = {c["id"]: c.get("verdict_source") for c in model.get("wall_candidates", [])}
    for i, w in enumerate(model.get("walls", [])):
        out.append({"id": f"wall:{i}", "kind": "wall", "at_ft": _mid(w["start"], w["end"]),
                    "layer": w["source_layer"], "thickness_in": round(w["thickness_ft"] * 12, 1),
                    "length_ft": round(math.dist(w["start"], w["end"]), 1),
                    "height_ft": w.get("height_ft") or model.get("wall_height_ft"),
                    "detector": w["detector"], "source_ids": list(w.get("source_ids", []))})
    for o in model.get("openings", []):
        out.append({"id": f"opening:{o['id']}", "kind": o["kind"] if o["kind"] in ("door", "window") else "opening",
                    "at_ft": _mid(o["start"], o["end"]), "width_ft": round(math.dist(o["start"], o["end"]), 1),
                    "symbol_id": o.get("symbol_id", ""), "evidence": o.get("evidence", ""),
                    "source_ids": list(o.get("source_ids", []))})
    for i, s in enumerate(model.get("spaces", [])):
        out.append({"id": f"space:{i}", "kind": "space", "at_ft": _centroid(s["boundary"]),
                    "area_sqft": round(s["area_sqft"]), "label": s.get("label", "")})
    for s in model.get("symbols", []):
        if s["kind"] in _SYMBOL_KINDS:
            out.append({"id": f"symbol:{s['id']}", "kind": s["kind"], "at_ft": [round(v, 2) for v in s["position"]],
                        "width_ft": round(s["width_ft"], 1), "evidence": s.get("evidence", ""),
                        "source_ids": list(s.get("source_ids", []))})
    for e in out:
        e.update({"verdict": None, "reason": None, "note": ""})
    return out


def write_template(manifest_path, out_path) -> dict:
    """A feedback file for every model in an interpretation manifest."""
    doc = json.loads(Path(manifest_path).read_text())
    models = doc["models"] if isinstance(doc, dict) and "models" in doc else [doc]
    template = {
        "how_to": ("Set verdict on each element to correct, wrong or unsure; for wrong, give a reason from "
                   "reasons[kind]. Add anything the model did not build to missing. Leave verdict null to skip."),
        "verdicts": list(VERDICTS), "reasons": REASONS, "missing_kinds": list(KINDS),
        "source": {"manifest": str(manifest_path), "source_sha256": doc.get("source_sha256", "")
                   if isinstance(doc, dict) else "", "versions": doc.get("versions", {}) if isinstance(doc, dict) else {}},
        "models": [{"region_id": m.get("region_id", ""), "storey": m.get("storey_name", ""),
                    "units_per_foot": (m.get("scale") or {}).get("units_per_foot"),
                    "elements": elements(m),
                    "missing": [], "missing_example": {"kind": "window", "at_ft": [0.0, 0.0], "note": ""}}
                   for m in models],
    }
    Path(out_path).write_text(json.dumps(template, indent=2))
    return template


def summarize(paths) -> dict:
    """Precision by kind, a recall estimate, reasons, and how much was left unreviewed."""
    judged: dict[str, Counter] = defaultdict(Counter)
    reasons: dict[str, Counter] = defaultdict(Counter)
    missing: Counter = Counter()
    unreviewed: Counter = Counter()
    for path in paths:
        doc = json.loads(Path(path).read_text())
        for m in doc["models"]:
            for e in m["elements"]:
                v = e.get("verdict")
                if v is None:
                    unreviewed[e["kind"]] += 1
                    continue
                if v not in VERDICTS:
                    raise ValueError(f"{path}: {e['id']} has verdict {v!r}; use one of {VERDICTS}")
                judged[e["kind"]][v] += 1
                if v == "wrong":
                    reasons[e["kind"]][e.get("reason") or "unspecified"] += 1
            for x in m.get("missing", []):
                missing[x["kind"]] += 1
    out = {}
    for kind in sorted(set(judged) | set(missing) | set(unreviewed)):
        c = judged[kind]
        sure = c["correct"] + c["wrong"]
        built_right = c["correct"]
        out[kind] = {
            "reviewed": sum(c.values()), "correct": c["correct"], "wrong": c["wrong"], "unsure": c["unsure"],
            "precision": round(c["correct"] / sure, 3) if sure else None,
            "missing": missing[kind],
            "recall_estimate": round(built_right / (built_right + missing[kind]), 3)
            if (built_right + missing[kind]) else None,
            "unreviewed": unreviewed[kind],
            "wrong_reasons": dict(reasons[kind].most_common()),
        }
    return {"kinds": out, "files": [str(p) for p in paths]}
