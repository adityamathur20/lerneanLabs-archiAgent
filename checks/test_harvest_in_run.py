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


# --- --harvest-min-instances ---------------------------------------------------

def unnamed_blocks(path: Path, count: int) -> Path:
    """`count` copies of one block whose name says nothing, in a drawn-in-inches room."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALLS")
    block = doc.blocks.new("Z9")
    block.add_lwpolyline([(0, 0), (20, 0), (20, 30), (0, 30)], close=True)
    block.add_circle((10, 15), 6)
    msp = doc.modelspace()
    for i in range(count):
        msp.add_blockref("Z9", (24 + i * 40, 24))
    for a, b in (((0, 0), (480, 0)), ((480, 0), (480, 240)), ((480, 240), (0, 240)), ((0, 240), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "WALLS"})
    doc.saveas(path)
    return path


def harvest_only(tmp_path, *extra):
    return run("--dxfFilePath", str(unnamed_blocks(tmp_path / "plan.dxf", 5)), "--outputDir", str(tmp_path / "out"),
               "--harvest-symbols", "--scale-from-wall", "0", "0", "480", "0", "40ft", *extra)


def test_five_unnamed_instances_are_not_proposed_at_the_default_limit(tmp_path):
    done = harvest_only(tmp_path)
    assert done.returncode == 0, done.stderr[-1500:]
    assert "0 candidates" in done.stdout


def test_a_lower_limit_proposes_them(tmp_path):
    done = harvest_only(tmp_path, "--harvest-min-instances", "4")
    assert done.returncode == 0, done.stderr[-1500:]
    assert "1 candidates" in done.stdout


def test_the_limit_must_be_a_positive_whole_number(tmp_path):
    done = harvest_only(tmp_path, "--harvest-min-instances", "0")
    assert done.returncode == 3
    assert "--harvest-min-instances" in done.stderr


# --- already-approved shapes are not proposed again ---------------------------

def square(side=1.0, offset=0.0):
    pts = [[offset, 0], [offset + side, 0], [offset + side, side], [offset, side], [offset, 0]]
    return [{"category": "closed", "points": pts, "holes": []}]


def bar():
    return [{"category": "closed", "points": [[0, 0], [1, 0], [1, .1], [0, .1], [0, 0]], "holes": []}]


def test_hide_approved_matches_on_the_shape_itself_not_its_id():
    from archiagent.classify.symbol_harvest import hide_approved
    found = [{"id": "unreviewed-aaaaaaaa", "geometry": square()},
             {"id": "unreviewed-bbbbbbbb", "geometry": bar()}]
    library = [{"id": "furniture-zzzzzzzz", "approved_from": "unreviewed-qqqqqqqq", "geometry": square(3.0)}]
    shown, hidden = hide_approved(found, library)
    # The square is the same shape at another scale: hidden. The bar has the same
    # part structure (one closed four-sided path) but is another shape: still shown.
    assert [c["id"] for c in shown] == ["unreviewed-bbbbbbbb"]
    assert hidden == 1


def test_the_same_id_digest_does_not_hide_a_different_shape():
    from archiagent.classify.symbol_harvest import hide_approved
    found = [{"id": "unreviewed-18bd23f2", "geometry": bar()}]
    library = [{"id": "vegetation-18bd23f2", "approved_from": "unreviewed-18bd23f2", "geometry": square()}]
    shown, hidden = hide_approved(found, library)
    assert [c["id"] for c in shown] == ["unreviewed-18bd23f2"] and hidden == 0


def test_a_library_is_not_needed_to_harvest():
    from archiagent.classify.symbol_harvest import hide_approved
    found = [{"id": "unreviewed-18bd23f2", "geometry": square()}]
    shown, hidden = hide_approved(found, ())
    assert list(shown) == found and hidden == 0


def approved_library_for(path: Path, drawing: Path) -> Path:
    """Harvest the drawing, approve what it finds, write that as a library."""
    out = path / "first"
    done = run("--dxfFilePath", str(drawing), "--outputDir", str(out), "--harvest-symbols",
               "--harvest-min-instances", "4", "--scale-from-wall", "0", "0", "480", "0", "40ft")
    assert done.returncode == 0, done.stderr[-1500:]
    found = json.loads((drawing.parent / ".archiagent-cache" / "symbols" / "candidates.json").read_text())
    template = {**found[0], "kind": "furniture", "status": "reviewed", "reviewed_by": "test"}
    library = path / "library.json"
    library.write_text(json.dumps({"schema_version": 1, "templates": [template]}))
    return library


def test_a_harvest_run_hides_what_the_library_already_holds(tmp_path):
    drawing = unnamed_blocks(tmp_path / "plan.dxf", 5)
    library = approved_library_for(tmp_path, drawing)
    again = tmp_path / "again"
    again.mkdir()
    second = unnamed_blocks(again / "plan.dxf", 5)
    done = run("--dxfFilePath", str(second), "--outputDir", str(again / "out"), "--harvest-symbols",
               "--harvest-min-instances", "4", "--symbol-library", str(library),
               "--scale-from-wall", "0", "0", "480", "0", "40ft")
    assert done.returncode == 0, done.stderr[-1500:]
    assert "0 candidates" in done.stdout
    assert "1 already in the library" in done.stdout
