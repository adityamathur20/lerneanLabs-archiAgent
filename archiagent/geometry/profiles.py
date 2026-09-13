"""Evidence-backed wall-face profiles, in feet.

Profiles preserve corners, holes and nonorthogonal boundaries which cannot be
represented by one constant-width centerline. They never repair or buffer the
source into a guessed wall. Simple rectangles retain the existing WallSeg path.
Exploded hatch inference requires explicitly selected, separate hatch layers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from shapely import normalize
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from archiagent.evidence import IngestWarning
from archiagent.primitives import Pt, PrimitiveSet


@dataclass(frozen=True)
class WallProfile:
    id: str
    boundary: tuple[Pt, ...]
    holes: tuple[tuple[Pt, ...], ...] = ()
    source_layer: str = ""
    source_ids: tuple[str, ...] = ()
    detector: str = "reviewed-profile"
    review_status: str = "accepted_after_review"


def profile_polygon(profile: WallProfile) -> Polygon:
    return Polygon(profile.boundary, profile.holes)


def _polygon(boundary, holes=(), *, units_per_foot=1.0):
    if not isinstance(boundary, (list, tuple)) or not isinstance(holes, (list, tuple)):
        raise ValueError("wall profile boundary and holes must be coordinate arrays")
    rings = []
    for ring in (boundary, *holes):
        if not isinstance(ring, (list, tuple)) or any(
                not isinstance(p, (list, tuple)) or len(p) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in ring):
            raise ValueError("wall profile rings require finite numeric 2D points")
        coords = tuple(tuple(v / units_per_foot for v in p) for p in ring)
        if len(coords) < 3:
            raise ValueError("wall profile rings require at least three finite 2D points")
        rings.append(coords)
    poly = Polygon(rings[0], rings[1:])
    if not poly.is_valid or poly.area <= 1e-10:
        raise ValueError("wall profile must be a valid positive-area polygon; repair needs explicit review")
    return normalize(poly)


def _profile(poly, source_ids, layer, detector, status="accepted_by_rule", identity=""):
    boundary = tuple(poly.exterior.coords)
    holes = tuple(tuple(r.coords) for r in poly.interiors)
    ids = tuple(sorted(set(source_ids)))
    key = json.dumps([boundary, holes, ids, layer, detector], separators=(",", ":"))
    return WallProfile(identity or "wp-" + hashlib.sha256(key.encode()).hexdigest()[:20],
                       boundary, holes, layer, ids, detector, status)


def reviewed_wall_profiles(records) -> tuple[WallProfile, ...]:
    """Explicit accepted contours in model feet, without implicit source scaling."""
    if not isinstance(records, (list, tuple)):
        raise ValueError("wall_profiles must be a list of reviewed profile objects in feet")
    result = []
    for record in records:
        if not isinstance(record, dict) or "boundary" not in record:
            raise ValueError("each reviewed wall profile needs a boundary")
        if record.get("units", "ft") != "ft":
            raise ValueError("reviewed wall_profiles use feet; convert source coordinates explicitly")
        status = record.get("review_status", "accepted_after_review")
        if status not in {"accepted_after_review", "accepted_by_rule"}:
            raise ValueError("only accepted wall profiles may be constructed")
        ids = record.get("source_ids", ())
        if not isinstance(ids, (list, tuple)) or any(not isinstance(s, str) for s in ids):
            raise ValueError("wall profile source_ids must be a list of strings")
        identity = record.get("id", "")
        if not isinstance(identity, str):
            raise ValueError("wall profile id must be a string")
        if not isinstance(record.get("source_layer", ""), str):
            raise ValueError("wall profile source_layer must be a string")
        result.append(_profile(_polygon(record["boundary"], record.get("holes", ())),
                               ids, record.get("source_layer", ""), "reviewed-profile", status, identity))
    if len({p.id for p in result}) != len(result):
        raise ValueError("reviewed wall profile IDs must be unique")
    return tuple(sorted(result, key=lambda p: p.id))


def _wall_like(poly, max_thickness_ft):
    # Necessary thin-material evidence, not a semantic proof. A broad filled
    # room must not turn into a solid wall merely because its layer is 'walls'.
    return (poly.area >= .01 and poly.length > 0
            and 2 * poly.area / poly.length <= max_thickness_ft
            and poly.buffer(-max_thickness_ft / 2).is_empty)


def detect_wall_profiles(ps: PrimitiveSet, wall_layers: set[str], units_per_foot: float,
                         *, max_thickness_in=24.0):
    """Keep complex native filled wall contours; report invalid/unproven bodies.

    A classified wall layer and thin material are required. This remains a
    rule hypothesis, not independent architectural verification. Layer/instance
    classification must exclude furniture and columns before this function.
    """
    if not all(math.isfinite(v) and v > 0 for v in (units_per_foot, max_thickness_in)):
        raise ValueError("profile scale and maximum thickness must be finite and positive")
    wanted = {layer.casefold() for layer in wall_layers}
    boundaries = {e.parent_id for e in ps.entities if e.kind == "HATCH_BOUNDARY"}
    entities = {e.id: e for e in ps.entities}
    candidates = []
    for e in sorted(ps.entities, key=lambda e: e.id):
        if e.layer.casefold() not in wanted or not e.closed or e.id in boundaries:
            continue
        if e.kind in {"HATCH", "HATCH_BOUNDARY", "SOLID", "TRACE", "PDF_FILL"}:
            ids = (e.id, e.parent_id) if e.parent_id else (e.id,)
            candidates.append((e.coords, e.holes, e.layer, ids))
    for p in ps.primitives:
        if p.kind != "fill" or p.layer.casefold() not in wanted:
            continue
        if p.source_id in entities or p.source_id in boundaries:
            continue
        candidates.append((p.coords, (), p.layer, (p.source_id,) if p.source_id else ()))
    profiles, warnings = {}, []
    for boundary, holes, layer, ids in candidates:
        try:
            poly = _polygon(boundary, holes, units_per_foot=units_per_foot)
        except (ValueError, TypeError) as exc:
            warnings.append(IngestWarning("invalid_wall_profile", ",".join(ids), str(exc)))
            continue
        rectangle = poly.minimum_rotated_rectangle
        if not holes and abs(poly.area / rectangle.area - 1) <= 1e-6:
            continue  # Existing thin-rectangle detector retains its exact sweep.
        if not _wall_like(poly, max_thickness_in / 12):
            warnings.append(IngestWarning("wall_profile_needs_review", ",".join(ids),
                "complex filled body lacks thin-wall evidence; supply an accepted profile if appropriate"))
            continue
        profile = _profile(poly, ids, layer, "native-wall-face")
        profiles[profile.id] = profile
    return tuple(profiles[k] for k in sorted(profiles)), tuple(warnings)


def detect_exploded_hatch_profiles(ps: PrimitiveSet, boundary_layers: set[str],
                                    hatch_layers: set[str], units_per_foot: float,
                                    *, min_strokes=3, max_thickness_in=24.0,
                                    max_segments=20000, max_cells=10000,
                                    max_intersections=100000):
    """Opt-in noded polygonization supported by dedicated hatch-layer strokes.

    No snapping, orthogonalization or automatic same-layer stroke guessing.
    Bounds prevent a dense sheet from causing an unbounded polygonization run.
    """
    if not all(math.isfinite(v) and v > 0 for v in (units_per_foot, max_thickness_in)):
        raise ValueError("scale and maximum thickness must be finite and positive")
    if any(type(v) is not int or v < 1 for v in (min_strokes, max_segments, max_cells, max_intersections)):
        raise ValueError("hatch support and complexity limits must be positive integers")
    borders = {s.casefold() for s in boundary_layers}
    hatches = {s.casefold() for s in hatch_layers}
    if not borders or not hatches or borders.intersection(hatches):
        raise ValueError("exploded hatch requires distinct explicit boundary and hatch layers")
    segments, segment_ids, markers, marker_ids = [], [], [], []
    for p in sorted(ps.primitives, key=lambda p: (p.source_id, p.layer, p.coords)):
        layer = p.layer.casefold()
        if layer not in borders | hatches or p.kind in {"fill", "curve"}:
            continue
        for a, b in p.segments():
            a, b = tuple(v / units_per_foot for v in a), tuple(v / units_per_foot for v in b)
            if not all(math.isfinite(v) for v in (*a, *b)):
                raise ValueError("nonfinite hatch/boundary coordinate")
            if math.dist(a, b) < 1e-9:
                continue
            if layer in borders:
                segments.append(LineString((a, b)))
                segment_ids.append(p.source_id)
            else:
                markers.append(Point((a[0]+b[0])/2, (a[1]+b[1])/2))
                marker_ids.append(p.source_id)
            if len(segments) + len(markers) > max_segments:
                raise ValueError("exploded hatch complexity limit exceeded; select a smaller reviewed region")
    if not segments or not markers:
        return ()
    marker_tree, border_tree = STRtree(markers), STRtree(segments)
    # A segment-count limit alone cannot protect noding: a crossed grid has
    # quadratically many intersections. Count intersecting pairs before GEOS
    # materializes the noded network, querying one segment at a time.
    intersections = 0
    for index, segment in enumerate(segments):
        intersections += sum(int(i) > index for i in border_tree.query(segment, predicate="intersects"))
        if intersections > max_intersections:
            raise ValueError("exploded hatch intersection limit exceeded; select a smaller reviewed region")
    profiles = []
    for index, poly in enumerate(polygonize(unary_union(segments))):
        if index >= max_cells:
            raise ValueError("exploded hatch cell limit exceeded; select a smaller reviewed region")
        if not _wall_like(poly, max_thickness_in / 12):
            continue
        support = marker_tree.query(poly, predicate="contains")
        # Distinct stroke geometry prevents duplicated source entities from
        # manufacturing support. Midpoints alone are not semantic certainty.
        unique = {(markers[int(i)].x, markers[int(i)].y) for i in support}
        if len(unique) < min_strokes:
            continue
        ids = {marker_ids[int(i)] for i in support}
        ids.update(segment_ids[int(i)] for i in border_tree.query(poly.boundary, predicate="intersects"))
        profiles.append(_profile(normalize(poly), ids - {""}, ",".join(sorted(boundary_layers)),
                                 "exploded-hatch-cell"))
    return tuple(sorted(profiles, key=lambda p: p.id))
