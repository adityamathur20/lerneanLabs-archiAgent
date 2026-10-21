"""Candidacy score bands fitted from the owner's element feedback.

Never from too little of it (spec section 9): at least MIN_LABELLED judged walls from
at least MIN_DRAWINGS different drawings, so the bands describe drawings in general
rather than the few that were reviewed.
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_LABELLED = 50
MIN_DRAWINGS = 5
PRECISION = 0.95
GRID = tuple(round(0.05 * k, 2) for k in range(1, 20))


def fit_bands(labelled, drawings: int) -> tuple[float, float]:
    """(reject_ceiling, accept_floor) on a 0.05 grid.

    Maximises walls accepted plus non-walls rejected without asking the model, keeping
    both sides at least PRECISION right; ties go to the narrower decision (more left to the model).
    """
    labelled = [(float(s), bool(w)) for s, w in labelled]
    if len(labelled) < MIN_LABELLED or drawings < MIN_DRAWINGS:
        raise ValueError(f"too little feedback to calibrate: {len(labelled)} labelled walls from {drawings} "
                         f"drawing(s); need {MIN_LABELLED} from {MIN_DRAWINGS}")
    best = None
    for floor in GRID:
        above = [w for s, w in labelled if s >= floor]
        if not above or sum(above) / len(above) < PRECISION:
            continue
        for ceiling in (g for g in GRID if g <= floor):
            below = [not w for s, w in labelled if s <= ceiling]
            if below and sum(below) / len(below) < PRECISION:
                continue
            correct = sum(above) + sum(below)
            key = (correct, floor, -ceiling)
            if best is None or key > best[0]:
                best = (key, ceiling, floor)
    if best is None:
        raise ValueError("no band keeps both sides 95% right on this feedback")
    return best[1], best[2]


def drawing_key(model: dict) -> str:
    """One key per drawing: the floors cut from one sheet (<run>/floors/<id>.dxf) share it."""
    source = Path(model.get("source_path") or "")
    if source.parent.name == "floors":
        return str(source.parent.parent)
    return model.get("source_sha256") or str(source)


def labelled_from_feedback(paths) -> tuple[list[tuple[float, bool]], int]:
    """(score, is_wall) for every judged wall, and how many drawings they came from."""
    out, drawings = [], set()
    for p in paths:
        p = Path(p)
        doc = json.loads(p.read_text())
        from archiagent.feedback import resolve_manifest
        manifest = resolve_manifest(p, doc.get("source", {}).get("manifest", ""))
        if manifest is None:
            continue
        models = json.loads(manifest.read_text())["models"]
        for fm, mm in zip(doc["models"], models):
            candidates = [(frozenset(c["wall"]["source_ids"]), c["score"]) for c in mm.get("wall_candidates", [])]
            for e in fm["elements"]:
                if e.get("kind") != "wall" or e.get("verdict") not in ("correct", "wrong"):
                    continue
                if e["verdict"] == "wrong" and e.get("reason") != "not a wall":
                    continue           # wrong height or place is still a wall
                ids = set(e.get("source_ids", []))
                scores = [s for cids, s in candidates if ids and len(ids & cids) >= 0.5 * len(ids)]
                if scores:
                    out.append((max(scores), e["verdict"] == "correct"))
                    drawings.add(drawing_key(mm))
    return out, len(drawings)
