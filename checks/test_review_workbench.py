"""Offline annotation exports must preserve source identity and registration."""
import json
import re
from types import SimpleNamespace

import pytest

from archiagent.evidence import SourceEntity
from archiagent.review_workbench import write_workbench, write_workbench_from_report


def model(**overrides):
    return {"source_sha256": "a"*64, "region_id": "floor-1", "storey_name": "First floor",
            "scale": {"units_per_foot": 12}, "scale_verified": False, "symbols": [],
            "source_origin": [100, 200], "source_region_bounds": [100, 200, 220, 320], **overrides}


def payload(path):
    text = path.read_text()
    match = re.search(r'<script id="workbench-data" type="application/json">(.*?)</script>', text, re.S)
    assert match
    return json.loads(match.group(1))


def source(*entities):
    return SimpleNamespace(entities=entities)


def test_workbench_preserves_source_ids_model_feet_and_original_bounds(tmp_path):
    line = SourceEntity("line-1", "LINE", "0", ((12, 24), (36, 48)))
    result = write_workbench(model(), source(line), tmp_path/"review.html")
    data = payload(result)
    assert data["source_sha256"] == "a"*64
    assert data["region_id"] == "floor-1"
    assert data["coordinate_system"] == "model-feet"
    assert data["source_origin"] == [100, 200]
    assert data["bounds"] == [100, 200, 220, 320]
    assert data["registration_available"] and data["registration_verified"]
    assert not data["scale_verified"]
    assert data["shapes"][0]["id"] == "line-1"
    assert data["shapes"][0]["points"] == [[1, 2], [3, 4]]


def test_source_viewport_crop_does_not_change_acceptance_bounds(tmp_path):
    near = SourceEntity("near", "LINE", "0", ((0, 0), (24, 24)))
    far = SourceEntity("far", "LINE", "0", ((60, 60), (120, 120)))
    crossing = SourceEntity("crossing", "LINE", "0", ((-12, 12), (60, 12)))
    result = write_workbench(model(), source(near, far, crossing), tmp_path/"crop.html",
                             bounds_source=(0, 0, 36, 36))
    data = payload(result)
    assert {s["id"] for s in data["shapes"]} == {"near", "crossing"}
    assert data["view_bounds_ft"] == [0, 0, 3, 3]
    assert data["bounds"] == [100, 200, 220, 320]


def test_hatch_boundary_selection_uses_actual_primitive_parent_id(tmp_path):
    ring = ((0, 0), (12, 0), (12, 12), (0, 12))
    hatch = SourceEntity("hatch", "HATCH", "0", ring, closed=True)
    boundary = SourceEntity("hatch/boundary/0", "HATCH_BOUNDARY", "0", ring,
                            closed=True, parent_id="hatch")
    data = payload(write_workbench(model(), source(hatch, boundary), tmp_path/"hatch.html"))
    assert len(data["shapes"]) == 1
    assert data["shapes"][0]["id"] == "hatch"
    assert data["shapes"][0]["entity_id"] == "hatch/boundary/0"


def test_embedded_source_text_cannot_break_out_of_data_script(tmp_path):
    attack = '</script><script>alert("source")</script>&'
    text = SourceEntity("text-1", "TEXT", "0", center=(12, 12), metadata=(("text", attack),))
    result = write_workbench(model(storey_name=attack), source(text), tmp_path/"safe.html")
    content = result.read_text()
    assert attack not in content
    assert payload(result)["shapes"][0]["text"] == attack
    assert "<script src=" not in content and "fetch(" not in content


def test_legacy_report_missing_registration_does_not_guess_origin(tmp_path):
    legacy = model()
    del legacy["source_origin"]
    del legacy["source_region_bounds"]
    line = SourceEntity("line", "LINE", "0", ((0, 0), (12, 12)))
    result = write_workbench(legacy, source(line), tmp_path/"legacy.html")
    data = payload(result)
    assert not data["registration_available"]
    assert not data["registration_verified"]
    assert data["source_origin"] is None
    assert data["bounds"] is None


def test_legacy_registration_override_is_explicit_and_unverified(tmp_path):
    legacy = model(source_origin=None, source_region_bounds=())
    line = SourceEntity("line", "LINE", "0", ((0, 0), (12, 12)))
    result = write_workbench(legacy, source(line), tmp_path/"legacy.html",
                             origin_source=(100, 200), region_bounds_source=(100, 200, 220, 320))
    data = payload(result)
    assert data["registration_available"]
    assert not data["registration_verified"]
    assert data["registration_basis"] == "caller-supplied"


