"""--prepare: convert, read the scale evidence, stop.

The web app's scale gate shows a drawing before anything is built and offers
the scale the drawing's own dimensions imply, or one asserted wall. --prepare
produces exactly what that needs -- the DXF (a DWG's conversion) and
<stem>.scale.json -- without classifying, calling an LLM or authoring.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
DWG = os.environ.get("ARCHIAGENT_DWG")


def run(*argv, env=None):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=600,
                          env={**os.environ, **(env or {})})


def room(path: Path, insunits: int, dimensioned: bool) -> Path:
    """A 40 ft x 30 ft outline in inches; optionally with native dimensions."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 360), (0, 360)], close=True)
    if dimensioned:
        # Drawn in inches, displayed in metres: DIMLFAC 0.0254, as Aiims Road
        # carries it. ezdxf's default style is otherwise metric throughout.
        doc.dimstyles.get("EZDXF").dxf.dimlfac = 0.0254
        for p1, p2, base, angle in (((0, 0), (480, 0), (240, -36), 0), ((0, 0), (0, 360), (-36, 180), 90),
                                    ((0, 360), (480, 360), (240, 400), 0)):
            msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle).render()
    doc.saveas(path)
    return path


def prepare(path: Path, out: Path, *extra):
    done = run("--dxfFilePath", str(path), "--outputDir", str(out), "--prepare", *extra,
               # A bogus key: if --prepare built the LLM classifier it would fail.
               env={"ARCHIAGENT_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "invalid"})
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads((out / f"{path.stem}.scale.json").read_text()), done


def test_a_dimensioned_drawing_reports_the_scale_its_dimensions_imply(tmp_path):
    report, _ = prepare(room(tmp_path / "plan.dxf", 1, True), tmp_path / "out")
    assert report["schema_version"] == 1
    assert report["header"] == {"insunits": 1, "units_per_foot": 12.0}
    assert report["extracted"]["units_per_foot"] == pytest.approx(12.0)
    assert report["extracted"]["support"] >= 2
    assert report["dimensions"] == 3


def test_a_drawing_without_dimensions_reports_none_and_never_guesses(tmp_path):
    report, _ = prepare(room(tmp_path / "plan.dxf", 1, False), tmp_path / "out")
    assert report["extracted"] is None
    assert report["dimensions"] == 0


def test_a_unitless_header_is_reported_as_such(tmp_path):
    report, _ = prepare(room(tmp_path / "plan.dxf", 0, False), tmp_path / "out")
    assert report["header"] == {"insunits": 0, "units_per_foot": None}


def test_prepare_reports_extents_and_the_source_checksum(tmp_path):
    report, _ = prepare(room(tmp_path / "plan.dxf", 1, False), tmp_path / "out")
    assert report["extents"]["min"] == pytest.approx([0, 0])
    assert report["extents"]["max"] == pytest.approx([480, 360])
    assert len(report["source_sha256"]) == 64


def test_prepare_builds_nothing_and_calls_no_model(tmp_path):
    out = tmp_path / "out"
    prepare(room(tmp_path / "plan.dxf", 1, True), out)
    # The token report is written on every run; here it must record no calls.
    assert sorted(p.name for p in out.iterdir()) == ["plan.scale.json", "plan.tokens.json"]
    assert json.loads((out / "plan.tokens.json").read_text())["totals"]["calls"] == 0


def test_prepare_needs_a_dxf_or_dwg(tmp_path):
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    done = run("--pdfFilePath", str(pdf), "--outputDir", str(tmp_path / "out"), "--prepare")
    assert done.returncode == 3
    assert "--prepare" in done.stderr


@pytest.mark.skipif(not (DWG and Path(DWG).exists()), reason="set ARCHIAGENT_DWG to a real .dwg")
def test_a_dwg_is_converted_and_its_dxf_kept(tmp_path):
    out = tmp_path / "out"
    done = run("--dwgFilePath", DWG, "--outputDir", str(out), "--prepare")
    assert done.returncode == 0, done.stderr[-2000:]
    stem = Path(DWG).stem
    assert (out / f"{stem}.dxf").is_file()
    assert (out / f"{stem}.scale.json").is_file()
