"""--wall-thickness: inches on the command line, feet inside, refused when nonsense."""
import math

import pytest

from archiagent.cli import _declared_thickness_ft, _parser, _validate_options

BASE = ["--dxfFilePath", "plan.dxf", "--outputDir", "out"]


def parse(*extra):
    return _parser().parse_args(BASE + list(extra))


def test_no_flag_means_nothing_declared():
    args = parse()
    assert _declared_thickness_ft(args) == ()
    assert args.wall_thickness_exhaustive is False
    assert args.wall_thickness_tolerance_in == 0.5


def test_inches_become_feet_exactly_once_sorted_and_deduplicated():
    args = parse("--wall-thickness", "9", "4.5", "9")
    assert _declared_thickness_ft(args) == (4.5 / 12, 9 / 12)


@pytest.mark.parametrize("bad", ["0", "-4", "48.5", "nan", "inf"])
def test_a_thickness_outside_zero_to_forty_eight_inches_is_refused(bad):
    with pytest.raises(ValueError, match="--wall-thickness"):
        _declared_thickness_ft(parse("--wall-thickness", bad))


def test_forty_eight_inches_is_the_largest_accepted():
    assert _declared_thickness_ft(parse("--wall-thickness", "48")) == (4.0,)


def test_exhaustive_without_a_set_names_the_missing_flag():
    with pytest.raises(ValueError, match="--wall-thickness-exhaustive.*--wall-thickness"):
        _validate_options(parse("--wall-thickness-exhaustive"))


@pytest.mark.parametrize("bad", ["0", "-1", "6.5", "nan", "inf"])
def test_the_tolerance_must_be_finite_positive_and_at_most_six_inches(bad):
    with pytest.raises(ValueError, match="--wall-thickness-tolerance-in"):
        _validate_options(parse("--wall-thickness", "4", "--wall-thickness-tolerance-in", bad))


def test_a_valid_declaration_passes_validation():
    _validate_options(parse("--wall-thickness", "4", "8", "--wall-thickness-exhaustive",
                            "--wall-thickness-tolerance-in", "0.25"))


# --- through the real command line ------------------------------------------

import os
import re
import subprocess
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]


def run(*argv):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=600, env=dict(os.environ))


def faces(msp, a, b, thickness, layer):
    """Two face lines either side of the centreline a-b (axis-aligned), in inches."""
    (x1, y1), (x2, y2), h = a, b, thickness / 2
    for d in (-h, h):
        start, end = ((x1, y1 + d), (x2, y2 + d)) if y1 == y2 else ((x1 + d, y1), (x2 + d, y2))
        msp.add_line(start, end, dxfattribs={"layer": layer})


@pytest.fixture
def house_with_junk(tmp_path):
    """A 30 x 20 ft room of 9 in walls, plus a 20 ft run of 6 in 'wall' across it."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL")
    msp = doc.modelspace()
    for a, b in (((0, 0), (360, 0)), ((360, 0), (360, 240)), ((360, 240), (0, 240)), ((0, 240), (0, 0))):
        faces(msp, a, b, 9, "WALL")
    faces(msp, (60, 120), (300, 120), 6, "WALL")
    path = tmp_path / "plan.dxf"
    doc.saveas(path)
    return path


def walls_built(path, out, *extra):
    done = run("--dxfFilePath", str(path), "--outputDir", str(out), "--rules",
               "--scale-from-wall", "0", "0", "360", "0", "30ft", *extra)
    assert done.returncode == 0, done.stderr[-2000:]
    return int(re.search(r"^\s*walls\s+(\d+)", done.stdout, re.M).group(1))


def test_an_exhaustive_declared_set_removes_the_run_that_is_not_in_it(house_with_junk, tmp_path):
    assert walls_built(house_with_junk, tmp_path / "plain") == 5
    assert walls_built(house_with_junk, tmp_path / "declared",
                       "--wall-thickness", "9", "--wall-thickness-exhaustive") == 4


def test_a_declared_set_that_is_not_exhaustive_changes_no_verdict_here(house_with_junk, tmp_path):
    # The 6 in run sits on a wall layer, so without the veto candidacy keeps it.
    assert walls_built(house_with_junk, tmp_path / "declared", "--wall-thickness", "9") == 5


def test_a_replay_refuses_the_thickness_flags(tmp_path):
    for flag in (["--wall-thickness", "9"],
                 ["--wall-thickness", "9", "--wall-thickness-exhaustive"],
                 ["--wall-thickness", "9", "--wall-thickness-tolerance-in", "1"]):
        done = run("--dxfFilePath", "plan.dxf", "--outputDir", str(tmp_path),
                   "--replay-manifest", "missing.json", *flag)
        assert done.returncode == 3, done.stderr[-500:]
        assert "replay" in done.stderr


def test_the_exhaustive_flag_alone_is_a_usage_error(tmp_path):
    done = run("--dxfFilePath", "plan.dxf", "--outputDir", str(tmp_path), "--wall-thickness-exhaustive")
    assert done.returncode == 3
    assert "--wall-thickness" in done.stderr
