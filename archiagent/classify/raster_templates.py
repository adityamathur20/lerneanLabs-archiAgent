"""Optional OpenCV proposals, verified against complete registered source paths.

This is drawing-local image template matching, not a pretrained model. Raster
scores do not establish semantic truth. Reviewed source seeds persist regardless
of self-match score. New instances require complete metric source association;
unassociated image peaks remain diagnostic records, not classified symbols.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
import math

from shapely import affinity
from shapely.ops import unary_union
from shapely.strtree import STRtree

from archiagent.classify.templates import _parts, _seed_ids, _symbol, KINDS

MAX_IMAGE_PIXELS = 20_000_000
MAX_RESPONSE_PIXELS = 80_000_000
MAX_SOURCE_PARTS = 200_000
MAX_PEAKS = 1000


def _paths(geometry):
    if geometry.geom_type in {"LineString","LinearRing"}:
        yield list(geometry.coords)
    else:
        for g in getattr(geometry,"geoms",()):
            yield from _paths(g)


def _render(geometries,bounds,pixels_per_foot):
    import cv2
    import numpy as np
    xmin,ymin,xmax,ymax=bounds
    width=int(math.ceil((xmax-xmin)*pixels_per_foot))+1
    height=int(math.ceil((ymax-ymin)*pixels_per_foot))+1
    if width<=0 or height<=0 or width*height>MAX_IMAGE_PIXELS:
        raise ValueError("raster template image exceeds 20M pixels; crop the region or lower pixels_per_foot")
    image=np.zeros((height,width),dtype=np.uint8)
    for geometry in geometries:
        for path in _paths(geometry):
            coordinates=np.rint([((x-xmin)*pixels_per_foot,(ymax-y)*pixels_per_foot) for x,y in path]).astype(np.int32)
            if len(coordinates)>=2:
                cv2.polylines(image,[coordinates],False,255,1,lineType=cv2.LINE_8)
    return image


def _expanded_bounds(bounds,padding):
    a,b,c,d=bounds
    return a-padding,b-padding,c+padding,d+padding


def _associate(parts,tree,source_counts,expected,tolerance):
    """Only complete paths inside the expected metric tube may be claimed."""
    tube=expected.buffer(tolerance,cap_style=1,join_style=2)
    eligible=[]
    for i in tree.query(tube):
        p=parts[int(i)]
        if p.geometry.length>0 and p.geometry.difference(tube).length<=1e-7:
            eligible.append(p)
    counts=Counter(p.source_id for p in eligible)
    eligible=tuple(p for p in eligible if counts[p.source_id]==source_counts[p.source_id])
    union=unary_union([p.geometry for p in eligible])
    if union.is_empty or expected.length<=0:
        return eligible,0.0,union
    covered=expected.intersection(union.buffer(tolerance,cap_style=1,join_style=2)).length
    return eligible,min(1.0,covered/expected.length),union


def match_raster_templates(ps,units_per_foot,records):
    """Return reviewed seeds and source-linked raster discoveries.

    Records: id/kind/source_ids, optional subtype, rotations_deg, target_layers,
    threshold (.92 to 1), pixels_per_foot (32, allowed 8..128), tolerance_in
    (.1, maximum .5), max_candidates (200, maximum1000). No scaling. Default
    layer scope is the seed layers. Seeds must span >=.2ft in both dimensions,
    >=8 raster pixels per dimension and >=1ft of source path to reject tiny
    text-like or one-dimensional patterns. Requires the optional cv extra.
    """
    if not isinstance(records,(list,tuple)) or not all(isinstance(r,dict) for r in records):
        raise ValueError("raster templates must be a list of record objects")
    records=tuple(records)
    for record in records:
        if not isinstance(record.get("matcher","raster"),str) or record.get("matcher","raster")!="raster":
            raise ValueError("raster template matcher must be the string raster")
        if not isinstance(record.get("kind"),str):
            raise ValueError("raster template kind must be a supported string")
    if not records:
        return ()
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV raster templates require the optional cv dependency extra") from exc
    if (isinstance(units_per_foot,bool) or not isinstance(units_per_foot,(int,float))
            or not math.isfinite(units_per_foot) or units_per_foot<=0):
        raise ValueError("raster template units_per_foot must be finite and positive")
    if len(records)>32 or len({str(r.get('id','')) for r in records})!=len(records):
        raise ValueError("at most 32 uniquely named raster templates are supported")
    parts=_parts(ps,units_per_foot)
    if len(parts)>MAX_SOURCE_PARTS:
        raise ValueError("raster template source exceeds 200000 paths; crop the drawing region")
    counts=Counter(p.source_id for p in ps.primitives if p.source_id)
    outputs={};rejections={};processed_pixels=0;peak_count=0
    for record in sorted(records,key=lambda r:str(r.get('id',''))):
        if not isinstance(record.get('id'),str) or not record['id'] or record.get('kind') not in KINDS:
            raise ValueError("raster template needs id and a supported symbol kind")
        if any(k in record for k in ('scale','scales','scale_factor')):
            raise ValueError("raster template scaling is unsupported")
        ids=record.get('source_ids',())
        if not isinstance(ids,(list,tuple)) or not ids or not all(isinstance(i,str) and i for i in ids):
            raise ValueError("raster source_ids must name seed entities in the current drawing")
        seed_ids=_seed_ids(ps,ids)
        seed=tuple(p for p in parts if p.source_id in seed_ids)
        if not seed or len(seed)>256:
            raise ValueError("raster seed must contain 1 to 256 complete paths")
        seed_counts=Counter(p.source_id for p in seed)
        if any(seed_counts[sid]!=counts[sid] for sid in seed_counts):
            raise ValueError("raster seed includes incomplete source entities")
        if record['kind'] in {'column','beam'} and any(p.signature[2] for p in seed):
            raise ValueError("holed structural templates require a reviewed structural profile")
        settings=(record.get('pixels_per_foot',32),record.get('tolerance_in',.1),record.get('threshold',.92))
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in settings):
            raise ValueError('raster resolution, tolerance and threshold must be numbers')
        scale,tolerance_in,threshold=map(float,settings)
        if not math.isfinite(scale) or not 8<=scale<=128:
            raise ValueError("pixels_per_foot must be finite and between 8 and 128")
        if not math.isfinite(tolerance_in) or not 0<tolerance_in<=.5:
            raise ValueError("tolerance_in must be positive and at most 0.5in")
        if not math.isfinite(threshold) or not .92<=threshold<=1:
            raise ValueError("raster threshold must be between .92 and 1")
        tolerance=tolerance_in/12
        angles=record.get('rotations_deg',(0,90,180,270))
        if not isinstance(angles,(list,tuple)) or not 1<=len(angles)<=16 or not all(type(a) in (int,float) and math.isfinite(a) for a in angles):
            raise ValueError("rotations_deg requires 1 to 16 finite angles")
        angles=tuple(sorted({float(a)%360 for a in angles}))
        max_candidates=record.get('max_candidates',200)
        if isinstance(max_candidates,bool) or not isinstance(max_candidates,int) or not 1<=max_candidates<=MAX_PEAKS:
            raise ValueError("max_candidates must be an integer from 1 to 1000")
        layer_values=record.get('target_layers',sorted({p.layer for p in seed}))
        if not isinstance(layer_values,(list,tuple)) or not layer_values or not all(isinstance(v,str) and v for v in layer_values):
            raise ValueError("target_layers must be a nonempty list of layer names")
        seed_symbol=_symbol(record,seed,0.0,tolerance_in)
        if seed_symbol is None:
            raise ValueError("reviewed raster seed cannot form a valid symbol instance")
        seed_symbol=replace(seed_symbol,evidence='reviewed-template',confidence=.8,
            properties=seed_symbol.properties+(('matcher','raster-seed'),('template_seed_reviewed','true'),
                ('seed_source_ids',json.dumps(seed_symbol.source_ids)),
                ('source_association','reviewed-complete-entities'),('source_coverage','1.0')))
        seed_key=('ids',seed_symbol.source_ids)
        previous=outputs.get(seed_key)
        if previous and (previous.kind,previous.subtype)!=(seed_symbol.kind,seed_symbol.subtype):
            raise ValueError("reviewed seeds assign conflicting roles to the same source instance")
        if previous is None or previous.evidence!='reviewed-template':outputs[seed_key]=seed_symbol
        rejections.setdefault(str(record['id']),[])
        layers={str(v).casefold() for v in layer_values}
        candidates=tuple(p for p in parts if p.layer.casefold() in layers)
        if not candidates:continue
        tree=STRtree([p.geometry for p in candidates])
        source=unary_union([p.geometry for p in candidates])
        template=unary_union([p.geometry for p in seed])
        xmin,ymin,xmax,ymax=template.bounds
        if min(xmax-xmin,ymax-ymin)<.2 or min(xmax-xmin,ymax-ymin)*scale<8 or template.length<1:
            raise ValueError("raster seed is too small or one-dimensional; select a complete symbol")
        bounds=_expanded_bounds(source.bounds,3/scale)
        image=_render((p.geometry for p in candidates),bounds,scale)
        record_peaks=0
        for angle in angles:
            rotated=affinity.rotate(template,angle,origin=(0,0))
            template_bounds=_expanded_bounds(rotated.bounds,2/scale)
            patch=_render((rotated,),template_bounds,scale)
            if patch.shape[0]>image.shape[0] or patch.shape[1]>image.shape[1]:continue
            foreground=int(np.count_nonzero(patch))
            if foreground<24 or foreground==patch.size:
                raise ValueError("raster seed lacks a distinctive foreground/background pattern")
            pixels=(image.shape[0]-patch.shape[0]+1)*(image.shape[1]-patch.shape[1]+1)
            processed_pixels+=pixels
            if processed_pixels>MAX_RESPONSE_PIXELS:
                raise ValueError("raster comparison budget exceeded; crop region or reduce rotations/resolution")
            response=cv2.matchTemplate(image,patch,cv2.TM_CCORR_NORMED)
            response=np.nan_to_num(response,nan=0,posinf=0,neginf=0)
            radius=max(1,int(min(patch.shape)/4))
            maxima=cv2.dilate(response,np.ones((2*radius+1,2*radius+1),np.uint8))
            ys,xs=np.nonzero((response>=threshold)&(response>=maxima-1e-7))
            if len(xs)+record_peaks>max_candidates or len(xs)+peak_count>MAX_PEAKS:
                raise ValueError("raster candidate budget exceeded; narrow target layers or review template specificity")
            record_peaks+=len(xs);peak_count+=len(xs)
            for y,x in sorted(zip(ys.tolist(),xs.tolist()),key=lambda p:(-float(response[p]),p)):
                dx=bounds[0]+x/scale-template_bounds[0]
                dy=bounds[3]-y/scale-template_bounds[3]
                expected=affinity.translate(rotated,dx,dy)
                # Raster quantization may shift a proposal by part of one pixel.
                # Refine using registered source geometry, then recheck at the
                # original metric tolerance; pixel size never relaxes acceptance.
                preliminary,_,associated=_associate(candidates,tree,counts,expected,tolerance+math.sqrt(2)/scale)
                if not associated.is_empty:
                    rx=associated.centroid.x-expected.centroid.x
                    ry=associated.centroid.y-expected.centroid.y
                    if math.hypot(rx,ry)<=math.sqrt(2)/scale:
                        dx+=rx;dy+=ry
                        expected=affinity.translate(rotated,dx,dy)
                matched,coverage,_=_associate(candidates,tree,counts,expected,tolerance)
                reliable=coverage>=.98 and bool(matched)
                score=float(response[y,x])
                if not reliable:
                    rejections[str(record['id'])].append({'position_ft':(expected.centroid.x,expected.centroid.y),
                        'opencv_score':score,'source_coverage':coverage,
                        'reason':'incomplete source association; no semantic instance or exclusion IDs created'})
                    continue
                symbol=_symbol(record,matched,angle,tolerance_in)
                if symbol is None:continue
                source_ids=tuple(sorted({p.source_id for p in matched}))
                # Dedup rotational symmetry and repeated local maxima, but do
                # not merge two distinct complete source instances.
                key=('ids',source_ids)
                digest=hashlib.sha256(repr((record['id'],key)).encode()).hexdigest()[:16]
                score=float(response[y,x])
                symbol=replace(symbol,id='raster-'+digest,source_ids=source_ids,evidence='opencv-template',
                    confidence=.75,
                    properties=symbol.properties+(('matcher','opencv'),('opencv_score',str(score)),
                        ('opencv_threshold',str(threshold)),('pixels_per_foot',str(scale)),
                        ('source_coverage',str(coverage)),('source_association','complete-entities')))
                existing=outputs.get(key)
                if existing and (existing.kind,existing.subtype)!=(symbol.kind,symbol.subtype):
                    raise ValueError("raster templates assign conflicting roles to the same source instance")
                if existing and existing.evidence=='reviewed-template':
                    props=dict(existing.properties)
                    props['opencv_self_match_score']=str(max(score,float(props.get('opencv_self_match_score','0'))))
                    outputs[key]=replace(existing,properties=tuple(sorted(props.items())))
                elif existing is None or score>float(dict(existing.properties)['opencv_score']):outputs[key]=symbol
    # Preserve explicit seeds. Overlapping discoveries lack unique ownership
    # and remain diagnostics instead of emitting unassociated class claims.
    reviewed=[s for s in outputs.values() if s.evidence=='reviewed-template']
    seed_claims=Counter(sid for s in reviewed for sid in s.source_ids)
    if any(count>1 for count in seed_claims.values()):
        raise ValueError("reviewed raster seeds overlap source entities; disambiguate seed membership")
    claimed=Counter(sid for s in outputs.values() for sid in s.source_ids)
    kept=[]
    for symbol in outputs.values():
        if symbol.evidence!='reviewed-template' and any(claimed[sid]>1 for sid in symbol.source_ids):
            template_id=dict(symbol.properties)['template_id']
            rejections.setdefault(template_id,[]).append({'position_ft':symbol.position,
                'reason':'overlapping source ownership; no semantic instance or exclusion IDs created'})
            continue
        kept.append(symbol)
    result=[]
    for symbol in kept:
        if symbol.evidence=='reviewed-template':
            props=dict(symbol.properties)
            notes=rejections.get(props['template_id'],[])
            props['raster_rejected_candidate_count']=str(len(notes))
            props['raster_rejected_candidates']=json.dumps(notes,sort_keys=True)
            symbol=replace(symbol,properties=tuple(sorted(props.items())))
        result.append(symbol)
    return tuple(sorted(result,key=lambda s:s.id))
