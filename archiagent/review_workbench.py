"""Self-contained source annotation workbench: no web service or CDN required.

The SVG and exported annotations use region-local model feet. Source IDs remain
those of the original evidence; original drawing coordinates are recoverable
only when source_origin has been retained or explicitly provided by the caller.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import html
import json
import math
from pathlib import Path


KINDS = ("door", "window", "column", "beam", "stair", "furniture", "electrical", "plumbing", "vehicle", "footprint", "void")


def _numbers(value, count, name):
    if not isinstance(value, (tuple, list)) or len(value) != count or not all(
        type(v) in (int, float) and math.isfinite(v) for v in value
    ):
        raise ValueError(f"{name} requires {count} finite numbers")
    return tuple(float(v) for v in value)


def _bounds(value, name):
    bounds = _numbers(value, 4, name)
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError(f"{name} requires xmin < xmax and ymin < ymax")
    return bounds


def _data(value):
    return asdict(value) if is_dataclass(value) else value


def _payload(model, entities, *, bounds_source=None, origin_source=None, region_bounds_source=None):
    model = _data(model)
    upf = model.get("scale", {}).get("units_per_foot")
    if type(upf) not in (int, float) or not math.isfinite(upf) or upf <= 0:
        raise ValueError("workbench requires finite positive source units per foot")
    fingerprint, region_id = model.get("source_sha256"), model.get("region_id")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("workbench requires the source SHA256")
    if not isinstance(region_id, str) or not region_id:
        raise ValueError("workbench requires an explicit region_id")
    known_origin = origin_source if origin_source is not None else model.get("source_origin")
    origin = _numbers(known_origin, 2, "source origin") if known_origin is not None else None
    original_bounds = region_bounds_source if region_bounds_source is not None else model.get("source_region_bounds")
    original_bounds = _bounds(original_bounds, "original region bounds") if original_bounds else None
    # bounds_source is a viewport crop in already registered source units.
    crop = _bounds(bounds_source, "source viewport bounds") if bounds_source is not None else None
    records = [_data(e) for e in entities]
    entity_by_id = {e.get("id"): e for e in records}
    hatch_parents = {e.get("parent_id") for e in records if e.get("kind") == "HATCH_BOUNDARY"}
    shapes = []
    for e in records:
        kind, sid = e.get("kind", ""), e.get("id", "")
        if kind in {"INSERT", "DIMENSION", "PDF_DRAWING", "PDF_CUBIC"} or (kind == "HATCH" and sid in hatch_parents):
            continue
        coords = [_numbers(p, 2, "source point") for p in e.get("coords", ())]
        center = _numbers(e["center"], 2, "source center") if e.get("center") is not None else None
        text = dict(e.get("metadata", ())).get("text", "") if kind in {"TEXT", "MTEXT", "ATTRIB", "PDF_TEXT", "OCR_TEXT"} else ""
        if not coords and center:
            coords = [center]
        if not coords:
            continue
        xs, ys = zip(*coords)
        if crop and (max(xs) < crop[0] or min(xs) > crop[2] or max(ys) < crop[1] or min(ys) > crop[3]):
            continue
        select_id = e.get("parent_id") if kind == "HATCH_BOUNDARY" else sid
        parent_metadata = dict(entity_by_id.get(select_id, {}).get("metadata", ()))
        shapes.append({"id": select_id or sid, "entity_id": sid, "kind": kind,
            "layer": e.get("layer", ""), "points": [[x/upf, y/upf] for x, y in coords],
            "closed": bool(e.get("closed")), "holes": [[[x/upf, y/upf] for x, y in
                (_numbers(p, 2, "hole point") for p in ring)] for ring in e.get("holes", ())],
            "text": text, "partial": dict(e.get("metadata", ())).get("region_partial") == "true" or
                                      parent_metadata.get("region_partial") == "true"})
    if crop:
        view = [v/upf for v in crop]
    elif shapes:
        points = [p for e in shapes for p in e["points"]]
        xs, ys = zip(*points)
        pad = max(max(xs)-min(xs), max(ys)-min(ys), 1)*.02
        view = [min(xs)-pad, min(ys)-pad, max(xs)+pad, max(ys)+pad]
    else:
        raise ValueError("workbench has no drawable source evidence in the selected bounds")
    # A viewport is not the full acceptance region. Recover original bounds
    # only from retained region metadata; missing coordinates disable export.
    return {"schema_version": 1, "source_sha256": fingerprint, "region_id": region_id,
            "bounds": original_bounds, "source_origin": origin,
            "coordinate_system": "model-feet", "source_units_per_foot": upf,
            "registration_available": origin is not None and original_bounds is not None,
            "registration_verified": origin is not None and original_bounds is not None and
                                     origin_source is None and region_bounds_source is None,
            "registration_basis": "caller-supplied" if origin_source is not None or region_bounds_source is not None
                                  else "retained-report" if origin is not None and original_bounds is not None else "unavailable",
            "scale_verified": bool(model.get("scale_verified", False)),
            "view_bounds_ft": view, "shapes": shapes, "model_symbols": model.get("symbols", []),
            "storey_name": model.get("storey_name", region_id)}


def write_workbench(model, source, output_path, *, bounds_source=None, origin_source=None,
                    region_bounds_source=None):
    """Write one offline HTML page; crop bounds use registered source units.

    The model must retain source_region_bounds (original source coordinates).
    For legacy reports, origin_source and region_bounds_source can explicitly
    supply registration. Such overrides remain marked unverified; missing
    registration disables annotation export instead of guessing coordinates.
    """
    return _write(_payload(model, source.entities, bounds_source=bounds_source,
                           origin_source=origin_source, region_bounds_source=region_bounds_source), output_path)


def write_workbench_from_report(report_path, output_path, *, region_id=None,
                                bounds_source=None, origin_source=None, region_bounds_source=None):
    report = json.loads(Path(report_path).read_text())
    regions = report.get("regions", [])
    if region_id is not None:
        regions = [r for r in regions if r.get("model", {}).get("region_id") == region_id]
    if len(regions) != 1:
        raise ValueError("select exactly one report region by region_id")
    entry = regions[0]
    return _write(_payload(entry["model"], entry.get("source_entities", ()),
                           bounds_source=bounds_source, origin_source=origin_source,
                           region_bounds_source=region_bounds_source), output_path)


def _write(payload, output_path):
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    # Prevent source text and identifiers from breaking out of a script block.
    for a, b in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(a, b)
    document = _HTML.replace("__TITLE__", html.escape(payload["storey_name"])).replace("__DATA__", encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


_HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Floor plan review — __TITLE__</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;color:#233047;background:#f3f5f8}header{padding:12px 18px;background:#fff;border-bottom:1px solid #ccd3dd}h1{font-size:20px;margin:0 0 5px}p{margin:6px 0;line-height:1.4}.layout{display:grid;grid-template-columns:minmax(0,1fr) 335px;height:calc(100vh - 102px)}main{position:relative;min-height:400px}aside{overflow:auto;background:white;padding:14px;border-left:1px solid #ccd3dd}svg{width:100%;height:100%;background:#fcfcfd;touch-action:none}button,input,select{font:inherit;border:1px solid #b2bccb;border-radius:5px;padding:6px;background:white}button{cursor:pointer}button.primary{background:#1355ac;color:white;border-color:#1355ac}button:disabled{opacity:.5;cursor:default}label{display:block;margin-top:9px}input[type=text],select{width:100%}.row{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.row input{width:74px}.note{font-size:12px;color:#526079}.warning{color:#8c3f00;background:#fff1dd;padding:7px;border-radius:4px}#message{min-height:20px;color:#833507}#selection{font:12px ui-monospace,monospace;max-height:115px;overflow:auto;word-break:break-all;background:#f4f6f8;padding:7px}#annotation-list{max-height:200px;overflow:auto}.item{padding:6px 0;border-bottom:1px solid #ddd}.source{fill:none;stroke:#6b7584;stroke-width:1;vector-effect:non-scaling-stroke;cursor:pointer}.source:hover{stroke:#eb9000;stroke-width:3}.source.selected{stroke:#1769d2;stroke-width:3}.source.partial{stroke-dasharray:3 3}.mark{fill:#047a4622;stroke:#047a46;stroke-width:2;vector-effect:non-scaling-stroke;pointer-events:none}.mark.void{fill:#d8434322;stroke:#bd2c2c}.mark.footprint{fill:#13706f15;stroke:#13706f}.scope{fill:none;stroke:#b529a4;stroke-dasharray:5 4;stroke-width:2;vector-effect:non-scaling-stroke;pointer-events:none}.proposed{fill:none;stroke:#9a4eb5;stroke-width:1;vector-effect:non-scaling-stroke;pointer-events:none}.sketch{fill:#f0a00022;stroke:#e28b00;stroke-width:2;vector-effect:non-scaling-stroke;pointer-events:none}hr{border:0;border-top:1px solid #ddd;margin:14px 0}#position{position:absolute;left:10px;bottom:10px;background:#fffffff0;padding:5px;font-size:12px}@media(max-width:800px){.layout{grid-template-columns:1fr;height:auto}main{height:65vh}aside{border-left:0}}
</style>
<header><h1>Floor plan review — __TITLE__</h1><p class="note">Select source strokes to label an instance. Scroll to zoom; drag empty space to pan. Draw an outline when the source is fragmented. Your work stays in this browser until you export it.</p></header>
<div class="layout"><main><svg id="canvas" aria-label="Selectable floor plan source geometry"><g id="source"></g><g id="proposals"></g><g id="marks"></g><g id="scopes"></g><path id="sketch" class="sketch"></path></svg><div id="position">Coordinates: feet in this plan region</div></main><aside>
<div id="registration" class="note"></div><div id="message" role="status" aria-live="polite"></div>
<div class="row"><button id="fit">Fit drawing</button><button id="clear">Clear selection</button></div>
<label><input id="show-text" type="checkbox" checked> Show text</label><label><input id="show-fills" type="checkbox" checked> Show filled outlines</label><label><input id="show-proposals" type="checkbox"> Show model proposals</label>
<label>Find source ID<input id="find-id" type="text" placeholder="For example: 4A21"></label><button id="find">Find and select</button>
<label>Selected source entities</label><div id="selection">None selected</div>
<label>Object or area<select id="kind"><option>door</option><option>window</option><option>column</option><option>beam</option><option>stair</option><option>furniture</option><option>electrical</option><option>plumbing</option><option>vehicle</option><option value="footprint">Floor footprint</option><option value="void">Void / courtyard</option></select></label>
<label>Instance label<input id="label" type="text" placeholder="Optional, e.g. east bedroom door"></label>
<div class="row"><button id="trace">Draw outline</button><button id="finish">Finish outline</button><button id="cancel">Cancel outline</button></div>
<label>Reviewer name or ID<input id="reviewer-id" type="text"></label><label>Reviewer role<select id="reviewer-role"><option value="unknown">Unspecified</option><option value="user">User</option><option value="external">External reviewer</option><option value="assistant">Assistant</option></select></label>
<label><input id="reviewed" type="checkbox"> I have reviewed this annotation</label><button id="add" class="primary">Save annotation</button>
<hr><strong>Saved annotations</strong><div id="annotation-list"></div>
<hr><strong>Review area</strong><p class="note">Bounds are feet in this plan region. A complete area means every instance of the selected class inside it has been reviewed.</p>
<div class="row"><input id="x0" aria-label="Minimum X"><input id="y0" aria-label="Minimum Y"><input id="x1" aria-label="Maximum X"><input id="y1" aria-label="Maximum Y"></div>
<div class="row"><button id="view">Apply view</button><button id="current">Use current view</button></div>
<label><input id="complete" type="checkbox"> Every instance of this class in this area is reviewed</label><button id="scope">Save review area</button><div id="scope-list" class="note"></div>
<hr><div class="row"><button id="export" class="primary">Export annotations</button><button id="templates">Export model review</button></div>
<label>Continue from an annotation file<input id="import" type="file" accept=".json,application/json"></label>
<p class="note">Exports stay partial. Footprint and void outlines remain draft model inputs until separately accepted. Saving labels, templates or areas never marks the model as verified. Complete review areas are explicit and apply only to the selected class.</p>
</aside></div>
<script id="workbench-data" type="application/json">__DATA__</script>
<script>
'use strict';
const DATA=JSON.parse(document.getElementById('workbench-data').textContent);
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const selected=new Set(), elements=new Map(), shapesById=new Map();
const isArea=kind=>kind==='footprint'||kind==='void';
let symbols=[], scopes=[], view=[...DATA.view_bounds_ft], sketch=[], drawing=false, pan=null, renderedSelection=new Set();
function message(s){$('message').textContent=s}
function node(name,attrs){const e=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e}
function path(points,closed=false){return points.map((p,i)=>(i?'L':'M')+p[0]+' '+(-p[1])).join(' ')+(closed?' Z':'')}
function ringBounds(points){const b=[Infinity,Infinity,-Infinity,-Infinity];for(const p of points){b[0]=Math.min(b[0],p[0]);b[1]=Math.min(b[1],p[1]);b[2]=Math.max(b[2],p[0]);b[3]=Math.max(b[3],p[1])}return b}
function validPoint(p){return Array.isArray(p)&&p.length===2&&p.every(v=>typeof v==='number'&&Number.isFinite(v))}
function validBounds(b){return Array.isArray(b)&&b.length===4&&b.every(v=>typeof v==='number'&&Number.isFinite(v))&&b[0]<b[2]&&b[1]<b[3]}
function validBoundary(ring){if(!Array.isArray(ring)||ring.length<4||!ring.every(validPoint))return false;const n=ring.length-1,first=ring[0],last=ring[n];if(first[0]!==last[0]||first[1]!==last[1])return false;let area=0;const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);const between=(a,b,p)=>p[0]>=Math.min(a[0],b[0])-1e-9&&p[0]<=Math.max(a[0],b[0])+1e-9&&p[1]>=Math.min(a[1],b[1])-1e-9&&p[1]<=Math.max(a[1],b[1])+1e-9;for(let i=0;i<n;i++){const a=ring[i],b=ring[i+1];if(a[0]===b[0]&&a[1]===b[1])return false;area+=(a[0]-first[0])*(b[1]-first[1])-(b[0]-first[0])*(a[1]-first[1]);for(let j=i+2;j<n;j++){if(i===0&&j===n-1)continue;const c=ring[j],d=ring[j+1],x=cross(a,b,c),y=cross(a,b,d),z=cross(c,d,a),w=cross(c,d,b);if((x*y<0&&z*w<0)||(Math.abs(x)<1e-9&&between(a,b,c))||(Math.abs(y)<1e-9&&between(a,b,d))||(Math.abs(z)<1e-9&&between(c,d,a))||(Math.abs(w)<1e-9&&between(c,d,b)))return false}}return Math.abs(area)>1e-9}
function changeView(b){view=[...b];$('canvas').setAttribute('viewBox',[b[0],-b[3],b[2]-b[0],b[3]-b[1]].join(' '))}
function fields(b){['x0','y0','x1','y1'].forEach((k,i)=>$(k).value=b[i].toFixed(4))}
function readBounds(){const b=['x0','y0','x1','y1'].map(k=>Number($(k).value));if(!validBounds(b))throw Error('Enter finite bounds with minimum values below maximum values.');return b}
function sourcePoint(event){const p=$('canvas').createSVGPoint();p.x=event.clientX;p.y=event.clientY;const q=p.matrixTransform($('canvas').getScreenCTM().inverse());return [q.x,-q.y]}
function refreshSelection(){for(const id of new Set([...renderedSelection,...selected]))if(renderedSelection.has(id)!==selected.has(id))for(const e of elements.get(id)||[])e.classList.toggle('selected',selected.has(id));renderedSelection=new Set(selected);$('selection').textContent=selected.size?[...selected].sort().join(', '):'None selected'}
function toggle(id){selected.has(id)?selected.delete(id):selected.add(id);refreshSelection()}
function reviewer(){const id=$('reviewer-id').value.trim(),role=$('reviewer-role').value;return {id:id||'unspecified',role}}
function requireReviewer(){const r=reviewer();if(r.id==='unspecified'||r.role==='unknown')throw Error('Enter a reviewer identity and role before marking work reviewed.');return r}
const fragment=document.createDocumentFragment();
for(const s of DATA.shapes){let e;if(s.text){e=node('text',{x:s.points[0][0],y:-s.points[0][1],'font-size':.65,fill:'#697485'});e.textContent=s.text}else e=node('path',{d:path(s.points,s.closed)+s.holes.map(h=>path(h,true)).join(' ')});
 e.classList.add('source');if(s.partial)e.classList.add('partial');e.dataset.sourceId=s.id;e.dataset.isText=s.text?'1':'0';e.dataset.isFill=/HATCH|FILL/.test(s.kind)?'1':'0';const title=node('title',{});title.textContent=s.id+' · '+s.kind+' · layer '+s.layer+(s.partial?' · crosses review boundary':'');e.appendChild(title);fragment.appendChild(e);
 if(!elements.has(s.id))elements.set(s.id,[]);elements.get(s.id).push(e);if(!shapesById.has(s.id))shapesById.set(s.id,[]);shapesById.get(s.id).push(s)}
$('source').appendChild(fragment);
for(const s of DATA.model_symbols){const points=s.boundary?.length?s.boundary:[s.position];if(points.length>1)$('proposals').appendChild(node('path',{d:path(points,true),class:'proposed'}));else if(validPoint(s.position))$('proposals').appendChild(node('circle',{cx:s.position[0],cy:-s.position[1],r:.15,class:'proposed'}))}
$('proposals').style.display='none';
function filter(){for(const nodes of elements.values())for(const e of nodes)e.style.display=(!$('show-text').checked&&e.dataset.isText==='1')||(!$('show-fills').checked&&e.dataset.isFill==='1')?'none':'';$('proposals').style.display=$('show-proposals').checked?'':'none'}
['show-text','show-fills','show-proposals'].forEach(k=>$(k).onchange=filter);
function nextId(prefix,list){let i=1;while(list.some(s=>s.id===prefix+i))i++;return prefix+i}
function drawMarks(){$('marks').replaceChildren();$('annotation-list').replaceChildren();for(const s of symbols){const p=s.boundary?.length?s.boundary:[s.position];$('marks').appendChild(p.length>1?node('path',{d:path(p,true)+(s.holes||[]).map(h=>path(h,true)).join(' '),class:'mark '+s.kind,'fill-rule':'evenodd'}):node('circle',{cx:p[0][0],cy:-p[0][1],r:.2,class:'mark'}));const div=document.createElement('div');div.className='item';const label=document.createElement('span');label.textContent=s.id+' · '+s.kind+' · '+s.status;div.appendChild(label);const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>{symbols=symbols.filter(x=>x.id!==s.id);drawMarks()};div.appendChild(remove);$('annotation-list').appendChild(div)}
 $('scopes').replaceChildren();$('scope-list').replaceChildren();for(const s of scopes){const [a,b,c,d]=s.bounds_ft;$('scopes').appendChild(node('rect',{x:a,y:-d,width:c-a,height:d-b,class:'scope'}));const item=document.createElement('div');item.textContent=s.id+' · '+s.kinds.join(', ')+' · '+(s.complete?'complete':'partial');const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>{scopes=scopes.filter(x=>x.id!==s.id);drawMarks()};item.appendChild(remove);$('scope-list').appendChild(item)}}
function clearSketch(){sketch=[];drawing=false;$('sketch').setAttribute('d','')}
$('trace').onclick=()=>{sketch=[];drawing=true;message('Click outline corners, then Finish outline. Scroll remains available.')};
$('finish').onclick=()=>{if(sketch.length<3)return message('An outline needs at least three corners.');if(!validBoundary([...sketch,sketch[0]]))return message('The outline crosses itself or has no area. Cancel and redraw it.');drawing=false;$('sketch').setAttribute('d',path(sketch,true));message('Outline ready. Choose its class and save the instance.')};
$('cancel').onclick=()=>{clearSketch();message('Outline cancelled.')};
$('clear').onclick=()=>{selected.clear();refreshSelection()};$('fit').onclick=()=>changeView(DATA.view_bounds_ft);
$('find').onclick=()=>{const id=$('find-id').value.trim();if(!shapesById.has(id))return message('That source ID is not present in this workbench.');selected.add(id);const pts=shapesById.get(id).flatMap(s=>s.points);const b=ringBounds(pts),pad=Math.max(b[2]-b[0],b[3]-b[1],1)*.6;changeView([b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad]);refreshSelection();message('Selected '+id)};
$('canvas').addEventListener('wheel',event=>{event.preventDefault();const p=sourcePoint(event),factor=event.deltaY>0?1.15:1/1.15;const b=[p[0]+(view[0]-p[0])*factor,p[1]+(view[1]-p[1])*factor,p[0]+(view[2]-p[0])*factor,p[1]+(view[3]-p[1])*factor];if(b[2]-b[0]>1e-4&&b[2]-b[0]<1e7)changeView(b)},{passive:false});
$('canvas').addEventListener('pointerdown',event=>{if(event.button!==0)return;const p=sourcePoint(event);if(drawing){sketch.push(p);$('sketch').setAttribute('d',path(sketch));return}const id=event.target.dataset.sourceId;if(id){toggle(id);return}pan={point:p,view:[...view]};$('canvas').setPointerCapture(event.pointerId)});
$('canvas').addEventListener('pointermove',event=>{const p=sourcePoint(event);$('position').textContent='X '+p[0].toFixed(3)+' ft · Y '+p[1].toFixed(3)+' ft';if(pan){const dx=p[0]-pan.point[0],dy=p[1]-pan.point[1];changeView([view[0]-dx,view[1]-dy,view[2]-dx,view[3]-dy])}});
['pointerup','pointercancel'].forEach(name=>$('canvas').addEventListener(name,()=>pan=null));
$('add').onclick=()=>{try{if(drawing)throw Error('Finish the outline before saving.');if(isArea($('kind').value)&&sketch.length<3)throw Error('Draw and finish an outline for a footprint or void; a selection box is not an area boundary.');const ids=[...selected].sort();const sourceShapes=ids.flatMap(id=>shapesById.get(id)||[]);if(sourceShapes.some(s=>s.partial))throw Error('Selected geometry crosses the review boundary. Choose a wider workbench or draw a separate outline.');let boundary=sketch.length>=3?[...sketch]:null;const points=boundary||sourceShapes.flatMap(s=>s.points);if(!points.length)throw Error('Select source geometry or draw an outline first.');const b=ringBounds(points);if(!boundary&&b[2]>b[0]&&b[3]>b[1])boundary=[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]];if(boundary){boundary=[...boundary,boundary[0]];if(!validBoundary(boundary))throw Error('The outline must be closed, non-crossing and have positive area.')}const r=$('reviewed').checked?requireReviewer():reviewer();const s={id:nextId('instance-',symbols),kind:$('kind').value,position:[(b[0]+b[2])/2,(b[1]+b[3])/2],source_ids:ids,status:$('reviewed').checked?'reviewed':'draft',reviewer:r};if(boundary)s.boundary=boundary;if($('label').value.trim())s.label=$('label').value.trim();symbols.push(s);clearSketch();selected.clear();refreshSelection();drawMarks();$('reviewed').checked=false;message('Instance saved in this browser. Export annotations to keep it.')}catch(e){message(e.message)}};
$('view').onclick=()=>{try{changeView(readBounds())}catch(e){message(e.message)}};$('current').onclick=()=>fields(view);
$('scope').onclick=()=>{try{if(isArea($('kind').value))throw Error('Complete review areas apply to symbol classes. Footprint and void acceptance is recorded separately.');const b=readBounds(),complete=$('complete').checked;const r=complete?requireReviewer():reviewer();scopes.push({id:nextId('area-',scopes),bounds_ft:b,kinds:[$('kind').value],complete,status:complete?'reviewed':'draft',reviewer:r});$('complete').checked=false;drawMarks();message('Review area saved.')}catch(e){message(e.message)}};
function baseExport(){if(!DATA.registration_available)throw Error('Original region bounds or origin are missing. Regenerate this workbench from a report retaining source registration.');return {schema_version:1,source_sha256:DATA.source_sha256,region_id:DATA.region_id,bounds:DATA.bounds,coordinate_system:'model-feet',source_origin:DATA.source_origin,registration_verified:DATA.registration_verified,registration_basis:DATA.registration_basis,source_units_per_foot:DATA.source_units_per_foot,scale_verified:DATA.scale_verified,annotation_status:'partial',footprint_verified:false,reviewer:reviewer()}}
function download(name,value){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function areaExport(){
 const areas=symbols.filter(s=>isArea(s.kind));
 const result={area_annotations:areas,footprint_verified:false};
 const footprints=areas.filter(s=>s.kind==='footprint').map(s=>({id:s.id,boundary:s.boundary,holes:s.holes||[],source_ids:s.source_ids,status:s.status,reviewer:s.reviewer}));
 const voids=areas.filter(s=>s.kind==='void').map(s=>s.boundary);
 if(footprints.length)result.footprints=footprints;
 if(voids.length)result.voids=voids;
 return result;
}
$('export').onclick=()=>{try{
 download(DATA.region_id+'.annotations.json',{...baseExport(),symbols:symbols.filter(s=>!isArea(s.kind)),scopes,...areaExport()});
 message('Annotation JSON exported. Area acceptance remains unverified.');
}catch(e){message(e.message)}};
$('templates').onclick=()=>{try{
 const templates=symbols.filter(s=>!isArea(s.kind)&&s.status==='reviewed'&&s.source_ids.length).map(s=>({id:s.id,kind:s.kind,source_ids:s.source_ids,tolerance_in:.1,rotations_deg:[0,90,180,270],reviewer:s.reviewer}));
 const areas=areaExport();
 if(!templates.length&&!areas.area_annotations.length)throw Error('Save an area outline or a reviewed source instance before exporting a model review.');
 if(templates.some(t=>t.source_ids.length>64))throw Error('A template may contain at most 64 source entities. Narrow the selected instance.');
 download(DATA.region_id+'.model-review.json',{...baseExport(),symbols_verified:false,templates,source_annotations:symbols.filter(s=>!isArea(s.kind)),scopes,...areas});
 message('Model review exported. Footprint acceptance and symbol verification remain false.');
}catch(e){message(e.message)}};
function importedArea(record,kind,index,parentReviewer){
 const boundary=kind==='void'&&Array.isArray(record)?record:record.boundary;
 if(!validBoundary(boundary))throw Error('An imported area needs a valid closed outline.');
 const b=ringBounds(boundary);
 const item={id:record.id||kind+'-import-'+index,kind,boundary,position:[(b[0]+b[2])/2,(b[1]+b[3])/2],source_ids:record.source_ids||[],status:record.status||'draft',reviewer:record.reviewer||parentReviewer||{id:'unspecified',role:'unknown'}};
 if(kind==='footprint')item.holes=record.holes||[];
 return item;
}
$('import').onchange=async event=>{try{
 const file=event.target.files[0];if(!file)return;
 const input=JSON.parse(await file.text());
 if(input.source_sha256!==DATA.source_sha256||input.region_id!==DATA.region_id||input.coordinate_system!=='model-feet'||JSON.stringify(input.bounds)!==JSON.stringify(DATA.bounds)||(input.source_origin!==undefined&&JSON.stringify(input.source_origin)!==JSON.stringify(DATA.source_origin))||(input.source_units_per_foot!==undefined&&input.source_units_per_foot!==DATA.source_units_per_foot))throw Error('This annotation file belongs to a different drawing, region, or coordinate system.');
 const ordinary=input.symbols??input.source_annotations??[], importedScopes=input.scopes??[];
 if(!Array.isArray(ordinary)||!Array.isArray(importedScopes))throw Error('Expected symbols and scopes lists.');
 let areas=input.area_annotations;
 if(areas===undefined){
  if((input.footprints!==undefined&&!Array.isArray(input.footprints))||(input.voids!==undefined&&!Array.isArray(input.voids)))throw Error('Expected footprint and void lists.');
  areas=[...(input.footprints||[]).map((s,i)=>importedArea(s,'footprint',i,input.reviewer)),...(input.voids||[]).map((s,i)=>importedArea(s,'void',i,input.reviewer))];
 }
 if(!Array.isArray(areas)||areas.some(s=>!isArea(s.kind)))throw Error('Expected footprint or void area annotations.');
 const incoming=[...ordinary,...areas].map(record=>{
  const s={...record,source_ids:record.source_ids||[],status:record.status||input.status||'draft',reviewer:record.reviewer||input.reviewer||{id:'unspecified',role:'unknown'}};
  if(!s.position&&validBoundary(s.boundary)){const b=ringBounds(s.boundary);s.position=[(b[0]+b[2])/2,(b[1]+b[3])/2]}
  return s;
 }), kinds=new Set(Array.from($('kind').options).map(o=>o.value)), ids=new Set();
 for(const s of incoming){
  if(typeof s.id!=='string'||!s.id||ids.has(s.id)||!kinds.has(s.kind)||!validPoint(s.position)||!['draft','reviewed'].includes(s.status)||!Array.isArray(s.source_ids)||!s.source_ids.every(id=>typeof id==='string')||(s.boundary&&!validBoundary(s.boundary))||(isArea(s.kind)&&!validBoundary(s.boundary))||(s.holes&&(!Array.isArray(s.holes)||!s.holes.every(validBoundary))))throw Error('An imported annotation has invalid fields.');
  ids.add(s.id);
 }
 for(const s of importedScopes){
  if(!validBounds(s.bounds_ft)||!Array.isArray(s.kinds)||!s.kinds.every(k=>kinds.has(k)&&!isArea(k))||typeof s.complete!=='boolean'||!['draft','reviewed'].includes(s.status))throw Error('An imported review area has invalid fields.');
 }
 symbols=incoming;scopes=importedScopes;drawMarks();message('Annotations loaded; reviewer attribution preserved. Area acceptance stays unverified.');
}catch(e){message(e.message)}finally{event.target.value=''}};
window.addEventListener('beforeunload',event=>{if(symbols.length||scopes.length){event.preventDefault();event.returnValue=''}});
$('registration').textContent=DATA.region_id+' · '+DATA.shapes.length+' source paths · '+DATA.source_units_per_foot+' source units/ft'+(DATA.scale_verified?' · scale checked':' · scale unverified');if(!DATA.registration_available){$('registration').classList.add('warning');$('registration').textContent+=' · original source registration unavailable; export disabled'}else if(!DATA.registration_verified){$('registration').classList.add('warning');$('registration').textContent+=' · source registration supplied by caller; unverified'}
changeView(view);fields(view);drawMarks();
</script></html>'''
