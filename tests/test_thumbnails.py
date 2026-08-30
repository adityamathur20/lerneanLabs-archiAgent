import ezdxf
import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")

from pathlib import Path
from PIL import Image
import numpy as np

from archiagent.classify.thumbnails import render_for_escalation, render_layer


def _doc(tmp_path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    # Add layers to layer table before adding entities
    doc.layers.add("WALL", color=7)
    doc.layers.add("TINY", color=7)
    msp = doc.modelspace()
    for i in range(20):
        msp.add_line((0, i * 5), (200, i * 5), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 0), (3, 3), dxfattribs={"layer": "TINY"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def _count_dark_pixels(png_path: Path) -> int:
    """Count pixels with low brightness (dark pixels) in a PNG."""
    img = Image.open(png_path)
    arr = np.array(img)
    # Dark pixels have low sum of RGB values (< 100 is clearly dark)
    return int(np.sum((arr[:, :, 0] + arr[:, :, 1] + arr[:, :, 2]) < 100))


def test_renders_a_single_layer_to_png(tmp_path):
    p = _doc(tmp_path)
    out = render_layer(p, "WALL", tmp_path / "w.png")
    assert out.exists() and out.stat().st_size > 1000
    # Verify it's not blank: should have dark pixels from rendered lines
    assert _count_dark_pixels(out) > 0, "Render should contain visible content (dark pixels)"


def test_reference_render_uses_every_layer(tmp_path):
    p = _doc(tmp_path)
    ref = render_layer(p, None, tmp_path / "ref.png")
    only = render_layer(p, "TINY", tmp_path / "tiny.png")
    # Reference (all layers) should have significantly more dark pixels than single sparse layer
    ref_dark = _count_dark_pixels(ref)
    tiny_dark = _count_dark_pixels(only)
    assert ref_dark > tiny_dark, (
        f"Reference render ({ref_dark} dark pixels) should have more content "
        f"than single sparse layer ({tiny_dark} dark pixels)"
    )


def test_render_for_escalation_returns_reference_plus_map(tmp_path):
    p = _doc(tmp_path)
    ref, per_layer = render_for_escalation(p, ["WALL", "TINY"], tmp_path / "cache")
    assert ref.exists()
    assert set(per_layer) == {"WALL", "TINY"}
    assert all(v.exists() for v in per_layer.values())


def test_a_layer_that_does_not_exist_is_omitted_not_fatal(tmp_path):
    p = _doc(tmp_path)
    ref, per_layer = render_for_escalation(p, ["WALL", "NOPE"], tmp_path / "cache")
    assert "WALL" in per_layer
    assert ref.exists()


def test_output_stays_inside_the_cache_dir(tmp_path):
    """Thumbnails are pictures of confidential drawings; they must land only
    where .gitignore covers them."""
    p = _doc(tmp_path)
    cache = tmp_path / "cache"
    ref, per_layer = render_for_escalation(p, ["WALL"], cache)
    assert cache in ref.parents
    assert all(cache in v.parents for v in per_layer.values())
