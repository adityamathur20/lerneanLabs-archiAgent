"""Floors run separately come back together as one building IFC."""
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import ifcopenshell
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCALE = ("--scale-from-wall", "0", "0", "720", "0", "60ft")


def run(*argv):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT, capture_output=True,
                          text=True, timeout=600, env=dict(os.environ))


def draw_plan(msp, x, y, w=720, h=480):
    for a, b in (((x, y), (x + w, y)), ((x + w, y), (x + w, y + h)),
                 ((x + w, y + h), (x, y + h)), ((x, y + h), (x, y))):
        (x1, y1), (x2, y2) = a, b
        for d in (-4.5, 4.5):
            s, e = ((x1, y1 + d), (x2, y2 + d)) if y1 == y2 else ((x1 + d, y1), (x2 + d, y2))
            msp.add_line(s, e, dxfattribs={"layer": "WALL"})
    for k in (1, 2, 3):
        px = x + k * w / 4
        for d in (-4.5, 4.5):
            msp.add_line((px + d, y), (px + d, y + h), dxfattribs={"layer": "WALL"})


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("assemble")
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL")
    doc.layers.add("DIM")
    msp = doc.modelspace()
    draw_plan(msp, 0, 0)
    draw_plan(msp, 1200, 0)
    # A sheet border far off, so the plans' own full-height walls are not read as the border.
    msp.add_line((-600, -1500), (3000, -1500), dxfattribs={"layer": "DIM"})
    msp.add_line((-600, 2500), (3000, 2500), dxfattribs={"layer": "DIM"})
    msp.add_text("GROUND FLOOR PLAN", dxfattribs={"insert": (300, -60), "height": 24})
    msp.add_text("FIRST FLOOR PLAN", dxfattribs={"insert": (1500, -60), "height": 24})
    sheet = tmp / "sheet.dxf"
    doc.saveas(sheet)

    listed = run("--dxfFilePath", str(sheet), "--detect-floors", *SCALE)
    assert listed.returncode == 0, listed.stderr[-1500:]
    floors_file = tmp / "floors.json"
    floors_file.write_text(listed.stdout)

    floors_dir = tmp / "floors"
    cut = run("--dxfFilePath", str(sheet), "--cut-floors", str(floors_file), "--outputDir", str(floors_dir))
    assert cut.returncode == 0, cut.stderr[-1500:]
    for f in json.loads(floors_file.read_text())["floors"]:
        done = run("--dxfFilePath", str(floors_dir / f"{f['id']}.dxf"), "--outputDir", str(floors_dir / f["id"]),
                   "--rules", *SCALE)
        assert done.returncode == 0, done.stderr[-1500:]
    return tmp, floors_file, floors_dir


def test_the_building_has_one_storey_per_floor_at_its_elevation(built):
    tmp, floors_file, floors_dir = built
    out = tmp / "building"
    done = run("--assemble-building", str(floors_file), "--floors-dir", str(floors_dir), "--outputDir", str(out))
    assert done.returncode == 0, done.stderr[-1500:]
    f = ifcopenshell.open(str(out / "building.ifc"))
    storeys = sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation)
    assert [s.Name for s in storeys] == ["Ground floor", "First floor"]
    assert [round(s.Elevation / 0.3048, 3) for s in storeys] == [0.0, 10.0]
    assert len(f.by_type("IfcWall")) + len(f.by_type("IfcWallStandardCase")) >= 2


def test_the_report_is_per_floor(built):
    tmp, floors_file, floors_dir = built
    out = tmp / "report"
    run("--assemble-building", str(floors_file), "--floors-dir", str(floors_dir), "--outputDir", str(out))
    report = json.loads((out / "building.report.json").read_text())
    assert [f["name"] for f in report["floors"]] == ["Ground floor", "First floor"]
    first = report["floors"][0]
    assert first["walls"] > 0 and first["elevation_source"] == "assumed" and "spaces" in first
    assert report["building"]["floors_assembled"] == 2 and report["building"]["floors_missing"] == []


def test_a_floor_that_did_not_run_is_reported_and_the_rest_are_assembled(built, tmp_path):
    _, floors_file, floors_dir = built
    partial = tmp_path / "partial"
    partial.mkdir()
    # only floor-01's outputs exist here
    for name in ("floor-01.dxf",):
        (partial / name).write_bytes((floors_dir / name).read_bytes())
    (partial / "floor-01").mkdir()
    for p in (floors_dir / "floor-01").iterdir():
        (partial / "floor-01" / p.name).write_bytes(p.read_bytes())
    out = tmp_path / "out"
    done = run("--assemble-building", str(floors_file), "--floors-dir", str(partial), "--outputDir", str(out))
    assert done.returncode == 0, done.stderr[-1500:]
    report = json.loads((out / "building.report.json").read_text())
    assert report["building"]["floors_missing"] == ["floor-02"]
    assert [s.Name for s in ifcopenshell.open(str(out / "building.ifc")).by_type("IfcBuildingStorey")] == ["Ground floor"]


def test_nothing_to_assemble_is_an_error(built, tmp_path):
    _, floors_file, _ = built
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run("--assemble-building", str(floors_file), "--floors-dir", str(empty), "--outputDir", str(tmp_path / "o"))
    assert done.returncode != 0 and "no floor" in done.stderr.lower()


def test_it_cannot_be_combined_with_a_replay(tmp_path):
    done = run("--assemble-building", "f.json", "--floors-dir", str(tmp_path), "--outputDir", str(tmp_path / "o"),
               "--replay-manifest", "m.json")
    assert done.returncode == 3
