"""--harvest-symbols-to: a normal run also proposes symbol candidates.

The web service collects every run's candidates for the owner to approve, so
harvesting rides along with conversion instead of being a separate invocation,
and sizes candidates with the scale that run actually resolved.
"""
import json
import subprocess
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]


def fixtures_drawing(path: Path) -> Path:
    """Ten WC blocks (18 x 27 in) and a room, drawn in inches; header says mm."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    doc.layers.add("WALLS")
    block = doc.blocks.new("WC")
    block.add_lwpolyline([(0, 0), (18, 0), (18, 27), (0, 27)], close=True)
    block.add_circle((9, 18), 6)
    msp = doc.modelspace()
    for i in range(10):
        msp.add_blockref("WC", (24 + i * 40, 24))
    for a, b in (((0, 0), (480, 0)), ((480, 0), (480, 240)), ((480, 240), (0, 240)), ((0, 240), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "WALLS"})
    doc.saveas(path)
    return path


def run(*argv):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)


def test_a_run_writes_candidates_sized_with_its_resolved_scale(tmp_path):
    target = tmp_path / "out" / "plan.symbols.json"
    done = run("--dxfFilePath", str(fixtures_drawing(tmp_path / "plan.dxf")), "--outputDir", str(tmp_path / "out"),
               "--rules", "--walls", "WALLS", "--scale-from-wall", "0", "0", "480", "0", "40ft",
               "--harvest-symbols-to", str(target))
    assert target.is_file(), done.stderr[-2000:]
    report = json.loads(target.read_text())
    assert report["schema_version"] == 1
    assert report["units_per_foot"] == 12.0
    wc = [c for c in report["candidates"] if c["found_by"] in ("name", "frequency")]
    assert wc, report
    # 27 in = 2.25 ft at the asserted 12 units/ft; the mm header would say 0.089 ft.
    assert any(c["size_ft"]["min"] <= 2.25 <= c["size_ft"]["max"] for c in wc), [c["size_ft"] for c in wc]
    assert all(c["status"] == "candidate" for c in report["candidates"])


def test_without_the_flag_nothing_is_harvested(tmp_path):
    out = tmp_path / "out"
    run("--dxfFilePath", str(fixtures_drawing(tmp_path / "plan.dxf")), "--outputDir", str(out),
        "--rules", "--walls", "WALLS", "--scale-from-wall", "0", "0", "480", "0", "40ft")
    assert not list(out.glob("*.symbols.json"))
