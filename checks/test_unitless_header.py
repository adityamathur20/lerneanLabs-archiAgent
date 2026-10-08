"""A DXF whose header declares no unit still loads; only the scale ladder decides.

The header is a declaration, not a measurement, so its absence must not stop a
run that has a real scale source -- an asserted wall, or the drawing's own
dimensions. Before 2026-10-08 load_dxf refused such a file before the ladder
ever ran, so asserting a wall could not help the drawings that most needed it.
"""
import math
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

from archiagent.cli import _parser, _resolve_scale
from archiagent.ingest.dxf_vector import DxfUnitsError, load_dxf

ROOT = Path(__file__).resolve().parents[1]


def unitless_room(path: Path, k: float = 1.0) -> Path:
    """A 12 ft x 10 ft room drawn in inches (k=25.4: millimetres), with an
    arc, and no $INSUNITS."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 0
    doc.layers.add("WALLS")
    msp = doc.modelspace()
    for a, b in (((0, 0), (144, 0)), ((144, 0), (144, 120)), ((144, 120), (0, 120)), ((0, 120), (0, 0)),
                 ((6, 6), (138, 6)), ((138, 6), (138, 114)), ((138, 114), (6, 114)), ((6, 114), (6, 6))):
        msp.add_line((a[0] * k, a[1] * k), (b[0] * k, b[1] * k), dxfattribs={"layer": "WALLS"})
    msp.add_arc((72 * k, 6 * k), 18 * k, 0, 180, dxfattribs={"layer": "WALLS"})
    doc.saveas(path)
    return path


def test_a_unitless_header_loads_and_reports_no_declared_scale(tmp_path):
    ps, header = load_dxf(unitless_room(tmp_path / "room.dxf"))
    assert header is None
    assert ps.declared_units_per_foot is None
    assert ps.entities, "the geometry must still be read"


def test_curves_are_flattened_at_a_tolerance_relative_to_the_drawing(tmp_path):
    """No unit means no feet-based tolerance; the drawing's own size sets it,
    so the same room flattens the same whether it was drawn in inches or mm."""
    arc_points = lambda ps: next(len(p.coords) for p in ps.primitives if len(p.coords) > 2)
    inches, _ = load_dxf(unitless_room(tmp_path / "in.dxf"))
    millimetres, _ = load_dxf(unitless_room(tmp_path / "mm.dxf", k=25.4))
    assert arc_points(inches) == arc_points(millimetres)
    assert 8 <= arc_points(inches) <= 400


@pytest.mark.parametrize("units", [0, -1, math.nan, math.inf])
def test_an_invalid_explicit_scale_is_still_refused(tmp_path, units):
    with pytest.raises(DxfUnitsError):
        load_dxf(unitless_room(tmp_path / "room.dxf"), units)


def test_the_refusal_says_the_header_declares_nothing(tmp_path):
    ps, header = load_dxf(unitless_room(tmp_path / "room.dxf"))
    args = _parser().parse_args(["--dxfFilePath", "room.dxf", "--outputDir", "out"])
    with pytest.raises(ValueError) as caught:
        _resolve_scale(args, ps, True, None, header)
    assert "declares no units" in str(caught.value)
    assert "--scale-from-wall" in str(caught.value)


def test_an_asserted_wall_scales_a_unitless_drawing(tmp_path):
    ps, header = load_dxf(unitless_room(tmp_path / "room.dxf"))
    args = _parser().parse_args(["--dxfFilePath", "room.dxf", "--outputDir", "out",
                                 "--scale-from-wall", "0", "0", "144", "0", "12ft"])
    scale, rung, _ = _resolve_scale(args, ps, True, None, header)
    assert (scale, rung) == (pytest.approx(12.0), "asserted")


def run(*argv, cwd):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)


def test_the_cli_builds_a_unitless_drawing_given_a_wall(tmp_path):
    path = unitless_room(tmp_path / "room.dxf")
    done = run("--dxfFilePath", str(path), "--outputDir", str(tmp_path / "out"), "--rules",
               "--walls", "WALLS", "--scale-from-wall", "0", "0", "144", "0", "12ft", cwd=tmp_path)
    assert "units-per-foot" not in done.stderr, done.stderr
    assert (tmp_path / "out" / "room.interpretation.json").is_file(), done.stderr[-2000:]


def test_the_cli_still_refuses_a_unitless_drawing_with_no_scale(tmp_path):
    path = unitless_room(tmp_path / "room.dxf")
    done = run("--dxfFilePath", str(path), "--outputDir", str(tmp_path / "out"), "--rules",
               "--walls", "WALLS", cwd=tmp_path)
    assert done.returncode != 0
    assert "scale is not established" in done.stderr
    assert "declares no units" in done.stderr


def test_harvesting_sizes_candidates_with_the_resolved_scale(tmp_path):
    """--harvest-symbols used the header's units: a unitless header crashed it
    and a wrong one mis-sized every candidate. It takes the ladder's scale."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4                 # declares mm; drawn in inches
    block = doc.blocks.new("WC")
    block.add_lwpolyline([(0, 0), (18, 0), (18, 27), (0, 27)], close=True)
    msp = doc.modelspace()
    for i in range(10):
        msp.add_blockref("WC", (i * 40, 0))
    path = tmp_path / "fixtures.dxf"
    doc.saveas(path)
    done = run("--dxfFilePath", str(path), "--outputDir", str(tmp_path / "out"), "--harvest-symbols",
               "--scale-from-wall", "0", "0", "120", "0", "10ft", cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-1500:]
    import json
    candidates = json.loads((tmp_path / ".archiagent-cache" / "symbols" / "candidates.json").read_text())
    sizes = [c["size_ft"] for c in candidates]
    # 27 inches = 2.25 ft at the asserted 12 units/ft (the mm header would say 0.089 ft).
    assert any(s["min"] <= 2.25 <= s["max"] for s in sizes), sizes
