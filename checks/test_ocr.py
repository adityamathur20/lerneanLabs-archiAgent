"""Coordinate-registration and local-process OCR tests; no Vision execution."""
import json
from types import SimpleNamespace

import pytest

from archiagent.ingest import ocr


RECORDS = [{"text": "SILL 3'-0\"", "confidence": .83, "bbox": [.1, .2, .3, .1]}]


def test_normalized_bottom_left_boxes_map_through_explicit_affine():
    metadata = {"pixel_width": 100, "pixel_height": 200,
                "pixel_to_source": [[2, 0, 100], [0, -3, 200]]}
    item = ocr.map_ocr_results(RECORDS, metadata, origin=(10, 20))[0]
    assert item.pixel_bbox == pytest.approx((10, 140, 40, 160))
    assert item.text_item.bbox == pytest.approx((130, -260, 190, -200))
    assert item.text_item.layer == "__OCR_UNVERIFIED__"
    assert item.confidence == .83
    assert item.text_item.text == RECORDS[0]["text"]


def test_svg_model_feet_metadata_maps_to_original_source_origin():
    metadata = {"pixel_width": 100, "pixel_height": 200,
                "svg_viewbox_model_feet": [5, -20, 10, 40], "source_units_per_foot": 12}
    item = ocr.map_ocr_results(RECORDS, metadata, origin=(1000, 2000))[0]
    assert item.text_item.bbox == pytest.approx((1072, 1856, 1108, 1904))


def test_affine_rotation_maps_all_corners_not_only_diagonal():
    metadata = {"pixel_width": 100, "pixel_height": 200,
                "pixel_to_source": [[0, 2, 100], [3, 0, 200]]}
    assert ocr.map_ocr_results(RECORDS, metadata)[0].text_item.bbox == pytest.approx((380, 230, 420, 320))


def test_mismatched_dimensions_and_invalid_registration_fail():
    metadata = {"pixel_width": 100, "pixel_height": 200,
                "pixel_to_source": [[1, 0, 0], [0, -1, 0]]}
    result = {"records": RECORDS, "coordinate_system": "normalized-bottom-left",
              "pixel_width": 101, "pixel_height": 200}
    with pytest.raises(ValueError, match="dimensions"):
        ocr.map_ocr_results(result, metadata)
    with pytest.raises(ValueError, match="invertible"):
        ocr.map_ocr_results(RECORDS, {**metadata, "pixel_to_source": [[1,0,0],[2,0,0]]})
    with pytest.raises(ValueError, match="range"):
        ocr.map_ocr_results([{**RECORDS[0], "bbox": [.9,.2,.5,.1]}], metadata)


def test_subprocess_failure_is_clean_and_caches_remain_local(monkeypatch, tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"not-executed-by-mocked-process")
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setattr(ocr, "__file__", str(tmp_path / "repo/archiagent/ingest/ocr.py"))
    called = {}
    def failed(command, **kwargs):
        called.update(command=command, **kwargs)
        return SimpleNamespace(returncode=1, stderr="Vision unavailable", stdout="")
    monkeypatch.setattr(ocr.subprocess, "run", failed)
    cache = tmp_path / ".test-tmp/swift-cache"
    with pytest.raises(ocr.OCRUnavailable, match="Vision unavailable"):
        ocr.run_macos_ocr(image, cache)
    assert called["command"][:3] == ["/usr/bin/swift", "-module-cache-path", str(cache)]
    assert called["env"]["CLANG_MODULE_CACHE_PATH"] == str(cache)
    assert called["env"]["TMPDIR"] == str(cache.parent)


def test_success_preserves_confidence_dimensions_and_image_digest(monkeypatch, tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"mock")
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setattr(ocr, "__file__", str(tmp_path / "repo/archiagent/ingest/ocr.py"))
    payload = {"records": RECORDS, "engine": "macos-vision", "coordinate_system": "normalized-bottom-left",
               "pixel_width": 100, "pixel_height": 200}
    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(payload)))
    result = ocr.run_macos_ocr(image, tmp_path / ".test-tmp/swift-cache")
    assert result["records"] == RECORDS
    assert len(result["image_sha256"]) == 64
    assert result["pixel_width"] == 100


def test_exact_model_match_does_not_verify_ocr_dimension_or_establish_scale():
    from dataclasses import replace
    from archiagent.geometry.walls import WallSeg
    from archiagent.scale.verify import Measurement, infer_associated_scale, verify_dimensions
    first = Measurement("width", (0,0), (120,0), 10, "centerline", "ocr-associated")
    second = Measurement("depth", (0,0), (0,240), 20, "centerline", "ocr-associated")
    wall = WallSeg((0,0), (10,0), .5, "walls", "paired-line", "measured")
    check = verify_dimensions((first,), (wall,), 12)[0]
    assert check.actual_ft == 10
    assert check.error_in == 0
    assert check.status == "unverified"
    assert "OCR text needs review" in check.message
    assert infer_associated_scale((first,second)) is None
    reviewed_first = replace(first, source="reviewed-measurement")
    assert infer_associated_scale((reviewed_first,second)) is None
    assert infer_associated_scale((reviewed_first,replace(second,source="reviewed-measurement"))) == 12
