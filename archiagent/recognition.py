"""Conservative, source-linked symbol hypotheses and opening host assignment.

Rules recognize instances from named blocks/layers and local geometry. They do
not claim a universal symbol vocabulary or calibrated recognition confidence.
Unhosted/ambiguous detections remain visible for review instead of cutting walls.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import re
import unicodedata

from shapely.geometry import LineString, MultiPoint, Polygon, box
from shapely.strtree import STRtree

from archiagent.classify.roles import Role
from archiagent.classify.rules import named_role
from archiagent.geometry.walls import WallSeg
from archiagent.semantic import Opening, SymbolInstance
from archiagent.validate import GEOMETRY_EPS_FT


def _spans_overlap(a0, a1, b0, b1):
    """Two opening spans share more than a point, within model tolerance.

    Exact segment intersection is not enough: spans a residue apart on a
    barely sloped wall meet in a point, so a window drawn twice was cut twice.
    """
    length = math.dist(a0, a1)
    if length <= GEOMETRY_EPS_FT:
        return False
    ux, uy = (a1[0]-a0[0])/length, (a1[1]-a0[1])/length
    if any(abs((p[0]-a0[0])*uy-(p[1]-a0[1])*ux) > GEOMETRY_EPS_FT for p in (b0, b1)):
        return False
    lo, hi = sorted((p[0]-a0[0])*ux+(p[1]-a0[1])*uy for p in (b0, b1))
    return min(hi, length)-max(lo, 0.0) > GEOMETRY_EPS_FT

KINDS = {Role.DOOR:"door",Role.WINDOW:"window",Role.COLUMN:"column",Role.BEAM_OVERHEAD:"beam",
         Role.STAIR:"stair",Role.FURNITURE:"furniture",Role.ELECTRICAL:"electrical",
         Role.PLUMBING:"plumbing",Role.VEHICLE:"vehicle"}


def _id(kind, ids):
    return kind+"-"+hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()[:12]


def _instance(kind, ids, points, names, evidence):
    if len(points)<2:
        return None
    rect=MultiPoint(points).minimum_rotated_rectangle
    if rect.geom_type!="Polygon":
        if rect.geom_type!="LineString":return None
        a,b=sorted((tuple(rect.coords[0]),tuple(rect.coords[-1])))
        width=math.dist(a,b);depth=0.0;angle=math.atan2(b[1]-a[1],b[0]-a[0])%math.pi
        boundary=(a,b)
    else:
        corners=list(rect.exterior.coords)[:-1]
        sides=[(math.dist(corners[i],corners[(i+1)%4]),i) for i in range(4)]
        width,i=max(sides);depth=min(x[0] for x in sides)
        a,b=corners[i],corners[(i+1)%4];angle=math.atan2(b[1]-a[1],b[0]-a[0])%math.pi
        boundary=tuple(corners+corners[:1])
    limits={"door":(.8,14),"window":(.3,40),"column":(.2,8),"beam":(.3,100),
            "stair":(1,50),"furniture":(.3,35),"electrical":(.05,10),"plumbing":(.15,15),"vehicle":(2,35)}
    lo,hi=limits[kind]
    if not lo<=width<=hi:
        return None
    if kind=="column" and (depth<.15 or width/max(depth,1e-6)>5):
        return None
    if kind=="beam" and depth<=0:
        return None  # a single line has no section, so it cannot become a solid
    label=" ".join(names).lower()
    subtype="unknown"
    if kind=="door":
        subtype=next((v for t,v in [("sliding","sliding"),("pocket","pocket"),("double","double_swing")] if t in label),"unknown")
    return SymbolInstance(_id(kind,ids),kind,(rect.centroid.x,rect.centroid.y),width,depth,angle,subtype,
                          tuple(sorted(ids)),evidence,.75 if evidence=="block-metadata" else .6,boundary)


def recognize_symbols(ps, units_per_foot, classification=()):
    if not math.isfinite(units_per_foot) or units_per_foot<=0:
        raise ValueError("symbol scale must be finite and positive")
    roles={d.layer:d.role for d in classification if d.confidence>=.7 and d.role in KINDS}
    entities={e.id:e for e in ps.entities}
    children={}
    for e in ps.entities:
        children.setdefault(e.parent_id,[]).append(e.id)
    def descendants(root):
        found=set();todo=[root]
        while todo:
            item=todo.pop()
            if item in found: continue
            found.add(item);todo.extend(children.get(item,()))
        return found
    symbols=[];claimed=set()
    for e in sorted(ps.entities,key=lambda e:e.id):
        if e.kind!="INSERT" or e.id in claimed or dict(e.metadata).get("region_partial")=="true":
            continue
        # Specific block metadata precedes the layer's general role.
        role=named_role(e.block_name+" "+" ".join(v for k,v in e.attributes))
        if role not in KINDS:
            role=roles.get(e.layer,named_role(e.layer))
        if role not in KINDS: continue
        ids=descendants(e.id)
        geometric=[entities[sid] for sid in ids if entities[sid].kind!="INSERT"]
        if not geometric: geometric=[e]
        if any(dict(v.metadata).get("region_partial")=="true" for v in geometric):continue
        points=[(x/units_per_foot,y/units_per_foot) for v in geometric for x,y in v.coords]
        symbol=_instance(KINDS[role],ids,points,(e.block_name,e.layer),"block-metadata")
        if symbol:
            symbols.append(symbol);claimed.update(ids)
    # Group touching geometry only within a semantic layer, retaining each
    # disconnected instance. Blank/unknown mixed layers are never all promoted.
    layers={}
    for i,p in enumerate(ps.primitives):
        sid=p.source_id or f"primitive-{i}"
        if sid in claimed or (sid in entities and dict(entities[sid].metadata).get("region_partial")=="true"): continue
        role=roles.get(p.layer,named_role(p.layer))
        if role not in KINDS or len(p.coords)<2: continue
        points=tuple((x/units_per_foot,y/units_per_foot) for x,y in p.coords)
        shape=LineString(points)
        layers.setdefault((p.layer,KINDS[role]),[]).append((sid,points,shape))
    for (layer,kind),parts in sorted(layers.items()):
        shapes=[p[2] for p in parts];tree=STRtree(shapes);seen=set()
        for i in range(len(parts)):
            if i in seen: continue
            group=set();todo=[i]
            while todo:
                j=todo.pop()
                if j in group:continue
                group.add(j)
                for k in tree.query(shapes[j].buffer(.08)):
                    k=int(k)
                    if k not in seen and k not in group and shapes[j].distance(shapes[k])<=.08:
                        todo.append(k)
            seen.update(group)
            ids={parts[j][0] for j in group}
            points=[p for j in group for p in parts[j][1]]
            symbol=_instance(kind,ids,points,(layer,),"layer-and-geometry")
            if symbol: symbols.append(symbol)
    # An analytic quarter-circle on an otherwise mixed layer can propose a
    # swing door only when a radial leaf is also present; host checks follow.
    line_primitives=[p for p in ps.primitives if p.entity_type=="LINE" and len(p.coords)==2]
    line_shapes=[LineString([(x/units_per_foot,y/units_per_foot) for x,y in p.coords]) for p in line_primitives]
    line_tree=STRtree(line_shapes) if line_shapes else None
    used={sid for s in symbols for sid in s.source_ids}
    for e in ps.entities:
        if e.kind!="ARC" or e.id in used or e.center is None or not e.radius or dict(e.metadata).get("region_partial")=="true":continue
        if e.start_angle is None or e.end_angle is None:continue
        sweep=(e.end_angle-e.start_angle)%360;radius=e.radius/units_per_foot
        if not 65<=sweep<=115 or not 1.5<=radius<=5 or line_tree is None:continue
        center=tuple(c/units_per_foot for c in e.center)
        from shapely.geometry import Point
        radial=False;leaf_id="";leaf_points=()
        for j in line_tree.query(Point(center).buffer(radius+.05)):
            line=line_shapes[int(j)];a,b=list(line.coords)
            if min(math.dist(center,a),math.dist(center,b))<.08 and abs(line.length-radius)<.1:
                tip=b if math.dist(center,a)<.08 else a
                angle=math.degrees(math.atan2(tip[1]-center[1],tip[0]-center[0]))%360
                if min(abs((angle-v+180)%360-180) for v in (e.start_angle,e.end_angle))>5:continue
                radial=True;leaf_id=line_primitives[int(j)].source_id;leaf_points=(center,tip);break
        if radial:
            points=tuple((x/units_per_foot,y/units_per_foot) for x,y in e.coords)+leaf_points
            symbol=_instance("door",tuple(v for v in (e.id,leaf_id) if v),points,(e.layer,),"arc-and-leaf")
            if symbol:
                arc_ends=tuple((center[0]+radius*math.cos(math.radians(angle)),
                                center[1]+radius*math.sin(math.radians(angle)))
                               for angle in (e.start_angle,e.end_angle))
                closed_tip=max(arc_ends,key=lambda p:math.dist(p,leaf_points[-1]))
                symbols.append(replace(symbol,subtype="single_swing",properties=(
                    ("opening_start_ft",json.dumps(center)),
                    ("opening_end_ft",json.dumps(closed_tip)),
                    ("opening_axis_evidence","analytic-arc-hinge-and-opposite-tip"))))
    from archiagent.classify.exploded import recognize_exploded_doors
    symbols.extend(recognize_exploded_doors(ps, units_per_foot,
                                           {sid for s in symbols for sid in s.source_ids}))
    return tuple(sorted({s.id:s for s in symbols}.values(),key=lambda s:s.id))


def exclude_symbol_geometry(ps,symbols):
    """Exclude claimed source members and their children, never a bbox mask.

    Reviewed instances may reference an INSERT or HATCH parent without listing
    every virtual child/boundary. Leaving those descendants behind can recreate
    a recognized fixture/column as a wall on mixed layers. Do not traverse up
    to ancestors: claiming one child must not consume its unrelated siblings.
    """
    excluded={sid for s in symbols for sid in s.source_ids}
    children={}
    for entity in ps.entities:
        if entity.parent_id:
            children.setdefault(entity.parent_id,[]).append(entity.id)
    pending=list(excluded)
    while pending:
        parent=pending.pop()
        for child in children.get(parent,()):
            if child not in excluded:
                excluded.add(child)
                pending.append(child)
    return replace(ps,primitives=tuple(p for i,p in enumerate(ps.primitives)
                                      if (p.source_id or f"primitive-{i}") not in excluded),
                   entities=tuple(e for e in ps.entities if e.id not in excluded and dict(e.metadata).get("region_partial")!="true"))


def _opening_axis(symbol):
    """Return the closed opening span, never the area swept by an open leaf."""
    props = dict(symbol.properties)
    if "opening_start_ft" in props or "opening_end_ft" in props:
        try:
            a = tuple(float(v) for v in json.loads(props["opening_start_ft"]))
            b = tuple(float(v) for v in json.loads(props["opening_end_ft"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("opening-axis properties require JSON coordinate pairs") from exc
        if len(a)!=2 or len(b)!=2 or not all(math.isfinite(v) for v in (*a,*b)) or math.dist(a,b)<=1e-6:
            raise ValueError("opening-axis endpoints must be finite and distinct")
        return a,b
    # Older exploded records retained the actual arc, then hinge and leaf tip.
    if symbol.evidence=="exploded-arc-and-leaf" and len(symbol.boundary)>=7:
        arc,hinge,leaf = symbol.boundary[:-2],symbol.boundary[-2],symbol.boundary[-1]
        closed = max((arc[0],arc[-1]),key=lambda p:math.dist(p,leaf))
        return hinge,closed
    return None


def contextualize_openings(ps,symbols,units_per_foot):
    """Flag inferred doors contradicted by nearby, confident OCR sill notes.

    Proximity supplies contextual uncertainty only: it does not reclassify a
    door as a window or infer sill height. OCR IDs remain context properties,
    never symbol-member IDs used for geometry exclusion.
    """
    if (isinstance(units_per_foot,bool) or not isinstance(units_per_foot,(int,float))
            or not math.isfinite(units_per_foot) or units_per_foot<=0):
        raise ValueError("opening context requires finite positive units_per_foot")
    entities={e.id:e for e in ps.entities}
    notes=[]
    for text in ps.texts:
        entity=entities.get(text.source_id)
        if entity is None or entity.kind!="OCR_TEXT" or not isinstance(text.text,str):
            continue
        normalized=unicodedata.normalize("NFKC",text.text).upper()
        if not any("SILL" in token for token in re.findall(r"[A-Z]+",normalized)):
            continue
        metadata=dict(entity.metadata)
        try:
            confidence=float(metadata.get("confidence",metadata.get("ocr_confidence","nan")))
        except (TypeError,ValueError):
            continue
        if not math.isfinite(confidence) or not .85<=confidence<=1:
            continue
        bbox=text.bbox
        if (not isinstance(bbox,(list,tuple)) or len(bbox)!=4
                or not all(type(v) in (int,float) and math.isfinite(v) for v in bbox)
                or bbox[0]>bbox[2] or bbox[1]>bbox[3]):
            continue
        center=tuple(v/units_per_foot for v in text.center())
        notes.append((text.source_id,text.text,confidence,center))
    output=[]
    reviewed_evidence={"reviewed-template","reviewed","manual","reviewed-instance","reviewed-symbol"}
    for symbol in symbols:
        if symbol.kind not in {"door","window"}:
            output.append(symbol);continue
        axis=_opening_axis(symbol)
        if axis is None:
            output.append(symbol);continue
        a,b=axis;length=math.dist(a,b)
        if length<=1e-6:
            output.append(symbol);continue
        ux,uy=(b[0]-a[0])/length,(b[1]-a[1])/length
        context=[]
        for sid,text,confidence,point in notes:
            dx,dy=point[0]-a[0],point[1]-a[1]
            along=dx*ux+dy*uy;perpendicular=abs(dx*uy-dy*ux)
            if not -.25<=along<=length+.25 or perpendicular>2:
                continue
            extension=max(0,-along,along-length)
            context.append({"source_id":sid,"text":text,"confidence":confidence,
                            "position_ft":point,"distance_ft":math.hypot(perpendicular,extension),
                            "perpendicular_distance_ft":perpendicular,"along_axis_ft":along})
        if not context:
            output.append(symbol);continue
        props=dict(symbol.properties)
        context.sort(key=lambda note:(note["distance_ft"],note["source_id"],note["text"]))
        props["opening_context_evidence"]=json.dumps(context,ensure_ascii=False,sort_keys=True)
        props["opening_context_rule"]="OCR SILL note: confidence>=0.85, axis margin<=0.25ft, perpendicular<=2ft"
        reviewed=(symbol.evidence in reviewed_evidence or
                  any(str(props.get(key,"")).casefold()=="true" for key in ("semantic_reviewed","opening_type_reviewed")))
        if symbol.kind=="door" and not reviewed:
            props["opening_type_ambiguous"]="true"
            props["opening_type_ambiguity_reason"]="Nearby OCR SILL note may indicate a window; source interpretation requires review"
        output.append(replace(symbol,properties=tuple(sorted(props.items()))))
    return tuple(output)


def _parallel(a,b):
    return abs(a[0]*b[0]+a[1]*b[1])>=.9999


def _direction(w):
    return ((w.end[0]-w.start[0])/w.length_ft,(w.end[1]-w.start[1])/w.length_ft)


def _interior_obstruction(start,end,walls,ignored):
    """A wall ending on the opening interior is an obstruction too (T case)."""
    line = LineString((start,end))
    if line.length<=1e-6:
        return True
    core = LineString((line.interpolate(1e-6),line.interpolate(line.length-1e-6)))
    return any(i not in ignored and core.intersects(LineString((w.start,w.end)))
               for i,w in enumerate(walls) if w.length_ft>1e-6)


def host_openings(walls,symbols,wall_height_ft):
    """Localize recognized spans and require parallel or corner wall support.

    Source hinge/closed-tip evidence constrains swing-door direction and width.
    Corner hosts use intersecting wall bodies, including their real thickness;
    no drafting tolerance is expanded to reach a missing flank. A generated
    host may span jamb material, while its void retains the localized width.
    """
    from archiagent.geometry.junctions import _line_intersection, _distance_to_segment
    if not math.isfinite(wall_height_ft) or wall_height_ft<=0:
        raise ValueError("wall height must be finite and positive")
    walls=list(walls);openings=[]
    for symbol in sorted(symbols,key=lambda s:s.id):
        if symbol.kind not in ("door","window"):
            continue
        if str(dict(symbol.properties).get("opening_type_ambiguous","")).casefold()=="true":
            continue
        sill=3.0 if symbol.kind=="window" else 0.0
        head=min(wall_height_ft,7.0)
        if head<=sill:
            continue
        localized = _opening_axis(symbol)
        points = localized or symbol.boundary or (symbol.position,)
        shape = MultiPoint(points).convex_hull
        desired = None
        if localized:
            size=math.dist(*localized)
            desired=((localized[1][0]-localized[0][0])/size,(localized[1][1]-localized[0][1])/size)
        candidates=[]
        for i,a in enumerate(walls):
            if a.length_ft<=1e-6:
                continue
            ux,uy=_direction(a)
            if desired and not _parallel(desired,(ux,uy)):
                continue
            def project(p):return (p[0]-a.start[0])*ux+(p[1]-a.start[1])*uy
            def at(t):return (a.start[0]+t*ux,a.start[1]+t*uy)
            vals=[project(p) for p in points];lo,hi=min(vals),max(vals);span=hi-lo
            if not .8<=span<=(14 if symbol.kind=="door" else 30):
                continue
            axis=LineString((a.start,a.end))
            perpendicular=max(abs((p[0]-a.start[0])*uy-(p[1]-a.start[1])*ux) for p in points) if localized else shape.distance(axis)
            if localized and perpendicular>a.thickness_ft/2+.05:
                continue
            # A swing bbox alone cannot justify cutting any continuous wall.
            if (lo>=-.05 and hi<=a.length_ft+.05
                    and shape.distance(axis)<=a.thickness_ft/2+.05
                    and (symbol.kind=="window" or localized)):
                start,end=at(max(0,lo)),at(min(a.length_ft,hi))
                if not _interior_obstruction(start,end,walls,{i}):
                    candidates.append((perpendicular+.2,i,start,end,None))

            def endpoint_allowance(t, ignored):
                allowance=a.thickness_ft/2+.05
                point=at(t)
                for k,other in enumerate(walls):
                    if k==i or other.length_ft<=1e-6 or abs(sum(x*y for x,y in zip((ux,uy),_direction(other))))>.1:
                        continue
                    hit=_line_intersection(a.start,a.end,other.start,other.end)
                    if hit is not None and math.dist(hit,point)<=.05 and _distance_to_segment(hit,other.start,other.end)<=a.thickness_ft/2+.05:
                        allowance=max(allowance,(a.thickness_ft+other.thickness_ft)/2+.05)
                return allowance

            def propose(low,high,ignored,source_ids):
                gap=high-low
                if not .8<=gap<=(14 if symbol.kind=="door" else 30):return
                if not .55<=gap/span<=1.55:return
                start,end=at(low),at(high)
                if shape.distance(LineString((start,end)))>a.thickness_ft/2+.12:return
                if min(hi,high)-max(lo,low)<.5*gap:return
                if _interior_obstruction(start,end,walls,ignored):return
                if localized:
                    if lo<low-.05 or hi>high+.05:return
                    if lo-low>endpoint_allowance(low,ignored) or high-hi>endpoint_allowance(high,ignored):return
                    opening_start,opening_end=at(max(lo,low)),at(min(hi,high))
                else:
                    opening_start,opening_end=start,end
                if any(_spans_overlap(opening_start,opening_end,o.start,o.end) for o in openings):return
                score=perpendicular+abs((lo+hi-low-high)/2)+abs(span-gap)*.2
                host=WallSeg(start,end,a.thickness_ft,a.source_layer,"opening-host","measured",
                             tuple(sorted(set(source_ids+symbol.source_ids))))
                candidates.append((score,len(walls),opening_start,opening_end,host))

            for j in range(i+1,len(walls)):
                b=walls[j]
                if b.length_ft<=1e-6 or a.source_layer!=b.source_layer or abs(a.thickness_ft-b.thickness_ft)>.15:continue
                if not _parallel((ux,uy),_direction(b)):continue
                if max(abs((p[0]-a.start[0])*uy-(p[1]-a.start[1])*ux) for p in (b.start,b.end))>.05:continue
                b0,b1=sorted((project(b.start),project(b.end)))
                if b0>=a.length_ft:low,high=a.length_ft,b0
                elif b1<=0:low,high=b1,0
                else:continue
                propose(low,high,{i,j},a.source_ids+b.source_ids)
            if not localized:
                continue
            # A door may terminate at a perpendicular wall instead of a second
            # collinear flank. Only that observed wall-body intersection closes
            # the new host; nearby wall tips/bounding boxes are insufficient.
            for j,b in enumerate(walls):
                if i==j or b.length_ft<=1e-6 or abs(sum(x*y for x,y in zip((ux,uy),_direction(b))))>.1:continue
                hit=_line_intersection(a.start,a.end,b.start,b.end)
                if hit is None or _distance_to_segment(hit,b.start,b.end)>a.thickness_ft/2+.05:continue
                t=project(hit)
                if t<0 and hi<=.05:low,high=t,0
                elif t>a.length_ft and lo>=a.length_ft-.05:low,high=a.length_ft,t
                else:continue
                corner_end=lo if t<0 else hi
                if abs(corner_end-t)>(a.thickness_ft+b.thickness_ft)/2+.05:continue
                propose(low,high,{i,j},a.source_ids+b.source_ids)
        candidates=[c for c in candidates if not any(
            _spans_overlap(c[2],c[3],o.start,o.end) for o in openings)]
        candidates.sort(key=lambda c:(c[0],c[2],c[3],c[1]))
        # Symmetric evidence on two distinct wall axes remains unresolved.
        if not candidates:continue
        best=candidates[0]
        if len(candidates)>1 and abs(best[0]-candidates[1][0])<.02 and (best[2],best[3])!=(candidates[1][2],candidates[1][3]):continue
        _,index,start,end,host=best
        if host is not None:
            index=len(walls);walls.append(host)
        if math.dist(start,end)<=1e-6:continue
        openings.append(Opening("opening-"+symbol.id,symbol.kind,index,start,end,head-sill,sill,
                                symbol.id,symbol.source_ids,symbol.subtype))
    return tuple(walls),tuple(openings)


def remap_openings(openings, final_walls):
    """Keep source-qualified hosts; ambiguous or split spans remain unresolved."""
    result=[]
    for opening in openings:
        segment=LineString((opening.start,opening.end));candidates=[]
        for i,w in enumerate(final_walls):
            axis=LineString((w.start,w.end))
            if axis.buffer(1e-5).covers(segment):
                linked=bool(opening.source_ids) and set(opening.source_ids).issubset(w.source_ids)
                candidates.append((not linked,w.length_ft,i))
        if candidates:
            candidates.sort()
            strongest=[c for c in candidates if c[0]==candidates[0][0]]
            if len(strongest)>1:
                continue
            result.append(replace(opening,host_wall_index=strongest[0][2]))
    return tuple(result)
