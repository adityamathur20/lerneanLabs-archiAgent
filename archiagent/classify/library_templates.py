"""Scale- and mirror-tolerant matching of reviewed library shapes.

A library template carries no real size: its geometry is normalised so the larger
bounding-box side is 1. A candidate's scale therefore falls out of the anchor
length ratio and is bounded by the template's declared size range, so no scale
search is needed. As in the drawing-seeded matcher, every template part must
match and only complete source entities are ever claimed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.strtree import STRtree

from archiagent.classify.shapes import measure, parts_from
from archiagent.classify.templates import KINDS
from archiagent.model import Issue
from archiagent.semantic import SymbolInstance

# Fraction of a matched instance's larger side. A tap and a bathtub need
# proportional slack, not the same absolute tolerance.
TOLERANCE_FRACTION = 0.03
MAX_MATCH_OPERATIONS = 200_000

# The orientations a template may declare, in degrees: every 45 degrees. Mirroring
# is a separate switch, so these eight with mirror cover all sixteen poses.
SUPPORTED_ANGLES = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
DEFAULT_ANGLES = (0.0, 90.0, 180.0, 270.0)
# A part's length over its longest chord does not change with scale, rotation or
# mirroring; curve tessellation moves it by a few percent at most.
SHAPE_TOLERANCE = 0.12
NEIGHBOURS_CHECKED = 5

# The kinds of space a library symbol can say it belongs in. A closed list, so a
# typo is refused at load rather than silently never matching a room.
SPACE_TYPES = frozenset({"living", "bedroom", "bathroom", "kitchen", "dining", "parking", "balcony",
                         "terrace", "garden", "staircase", "utility", "store", "corridor", "pooja",
                         "any"})
SPACE_EVIDENCE = frozenset({"strong", "weak"})


class LibraryBudgetExceeded(ValueError):
    """A template cost more to match than the budget allows. The caller skips that template."""

CATEGORIES = {"fill", "curve", "closed", "path"}
DEFAULT_LIBRARY = Path(__file__).resolve().parents[1] / "data" / "symbol_library.json"


def load_library(path=None) -> tuple[dict, ...]:
    """Read reviewed templates, validating each so bad data fails at load.

    An explicitly supplied path that does not exist is a configuration mistake
    and raises. A missing bundled library only means nothing has been harvested
    yet, so it yields no templates and lets the run continue.
    """
    source = Path(path) if path else DEFAULT_LIBRARY
    if not source.is_file():
        if path:
            raise FileNotFoundError(f"symbol library not found: {source}")
        return ()
    try:
        data = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"symbol library {source} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("templates", []), list):
        raise ValueError(f"symbol library {source} must hold a templates list")
    reviewed = [t for t in data.get("templates", []) if isinstance(t, dict)
                and t.get("status") == "reviewed"]
    for record in reviewed:
        _validated(record)
    return tuple(reviewed)


@dataclass(frozen=True)
class _TemplatePart:
    key: tuple
    geometry: object
    points: tuple
    length: float
    holes: int


def _key(signature):
    """Group shapes by what survives discretisation.

    Vertex counts of flattened geometry describe the tessellation, not the
    shape: one hatch boundary yields 181 points in one drawing and 182 in
    another at the same size, and a tessellated curve's count grows with its
    size. Only authored polyline vertices are stable, so counts key only those.
    Hole *count* is topological and stable; hole vertex counts are not.
    """
    category, segments, holes = signature
    if category in {"fill", "curve"}:
        return (category, len(holes))
    return (category, segments, len(holes))


def normalise(paths):
    """Centre paths on their bounding box and scale so the larger side is 1.

    Orientation is left alone: the matcher searches rotations anyway, and the
    principal axis of a near-square glyph is unstable enough that aligning it
    would make a harvested template depend on drafting noise.
    """
    rings = [(path["points"], *path.get("holes", ())) for path in paths]
    points = [p for ring_group in rings for ring in ring_group for p in ring]
    if not points:
        raise ValueError("library template geometry must contain points")
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    larger = max(max(xs)-min(xs), max(ys)-min(ys))
    if not math.isfinite(larger) or larger <= 0:
        raise ValueError("library template geometry must have a positive extent")

    def move(ring):
        return [[(float(x)-cx)/larger, (float(y)-cy)/larger] for x, y in ring]

    return tuple({"category": path["category"], "points": move(path["points"]),
                  "holes": [move(r) for r in path.get("holes", ())]} for path in paths)


def _validated(record):
    if not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError("library template requires a nonempty id")
    if record.get("kind") not in KINDS:
        raise ValueError(f"library template {record['id']} has an unsupported kind")
    paths = record.get("geometry")
    if not isinstance(paths, (list, tuple)) or not paths:
        raise ValueError(f"library template {record['id']} requires geometry")
    for path in paths:
        if not isinstance(path, dict) or path.get("category") not in CATEGORIES:
            raise ValueError(f"library template {record['id']} has an unsupported path category")
        if not isinstance(path.get("points"), (list, tuple)) or len(path["points"]) < 2:
            raise ValueError(f"library template {record['id']} has a path with fewer than two points")
    size = record.get("size_ft")
    if not isinstance(size, dict):
        raise ValueError(f"library template {record['id']} requires a size_ft range")
    low, high = size.get("min"), size.get("max")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (low, high)) or low > high:
        raise ValueError(f"library template {record['id']} needs a positive size_ft min <= max")
    angles = record.get("rotations_deg", DEFAULT_ANGLES)
    if not isinstance(angles, (list, tuple)) or not 1 <= len(angles) <= len(SUPPORTED_ANGLES) or any(
            type(a) not in (int, float) or not math.isfinite(a) or float(a) % 360 not in SUPPORTED_ANGLES
            for a in angles):
        raise ValueError(f"library template {record['id']} needs 1 to {len(SUPPORTED_ANGLES)} rotations "
                         f"from {[int(a) for a in SUPPORTED_ANGLES]} degrees")
    mirror = record.get("mirror_allowed", False)
    if not isinstance(mirror, bool):
        raise ValueError(f"library template {record['id']} mirror_allowed must be a boolean")
    out = {**record, "rotations_deg": tuple(sorted({float(a) % 360 for a in angles})),
           "mirror_allowed": mirror, "size_ft": {"min": float(low), "max": float(high)}}
    if "space_types" in record:
        spaces = record["space_types"]
        if (not isinstance(spaces, (list, tuple)) or not spaces
                or any(s not in SPACE_TYPES for s in spaces)):
            raise ValueError(f"library template {record['id']} has an unknown space type; "
                             f"use a list from {sorted(SPACE_TYPES)}")
        out["space_types"] = tuple(spaces)
    if "space_evidence" in record and record["space_evidence"] not in SPACE_EVIDENCE:
        raise ValueError(f"library template {record['id']} space_evidence must be 'strong' or 'weak'")
    return out


def _template_parts(record):
    out = []
    for path in record["geometry"]:
        points = tuple((float(x), float(y)) for x, y in path["points"])
        holes = tuple(tuple((float(x), float(y)) for x, y in ring)
                      for ring in path.get("holes", ()))
        geometry = MultiLineString((points, *holes)) if holes else LineString(points)
        signature = (path["category"], len(points)-1,
                     tuple(sorted(len(ring) for ring in holes)))
        out.append(_TemplatePart(_key(signature), geometry, points, geometry.length, len(holes)))
    return tuple(out)


def _trial(seed, anchor, index, candidates, keys, centers, scale, mirror, angle, tolerance, operations):
    """Place the whole template on one candidate; return its parts, or None."""
    xfact = -scale if mirror else scale

    def place(geometry):
        scaled = affinity.scale(geometry, xfact=xfact, yfact=scale, origin=(0, 0))
        return affinity.rotate(scaled, angle, origin=(0, 0))

    placed_anchor = place(anchor.geometry)
    target = candidates[index].center
    dx = target[0] - placed_anchor.centroid.x
    dy = target[1] - placed_anchor.centroid.y
    chosen, taken = [], set()
    for part in (anchor,) + tuple(p for p in seed if p is not anchor):
        expected = affinity.translate(place(part.geometry), dx, dy)
        nearby = (index,) if part is anchor else centers.query(expected.centroid.buffer(tolerance*2))
        matches = []
        for j in nearby:
            j = int(j)
            operations += 1
            if operations > MAX_MATCH_OPERATIONS:
                raise LibraryBudgetExceeded("the comparison budget for this drawing is used up")
            if j in taken or keys[j] != part.key:
                continue
            distance = expected.hausdorff_distance(candidates[j].geometry)
            if distance <= tolerance:
                matches.append((distance, candidates[j].source_id, candidates[j].points, j))
        if not matches:
            return None, operations
        j = min(matches)[-1]
        taken.add(j)
        chosen.append(candidates[j])
    return chosen, operations


def _symbol(record, parts, scale, mirror, angle):
    ids = tuple(sorted({p.source_id for p in parts}))
    measured = measure(parts)
    if measured is None:
        return None
    position, width, depth, orientation, boundary = measured
    digest = hashlib.sha256((record["id"]+"|"+"|".join(ids)).encode()).hexdigest()[:16]
    return SymbolInstance("library-"+digest, record["kind"], position, width, depth,
                          orientation, record.get("subtype", "unknown"), ids,
                          # Load-bearing: candidacy.yields_to_walls treats only
                          # "layer-and-geometry" as yielding, so this string is
                          # what keeps a matched fixture out of wall detection.
                          "symbol-library", .8, boundary,
                          properties=(("library_template_id", record["id"]),
                                      ("library_scale_ft", f"{scale:.6f}"),
                                      ("library_mirrored", str(mirror)),
                                      ("library_angle_deg", str(angle)),
                                      *((("space_types", ",".join(record["space_types"])),
                                         ("space_evidence", record.get("space_evidence", "weak")))
                                        if record.get("space_types") else ())))


def _diameter(points) -> float:
    """The longest chord of a part: its farthest pair of points, found on the hull."""
    hull = MultiPoint(list(points)).convex_hull
    ring = list(hull.exterior.coords) if hull.geom_type == "Polygon" else list(hull.coords)
    return max((math.dist(a, b) for a in ring for b in ring), default=0.0)


def _form(length: float, points) -> float | None:
    """Length over longest chord: unchanged by scale, rotation and mirroring."""
    diameter = _diameter(points)
    return length / diameter if diameter > 0 else None


def fit_orientations(angles, mirror_allowed, anchors, budget):
    """The most orientations a template may be tried at within the trial budget.

    A template declares the most it may try (angles, mirror). When that costs
    more than the budget in this drawing, try fewer rather than none: the
    0/45/90/180/270 set, then a single angle, each with and without mirror,
    taking the most trials that fit. It never adds an orientation the reviewer
    did not allow. Returns (angles, mirrors) or None when even one does not fit.
    """
    declared = tuple(sorted(float(a) % 360 for a in angles))
    axes = tuple(a for a in declared if a in (0.0, 45.0, 90.0, 180.0, 270.0))
    one = ((0.0,) if 0.0 in declared else declared[:1])
    sets = []
    for s in (declared, axes, one):
        if s and s not in sets:
            sets.append(s)
    mirror_options = ((False, True), (False,)) if mirror_allowed else ((False,),)
    combos = [(s, m) for s in sets for m in mirror_options]
    # Most trials first: the widest coverage the budget allows.
    combos.sort(key=lambda c: (-len(c[0]) * len(c[1]), -len(c[0]), -len(c[1])))
    for s, m in combos:
        if anchors * len(s) * len(m) <= budget:
            return s, m
    return None


def _anchor_spots(seed, anchor, by_key, candidates, centers, low, high):
    """The candidate parts that could be this template's anchor, cheapest tests first.

    Returns (spots, number outside the size range). A spot must (1) imply a size
    inside the template's range, (2) have the anchor's form (length over longest
    chord) and (3) have, at the distance the template puts them, the template's
    other parts nearby. None of the three can reject a true match: each compares
    something a placement leaves unchanged.
    """
    anchor_form = _form(anchor.length, anchor.points) if not anchor.holes and anchor.key[0] != "fill" else None
    anchor_centre = anchor.geometry.centroid
    others = [p for p in seed if p is not anchor]
    # The rarest other parts reject the most; a few are enough.
    others = sorted(others, key=lambda p: len(by_key.get(p.key, ())))[:NEIGHBOURS_CHECKED]
    out = []
    outside = 0
    for index in by_key.get(anchor.key, ()):
        part = candidates[index]
        scale = part.length / anchor.length
        if not math.isfinite(scale) or scale <= 0:
            continue
        if not low <= scale <= high:
            outside += 1
            continue
        if anchor_form is not None and not part.holes:
            form = _form(part.length, part.points)
            if form is not None and abs(form - anchor_form) > SHAPE_TOLERANCE * anchor_form:
                continue
        slack = 3 * TOLERANCE_FRACTION * scale
        centre = Point(part.center)
        ok = True
        for other in others:
            reach = math.dist((anchor_centre.x, anchor_centre.y), (other.geometry.centroid.x,
                                                                      other.geometry.centroid.y)) * scale
            found = False
            for j in centers.query(centre.buffer(reach + slack)):
                j = int(j)
                if j != index and _key(candidates[j].signature) == other.key and \
                        abs(math.dist(candidates[j].center, part.center) - reach) <= slack:
                    found = True
                    break
            if not found:
                ok = False
                break
        if ok:
            out.append(index)
    return tuple(out), outside


def match_library_templates(ps, units_per_foot, templates, *, max_candidates=5000
                            ) -> tuple[tuple[SymbolInstance, ...], tuple[Issue, ...]]:
    """Match reviewed library shapes at any uniform scale within their size range."""
    if not math.isfinite(units_per_foot) or units_per_foot <= 0:
        raise ValueError("library units_per_foot must be finite and positive")
    if not isinstance(templates, (tuple, list)) or any(not isinstance(t, dict) for t in templates):
        raise ValueError("library templates must be a list of template objects")
    records = [_validated(t) for t in templates if t.get("status") == "reviewed"]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("library template IDs must be unique")
    candidates = parts_from(ps, units_per_foot)
    if not candidates or not records:
        return (), ()
    source_counts = Counter(p.source_id for p in ps.primitives if p.source_id)
    keys = tuple(_key(p.signature) for p in candidates)
    by_key = {}
    for i, key in enumerate(keys):
        by_key.setdefault(key, []).append(i)
    centers = STRtree([Point(p.center) for p in candidates])
    results, issues = {}, []

    for record in sorted(records, key=lambda r: r["id"]):
        before = set(results)
        operations = 0        # the comparison budget is per template
        try:
            seed = _template_parts(record)
            # A holed part's length sums its rings, and ring closure differs between
            # drawings, so the scale it implies is unreliable: anchor on a simple
            # part whenever one is equally selective.
            # Every kind of part the template has must exist somewhere in the drawing.
            if any(not by_key.get(t.key) for t in seed):
                continue
            anchor = min(seed, key=lambda t: (len(by_key.get(t.key, ())), t.holes > 0, -t.length, t.points))
            low, high = record["size_ft"]["min"], record["size_ft"]["max"]
            possible, outside = _anchor_spots(seed, anchor, by_key, candidates, centers, low, high)
            if outside:
                issues.append(Issue("info", record["id"], "symbol_size_out_of_range",
                                    f"{record['id']}: {outside} candidate spots lie outside {low}-{high} ft"))
            declared = (tuple(record["rotations_deg"]), (False, True) if record["mirror_allowed"] else (False,))
            fitted = fit_orientations(declared[0], record["mirror_allowed"], len(possible), max_candidates)
            if fitted is None:
                raise LibraryBudgetExceeded(f"it has {len(possible)} possible anchor spots in this drawing; even "
                                            f"one orientation exceeds the limit of {max_candidates} trials")
            angles, mirrors = fitted
            if (angles, mirrors) != declared:
                issues.append(Issue("info", record["id"], "library_template_reduced",
                                    f"library template {record['id']} was matched at {len(angles)} of "
                                    f"{len(declared[0])} orientations{'' if mirrors == declared[1] else ' and without mirroring'}"
                                    f" to fit the budget in this drawing ({len(possible)} anchor spots)"))
            for index in possible:
                scale = candidates[index].length / anchor.length
                tolerance = TOLERANCE_FRACTION * scale
                for mirror in mirrors:
                    for angle in angles:
                        chosen, operations = _trial(seed, anchor, index, candidates, keys, centers,
                                                    scale, mirror, angle, tolerance, operations)
                        if chosen is None:
                            continue
                        member_ids = tuple(sorted({p.source_id for p in chosen}))
                        if member_ids in results:
                            continue
                        counts = Counter(p.source_id for p in chosen)
                        # Never claim only part of an entity then erase its remainder.
                        if any(count != source_counts[sid] for sid, count in counts.items()):
                            continue
                        symbol = _symbol(record, chosen, scale, mirror, angle)
                        if symbol is not None:
                            results[member_ids] = symbol
        except LibraryBudgetExceeded as exc:
            # A shape that is common in this drawing is skipped, not fatal: what it
            # would have matched is simply not recognised, and the run goes on.
            for ids in set(results) - before:
                del results[ids]
            issues.append(Issue("warn", record["id"], "library_template_skipped",
                                f"library template {record['id']} skipped: {exc}"))
    # One stroke cannot belong to two symbols; overlapping claims need review.
    claims = Counter(sid for ids in results for sid in ids)
    symbols = tuple(sorted((s for ids, s in results.items() if all(claims[sid] == 1 for sid in ids)),
                           key=lambda s: s.id))
    return symbols, tuple(issues)
