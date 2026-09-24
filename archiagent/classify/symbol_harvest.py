"""Propose library candidates from the block instances in a drawing.

Two paths reach a candidate. A block whose name resolves to a role is proposed
because it says what it is; a shape repeated often enough is proposed because it
recurs, whatever it is called. The corpus needs both: roughly three quarters of
its block instances carry names that mean nothing, and the same glyph appears in
unrelated drawings under those names.

Nothing here approves anything. A candidate is written for review, with a
preview, and only a reviewer moves it into the library.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

from archiagent.classify.library_templates import _key, normalise
from archiagent.classify.rules import named_role
from archiagent.classify.roles import Role
from archiagent.classify.shapes import parts_from

# A shape repeated at least this often is worth a reviewer's attention even when
# its name says nothing.
MIN_INSTANCES = 8

HARVEST_KINDS = {Role.PLUMBING: "plumbing", Role.FURNITURE: "furniture",
                 Role.VEHICLE: "vehicle", Role.ELECTRICAL: "electrical"}


def _roots(ps):
    """Map each source id to the top-level INSERT entity that produced it."""
    parent = {e.id: e.parent_id for e in ps.entities}
    root = {}
    for sid in parent:
        seen, cursor = set(), sid
        while parent.get(cursor) and cursor not in seen:
            seen.add(cursor)
            cursor = parent[cursor]
        root[sid] = cursor
    return root


def _instances(ps, units_per_foot):
    """Group parts by the block instance they belong to."""
    root = _roots(ps)
    by_id = {e.id: e for e in ps.entities}
    groups = defaultdict(list)
    for part in parts_from(ps, units_per_foot):
        top = root.get(part.source_id, part.source_id)
        entity = by_id.get(top)
        if entity is not None and entity.block_name:
            groups[top].append(part)
    return groups, by_id


def _paths(parts):
    return [{"category": part.signature[0],
             "points": [[x, y] for x, y in part.points],
             "holes": [[[x, y] for x, y in ring] for ring in part.holes]}
            for part in parts]


def _extent(parts):
    xs = [x for p in parts for x, _ in p.points]
    ys = [y for p in parts for _, y in p.points]
    return max(max(xs)-min(xs), max(ys)-min(ys)) if xs else 0.0


def harvest(ps, units_per_foot, *, min_instances: int = MIN_INSTANCES) -> tuple[dict, ...]:
    """Propose candidate templates from one drawing's block instances."""
    groups, by_id = _instances(ps, units_per_foot)
    families = defaultdict(list)
    for top, parts in groups.items():
        if len(parts) < 1:
            continue
        shape = tuple(sorted(_key(p.signature) for p in parts))
        families[shape].append((top, parts))

    candidates = []
    for shape, members in sorted(families.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        names = {by_id[top].block_name for top, _ in members if top in by_id}
        roles = {named_role(name) for name in names}
        kinds = {HARVEST_KINDS[r] for r in roles if r in HARVEST_KINDS}
        if len(members) < min_instances and not kinds:
            continue
        sizes = [_extent(parts) for _, parts in members]
        sizes = [s for s in sizes if s > 0]
        if not sizes:
            continue
        _, representative = max(members, key=lambda m: len(m[1]))
        digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:8]
        kind = sorted(kinds)[0] if len(kinds) == 1 else "unreviewed"
        candidates.append({
            "id": f"{kind}-{digest}",
            "kind": kind,
            "subtype": "unreviewed",
            "label": "",
            "geometry": [dict(path) for path in normalise(_paths(representative))],
            # Widened either side, then rounded: a reviewer narrows this, and a
            # range that excludes the instances it came from helps nobody.
            "size_ft": {"min": round(min(sizes)*.75, 3), "max": round(max(sizes)*1.25, 3)},
            "mirror_allowed": False,
            "provenance": [{"source_sha256": ps.source_sha256,
                            "block_name_sha256": hashlib.sha256(
                                "|".join(sorted(names)).encode()).hexdigest(),
                            "instances": len(members)}],
            "status": "candidate",
            "reviewed_by": "",
            "found_by": "name" if kinds else "frequency",
        })
    return tuple(candidates)


def merge(existing: tuple[dict, ...], found: tuple[dict, ...]) -> tuple[dict, ...]:
    """Union candidates across drawings, summing provenance for one shape."""
    by_id = {c["id"]: dict(c) for c in existing}
    for candidate in found:
        previous = by_id.get(candidate["id"])
        if previous is None:
            by_id[candidate["id"]] = dict(candidate)
            continue
        previous["provenance"] = list(previous["provenance"]) + list(candidate["provenance"])
        previous["size_ft"] = {
            "min": min(previous["size_ft"]["min"], candidate["size_ft"]["min"]),
            "max": max(previous["size_ft"]["max"], candidate["size_ft"]["max"])}
    return tuple(sorted(by_id.values(), key=lambda c: c["id"]))


def write_preview(candidate: dict, out_png: Path) -> Path | None:
    """Draw the normalised glyph so a reviewer can name it. None without matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:                                 # noqa: BLE001 - optional extra
        return None
    figure, axes = plt.subplots(figsize=(2.5, 2.5), dpi=120)
    for path in candidate["geometry"]:
        for ring in (path["points"], *path.get("holes", ())):
            axes.plot([p[0] for p in ring], [p[1] for p in ring], color="#000000", linewidth=.6)
    axes.set_aspect("equal")
    axes.axis("off")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_png, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(figure)
    return out_png
