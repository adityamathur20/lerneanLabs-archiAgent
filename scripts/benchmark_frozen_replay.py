"""Compare vector extraction with frozen-model loading on a generated drawing.

This measures interpretation/loading only, not IFC writing or Blender runtime.
No provider, OCR, network or client drawing is involved. Outputs stay in the
explicit directory; an existing directory is refused to preserve prior runs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import ezdxf

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.ingest.dxf_vector import load_dxf
from archiagent.interpretation import implementation_versions, read_manifest, save_manifest
from archiagent.pipeline import extract_from_dxf


def run(output_dir: Path, annotations: int = 2000, repeats: int = 3):
    if annotations < 0 or annotations > 20000 or not 1 <= repeats <= 5:
        raise ValueError("annotations must be 0..20000 and repeats 1..5")
    output_dir.mkdir(parents=True, exist_ok=False)
    doc = ezdxf.new()
    doc.units = 2
    doc.layers.new("A-WALL")
    doc.layers.new("A-NOTE")
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "A-WALL"})
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 8), (0, 8)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(.5, .5), (9.5, .5), (9.5, 7.5), (.5, 7.5)], is_closed=True, flags=0)
    for i in range(annotations):
        doc.modelspace().add_text(f"annotation {i}", dxfattribs={
            "layer": "A-NOTE", "insert": (i % 100, 20 + i // 100), "height": .1})
    source = output_dir / "generated.dxf"
    doc.saveas(source)
    classifier = StubClassifier({"A-WALL": (Role.WALL_PARTITION, 1.)})
    def extract():
        primitives, upf = load_dxf(source)
        return extract_from_dxf(primitives, classifier, units_per_foot=upf)
    model = extract()  # Warm imports/font caches before comparing repeated runs.
    frozen = save_manifest([model], output_dir / "generated.interpretation.json")
    read_manifest(frozen, source)
    extraction_seconds, replay_seconds = [], []
    for _ in range(repeats):
        start = perf_counter()
        fresh = extract()
        extraction_seconds.append(perf_counter() - start)
        start = perf_counter()
        restored, _ = read_manifest(frozen, source)
        replay_seconds.append(perf_counter() - start)
        assert asdict(fresh) == asdict(restored[0]) == asdict(model)
    result = {
        "fixture": "generated ring wall and unrelated native text annotations",
        "annotations": annotations, "repeats": repeats, "warmup": True,
        "extraction_seconds": extraction_seconds, "replay_seconds": replay_seconds,
        "median_extraction_seconds": median(extraction_seconds),
        "median_replay_seconds": median(replay_seconds),
        "interpretation_phase_ratio": median(extraction_seconds) / median(replay_seconds),
        "canonical_models_equal": True, "versions": implementation_versions(),
        "limits": "Single synthetic fixture; excludes IFC/Blender, provider and OCR. Not an accuracy score or whole-pipeline speedup.",
    }
    (output_dir / "timings.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.annotations, args.repeats), indent=2))
