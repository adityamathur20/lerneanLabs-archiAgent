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
KINDS = ("wall", "profile_wall", "door", "window", "opening", "space", "column", "stair", "beam", "void", "railing")
REASONS = {
    "wall": ["not a wall", "wrong position", "wrong thickness", "wrong height", "should be railing",
             "site or boundary wall", "duplicate", "other"],
    "door": ["not a door", "wrong position", "wrong width", "wrong host wall", "other"],
    "window": ["not a window", "wrong position", "wrong width", "other"],
    "opening": ["not an opening", "wrong position", "wrong width", "other"],
    "space": ["not a room", "merged rooms", "wrong label", "should be void", "other"],
    "column": ["not a column", "wrong size", "other"],
    "profile_wall": ["not a wall", "wrong shape", "wrong height", "should be railing", "other"],
    "stair": ["not a stair", "other"],
    "beam": ["not a beam", "other"],
}
_SYMBOL_KINDS = {"door", "window", "column", "stair", "beam"}


def _mid(a, b):
    return [round((a[0] + b[0]) / 2, 2), round((a[1] + b[1]) / 2, 2)]


def _centroid(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return [round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)]


PREFIX = {"wall": "W", "profile_wall": "WP", "door": "D", "window": "WN", "opening": "O", "space": "R",
          "column": "C", "stair": "ST", "beam": "B"}


def _labelled(model: dict) -> list[tuple[dict, dict]]:
    """(element, geometry) pairs in a fixed order; labels are numbered per kind from 1.

    The label is what a reviewer reads on the sheet and writes about in the JSON.
    A symbol already represented by an opening (its door or window) is not listed twice.
    """
    pairs = []
    for i, w in enumerate(model.get("walls", [])):
        pairs.append(({"id": f"wall:{i}", "kind": "wall", "at_ft": _mid(w["start"], w["end"]),
                       "layer": w["source_layer"], "thickness_in": round(w["thickness_ft"] * 12, 1),
                       "length_ft": round(math.dist(w["start"], w["end"]), 1),
                       "height_ft": w.get("height_ft") or model.get("wall_height_ft"),
                       "detector": w["detector"], "source_ids": list(w.get("source_ids", []))},
                      {"wall": (w["start"], w["end"], w["thickness_ft"]), "low": bool(w.get("height_ft"))}))
    for pr in model.get("wall_profiles", []):
        ring = pr.get("boundary") or []
        if len(ring) >= 3:
            pairs.append(({"id": f"profile:{pr['id']}", "kind": "profile_wall", "at_ft": _centroid(ring),
                           "layer": pr.get("source_layer", ""), "source_ids": list(pr.get("source_ids", []))},
                          {"profile": ring}))
    hosted = set()
    for o in model.get("openings", []):
        hosted.add(o.get("symbol_id", ""))
        kind = o["kind"] if o["kind"] in ("door", "window") else "opening"
        pairs.append(({"id": f"opening:{o['id']}", "kind": kind,
                       "at_ft": _mid(o["start"], o["end"]), "width_ft": round(math.dist(o["start"], o["end"]), 1),
                       "symbol_id": o.get("symbol_id", ""), "evidence": o.get("evidence", ""),
                       "source_ids": list(o.get("source_ids", []))},
                      {"line": (o["start"], o["end"])}))
    for i, sp in enumerate(model.get("spaces", [])):
        pairs.append(({"id": f"space:{i}", "kind": "space", "at_ft": _centroid(sp["boundary"]),
                       "area_sqft": round(sp["area_sqft"]), "label_text": sp.get("label", "")},
                      {"ring": sp["boundary"]}))
    for sy in model.get("symbols", []):
        if sy["kind"] in _SYMBOL_KINDS and sy["id"] not in hosted:
            pairs.append(({"id": f"symbol:{sy['id']}", "kind": sy["kind"],
                           "at_ft": [round(v, 2) for v in sy["position"]],
                           "width_ft": round(sy["width_ft"], 1), "evidence": sy.get("evidence", ""),
                           "source_ids": list(sy.get("source_ids", []))},
                          {"point": tuple(sy["position"]), "ring": sy.get("boundary") or ()}))
    counts: dict[str, int] = defaultdict(int)
    for e, _ in pairs:
        counts[e["kind"]] += 1
        e["label"] = f"{PREFIX[e['kind']]}{counts[e['kind']]}"
        e.update({"verdict": None, "reason": None, "note": ""})
    return pairs


def elements(model: dict) -> list[dict]:
    return [e for e, _ in _labelled(model)]


