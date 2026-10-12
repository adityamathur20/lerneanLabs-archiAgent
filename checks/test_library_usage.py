"""Which library symbols a run used, how often, at what orientation and size."""
import json
import subprocess
import sys
from pathlib import Path

import ezdxf

from archiagent.classify.library_usage import library_usage
from archiagent.model import Issue
from archiagent.semantic import SymbolInstance

ROOT = Path(__file__).resolve().parents[1]


def library_symbol(template_id, angle, scale, mirrored=False, spaces=None):
    props = [("library_template_id", template_id), ("library_scale_ft", f"{scale:.6f}"),
             ("library_mirrored", str(mirrored)), ("library_angle_deg", str(angle))]
    if spaces:
        props += [("space_types", spaces), ("space_evidence", "strong")]
    return SymbolInstance(f"library-{template_id}-{angle}-{scale}", "plumbing", (0, 0), 1, 1, 0.0, "wc", ("e",),
                          "symbol-library", .8, (), properties=tuple(props))


LIBRARY = [{"id": "plumbing-aaa", "kind": "plumbing", "subtype": "wc", "label": "WC", "size_ft": {"min": 1, "max": 4},
            "rotations_deg": [0, 90], "mirror_allowed": True, "space_types": ["bathroom"]},
           {"id": "door-bbb", "kind": "door", "subtype": "swing", "label": "Door", "size_ft": {"min": 2, "max": 4},
            "rotations_deg": [0], "mirror_allowed": False}]


def test_counts_orientations_sizes_and_mirroring_per_template():
    symbols = [library_symbol("plumbing-aaa", 0.0, 2.0), library_symbol("plumbing-aaa", 90.0, 2.5, mirrored=True),
               library_symbol("plumbing-aaa", 90.0, 3.0), library_symbol("plumbing-zzz", 0.0, 1.0)]
    report = library_usage(symbols, (), LIBRARY, source_sha256="abc", units_per_foot=12.0)
    used = {t["id"]: t for t in report["templates"]}
    wc = used["plumbing-aaa"]
    assert wc["instances"] == 3
    assert wc["angles_deg"] == {"0": 1, "90": 2}
    assert wc["mirrored"] == 1
    assert wc["size_ft"] == {"min": 2.0, "median": 2.5, "max": 3.0}
    assert wc["kind"] == "plumbing" and wc["space_types"] == ["bathroom"]
    assert report["symbols_total"] == 3                      # the one not in this library is not counted
    assert report["templates_matched"] == 1 and report["templates_in_library"] == 2


def test_a_template_that_matched_nothing_is_listed_with_zero():
    report = library_usage([library_symbol("plumbing-aaa", 0.0, 2.0)], (), LIBRARY, source_sha256="abc", units_per_foot=12.0)
    never = {t["id"]: t["instances"] for t in report["templates"]}
    assert never["door-bbb"] == 0


def test_reduced_and_skipped_templates_are_named():
    issues = (Issue("info", "plumbing-aaa", "library_template_reduced", "reduced"),
              Issue("warn", "door-bbb", "library_template_skipped", "skipped"))
    report = library_usage([], issues, LIBRARY, source_sha256="abc", units_per_foot=12.0)
    assert report["reduced"] == ["plumbing-aaa"] and report["skipped"] == ["door-bbb"]


def test_the_library_is_identified_by_a_digest_so_a_result_can_be_traced():
    a = library_usage([], (), LIBRARY, source_sha256="x", units_per_foot=12.0)["library"]
    b = library_usage([], (), LIBRARY[:1], source_sha256="x", units_per_foot=12.0)["library"]
    assert a["templates"] == 2 and b["templates"] == 1
    assert a["digest"] != b["digest"] and len(a["digest"]) == 64


def test_a_run_writes_the_usage_file_beside_its_other_outputs(tmp_path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALLS")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 30), (0, 30)], close=True, dxfattribs={"layer": "FIX"})
    for a, b in (((0, 100), (480, 100)), ((480, 100), (480, 340)), ((480, 340), (0, 340)), ((0, 340), (0, 100))):
        msp.add_line(a, b, dxfattribs={"layer": "WALLS"})
    plan = tmp_path / "plan.dxf"
    doc.saveas(plan)
    from archiagent.classify.library_templates import normalise
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"schema_version": 1, "templates": [{
        "id": "furniture-rect", "kind": "furniture", "subtype": "box", "label": "Box", "status": "reviewed",
        "geometry": normalise([{"category": "closed", "points": [[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]], "holes": []}]),
        "size_ft": {"min": 0.5, "max": 10.0}, "rotations_deg": [0, 90], "mirror_allowed": False}]}))
    out = tmp_path / "out"
    done = subprocess.run([sys.executable, "-m", "archiagent", "--dxfFilePath", str(plan), "--outputDir", str(out),
                           "--rules", "--walls", "WALLS", "--symbol-library", str(library),
                           "--scale-from-wall", "0", "100", "480", "100", "40ft"],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    usage = next(out.glob("*.symbol-usage.json"), None)
    assert usage is not None, done.stderr[-1500:] + done.stdout[-500:]
    data = json.loads(usage.read_text())
    assert data["schema_version"] == 1 and data["library"]["templates"] == 1
    assert data["symbols_total"] >= 1, data
