"""Attach explicitly requested local OCR with coordinates and uncertainty."""
from dataclasses import replace
import json
from pathlib import Path

from archiagent.evidence import SourceEntity, IngestWarning
from archiagent.ingest.ocr import render_source_paths, map_ocr_results, run_tiled_macos_ocr
from archiagent.scale.verify import associate_dimension_text


def enrich_source_with_ocr(source, units_per_foot, output_prefix, cache_dir):
    prefix = str(output_prefix)
    image_path = Path(prefix + ".png")
    metadata_path = Path(prefix + ".render.json")
    evidence_path = Path(prefix + ".json")
    tile_dir = Path(prefix + "-tiles")
    if any(p.exists() for p in (image_path, metadata_path, evidence_path, tile_dir)):
        raise FileExistsError("refusing to overwrite OCR evidence; choose a new output directory")
    metadata = render_source_paths(source, image_path)
    metadata_path.write_text(json.dumps(metadata, indent=2))
    raw = run_tiled_macos_ocr(image_path, cache_dir, tile_dir)
    evidence_path.write_text(json.dumps(raw, indent=2, allow_nan=False))
    mapped = map_ocr_results(raw, metadata)
    entities = []
    for result in mapped:
        item = result.text_item
        x0, y0, x1, y1 = item.bbox
        entities.append(SourceEntity(item.source_id, "OCR_TEXT", item.layer,
            coords=((x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)), closed=True,
            metadata=(("text",item.text),("confidence",str(result.confidence)),
                      ("engine",result.engine),("verification","unreviewed"),
                      ("render_metadata",str(metadata_path)),("ocr_evidence",str(evidence_path)))))
    enriched = replace(source, texts=source.texts + tuple(r.text_item for r in mapped),
                       entities=source.entities + tuple(entities))
    dimensions = associate_dimension_text(enriched, units_per_foot)
    associated = sum(d.text_evidence == "ocr-associated" for d in dimensions)
    warning = IngestWarning("ocr_unverified", "OCR",
                            f"{len(mapped)} local OCR labels; {associated} native dimension associations. "
                            "OCR characters and contextual symbol interpretations require review.")
    warnings = (warning,)
    discarded = raw.get("discarded_observation_count", 0)
    if discarded:
        warnings += (IngestWarning("ocr_observations_discarded", "OCR",
            f"{discarded} invalid tile observations discarded; raw text and coordinates retained in {evidence_path}."),)
    return replace(enriched, dimensions=dimensions, warnings=enriched.warnings + warnings)
