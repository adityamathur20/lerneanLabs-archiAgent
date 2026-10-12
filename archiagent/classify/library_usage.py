"""What a run took from the approved symbol library.

One small JSON per run: for each template how many instances matched, at which
orientations, sizes and mirroring, and which templates were reduced or skipped to
fit the matcher's budget. Aggregated across runs it says which approved symbols
earn their place and which never match; it is also what lets a result be traced
to the exact library it used.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import statistics


def _digest(library) -> str:
    canonical = json.dumps(sorted(library, key=lambda t: str(t.get("id", ""))), sort_keys=True,
                           separators=(",", ":"), default=list)
    return hashlib.sha256(canonical.encode()).hexdigest()


def library_usage(symbols, issues, library, *, source_sha256: str, units_per_foot: float) -> dict:
    """Usage of `library` by `symbols` (a run's symbol instances) and its matching `issues`."""
    by_id = {t["id"]: t for t in library}
    seen: dict[str, list] = {t["id"]: [] for t in library}
    for symbol in symbols:
        props = dict(symbol.properties)
        template_id = props.get("library_template_id")
        if template_id in seen:
            seen[template_id].append(props)
    templates = []
    for template_id, record in sorted(by_id.items()):
        found = seen[template_id]
        sizes = [float(p["library_scale_ft"]) for p in found if "library_scale_ft" in p]
        angles = Counter(str(int(float(p["library_angle_deg"]) % 360)) for p in found if "library_angle_deg" in p)
        templates.append({
            "id": template_id, "kind": record.get("kind"), "subtype": record.get("subtype"),
            "label": record.get("label", ""), "space_types": list(record.get("space_types", ())),
            "instances": len(found),
            "angles_deg": dict(sorted(angles.items(), key=lambda kv: int(kv[0]))),
            "mirrored": sum(1 for p in found if p.get("library_mirrored") == "True"),
            "size_ft": ({"min": round(min(sizes), 3), "median": round(statistics.median(sizes), 3),
                         "max": round(max(sizes), 3)} if sizes else None),
        })
    return {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "units_per_foot": units_per_foot,
        "library": {"templates": len(library), "digest": _digest(library)},
        "symbols_total": sum(t["instances"] for t in templates),
        "templates_in_library": len(library),
        "templates_matched": sum(1 for t in templates if t["instances"]),
        "reduced": sorted({i.entity for i in issues if i.code == "library_template_reduced"}),
        "skipped": sorted({i.entity for i in issues if i.code == "library_template_skipped"}),
        "templates": templates,
    }