def write_template(manifest_path, out_path) -> dict:
    """A feedback file for every model in an interpretation manifest."""
    doc = json.loads(Path(manifest_path).read_text())
    models = doc["models"] if isinstance(doc, dict) and "models" in doc else [doc]
    template = {
        "how_to": ("Open the .feedback.svg beside this file: every element is drawn and labelled there (W12, D3, "
                   "WN2, R4 ...). Find the label, then set that element's verdict to correct, wrong or unsure; for "
                   "wrong, give a reason from reasons[kind]. Add anything the model did not build to missing, "
                   "with the nearest label in note. Leave verdict null to skip."),
        "verdicts": list(VERDICTS), "reasons": REASONS, "missing_kinds": list(KINDS),
        "source": {"manifest": str(Path(manifest_path).resolve()), "source_sha256": doc.get("source_sha256", "")
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


_COLOURS = {"wall": "#4a4a4a", "low": "#e8710a", "door": "#1e8e3e", "window": "#1a73e8", "opening": "#9334e6",
            "space": "#fce8b2", "column": "#a50e0e", "stair": "#00796b", "beam": "#795548"}


def _background(model: dict):
    """The floor's own drawing, in feet, as thin grey polylines (empty when the source is not on disk)."""
    source = model.get("source_path") or ""
    upf = (model.get("scale") or {}).get("units_per_foot")
    if not source or not upf or not Path(source).is_file():
        return []
    from archiagent.ingest.dxf_vector import load_dxf
    ps, _ = load_dxf(source, upf)
    return [[(x / upf, y / upf) for x, y in p.coords] + ([] if not p.closed else [(p.coords[0][0] / upf, p.coords[0][1] / upf)])
            for p in ps.primitives if len(p.coords) >= 2]


def write_sheet(manifest_path, out_path) -> Path:
    """An SVG of each floor with every element drawn and labelled exactly as in the feedback JSON.

    Walls are grey (orange when lowered, e.g. a railing), doors green, windows blue, rooms
    shaded; the source drawing sits underneath in light grey. Open it in a browser and zoom.
    """
    doc = json.loads(Path(manifest_path).read_text())
    models = doc["models"] if isinstance(doc, dict) and "models" in doc else [doc]
    parts, labels, x_offset, height = [], [], 0.0, 0.0
    for m in models:
        pairs = _labelled(m)
        pts = [p for e, g in pairs for p in _points(g)]
        if not pts:
            continue
        x0, y0 = min(p[0] for p in pts) - 5, min(p[1] for p in pts) - 5
        x1, y1 = max(p[0] for p in pts) + 5, max(p[1] for p in pts) + 5
        dx = x_offset - x0
        tx = lambda p: (round(p[0] + dx, 3), round(y1 - p[1], 3))       # y up in the drawing, down in SVG
        for line in _background(m):
            if all(not (x0 <= x <= x1 and y0 <= y <= y1) for x, y in line):
                continue
            parts.append(f'<polyline points="{_pts(line, tx)}" class="bg"/>')
        for e, g in pairs:
            if "ring" in g and g["ring"] and e["kind"] == "space":
                parts.append(f'<polygon points="{_pts(g["ring"], tx)}" class="space"/>')
            if "profile" in g:
                parts.append(f'<polygon points="{_pts(g["profile"], tx)}" fill="{_COLOURS["wall"]}" opacity="0.6"/>')
        for e, g in pairs:
            if "wall" in g:
                a, b, t = g["wall"]
                n = math.dist(a, b) or 1.0
                nx, ny = -(b[1] - a[1]) / n * t / 2, (b[0] - a[0]) / n * t / 2
                quad = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
                parts.append(f'<polygon points="{_pts(quad, tx)}" fill="{_COLOURS["low" if g["low"] else "wall"]}" opacity="0.75"/>')
            elif "line" in g:
                parts.append(f'<polyline points="{_pts(g["line"], tx)}" stroke="{_COLOURS[e["kind"]]}" class="op"/>')
            elif "point" in g:
                cx, cy = tx(g["point"])
                parts.append(f'<circle cx="{cx}" cy="{cy}" r="0.6" fill="{_COLOURS[e["kind"]]}"/>')
            lx, ly = tx(e["at_ft"])
            colour = (_COLOURS["low"] if g.get("low") else "#8a4b00" if e["kind"] == "space"
                      else _COLOURS.get(e["kind"], "#000"))
            labels.append(f'<text x="{lx}" y="{ly}" fill="{colour}">{e["label"]}</text>')
        storey = m.get("storey_name") or ""
        title = Path(manifest_path).name.split(".")[0] + ("" if storey in ("", "Unassigned plan") else f" - {storey}")
        labels.append(f'<text x="{x_offset}" y="-1.5" class="title">{title}</text>')
        x_offset += (x1 - x0) + 10
        height = max(height, y1 - y0)
    style = ("<style>.bg{fill:none;stroke:#bbb;stroke-width:0.05}.space{fill:#fce8b2;opacity:.45}"
             ".op{fill:none;stroke-width:0.5}text{font:0.9px sans-serif;text-anchor:middle;dominant-baseline:middle;"
             "paint-order:stroke;stroke:#fff;stroke-width:0.25}.title{font-size:2px;text-anchor:start}</style>")
    legend = ("<!-- W wall (orange = lowered, e.g. railing), WP solid wall outline, D door, WN window, O opening, R room, "
              "C column, ST stair, B beam -->")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -4 {x_offset + 2:.1f} {height + 6:.1f}" '
           f'style="background:#fff">{legend}{style}{"".join(parts)}{"".join(labels)}</svg>')
    out = Path(out_path)
    out.write_text(svg)
    return out


def _points(g):
    if "profile" in g:
        return list(g["profile"])
    if "wall" in g:
        return [g["wall"][0], g["wall"][1]]
    if "line" in g:
        return list(g["line"])
    if "point" in g:
        return [g["point"]]
    return list(g.get("ring", ()))


def _pts(points, tx):
    return " ".join("%s,%s" % tx(p) for p in points)


# --- feedback as reconcile precedence on a re-run of the same drawing ---------------------------

from dataclasses import dataclass, field, replace as _replace


@dataclass(frozen=True)
class FeedbackDecisions:
    reject_sets: tuple[frozenset[str], ...] = ()
    class_sets: tuple[tuple[frozenset[str], str], ...] = ()
    missing: tuple[dict, ...] = ()

    @property
    def reject_source_ids(self) -> frozenset[str]:
        return frozenset().union(*self.reject_sets) if self.reject_sets else frozenset()


def _wall_class_from(reason: str, note: str) -> str:
    text = f"{reason or ''} {note or ''}".casefold()
    if "railing" in text:
        return "railing"
    if "pool" in text or "-z" in text:
        return "pool_edge"
    if "boundary" in text or "compound" in text or "site wall" in text:
        return "site_wall"
    if "parapet" in text:
        return "parapet"
    return ""


def resolve_manifest(feedback_path, manifest: str) -> Path | None:
    """The manifest a feedback file names, even when it was written relative to another folder."""
    if not manifest:
        return None
    feedback_path = Path(feedback_path).resolve()
    stripped = Path(*[part for part in Path(manifest).parts if part not in ("..", ".")])
    for candidate in (Path(manifest), *(a / manifest for a in feedback_path.parents),
                      *(a / stripped for a in feedback_path.parents)):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _sha_of(doc: dict, path: Path) -> set[str]:
    shas = {doc.get("source", {}).get("source_sha256") or ""}
    found = resolve_manifest(path, doc.get("source", {}).get("manifest") or "")
    if found is not None:
        m = json.loads(found.read_text())
        shas |= {x.get("source_sha256", "") for x in m.get("models", [])}
    return shas - {""}


def decisions_for(paths, source_sha256: str) -> FeedbackDecisions:
    """The owner's wall verdicts for this drawing (matched by its hash); other drawings' are ignored."""
    rejects, classes, missing = [], [], []
    for p in paths:
        p = Path(p)
        doc = json.loads(p.read_text())
        if source_sha256 not in _sha_of(doc, p):
            continue
        for m in doc["models"]:
            missing += m.get("missing", [])
            for e in m["elements"]:
                if e.get("kind") != "wall" or e.get("verdict") != "wrong" or not e.get("source_ids"):
                    continue
                ids = frozenset(e["source_ids"])
                cls = _wall_class_from(e.get("reason"), e.get("note"))
                if cls:
                    classes.append((ids, cls))
                elif (e.get("reason") or "") == "not a wall":
                    rejects.append(ids)
    return FeedbackDecisions(tuple(rejects), tuple(classes), tuple(missing))


def _matches(ids: frozenset[str], wall_ids: tuple[str, ...]) -> bool:
    shared = ids.intersection(wall_ids)
    return bool(shared) and len(shared) >= 0.5 * max(1, len(wall_ids))


def apply_feedback(walls, decisions: FeedbackDecisions):
    """(walls with the owner's rejections removed and classes set, notes)."""
    kept, rejected, classed = [], 0, 0
    for w in walls:
        if any(_matches(r, w.source_ids) for r in decisions.reject_sets):
            rejected += 1
            continue
        cls = next((c for ids, c in decisions.class_sets if _matches(ids, w.source_ids)), "")
        if cls:
            w = _replace(w, wall_class=cls)
            classed += 1
        kept.append(w)
    notes = []
    if rejected or classed or decisions.missing:
        notes.append(f"owner feedback: {rejected} wall(s) removed, {classed} given a class, "
                     f"{len(decisions.missing)} missing element(s) noted")
    return tuple(kept), tuple(notes)
