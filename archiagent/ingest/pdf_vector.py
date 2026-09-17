"""Vector PDF ingestion with source paths, curves, fills and text evidence.

PDF coordinates are flipped once into upward Y source coordinates. Flattened
vectors are accepted with a warning; image-only pages require raster ingestion.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pymupdf

from archiagent.evidence import IngestWarning, SourceEntity
from archiagent.primitives import Primitive, PrimitiveSet, TextItem


class NoLayersError(Exception):
    """Compatibility exception for callers of the former layer-only reader."""


class RasterOnlyPdfError(NoLayersError):
    """The selected page has no usable vector geometry."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _flatten_cubic(points, tolerance=0.1):
    """Adaptive de Casteljau subdivision in PDF points, retaining endpoints."""
    def midpoint(a, b):
        return ((a[0]+b[0])/2, (a[1]+b[1])/2)
    def distance(p, a, b):
        length = math.dist(a, b)
        if length == 0:
            return math.dist(p, a)
        # Distance to the segment (not infinite line) also catches collinear
        # control points that backtrack beyond the endpoints.
        t = max(0., min(1., ((p[0]-a[0])*(b[0]-a[0])+(p[1]-a[1])*(b[1]-a[1]))/length**2))
        return math.dist(p, (a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])))
    def divide(p, depth):
        a, b, c, d = p
        if depth >= 18 or max(distance(b, a, d), distance(c, a, d)) <= tolerance:
            return [a, d]
        ab, bc, cd = midpoint(a, b), midpoint(b, c), midpoint(c, d)
        abc, bcd = midpoint(ab, bc), midpoint(bc, cd)
        middle = midpoint(abc, bcd)
        return divide((a, ab, abc, middle), depth+1)[:-1] + divide((middle, bcd, cd, d), depth+1)
    return tuple(divide(points, 0))


def load_pdf(path: str | Path, page: int = 0) -> PrimitiveSet:
    path = Path(path)
    doc = pymupdf.open(path)
    try:
        pg = doc[page]
        height = pg.rect.height
        def xy(p):
            return float(p.x), float(height-p.y)
        warnings = []
        if not doc.get_ocgs():
            warnings.append(IngestWarning("flattened_pdf", f"page/{page}",
                "No CAD layers: classification must use geometry, text and symbol evidence."))
        primitives, entities, texts = [], [], []
        for n, drawing in enumerate(pg.get_drawings()):
            sid = f"page/{page}/path/{n}"
            layer = drawing.get("layer") or ""
            width, color = drawing.get("width"), drawing.get("color")
            fill = drawing.get("fill")
            subpaths = []
            current = []
            curved = False
            for j, item in enumerate(drawing["items"]):
                typ = item[0]
                itemid = f"{sid}/{j}"
                closed = False
                if typ == "l":
                    coords = (xy(item[1]), xy(item[2]))
                elif typ == "c":
                    control = tuple(xy(p) for p in item[1:5])
                    entities.append(SourceEntity(itemid, "PDF_CUBIC", layer, control,
                        parent_id=sid, metadata=(("tessellation_tolerance", "0.1"),)))
                    coords = _flatten_cubic(control)
                    curved = True
                elif typ == "re":
                    r = item[1]
                    coords = ((r.x0, height-r.y0), (r.x1, height-r.y0),
                              (r.x1, height-r.y1), (r.x0, height-r.y1))
                    if len(item) > 2 and item[2] < 0:
                        coords = tuple(reversed(coords))
                    closed = True
                elif typ == "qu":
                    q = item[1]
                    coords = tuple(xy(p) for p in (q.ul, q.ur, q.lr, q.ll))
                    closed = True
                else:
                    warnings.append(IngestWarning("unsupported_pdf_path", itemid,
                                                  f"Unsupported PDF path operator {typ}."))
                    continue
                if closed:
                    if current:
                        subpaths.append((tuple(current), False))
                        current = []
                    subpaths.append((coords, True))
                elif current and current[-1] == coords[0]:
                    current.extend(coords[1:])
                else:
                    if current:
                        subpaths.append((tuple(current), False))
                    current = list(coords)
            if current:
                subpaths.append((tuple(current), bool(drawing.get("closePath"))))
            def inside(point, ring):
                x, y = point
                hit = False
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                        hit = not hit
                return hit
            def winding(ring):
                return 1 if sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(ring, ring[1:]+ring[:1])) >= 0 else -1
            rings = [coords for coords, _ in subpaths]
            parents = [[k for k, outer in enumerate(rings) if k != j and inside(ring[0], outer)]
                       for j, ring in enumerate(rings)] if fill is not None else [[] for _ in rings]
            is_hole = []
            for j, ring in enumerate(rings):
                if drawing.get("even_odd", False):
                    is_hole.append(len(parents[j]) % 2 == 1)
                else:
                    outside = sum(winding(rings[k]) for k in parents[j])
                    is_hole.append(outside != 0 and outside + winding(ring) == 0)
            # Fill closes each subpath implicitly under PDF painting rules.
            for j, (coords, closed) in enumerate(subpaths):
                closed = closed or fill is not None or coords[0] == coords[-1]
                pathid = f"{sid}/subpath/{j}"
                entities.append(SourceEntity(pathid, "PDF_FILL" if fill is not None else "PDF_PATH",
                    layer, coords, closed=closed, parent_id=sid,
                    holes=tuple(rings[k] for k in range(len(rings)) if is_hole[k] and
                                j in parents[k] and len(parents[k]) == len(parents[j]) + 1),
                    metadata=(("fill", repr(fill)), ("even_odd", str(drawing.get("even_odd", False))),
                              ("stroke", repr(color)), ("is_hole", str(is_hole[j])))))
                primitives.append(Primitive("fill" if fill is not None else "curve" if curved else "line",
                    coords, layer, width, color, pathid, "PDF_PATH", closed))
            entities.append(SourceEntity(sid, "PDF_DRAWING", layer,
                metadata=(("fill", repr(fill)), ("even_odd", str(drawing.get("even_odd", False))),
                          ("subpaths", str(len(subpaths))))))
        for n, (x0, y0, x1, y1, word, *_) in enumerate(pg.get_text("words")):
            sid = f"page/{page}/text/{n}"
            bbox = (x0, height-y1, x1, height-y0)
            texts.append(TextItem(word, bbox, "", sid))
            entities.append(SourceEntity(sid, "PDF_TEXT", "",
                ((bbox[0], bbox[1]), (bbox[2], bbox[3])),
                metadata=(("text", word),)))
        if not primitives:
            raise RasterOnlyPdfError(f"{path.name}, page {page + 1}: no vector paths; raster recognition and scale calibration are required.")
        return PrimitiveSet(tuple(primitives), tuple(texts), pg.rect.width, height,
                            str(path), _sha256(path), tuple(entities), (), tuple(warnings))
    finally:
        doc.close()
