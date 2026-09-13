"""Reviewed drawing-local geometric symbol templates, without scale fitting.

Seeds name existing source entities. Matching compares transformed source paths
and claims only complete matched entities; it never excludes a bounding box.
Default targets are the seed layers. Repeated generic geometry can still have
another architectural meaning, so template matches retain explicit provenance.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.strtree import STRtree

from archiagent.semantic import SymbolInstance

KINDS = {"door","window","column","beam","stair","furniture","electrical","plumbing","vehicle"}
MAX_SEED_PRIMITIVES = 64
MAX_MATCH_OPERATIONS = 200_000


@dataclass(frozen=True)
class _Part:
    index: int
    source_id: str
    layer: str
    geometry: object
    signature: tuple
    center: tuple[float,float]
    length: float
    points: tuple


def _parts(ps, units_per_foot):
    entities = {e.id:e for e in ps.entities}
    out = []
    for i,p in enumerate(ps.primitives):
        if not p.source_id or len(p.coords)<2:
            continue
        e = entities.get(p.source_id)
        if e is not None and dict(e.metadata).get("region_partial")=="true":
            continue
        coords = tuple((x/units_per_foot,y/units_per_foot) for x,y in p.coords)
        if not all(math.isfinite(v) for point in coords for v in point):
            raise ValueError("template source coordinates must be finite")
        closed = p.closed or p.kind in {"rect","fill"} or coords[0]==coords[-1]
        if closed and coords[0]!=coords[-1]:
            coords += coords[:1]
        if len(set(coords))<2:
            continue
        holes = tuple(tuple((x/units_per_foot,y/units_per_foot) for x,y in ring) for ring in (e.holes if e else ()))
        geometry = MultiLineString((coords,*holes)) if holes else LineString(coords)
        category = "fill" if p.kind=="fill" else "curve" if p.kind=="curve" else "closed" if closed else "path"
        signature = (category,len(coords)-1,tuple(sorted(len(ring) for ring in holes)))
        center = (geometry.centroid.x,geometry.centroid.y)
        out.append(_Part(i,p.source_id,p.layer,geometry,signature,center,geometry.length,coords))
    return tuple(sorted(out,key=lambda p:(p.source_id,p.layer,p.points,p.index)))


def _seed_ids(ps, ids):
    children = {}
    known = {p.source_id for p in ps.primitives if p.source_id}
    for e in ps.entities:
        known.add(e.id)
        children.setdefault(e.parent_id,[]).append(e.id)
    if any(sid not in known for sid in ids):
        raise ValueError("template seed source_ids must identify entities in the current drawing")
    result = set()
    todo = list(ids)
    while todo:
        sid = todo.pop()
        if sid in result:
            continue
        result.add(sid)
        todo.extend(children.get(sid,()))
    return result


def _symbol(record, parts, angle, tolerance_in):
    ids = tuple(sorted({p.source_id for p in parts}))
    points = [point for p in parts for point in p.points]
    rect = MultiPoint(points).minimum_rotated_rectangle
    if rect.geom_type=="Polygon":
        corners = list(rect.exterior.coords)
        sides = [(math.dist(a,b),a,b) for a,b in zip(corners,corners[1:])]
        width,a,b = max(sides)
        depth = min(s[0] for s in sides)
        orientation = math.atan2(b[1]-a[1],b[0]-a[0])%math.pi
        boundary = tuple(corners)
        # A single closed column outline supplies its real outline directly.
        if record["kind"]=="column" and len(parts)==1 and parts[0].signature[0] in {"closed","fill"}:
            boundary = parts[0].points
    elif rect.geom_type=="LineString":
        a,b = sorted((tuple(rect.coords[0]),tuple(rect.coords[-1])))
        width = math.dist(a,b)
        depth = 0.0
        orientation = math.atan2(b[1]-a[1],b[0]-a[0])%math.pi
        boundary = (a,b)
    else:
        return None
    if record["kind"] in {"column","beam"} and depth<=0:
        return None
    digest = hashlib.sha256((str(record["id"])+"|"+"|".join(ids)).encode()).hexdigest()[:16]
    return SymbolInstance("template-"+digest,record["kind"],(rect.centroid.x,rect.centroid.y),
                          width,depth,orientation,record.get("subtype","unknown"),ids,
                          "reviewed-template",.8,boundary,
                          properties=(("template_id",str(record["id"])),
                                      ("template_angle_deg",str(angle)),
                                      ("template_tolerance_in",str(tolerance_in))))


def match_templates(ps, units_per_foot, records) -> tuple[SymbolInstance,...]:
    """Match reviewed source patterns at bounded tolerance and explicit rotations.

    Record keys: id, kind, source_ids; optional subtype, tolerance_in (0.1in,
    maximum 0.5in), rotations_deg (0/90/180/270), target_layers (seed layers),
    max_candidates (5000, at most 20000). There is no scaling option. A budget
    overrun raises ValueError so callers can narrow the region or target layers.
    """
    if not math.isfinite(units_per_foot) or units_per_foot<=0:
        raise ValueError("template units_per_foot must be finite and positive")
    if not isinstance(records, (tuple, list)) or any(not isinstance(r, dict) for r in records):
        raise ValueError("templates must be a list of template objects")
    records = tuple(records)
    if len(records)>100:
        raise ValueError("at most 100 reviewed templates are supported per region")
    if len({str(r.get("id","")) for r in records})!=len(records):
        raise ValueError("template IDs must be unique")
    parts = _parts(ps,units_per_foot)
    source_counts = Counter(p.source_id for p in ps.primitives if p.source_id)
    results = {}
    operations = 0
    for record in sorted(records,key=lambda r:str(r.get("id",""))):
        if not isinstance(record.get("id"), str) or not record["id"] or not isinstance(record.get("kind"), str) or record["kind"] not in KINDS:
            raise ValueError("template requires id and a supported symbol kind")
        ids = record.get("source_ids",())
        if not isinstance(ids,(list,tuple)) or not ids or not all(isinstance(v,str) and v for v in ids):
            raise ValueError("template source_ids must be a nonempty list of entity IDs")
        if "scale" in record or "scales" in record:
            raise ValueError("template scaling is unsupported; review a seed at the correct scale")
        raw_tolerance = record.get("tolerance_in", .1)
        if type(raw_tolerance) not in (int, float):
            raise ValueError("template tolerance_in must be a number")
        tolerance_in = float(raw_tolerance)
        if not math.isfinite(tolerance_in) or not 0<tolerance_in<=.5:
            raise ValueError("template tolerance_in must be positive and at most 0.5in")
        tolerance = tolerance_in/12
        angles = record.get("rotations_deg",(0,90,180,270))
        if not isinstance(angles,(list,tuple)) or not 1<=len(angles)<=16 or not all(type(a) in (int,float) and math.isfinite(a) for a in angles):
            raise ValueError("rotations_deg must contain 1 to 16 finite angles")
        angles = tuple(sorted({float(a)%360 for a in angles}))
        budget = record.get("max_candidates",5000)
        if isinstance(budget,bool) or not isinstance(budget,int) or not 1<=budget<=20000:
            raise ValueError("max_candidates must be an integer between 1 and 20000")
        seed_ids = _seed_ids(ps,ids)
        seed = tuple(p for p in parts if p.source_id in seed_ids)
        if not seed or len(seed)>MAX_SEED_PRIMITIVES:
            raise ValueError("template must resolve to between 1 and 64 complete source primitives")
        seed_counts = Counter(p.source_id for p in seed)
        if any(count!=source_counts[sid] for sid,count in seed_counts.items()):
            raise ValueError("template seed contains incomplete or unsupported source geometry")
        if record["kind"] in {"column","beam"} and any(p.signature[2] for p in seed):
            raise ValueError("holed structural templates need a reviewed structural profile")
        layer_values = record.get("target_layers",sorted({p.layer for p in seed}))
        if not isinstance(layer_values,(list,tuple)) or not layer_values or not all(isinstance(v,str) for v in layer_values):
            raise ValueError("target_layers must be a nonempty list of layer names")
        layers = {v.casefold() for v in layer_values}
        candidates = tuple(p for p in parts if p.layer.casefold() in layers)
        if not candidates:
            continue
        by_signature = {}
        for i,p in enumerate(candidates):
            by_signature.setdefault(p.signature,[]).append((p.length,i))
        for family in by_signature.values():
            family.sort()
        def anchors(part):
            family = by_signature.get(part.signature,())
            allowance = tolerance*max(1,part.signature[1])*2
            start = bisect_left(family,(part.length-allowance,-1))
            end = bisect_right(family,(part.length+allowance,len(candidates)))
            return tuple(i for _,i in family[start:end])
        anchor = min(seed,key=lambda p:(len(anchors(p)),-p.length,p.source_id,p.points))
        possible = anchors(anchor)
        if len(possible)*len(angles)>budget:
            raise ValueError(f"template {record['id']} candidate budget exceeded; narrow target_layers or region")
        centers = STRtree([Point(p.center) for p in candidates])
        seen_members = set()
        for index in sorted(possible,key=lambda i:(candidates[i].source_id,candidates[i].points)):
            candidate_anchor = candidates[index]
            for angle in angles:
                chosen = []
                taken = set()
                valid = True
                dx = candidate_anchor.center[0]-anchor.center[0]
                dy = candidate_anchor.center[1]-anchor.center[1]
                ordered = (anchor,)+tuple(p for p in seed if p is not anchor)
                for original in ordered:
                    expected = affinity.translate(affinity.rotate(original.geometry,angle,origin=anchor.center),dx,dy)
                    nearby = (index,) if original is anchor else centers.query(expected.centroid.buffer(tolerance*2))
                    matches = []
                    for j in nearby:
                        j = int(j)
                        operations += 1
                        if operations>MAX_MATCH_OPERATIONS:
                            raise ValueError("template comparison budget exceeded; narrow source region or templates")
                        if j in taken or candidates[j].signature!=original.signature:
                            continue
                        distance = expected.hausdorff_distance(candidates[j].geometry)
                        if distance<=tolerance:
                            matches.append((distance,candidates[j].source_id,candidates[j].points,j))
                    if not matches:
                        valid = False
                        break
                    j = min(matches)[-1]
                    taken.add(j)
                    chosen.append(candidates[j])
                if not valid:
                    continue
                member_ids = tuple(sorted({p.source_id for p in chosen}))
                counts = Counter(p.source_id for p in chosen)
                if any(count!=source_counts[sid] for sid,count in counts.items()):
                    continue  # Never claim only part of an entity then erase its remainder.
                if member_ids in seen_members:
                    continue
                symbol = _symbol(record,chosen,angle,tolerance_in)
                if symbol is None:
                    continue
                previous = results.get(member_ids)
                if previous and (previous.kind,previous.subtype)!=(symbol.kind,symbol.subtype):
                    raise ValueError("reviewed templates assign conflicting roles to the same source instance")
                seen_members.add(member_ids)
                results.setdefault(member_ids,symbol)
    # Overlapping multi-path hypotheses require review instead of double use of
    # one stroke in two architectural objects. Disjoint exact matches survive.
    claims = Counter(sid for ids in results for sid in ids)
    return tuple(sorted((s for ids,s in results.items() if all(claims[sid]==1 for sid in ids)),key=lambda s:s.id))
