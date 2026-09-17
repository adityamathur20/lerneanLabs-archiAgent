"""Optional local OCR with explicit pixel registration; no cloud calls/downloads.

run_macos_ocr(image_path, cache_dir) invokes the companion Swift/Vision script.
Pass a workspace .test-tmp/swift-cache directory; compiler caches and temporary
files are routed there. It returns normalized-bottom-left records and dimensions.

map_ocr_results(records, metadata, origin) returns OCRText records containing a
TextItem, confidence and top-left pixel bbox. metadata.pixel_to_source is a 2x3
matrix mapping TOP-LEFT pixel (x,y) to source (x,y). Its translation already
includes registration; the origin argument is added in source units afterward.
Existing svg_viewbox_model_feet metadata is also supported: y is inverted and
source_units_per_foot applied explicitly before adding origin. Use origin=(0,0)
when attaching OCR to already registered source geometry; pass the original
registration origin only when mapping back to the unregistered drawing.

OCR text is uncertain evidence, not independently verified dimensions. A SILL
or LINTEL label can help distinguish casement-window candidates from door arcs,
but its proximity alone does not establish opening type or vertical dimensions.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from archiagent.primitives import TextItem


class OCRUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRText:
    text_item: TextItem
    confidence: float
    pixel_bbox: tuple[float, float, float, float]
    engine: str = "macos-vision"
    tile_ids: tuple[str, ...] = ()
    ambiguous_with: tuple[str, ...] = ()
    tile_provenance: tuple[dict, ...] = ()


def _finite(values, label):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError(f"{label} must contain finite numbers")


def run_macos_ocr(image_path, cache_dir):
    """Run the installed macOS OCR engine only when explicitly requested."""
    if sys.platform != "darwin":
        raise OCRUnavailable("macOS Vision OCR requires macOS; no alternate engine is downloaded")
    image_path, cache_dir = Path(image_path).resolve(), Path(cache_dir).resolve()
    script = Path(__file__).with_name("vision_ocr.swift")
    project = Path(__file__).resolve().parents[2]
    # The project and its parent workspace hold the supplied source files.
    workspace = project.parent
    if not image_path.is_relative_to(workspace) or not cache_dir.is_relative_to(workspace):
        raise ValueError("OCR image and compiler cache must stay inside the project workspace")
    if cache_dir.name != "swift-cache" or cache_dir.parent.name != ".test-tmp":
        raise ValueError("OCR compiler cache must be a workspace .test-tmp/swift-cache directory")
    if not image_path.is_file():
        raise OCRUnavailable(f"OCR image does not exist: {image_path}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(SWIFT_MODULECACHE_PATH=str(cache_dir), CLANG_MODULE_CACHE_PATH=str(cache_dir),
                       TMPDIR=str(cache_dir.parent))
    command = ["/usr/bin/swift", "-module-cache-path", str(cache_dir), str(script), str(image_path)]
    try:
        completed = subprocess.run(command, cwd=project, env=environment, capture_output=True,
                                   text=True, check=False, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OCRUnavailable(f"cannot run local macOS Vision OCR: {exc}") from exc
    if completed.returncode:
        raise OCRUnavailable(f"macOS Vision OCR failed: {completed.stderr.strip()[-2000:]}")
    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or not isinstance(result.get("records"), list):
            raise ValueError("missing OCR records")
        for key in ("pixel_width", "pixel_height"):
            if type(result.get(key)) is not int or result[key] <= 0:
                raise ValueError(f"invalid {key}")
        if result.get("coordinate_system") != "normalized-bottom-left":
            raise ValueError("unexpected OCR coordinate system")
    except (TypeError, ValueError) as exc:
        raise OCRUnavailable(f"invalid output from local OCR: {exc}") from exc
    result["image_path"] = str(image_path)
    result["image_sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    return result


def _pixel_affine(metadata):
    width, height = metadata.get("pixel_width"), metadata.get("pixel_height")
    _finite((width, height), "render dimensions")
    if width <= 0 or height <= 0:
        raise ValueError("render dimensions must be positive")
    affine = metadata.get("pixel_to_source")
    if affine is not None:
        if not isinstance(affine, (list, tuple)) or len(affine) != 2 or any(
                not isinstance(row, (list, tuple)) or len(row) != 3 for row in affine):
            raise ValueError("pixel_to_source must be a 2x3 affine matrix")
        _finite((v for row in affine for v in row), "pixel_to_source")
    else:
        view = metadata.get("svg_viewbox_model_feet")
        if not isinstance(view, (list, tuple)) or len(view) != 4:
            raise ValueError("render metadata needs pixel_to_source or svg_viewbox_model_feet")
        units = metadata.get("source_units_per_foot")
        _finite((*view, units), "SVG registration")
        x, y, w, h = view
        if w <= 0 or h <= 0 or units <= 0:
            raise ValueError("SVG extent and source scale must be positive")
        affine = ((w / width * units, 0., x * units),
                  (0., -h / height * units, -y * units))
    a, b, _ = affine[0]
    c, d, _ = affine[1]
    if abs(a * d - b * c) < 1e-15:
        raise ValueError("pixel_to_source must be invertible")
    return width, height, affine


def map_ocr_results(records, render_metadata, origin=(0., 0.)):
    """Map normalized-bottom-left OCR boxes to source-coordinate TextItems."""
    if len(origin) != 2:
        raise ValueError("OCR origin must contain two source coordinates")
    _finite(origin, "OCR origin")
    if isinstance(records, dict):
        payload = records
        if payload.get("coordinate_system") != "normalized-bottom-left":
            raise ValueError("OCR records must use normalized-bottom-left coordinates")
        if any(payload.get(key) != render_metadata.get(key) for key in ("pixel_width", "pixel_height")):
            raise ValueError("OCR image dimensions do not match render registration")
        records = payload.get("records")
    if not isinstance(records, (list, tuple)):
        raise ValueError("OCR records must be a sequence")
    width, height, affine = _pixel_affine(render_metadata)
    def transform(px, py):
        return (affine[0][0] * px + affine[0][1] * py + affine[0][2] + origin[0],
                affine[1][0] * px + affine[1][1] * py + affine[1][2] + origin[1])
    result = []
    for i, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("text"), str):
            raise ValueError("OCR records need text and a normalized bbox")
        bounds = record.get("bbox")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("OCR bbox must contain normalized x,y,width,height")
        confidence = record.get("confidence")
        _finite((*bounds, confidence), "OCR bbox/confidence")
        x, y, w, h = bounds
        if w <= 0 or h <= 0 or x < -1e-6 or y < -1e-6 or x + w > 1 + 1e-6 or y + h > 1 + 1e-6 or not 0 <= confidence <= 1:
            raise ValueError(f"OCR bbox/confidence is outside its normalized range: record {i}, "
                             f"bbox={list(bounds)}, confidence={confidence}")
        left, top, right, bottom = x * width, (1-y-h) * height, (x+w) * width, (1-y) * height
        corners = [transform(px, py) for px, py in ((left, top), (right, top), (right, bottom), (left, bottom))]
        source_box = (min(p[0] for p in corners), min(p[1] for p in corners),
                      max(p[0] for p in corners), max(p[1] for p in corners))
        text = record["text"].strip()
        if not text:
            continue
        digest = hashlib.sha256(json.dumps([text, list(bounds)], ensure_ascii=False).encode()).hexdigest()[:12]
        record_id = record.get("id", f"{i}:{digest}")
        item = TextItem(text, source_box, "__OCR_UNVERIFIED__", f"ocr:{record_id}")
        result.append(OCRText(item, float(confidence), (left, top, right, bottom),
                              tile_ids=tuple(record.get("tile_ids", ())),
                              ambiguous_with=tuple("ocr:" + rid for rid in record.get("ambiguous_with", ())),
                              tile_provenance=tuple(record.get("tile_provenance", ()))))
    return tuple(result)


def render_source_paths(source, output_path, *, max_pixels=6000, padding_pixels=20):
    """Rasterize existing registered vector paths, preserving an explicit affine.

    No invented fonts are used for native TextItems; this renderer targets the
    outlined annotation strokes present in primitives. Native text stays in its
    existing ingestion channel. The returned metadata accompanies the PNG.
    """
    import pymupdf as fitz
    if type(max_pixels) is not int or max_pixels < 100 or type(padding_pixels) is not int or not 0 <= padding_pixels < max_pixels / 4:
        raise ValueError("invalid source render size or padding")
    points = [p for primitive in source.primitives for p in primitive.coords]
    if not points:
        raise ValueError("no source paths to render for OCR")
    _finite((v for p in points for v in p), "source geometry")
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    if x0 == x1 or y0 == y1:
        raise ValueError("OCR source paths require two-dimensional extent")
    scale = (max_pixels - 2 * padding_pixels) / max(x1-x0, y1-y0)
    width = math.ceil((x1-x0) * scale + 2 * padding_pixels)
    height = math.ceil((y1-y0) * scale + 2 * padding_pixels)
    def pixel(p):
        return ((p[0]-x0) * scale + padding_pixels, (y1-p[1]) * scale + padding_pixels)
    doc = fitz.open()
    try:
        page = doc.new_page(width=width, height=height)
        shape = page.new_shape()
        for primitive in source.primitives:
            if len(primitive.coords) < 2:
                continue
            shape.draw_polyline([pixel(p) for p in primitive.coords])
            shape.finish(color=(0,0,0), fill=(0,0,0) if primitive.kind == "fill" else None,
                         width=.6, closePath=primitive.closed or primitive.kind == "rect")
        shape.commit()
        pixmap = page.get_pixmap(alpha=False)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(output_path))
        metadata = {"pixel_width": pixmap.width, "pixel_height": pixmap.height,
                    "pixel_to_source": [[1/scale, 0., x0-padding_pixels/scale],
                                        [0., -1/scale, y1+padding_pixels/scale]],
                    "source_sha256": source.source_sha256,
                    "coordinate_system": "pixel-top-left-to-registered-source",
                    "image_path": str(output_path)}
    finally:
        doc.close()
    return metadata


def _normalized_iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0., min(ax+aw, bx+bw)-max(ax,bx)) * max(0., min(ay+ah, by+bh)-max(ay,by))
    union = aw*ah + bw*bh - intersection
    return intersection / union if union > 0 else 0.


def _deduplicate_tiles(records, threshold=.7):
    """Only merge equal readings whose every observation has high box IoU."""
    groups = []
    for record in sorted(records, key=lambda r: (-r["confidence"], r["text"], r["bbox"], r["tile_id"])):
        matching = next((group for group in groups if group[0]["text"] == record["text"] and
                         all(_normalized_iou(member["bbox"], record["bbox"]) >= threshold for member in group)), None)
        if matching is None:
            groups.append([record])
        else:
            matching.append(record)
    result = []
    for group in groups:
        representative = group[0]
        result.append({"text": representative["text"], "confidence": representative["confidence"],
                       "bbox": representative["bbox"], "tile_ids": sorted({r["tile_id"] for r in group}),
                       "tile_provenance": [{"tile_id": r["tile_id"], "confidence": r["confidence"],
                                            "local_bbox": r["local_bbox"], "bbox": r["bbox"]}
                                           for r in sorted(group, key=lambda r: r["tile_id"])],
                       "ambiguous_with": []})
    result.sort(key=lambda r: (-(r["bbox"][1]+r["bbox"][3]), r["bbox"][0], r["text"]))
    for i, record in enumerate(result):
        record["id"] = f"tiled-{i:05d}"
    ambiguities = []
    for i, a in enumerate(result):
        for b in result[i+1:]:
            if a["text"] == b["text"]:
                continue
            overlap = _normalized_iou(a["bbox"], b["bbox"])
            if overlap >= threshold:
                a["ambiguous_with"].append(b["id"])
                b["ambiguous_with"].append(a["id"])
                ambiguities.append({"record_ids": [a["id"], b["id"]], "iou": overlap,
                                    "readings": [a["text"], b["text"]]})
    return result, ambiguities



def _validate_tile_observations(payload, tile_id, width, height):
    """Reject invalid observations explicitly; never clamp edge-crossing text.

    Vision may infer a box extending beyond a crop (the observed failure was
    -0.1877 pixels at its left edge). That is not floating-point roundoff: the
    text may be cut by the tile boundary. Preserve the native record and reject
    it here, allowing an overlapping tile to supply a complete observation.
    Direct map_ocr_results calls continue to reject out-of-range coordinates.
    """
    accepted, discarded = [], []
    metadata = {"pixel_width": width, "pixel_height": height,
                "pixel_to_source": [[1.,0.,0.],[0.,-1.,float(height)]]}
    for index, record in enumerate(payload["records"]):
        try:
            map_ocr_results([record], metadata)
        except ValueError as exc:
            diagnostic = {"code": "ocr_observation_rejected", "tile_id": tile_id,
                          "record_index": index, "reason": str(exc),
                          "disposition": "discarded without clipping or text correction",
                          "observation": record}
            bounds = record.get("bbox") if isinstance(record, dict) else None
            if (isinstance(bounds, (list, tuple)) and len(bounds) == 4 and
                    all(type(v) in (int, float) and math.isfinite(v) for v in bounds)):
                x,y,w,h = bounds
                diagnostic["bbox_overrun_pixels"] = {
                    "left": max(0., -x * width), "right": max(0., (x+w-1) * width),
                    "top": max(0., (y+h-1) * height), "bottom": max(0., -y * height)}
            discarded.append(diagnostic)
        else:
            accepted.append(record)
    return accepted, discarded


def run_tiled_macos_ocr(image_path, cache_dir, tile_dir, tile_size=1600, overlap=160):
    """OCR image crops at native resolution, retaining unverified tile evidence.

    Requires installed cv2 and local macOS Vision; nothing is downloaded.
    Source images are limited to 40 megapixels and work to at most 32 tiles.
    tile_dir must be a fresh output location inside this project workspace.
    Same-text observations merge only above 0.7 IoU; different readings remain
    separate and overlapping alternatives receive explicit ambiguity links.
    """
    try:
        import cv2
    except ImportError as exc:
        raise OCRUnavailable("tiled OCR requires locally installed OpenCV; no package is downloaded") from exc
    if type(tile_size) is not int or not 64 <= tile_size <= 4096:
        raise ValueError("OCR tile_size must be an integer between 64 and 4096")
    if type(overlap) is not int or not 0 <= overlap < tile_size:
        raise ValueError("OCR overlap must be nonnegative and smaller than tile_size")
    image_path, cache_dir, tile_dir = (Path(path).resolve() for path in (image_path, cache_dir, tile_dir))
    workspace = Path(__file__).resolve().parents[2].parent
    if any(not path.is_relative_to(workspace) for path in (image_path, cache_dir, tile_dir)):
        raise ValueError("OCR source, tiles and compiler cache must stay inside the project workspace")
    if cache_dir.name != "swift-cache" or cache_dir.parent.name != ".test-tmp":
        raise ValueError("OCR compiler cache must be a workspace .test-tmp/swift-cache directory")
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise OCRUnavailable(f"cannot read image for tiled OCR: {image_path}")
    height, width = image.shape[:2]
    if width * height > 40_000_000:
        raise ValueError("OCR source exceeds the 40 megapixel limit")
    step = tile_size - overlap
    def starts(length):
        last = max(0, length-tile_size)
        values = list(range(0, last+1, step))
        if values[-1] != last:
            values.append(last)
        return values
    xs, ys = starts(width), starts(height)
    if len(xs) * len(ys) > 32:
        raise ValueError("OCR would exceed 32 tiles; increase tile_size, reduce overlap or crop the source region")
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    tile_paths = [tile_dir / f"tile-{i:03d}.png" for i in range(len(xs) * len(ys))]
    if any(path.exists() for path in tile_paths):
        raise FileExistsError("OCR tile images already exist; use a fresh tile_dir")
    tile_dir.mkdir(parents=True, exist_ok=True)
    all_records, tiles, discarded = [], [], []
    raw_count = 0
    for row, y0 in enumerate(ys):
        for column, x0 in enumerate(xs):
            index = row * len(xs) + column
            tile_id = f"tile-{index:03d}"
            tile_path = tile_paths[index]
            crop = image[y0:min(y0+tile_size,height), x0:min(x0+tile_size,width)]
            th, tw = crop.shape[:2]
            if not cv2.imwrite(str(tile_path), crop):
                raise OCRUnavailable(f"cannot write OCR tile {tile_path}")
            payload = run_macos_ocr(tile_path, cache_dir)
            # Persist native evidence before checking registration, so a failed
            # edge record can be diagnosed and replayed without another OCR run.
            payload_path = tile_path.with_suffix(".ocr.json")
            payload_path.write_text(json.dumps(payload, indent=2, allow_nan=False))
            if (payload.get("coordinate_system") != "normalized-bottom-left" or
                    payload.get("pixel_width") != tw or payload.get("pixel_height") != th):
                raise OCRUnavailable("tile OCR dimensions or coordinate system do not match the saved crop")
            accepted, rejected = _validate_tile_observations(payload, tile_id, tw, th)
            raw_count += len(payload["records"])
            discarded.extend(rejected)
            tile = {"id": tile_id, "image_path": str(tile_path), "pixel_bbox": [x0,y0,x0+tw,y0+th],
                    "pixel_width": tw, "pixel_height": th, "record_count": len(payload["records"]),
                    "accepted_observation_count": len(accepted), "discarded_observation_count": len(rejected),
                    "image_sha256": payload.get("image_sha256"), "ocr_payload_path": str(payload_path)}
            tiles.append(tile)
            for record in accepted:
                text = record["text"].strip()
                if not text:
                    continue
                x, y, w, h = record["bbox"]
                full_box = [(x0+x*tw)/width, (height-y0-th+y*th)/height, w*tw/width, h*th/height]
                all_records.append({"text": text, "confidence": float(record["confidence"]), "bbox": full_box,
                                    "tile_id": tile_id, "local_bbox": list(record["bbox"])})
    records, ambiguities = _deduplicate_tiles(all_records)
    return {"engine": "macos-vision", "mode": "tiled", "coordinate_system": "normalized-bottom-left",
            "pixel_width": width, "pixel_height": height, "image_path": str(image_path),
            "image_sha256": image_hash, "tile_size": tile_size, "overlap": overlap,
            "dedup_iou": .7, "raw_record_count": raw_count, "records": records,
            "accepted_observation_count": len(all_records), "discarded_observation_count": len(discarded),
            "discarded_observations": discarded,
            "tiles": tiles, "ambiguities": ambiguities, "text_verification": "unverified"}
