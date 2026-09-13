"""Reviewable model evidence and vector overlays, without rendering dependencies."""
from __future__ import annotations
from dataclasses import asdict
import html
import json
from pathlib import Path


def write_review(models, sources, output_path):
    if len(models) != len(sources) or not models:
        raise ValueError("each model needs its corresponding source evidence")
    path=Path(output_path)
    report_path=path.with_suffix(".report.json")
    overlay_path=path.with_suffix(".overlay.svg")
    for dest in (report_path,overlay_path):
        if dest.exists():raise FileExistsError(f"refusing to overwrite {dest}")
    path.parent.mkdir(parents=True,exist_ok=True)
    entries=[]
    for model,source in zip(models,sources):
        entries.append({"status":"draft" if any(i.severity=="error" for i in model.issues) else "checks-passed",
                        "model":asdict(model),"source_entities":[asdict(e) for e in source.entities],
                        "source_dimensions":[asdict(d) for d in source.dimensions],
                        "ingest_warnings":[asdict(w) for w in source.warnings]})
    report_path.write_text(json.dumps({"schema_version":1,"tolerance_mm":50.8,
                                      "ifc_validation":"not executed by this report writer",
                                      "regions":entries},indent=2,allow_nan=False))
    points=[(x/m.scale.units_per_foot,y/m.scale.units_per_foot) for m,s in zip(models,sources)
            for p in s.primitives for x,y in p.coords]
    points.extend(point for model in models for profile in model.wall_profiles for point in profile.boundary)
    if not points:points=[(0,0),(1,1)]
    x0,x1=min(p[0] for p in points),max(p[0] for p in points)
    y0,y1=min(p[1] for p in points),max(p[1] for p in points)
    span=max(x1-x0,y1-y0,1);pad=span*.03;stroke=span/1800
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0-pad} {-y1-pad} {x1-x0+2*pad} {y1-y0+2*pad}">',
         '<title>Source grey; reconstructed walls red; hosted openings blue; symbol instances green</title>',
         f'<rect x="{x0-pad}" y="{-y1-pad}" width="{x1-x0+2*pad}" height="{y1-y0+2*pad}" fill="white"/>']
    def line(a,b,color,width,label=""):
        svg.append(f'<path d="M {a[0]} {-a[1]} L {b[0]} {-b[1]}" stroke="{color}" stroke-width="{width}" fill="none"><title>{html.escape(label)}</title></path>')
    for model,source in zip(models,sources):
        upf=model.scale.units_per_foot
        svg.append(f'<g id="region-{html.escape(model.region_id, quote=True)}"><title>{html.escape(model.storey_name)}</title>')
        for p in source.primitives:
            for a,b in p.segments():line(tuple(x/upf for x in a),tuple(x/upf for x in b),"#c4c8ce",stroke,p.source_id)
        for w in model.walls:line(w.start,w.end,"#cf3340",max(w.thickness_ft,stroke),",".join(w.source_ids))
        for profile in model.wall_profiles:
            paths=[]
            for ring in (profile.boundary,*profile.holes):
                if not ring:continue
                paths.append("M "+" L ".join(f"{x} {-y}" for x,y in ring)+" Z")
            label=f"{profile.id}: {profile.detector}; {profile.review_status}; sources={','.join(profile.source_ids)}"
            svg.append(f'<path d="{" ".join(paths)}" fill="#cf3340" fill-opacity="0.35" fill-rule="evenodd" stroke="#a42030" stroke-width="{stroke}"><title>{html.escape(label)}</title></path>')
        for o in model.openings:
            valid_host = type(o.host_wall_index) is int and 0 <= o.host_wall_index < len(model.walls)
            width = max(model.walls[o.host_wall_index].thickness_ft,stroke*2) if valid_host else stroke*2
            line(o.start,o.end,"#087bce" if valid_host else "#777777",width,o.id)
        for s in model.symbols:
            svg.append(f'<circle cx="{s.position[0]}" cy="{-s.position[1]}" r="{max(stroke*3,.1)}" fill="#087d47"><title>{html.escape(s.id+" "+s.kind+" "+s.evidence)}</title></circle>')
        svg.append('</g>')
    svg.append('</svg>');overlay_path.write_text("\n".join(svg))
    return report_path,overlay_path


def record_export_validation(report_path, result):
    """Complete the report just created for this run after reopening its IFC."""
    path = Path(report_path)
    data = json.loads(path.read_text())
    data["ifc_validation"] = result
    if not result["passed"]:
        for region in data["regions"]:
            region["status"] = "draft"
    path.write_text(json.dumps(data, indent=2, allow_nan=False))


def record_reference_evaluation(report_path, result):
    """Attach annotation scores without promoting any model acceptance flag."""
    path = Path(report_path)
    data = json.loads(path.read_text())
    data["reference_evaluation"] = result
    path.write_text(json.dumps(data, indent=2, allow_nan=False))
