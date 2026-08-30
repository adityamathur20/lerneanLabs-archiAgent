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
    assert "NOPE" not in per_layer, "Layer with no entities should be omitted from map"
    assert ref.exists()


def test_output_stays_inside_the_cache_dir(tmp_path):
    """Thumbnails are pictures of confidential drawings; they must land only
    where .gitignore covers them."""
    p = _doc(tmp_path)
    cache = tmp_path / "cache"
    ref, per_layer = render_for_escalation(p, ["WALL"], cache)
    assert cache in ref.parents
    assert all(cache in v.parents for v in per_layer.values())


def test_render_unavailable_message_matches_the_current_flag():
    """RenderUnavailable's message is user-facing: dxf_classifier.py turns
    it into a layer_escalation_skipped Issue, and the CLI's -v prints that
    to a real terminal. It once told the user to "run without --vision" --
    a flag that was renamed to --no_vision (and had its default flipped to
    vision-on). Asserting the CURRENT flag is named, and the OLD one is
    not, is what would have caught that drift."""
    from archiagent.classify.thumbnails import RENDER_UNAVAILABLE_MSG
    assert "--no_vision" in RENDER_UNAVAILABLE_MSG
    assert "--vision" not in RENDER_UNAVAILABLE_MSG
    assert "matplotlib" in RENDER_UNAVAILABLE_MSG
    assert "Pillow" in RENDER_UNAVAILABLE_MSG


def test_render_unavailable_propagates(tmp_path, monkeypatch):
    """RenderUnavailable from missing matplotlib/Pillow must propagate, not be swallowed."""
    from archiagent.classify.thumbnails import RenderUnavailable
    p = _doc(tmp_path)

    # Monkeypatch _backend to raise RenderUnavailable
    import archiagent.classify.thumbnails as thumbs_module
    original_backend = thumbs_module._backend

    def mock_backend():
        raise RenderUnavailable("test: matplotlib not available")

    monkeypatch.setattr(thumbs_module, "_backend", mock_backend)

    # render_for_escalation should propagate RenderUnavailable, not catch it
    with pytest.raises(RenderUnavailable):
        render_for_escalation(p, ["WALL"], tmp_path / "cache")


def _doc_with_confusable_layers(tmp_path):
    """Two layer names that sanitise to the identical stem under the old
    `_safe()`: a space versus an underscore, an ordinary real-world naming
    inconsistency."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    doc.layers.add("NEW WALLS", color=7)
    doc.layers.add("NEW_WALLS", color=1)
    msp = doc.modelspace()
    for i in range(10):
        msp.add_line((0, i * 5), (200, i * 5), dxfattribs={"layer": "NEW WALLS"})
    for i in range(10):
        msp.add_line((0, i * 5 + 2), (80, i * 5 + 2), dxfattribs={"layer": "NEW_WALLS"})
    p = tmp_path / "confusable.dxf"
    doc.saveas(p)
    return p


def test_confusable_layer_names_do_not_collide_on_disk(tmp_path):
    """"NEW WALLS" and "NEW_WALLS" must never clobber each other's render --
    that would silently send the classifier the same image twice, captioned
    as two different layers."""
    p = _doc_with_confusable_layers(tmp_path)
    ref, per_layer = render_for_escalation(
        p, ["NEW WALLS", "NEW_WALLS"], tmp_path / "cache")

    assert set(per_layer) == {"NEW WALLS", "NEW_WALLS"}
    path_a, path_b = per_layer["NEW WALLS"], per_layer["NEW_WALLS"]
    assert path_a != path_b
    assert path_a.exists() and path_b.exists()
    assert path_a.read_bytes() != path_b.read_bytes()


def test_per_layer_exceptions_caught_but_batch_continues(tmp_path, monkeypatch):
    """Exceptions on individual layer renders (but not RenderUnavailable) must be caught."""
    p = _doc(tmp_path)

    # Monkeypatch render_layer to raise for layer "WALL" only
    import archiagent.classify.thumbnails as thumbs_module
    original_render = thumbs_module.render_layer

    def mock_render_layer(dxf_path, layer, out_png, **kwargs):
        if layer == "WALL":
            raise RuntimeError("test: rendering failed for WALL")
        return original_render(dxf_path, layer, out_png, **kwargs)

    monkeypatch.setattr(thumbs_module, "render_layer", mock_render_layer)

    # render_for_escalation should catch the exception for WALL but render TINY
    ref, per_layer = render_for_escalation(p, ["WALL", "TINY"], tmp_path / "cache")
    assert "WALL" not in per_layer, "Layer with rendering exception should be omitted"
    assert "TINY" in per_layer, "Other layers should still be rendered"
    assert ref.exists()
