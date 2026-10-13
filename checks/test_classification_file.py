"""A layer classification is made once per file and handed to every floor run."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

from archiagent.classify.cache import decode_classification, encode_classification
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role

ROOT = Path(__file__).resolve().parents[1]


def run(*argv, env=None):
    return subprocess.run([sys.executable, "-m", "archiagent", *argv], cwd=ROOT, capture_output=True,
                          text=True, timeout=300, env=env if env is not None else dict(os.environ))


DECISIONS = (LayerDecision("WALL", Role.WALL_STRUCTURAL, 0.9, "thick walls", "llm"),
             LayerDecision("FURN", Role.FURNITURE, 0.8, "", "llm"))


def test_a_classification_round_trips():
    assert decode_classification(encode_classification(DECISIONS)) == DECISIONS


@pytest.mark.parametrize("bad", [[{"layer": "A", "role": "wall", "confidence": 1, "reason": "", "source": "oracle"}],
                                 [{"layer": "A", "role": "not-a-role", "confidence": 1, "reason": "", "source": "llm"}],
                                 [{"layer": "A"}], "nope"])
def test_a_corrupt_file_is_refused_not_trusted(bad):
    with pytest.raises((ValueError, KeyError, TypeError)):
        decode_classification(bad)


@pytest.fixture
def house(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL")
    msp = doc.modelspace()
    for a, b in (((0, 0), (360, 0)), ((360, 0), (360, 240)), ((360, 240), (0, 240)), ((0, 240), (0, 0))):
        (x1, y1), (x2, y2) = a, b
        for d in (-4.5, 4.5):
            s, e = ((x1, y1 + d), (x2, y2 + d)) if y1 == y2 else ((x1 + d, y1), (x2 + d, y2))
            msp.add_line(s, e, dxfattribs={"layer": "WALL"})
    path = tmp_path / "house.dxf"
    doc.saveas(path)
    return path


SCALE = ("--scale-from-wall", "0", "0", "360", "0", "30ft")


def walls_in(out):
    return int(re.search(r"^\s*walls\s+(\d+)", out, re.M).group(1))


def test_classify_only_can_write_the_classification(house, tmp_path):
    target = tmp_path / "plan.classification.json"
    done = run("--dxfFilePath", str(house), "--classify-only", "--rules", "--classification-out", str(target))
    assert done.returncode == 0, done.stderr[-1000:]
    layers = {d["layer"]: d["role"] for d in json.loads(target.read_text())}
    assert "WALL" in layers


def test_a_run_with_the_file_needs_no_provider_and_builds_the_same_walls(house, tmp_path):
    target = tmp_path / "plan.classification.json"
    run("--dxfFilePath", str(house), "--classify-only", "--rules", "--classification-out", str(target))
    baseline = run("--dxfFilePath", str(house), "--outputDir", str(tmp_path / "a"), "--rules", *SCALE)
    # No --rules, no provider key at all: only the file can supply the classification.
    # (Wall adjudication is a separate, per-floor model call; it is switched off here.)
    env = {k: v for k, v in os.environ.items() if "KEY" not in k and not k.startswith("ARCHIAGENT_LLM")}
    done = run("--dxfFilePath", str(house), "--outputDir", str(tmp_path / "b"),
               "--classification-file", str(target), "--no-wall-adjudication", *SCALE, env=env)
    assert done.returncode == 0, done.stderr[-1500:]
    assert walls_in(done.stdout) == walls_in(baseline.stdout) == 4


def test_the_file_cannot_be_combined_with_rules_or_a_manual_wall_map(house, tmp_path):
    for flag in (["--rules"], ["--walls", "WALL"]):
        done = run("--dxfFilePath", str(house), "--outputDir", str(tmp_path / "x"),
                   "--classification-file", "c.json", *flag, *SCALE)
        assert done.returncode == 3 and "--classification-file" in done.stderr


def test_a_missing_file_is_a_usage_error(house, tmp_path):
    done = run("--dxfFilePath", str(house), "--outputDir", str(tmp_path / "y"),
               "--classification-file", str(tmp_path / "nope.json"), *SCALE)
    assert done.returncode != 0 and "nope.json" in done.stderr
