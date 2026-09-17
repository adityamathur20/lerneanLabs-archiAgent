"""Spatially associated dimensions and final-model verification.

Native CAD measurement alone cannot independently validate its own units.
Only explicit unit-bearing text or reviewed measurement records contributes
physical truth. Missing/ambiguous checks stay unverified.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import re
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

from archiagent.scale.dimensions import parse_dimension
from archiagent.semantic import DimensionCheck


def _positive(value, name):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value
    ):
        raise ValueError(f"{name} must contain two finite numeric coordinates")
    return tuple(float(v) for v in value)


def _axis(value):
    axis = _point(value, "measurement_axis")
    length = math.hypot(*axis)
    if not math.isfinite(length) or length == 0:
        raise ValueError("measurement_axis must be finite and nonzero")
    return axis[0] / length, axis[1] / length


@dataclass(frozen=True)
class Measurement:
    id: str
    start: tuple[float,float]
    end: tuple[float,float]
    expected_ft: float | None
    basis: str = "face"
    source: str = "native-dimension"
    axis: tuple[float,float] | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("measurement id must be a nonempty string")
        _point(self.start, "measurement start")
        _point(self.end, "measurement end")
        if self.axis is not None:
            _axis(self.axis)
        span = _span(self.start, self.end, self.axis)
        if not math.isfinite(span) or span <= 0:
            raise ValueError("measurement endpoints must have a positive measured span")
        if self.expected_ft is not None:
            _positive(self.expected_ft, "expected_ft")
        if self.basis not in ("face", "centerline"):
            raise ValueError("measurement basis must be face or centerline")


def parse_explicit_length(text):
    feet=parse_dimension(text)
    if feet is not None:return feet
    match=re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|ft|in)\s*",text,re.I)
    if match:
        value = float(match[1])*{"mm":1/304.8,"cm":1/30.48,"m":1/.3048,"ft":1,"in":1/12}[match[2].lower()]
        return value if math.isfinite(value) and value > 0 else None
    return None


def measurements_from_source(ps):
    return tuple(Measurement(d.id,d.start,d.end,parse_explicit_length(d.text),axis=d.measurement_axis,
                             source=d.text_evidence if d.text_evidence != "native" else "native-dimension")
                 for d in ps.dimensions if d.measurement_type in ("linear","aligned","rotated"))


def associate_dimension_text(ps, units_per_foot, max_text_distance_in=6):
    """Associate a single high-confidence OCR label to a native text anchor.

    Returns updated NativeDimension records; it never modifies source entities
    or estimates scale. TextItem.source_id must resolve to an OCR_TEXT entity
    with metadata confidence (or ocr_confidence) between 0.85 and 1 inclusive.
    Only whole, explicitly unit-bearing labels are eligible: no concatenation,
    numeric guessing, native-text replacement, or reuse across dimensions.

    The association distance is measured between native text midpoint and OCR
    bounding-box center, in the same registered source coordinate system. All
    nearby OCR tokens count toward ambiguity, including low-confidence tokens
    and fragments, so a separated inches token cannot silently be discarded.
    This is a candidate association, not proof that OCR characters are correct.
    """
    upf = _positive(units_per_foot, "source units per foot")
    inches = _positive(max_text_distance_in, "OCR text association distance")
    if inches > 12:
        raise ValueError("OCR text association distance must be at most 12 inches")
    distance = upf * inches / 12
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("OCR text association distance is not representable in source units")
    entities = {e.id: e for e in ps.entities}
    candidates = []
    counts = {}
    for text in ps.texts:
        entity = entities.get(text.source_id)
        if entity is None or entity.kind != "OCR_TEXT" or not text.text.strip():
            continue
        counts[text.source_id] = counts.get(text.source_id, 0) + 1
        bbox = text.bbox
        if (not isinstance(bbox, (tuple, list)) or len(bbox) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in bbox)
                or bbox[0] > bbox[2] or bbox[1] > bbox[3]):
            continue
        center = text.center()
        if not all(math.isfinite(v) for v in center):
            continue
        metadata = dict(entity.metadata)
        try:
            confidence = float(metadata.get("confidence", metadata.get("ocr_confidence", "nan")))
        except (TypeError, ValueError):
            confidence = math.nan
        full_text = text.text.strip()
        # The legacy dimension parser tolerates a trailing X in printed CAD
        # labels. OCR association must not reinterpret a multiplicity marker
        # as a complete independent dimension.
        expected = parse_explicit_length(full_text) if not full_text.upper().endswith(("X", "-")) else None
        candidates.append((text, center, confidence, expected, metadata))
    if not candidates:
        return ps.dimensions
    from shapely.strtree import STRtree
    points = [Point(c[1]) for c in candidates]
    tree = STRtree(points)
    by_dimension, owners = {}, {}
    for i, dimension in enumerate(ps.dimensions):
        if dimension.text_position is None:
            continue
        position = _point(dimension.text_position, "native dimension text position")
        nearby = [int(j) for j in tree.query(Point(position).buffer(distance))
                  if math.dist(position, candidates[int(j)][1]) <= distance]
        by_dimension[i] = nearby
        for j in nearby:
            owners.setdefault(j, set()).add(i)
    result = []
    for i, dimension in enumerate(ps.dimensions):
        nearby = by_dimension.get(i, ())
        # A single space suppresses DXF dimension text deliberately; do not
        # resurrect it from unrelated nearby OCR. Only auto-label placeholders
        # and genuinely empty native labels are eligible.
        if dimension.text not in ("", "<>") or len(nearby) != 1:
            result.append(dimension)
            continue
        j = nearby[0]
        text, center, confidence, expected, metadata = candidates[j]
        if (owners[j] != {i} or counts[text.source_id] != 1 or expected is None or
                not math.isfinite(confidence) or not .85 <= confidence <= 1 or
                metadata.get("region_partial") == "true"):
            result.append(dimension)
            continue
        result.append(replace(dimension, text=text.text.strip(), text_source_ids=(text.source_id,),
                              text_evidence="ocr-associated"))
    return tuple(result)


def load_measurements(path, region=None):
    """Read reviewed references in source coordinates, before registration.

    Optional ``measurement_axis: [dx, dy]`` measures the projected span along
    that finite nonzero direction. Omission uses straight endpoint distance.
    ``expected_ft`` always uses physical feet; ``basis`` is face or centerline.
    Region translation changes witness points, never the direction vector.
    """
    values=json.loads(Path(path).read_text())
    if isinstance(values,dict):values=values.get("measurements",[])
    if not isinstance(values,list):raise ValueError("measurements file must contain a list")
    result=[]
    origin = _point(region.origin, "region origin") if region is not None else (0., 0.)
    for d in values:
        if not isinstance(d, dict):
            raise ValueError("each measurement must be an object")
        if region is not None and d.get("region_id",region.id)!=region.id:continue
        if "axis" in d:
            raise ValueError("use measurement_axis: [dx, dy], not axis, in reviewed measurement JSON")
        start=_point(d.get("start"), "measurement start")
        end=_point(d.get("end"), "measurement end")
        expected=_positive(d.get("expected_ft"), "expected_ft")
        axis=_axis(d["measurement_axis"]) if d.get("measurement_axis") is not None else None
        basis=d.get("basis","face")
        if basis not in ("face","centerline"):raise ValueError("measurement basis must be face or centerline")
        if region:
            x0,y0,x1,y1=region.bounds
            contained = all(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in (start,end))
            if not contained:
                if "region_id" in d:
                    raise ValueError("reviewed measurement witness points lie outside its named region")
                continue
            start=(start[0]-origin[0],start[1]-origin[1])
            end=(end[0]-origin[0],end[1]-origin[1])
        result.append(Measurement(d.get("id"),start,end,expected,basis,"reviewed-measurement",axis))
    if len({m.id for m in result})!=len(result):raise ValueError("measurement IDs must be unique")
    return tuple(result)


def _span(a,b,axis=None):
    if axis is None:return math.dist(a,b)
    x,y = _axis(axis)
    return abs((b[0]-a[0])*x+(b[1]-a[1])*y)


def infer_associated_scale(measurements, tolerance_in=2.0):
    _positive(tolerance_in, "dimension tolerance_in")
    references=[m for m in measurements if m.expected_ft is not None and m.expected_ft>0
                and m.source != "ocr-associated"]
    ratios=sorted(_span(m.start,m.end,m.axis)/m.expected_ft for m in references)
    if not ratios:return None
    scale=ratios[len(ratios)//2]
    _positive(scale, "inferred source units per foot")
    # Independent geometric spans, not repeated texts at the same endpoints.
    unique={tuple(sorted((m.start,m.end))) for m in references}
    if len(unique)<2:return None
    if any(abs(_span(m.start,m.end,m.axis)/scale-m.expected_ft)*12>tolerance_in for m in references):
        raise ValueError("associated source dimensions disagree on scale; review units or dimension overrides")
    return scale


def verify_dimensions(measurements,walls,units_per_foot,tolerance_in=2.0):
    _positive(units_per_foot, "source units per foot")
    _positive(tolerance_in, "dimension tolerance_in")
    axes=[LineString((w.start,w.end)) for w in walls if w.length_ft>0]
    bodies=[LineString((w.start,w.end)).buffer(w.thickness_ft/2,cap_style=2,join_style=2)
            for w in walls if w.length_ft>0 and w.thickness_ft>0]
    centerlines=unary_union(axes)
    # Include each face, not only union exterior: internal walls matter too.
    faces=unary_union([p.boundary for p in bodies])
    out=[]
    for m in measurements:
        a=tuple(x/units_per_foot for x in m.start);b=tuple(x/units_per_foot for x in m.end)
        if m.expected_ft is None:
            out.append(DimensionCheck(m.id,a,b,_span(a,b,m.axis),None,None,"source-endpoints","unverified",(m.id,),
                                      "native measurement has no independent unit-bearing reference; units unverified"));continue
        target=centerlines if m.basis=="centerline" else faces
        if target.is_empty:
            out.append(DimensionCheck(m.id,a,b,m.expected_ft,None,None,"model-"+m.basis,"unverified",(m.id,),"no model geometry"));continue
        pa=nearest_points(Point(a),target)[1];pb=nearest_points(Point(b),target)[1]
        max_distance=max(pa.distance(Point(a)),pb.distance(Point(b)))
        if max_distance>tolerance_in/12:
            out.append(DimensionCheck(m.id,a,b,m.expected_ft,None,None,"model-"+m.basis,"unverified",(m.id,),
                                      "dimension endpoint does not match a reconstructed "+m.basis));continue
        actual=_span((pa.x,pa.y),(pb.x,pb.y),m.axis);error=abs(actual-m.expected_ft)*12
        # The source span must corroborate units too; choosing nearby model
        # faces must not conceal a source/reference inconsistency.
        source_error=abs(_span(a,b,m.axis)-m.expected_ft)*12
        status="verified" if actual>0 and max(error,source_error)<=tolerance_in else "failed"
        if status == "verified" and m.source == "ocr-associated":
            status = "unverified"
        out.append(DimensionCheck(m.id,a,b,m.expected_ft,actual,error,"model-"+m.basis,status,(m.id,),
                                  f"endpoint displacement {max_distance*12:.4f}in; source residual {source_error:.4f}in"
                                  + ("; OCR text needs review" if m.source == "ocr-associated" else "")))
    return tuple(out)