def test_workbench_requires_exact_report_region_and_refuses_overwrite(tmp_path):
    report = tmp_path/"source.report.json"
    entity = {"id": "line", "kind": "LINE", "layer": "0", "coords": [[0, 0], [12, 12]]}
    report.write_text(json.dumps({"regions": [
        {"model": model(), "source_entities": [entity]},
        {"model": model(region_id="floor-2"), "source_entities": [entity]}]}))
    with pytest.raises(ValueError, match="exactly one"):
        write_workbench_from_report(report, tmp_path/"review.html")
    result = write_workbench_from_report(report, tmp_path/"review.html", region_id="floor-2")
    assert payload(result)["region_id"] == "floor-2"
    with pytest.raises(FileExistsError):
        write_workbench_from_report(report, result, region_id="floor-2")


@pytest.mark.parametrize("bounds", [(0, 0, 0, 1), (0, 0, float("nan"), 1), (1, 2)])
def test_invalid_review_viewport_rejected(tmp_path, bounds):
    line = SourceEntity("line", "LINE", "0", ((0, 0), (12, 12)))
    with pytest.raises(ValueError, match="bounds"):
        write_workbench(model(), source(line), tmp_path/"bad.html", bounds_source=bounds)


def test_export_controls_default_to_partial_and_explicit_scope_review(tmp_path):
    line = SourceEntity("line", "LINE", "0", ((0, 0), (12, 12)))
    text = write_workbench(model(), source(line), tmp_path/"review.html").read_text()
    assert "annotation_status:'partial'" in text
    assert "symbols_verified:false" in text
    assert "footprint_verified:false" in text
    assert '<input id="reviewed" type="checkbox">' in text
    assert '<input id="complete" type="checkbox">' in text
    assert "complete?requireReviewer():reviewer()" in text
    assert "source_ids:s.source_ids" in text


def test_area_annotations_are_separate_from_benchmark_symbols_and_templates(tmp_path):
    line = SourceEntity("line", "LINE", "0", ((0, 0), (12, 12)))
    text = write_workbench(model(), source(line), tmp_path/"review.html").read_text()
    assert '<option value="footprint">Floor footprint</option>' in text
    assert '<option value="void">Void / courtyard</option>' in text
    assert "area_annotations:areas,footprint_verified:false" in text
    assert "symbols:symbols.filter(s=>!isArea(s.kind))" in text
    assert "symbols.filter(s=>!isArea(s.kind)&&s.status==='reviewed'" in text
    assert "if(footprints.length)result.footprints=footprints" in text
    assert "if(voids.length)result.voids=voids" in text
    assert "Draw and finish an outline for a footprint or void" in text
    assert "source_annotations:symbols.filter(s=>!isArea(s.kind))" in text


def test_workbench_draft_footprint_is_authored_without_becoming_accepted():
    pytest.importorskip("shapely")
    pytest.importorskip("ezdxf")
    pytest.importorskip("pymupdf")
    pytest.importorskip("ifcopenshell")
    from archiagent.classify.layers import StubClassifier
    from archiagent.classify.roles import Role
    from archiagent.pipeline import extract_from_dxf
    from archiagent.primitives import Primitive, PrimitiveSet
    from archiagent.semantic import PlanRegion
    wall = Primitive("rect", ((0, 0), (10, 0), (10, .5), (0, .5)), "WALL", None, None,
                     "wall", "LWPOLYLINE", True)
    ps = PrimitiveSet((wall,), (), 10, 10, "fixture.dxf", "a"*64)
    boundary = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    void = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    # The model-review export includes geometry and provenance but explicitly
    # withholds footprint acceptance, even after a user has saved an outline.
    exported = {"source_sha256": ps.source_sha256, "region_id": "floor-1",
                "coordinate_system": "model-feet", "annotation_status": "partial",
                "footprint_verified": False, "symbols_verified": False, "templates": [],
                "footprints": [{"id": "area-1", "boundary": boundary, "holes": [], "status": "draft"}],
                "voids": [void], "area_annotations": [{"id": "area-1", "kind": "footprint",
                    "boundary": boundary, "status": "draft", "reviewer": {"id": "unspecified", "role": "unknown"}}]}
    classifier = StubClassifier({"WALL": (Role.WALL_PARTITION, 1)})
    result = extract_from_dxf(ps, classifier, units_per_foot=1, review=exported,
                             region=PlanRegion("floor-1", (0, 0, 10, 10)))
    assert len(result.footprints) == 1
    assert result.footprints[0].area_sqft == pytest.approx(96)
    assert len(result.footprints[0].holes) == 1
    assert not result.footprint_verified
    assert "footprint_unverified" in {issue.code for issue in result.issues}
