"""Tiled OCR coordinate/provenance checks; native Vision calls are mocked."""
import hashlib

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from archiagent.ingest import ocr


@pytest.fixture
def tiled_source(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "__file__", str(tmp_path / "repo/archiagent/ingest/ocr.py"))
    image = tmp_path / "source.png"
    assert cv2.imwrite(str(image), np.full((80,96,3), 255, dtype=np.uint8))
    return image, tmp_path / ".test-tmp/swift-cache", tmp_path / "tiles"


def fake_native(conflicting=False):
    def recognize(path, cache):
        image = cv2.imread(str(path))
        height, width = image.shape[:2]
        index = int(path.stem.split("-")[1])
        x0, y0 = (index % 2) * 32, (index // 2) * 16
        # This full-image box lies in all four 64x64 crops.
        left, top, right, bottom = 36, 24, 52, 40
        text = "LINTEL" if conflicting and index == 3 else "SILL"
        return {"engine":"macos-vision", "coordinate_system":"normalized-bottom-left",
                "pixel_width":width, "pixel_height":height,
                "image_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                "records":[{"text":text, "confidence":.7 + .05*index,
                            "bbox":[(left-x0)/width, (height-bottom+y0)/height,
                                    (right-left)/width, (bottom-top)/height]}]}
    return recognize


def test_tile_boxes_map_to_full_image_and_identical_readings_deduplicate(monkeypatch, tiled_source):
    monkeypatch.setattr(ocr, "run_macos_ocr", fake_native())
    result = ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=16)
    assert len(result["tiles"]) == 4
    assert result["tiles"][3]["pixel_bbox"] == [32,16,96,80]
    assert result["raw_record_count"] == 4
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["bbox"] == pytest.approx([36/96,40/80,16/96,16/80])
    assert record["confidence"] == pytest.approx(.85)
    assert len(record["tile_ids"]) == len(record["tile_provenance"]) == 4
    mapped = ocr.map_ocr_results(result, {"pixel_width":96,"pixel_height":80,
                                "pixel_to_source":[[1,0,0],[0,-1,80]]})[0]
    assert mapped.text_item.bbox == pytest.approx((36,40,52,56))
    assert mapped.tile_ids == tuple(record["tile_ids"])
    assert len(mapped.tile_provenance) == 4
    assert result["ambiguities"] == []


def test_different_readings_remain_separate_with_ambiguity_links(monkeypatch, tiled_source):
    monkeypatch.setattr(ocr, "run_macos_ocr", fake_native(conflicting=True))
    result = ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=16)
    assert {r["text"] for r in result["records"]} == {"SILL", "LINTEL"}
    assert len(result["ambiguities"]) == 1
    assert all(len(r["ambiguous_with"]) == 1 for r in result["records"])
    mapped = ocr.map_ocr_results(result, {"pixel_width":96,"pixel_height":80,
                                "pixel_to_source":[[1,0,0],[0,-1,80]]})
    assert all(r.ambiguous_with[0] in {v.text_item.source_id for v in mapped} for r in mapped)


def test_tile_work_is_bounded_before_native_calls(monkeypatch, tiled_source):
    called = []
    monkeypatch.setattr(ocr, "run_macos_ocr", lambda *a: called.append(a))
    with pytest.raises(ValueError, match="32 tiles"):
        ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=63)
    assert not called
    assert not tiled_source[2].exists()


def test_existing_tile_artifacts_are_not_overwritten(monkeypatch, tiled_source):
    monkeypatch.setattr(ocr, "run_macos_ocr", fake_native())
    ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=16)
    tile = tiled_source[2] / "tile-000.png"
    before = tile.read_bytes()
    with pytest.raises(FileExistsError, match="fresh tile_dir"):
        ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=16)
    assert tile.read_bytes() == before


def test_identical_text_at_different_locations_is_not_deduplicated():
    records = [dict(text="SILL",confidence=.8,bbox=[x,.1,.1,.1],local_bbox=[0,0,.1,.1],tile_id=f"t{i}")
               for i,x in enumerate((.1,.5))]
    result, ambiguous = ocr._deduplicate_tiles(records)
    assert len(result) == 2
    assert ambiguous == []


def test_actual_tile004_edge_label_is_discarded_without_inventing_a_bbox():
    # Actual Vision observation11 from a1600x1600 crop: text begins outside
    # the tile by0.1877px, so it may be the truncated tail of LINTEL.
    bad = {"bbox": [-0.00011732819549777007, 0.3659013289636194,
                    0.036571865081787114, 0.01238338828086849],
           "confidence": 1, "text": "L=8'-0\""}
    good = {"bbox": [.2,.2,.1,.02], "confidence": 1, "text": "SILL=5'-6\""}
    payload = {"records": [good.copy() for _ in range(11)] + [bad]}
    accepted, rejected = ocr._validate_tile_observations(payload, "tile-004", 1600, 1600)
    assert len(accepted) == 11
    assert len(rejected) == 1
    diagnostic = rejected[0]
    assert diagnostic["record_index"] == 11
    assert diagnostic["tile_id"] == "tile-004"
    assert diagnostic["observation"] == bad
    assert diagnostic["bbox_overrun_pixels"]["left"] == pytest.approx(.1877251127964321)
    assert diagnostic["disposition"] == "discarded without clipping or text correction"
    assert payload["records"][11]["bbox"][0] < 0  # Original evidence is untouched.
    with pytest.raises(ValueError, match="outside its normalized range"):
        ocr.map_ocr_results([bad], {"pixel_width":1600,"pixel_height":1600,
                                  "pixel_to_source":[[1,0,0],[0,-1,1600]]})


def test_tiler_continues_after_invalid_observation_and_persists_native_record(monkeypatch, tiled_source):
    import json
    native = fake_native()
    bad = {"bbox": [-.00011732819549777007,.3,.03,.02], "confidence":1,"text":"L=8'-0\""}
    def recognize(path, cache):
        payload = native(path,cache)
        if path.stem == "tile-000":
            payload["records"].append(bad)
        return payload
    monkeypatch.setattr(ocr, "run_macos_ocr", recognize)
    result = ocr.run_tiled_macos_ocr(*tiled_source, tile_size=64, overlap=16)
    assert result["raw_record_count"] == 5
    assert result["accepted_observation_count"] == 4
    assert result["discarded_observation_count"] == 1
    assert len(result["records"]) == 1
    assert result["discarded_observations"][0]["observation"] == bad
    persisted = json.loads((tiled_source[2] / "tile-000.ocr.json").read_text())
    assert persisted["records"][-1] == bad
