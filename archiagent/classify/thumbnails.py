"""Render a DXF layer to a PNG for the vision stage.

Uses ezdxf's matplotlib backend in-process: no Blender, no IFC round-trip.
matplotlib and Pillow are optional -- absence degrades to stage 1, it does
not fail the run.

The output is a picture of a confidential client drawing. It is written only
under a caller-supplied cache directory, which .gitignore covers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence


class RenderUnavailable(RuntimeError):
    """matplotlib and/or Pillow are not installed."""


# A module-level constant, not an inline literal, so a test can assert on it
# directly instead of needing to actually uninstall matplotlib/Pillow to
# trigger this path. This message is user-facing: dxf_classifier.py catches
# RenderUnavailable and turns it into a layer_escalation_skipped Issue,
# which the CLI's -v now prints (Task 7's on_issue wiring), so stale advice
# here reaches a real terminal, not just a log nobody reads. It once said
# "run without --vision" -- a flag that was renamed and had its default
# inverted (vision is ON by default now); the flag that turns it off is
# --no_vision.
RENDER_UNAVAILABLE_MSG = (
    "the vision stage needs the optional matplotlib and Pillow "
    "dependencies to render layer thumbnails; install the 'vision' extra, "
    "or pass --no_vision to turn the vision stage off")


def _backend():
    try:
        import matplotlib
        matplotlib.use("Agg")
        from ezdxf.addons.drawing.matplotlib import qsave
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        raise RenderUnavailable(RENDER_UNAVAILABLE_MSG) from exc
    return qsave


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "_"


def render_layer(dxf_path: str | Path, layer: str | None, out_png: Path,
                 size_inches: tuple[float, float] = (7.0, 7.0),
                 dpi: int = 140) -> Path:
    import ezdxf
    qsave = _backend()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    ff = None if layer is None else (lambda e: e.dxf.layer == layer)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    qsave(msp, str(out_png), bg="#FFFFFF", fg="#000000", dpi=dpi,
          filter_func=ff, size_inches=size_inches)
    return out_png


def render_for_escalation(dxf_path: str | Path, layers: Sequence[str],
                          cache_dir: Path) -> tuple[Path, dict[str, Path]]:
    import ezdxf
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    ref = render_layer(dxf_path, None, cache_dir / "_reference.png")
    out: dict[str, Path] = {}
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    for lay in layers:
        # Skip layers with no entities to avoid blank renders
        if not any(e.dxf.layer == lay for e in msp):
            continue
        try:
            out[lay] = render_layer(dxf_path, lay,
                                    cache_dir / f"layer_{_safe(lay)}.png")
        except RenderUnavailable:
            raise
        except Exception:                          # noqa: BLE001 - one bad layer is not fatal
            continue
    return ref, out
