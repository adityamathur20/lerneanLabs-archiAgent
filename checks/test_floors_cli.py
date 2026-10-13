"""--detect-floors and --cut-floors through the real command line."""
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*argv):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=300, env=dict(os.environ))


def draw_plan(msp, x, y, w=720, h=480, layer="WALL"):
    """A plan in inches: outline plus internal walls, so it is a real cluster."""
    for a, b in (((x, y), (x + w, y)), ((x + w, y), (x + w, y + h)),
                 ((x + w, y + h), (x, y + h)), ((x, y + h), (x, y))):
        msp.add_line(a, b, dxfattribs={"layer": layer})
    for k in range(1, 8):
        msp.add_line((x + k * w / 8, y), (x + k * w / 8, y + h * (0.5 if k % 2 else 1)), dxfattribs={"layer": layer})
        msp.add_line((x, y + k * h / 8), (x + w * (0.5 if k % 2 else 1), y + k * h / 8), dxfattribs={"layer": layer})


@pytest.fixture
def two_floor_sheet(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL")
    msp = doc.modelspace()
    draw_plan(msp, 0, 0)
    draw_plan(msp, 1200, 0)
    msp.add_text("GROUND FLOOR PLAN", dxfattribs={"insert": (300, -60), "height": 24})
    msp.add_text("FIRST FLOOR PLAN", dxfattribs={"insert": (1500, -60), "height": 24})
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    return path


SCALE = ("--scale-from-wall", "0", "0", "720", "0", "60ft")


def test_floors_are_listed_as_json(two_floor_sheet, tmp_path):
    done = run("--dxfFilePath", str(two_floor_sheet), "--detect-floors", *SCALE)
    assert done.returncode == 0, done.stderr[-1500:]
    data = json.loads(done.stdout)
    assert [f["name"] for f in data["floors"]] == ["Ground floor", "First floor"]
    assert [f["elevation_ft"] for f in data["floors"]] == [0.0, 10.0]
    assert data["wall_height_ft"] == 10.0
    assert data["floors"][0]["elevation_source"] == "assumed"


def test_the_wall_height_flag_sets_the_assumed_spacing(two_floor_sheet):
    done = run("--dxfFilePath", str(two_floor_sheet), "--detect-floors", "--height", "12", *SCALE)
    assert [f["elevation_ft"] for f in json.loads(done.stdout)["floors"]] == [0.0, 12.0]


def test_a_cut_writes_one_drawing_per_approved_floor(two_floor_sheet, tmp_path):
    listed = json.loads(run("--dxfFilePath", str(two_floor_sheet), "--detect-floors", *SCALE).stdout)
    listed["floors"][1]["approved"] = False
    floors_file = tmp_path / "floors.json"
    floors_file.write_text(json.dumps(listed))
    out = tmp_path / "cut"
    done = run("--dxfFilePath", str(two_floor_sheet), "--cut-floors", str(floors_file), "--outputDir", str(out))
    assert done.returncode == 0, done.stderr[-1500:]
    assert sorted(p.name for p in out.glob("floor-*.dxf")) == ["floor-01.dxf"]
    report = json.loads((out / "floors.cut.json").read_text())
    assert report["floors"][0]["floor_id"] == "floor-01"
    kept = len(ezdxf.readfile(str(out / "floor-01.dxf")).modelspace().query("LINE"))
    assert kept == 18          # the ground plan only


def test_the_new_modes_refuse_a_pdf_and_a_replay(tmp_path):
    for flag in (["--detect-floors"], ["--cut-floors", "f.json"]):
        done = run("--pdfFilePath", "plan.pdf", "--outputDir", str(tmp_path), *flag)
        assert done.returncode == 3 and "PDF" in done.stderr
        done = run("--dxfFilePath", "plan.dxf", "--outputDir", str(tmp_path),
                   "--replay-manifest", "m.json", *flag)
        assert done.returncode == 3


def test_a_single_plan_lists_no_floors(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL")
    draw_plan(doc.modelspace(), 0, 0)
    path = tmp_path / "one.dxf"
    doc.saveas(path)
    done = run("--dxfFilePath", str(path), "--detect-floors", *SCALE)
    assert done.returncode == 0
    assert json.loads(done.stdout)["floors"] == []
