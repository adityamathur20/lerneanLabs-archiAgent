import ifcopenshell
import pytest

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.pipeline import run_pipeline

# Amended from the task brief: the hatch layers (HATCH1, HATCH-CONS) and the
# window layers are genuinely wall body in this drawing. Measured effect:
#   {'walll','wall'}                       -> 51 walls,  1 room,  267 sqft
#   + win, window, hatch1, hatch-cons      -> 114 walls, 4 rooms, 720 sqft
#
# StubClassifier looks up by EXACT LayerStats.name (case-sensitive), unlike
# PrimitiveSet.by_layer downstream, which is case-insensitive. The drawing's
# real layer name is "HATCH-CONS" (uppercase) -- "hatch-cons" would silently
# fail to match and drop 57 walls back out of the model. "wall" (no layer
# by that exact name exists in this single-page drawing; only "walll" does)
# is kept here as a harmless no-op key for classifiers built against other
# drawings in the same family.
DEMOLITION_ROLES = {
    "wall": (Role.WALL_STRUCTURAL, 0.95),
    "walll": (Role.WALL_STRUCTURAL, 0.90),
    "HATCH-CONS": (Role.WALL_STRUCTURAL, 0.75),
    "HATCH1": (Role.WALL_STRUCTURAL, 0.70),
    "window": (Role.WINDOW, 0.90),
    "win": (Role.WINDOW, 0.85),
    "FURNITURE": (Role.FURNITURE, 0.95),
    "Text": (Role.TEXT_LABEL, 0.95),
    "TITLE": (Role.TITLE_BLOCK, 0.95),
}


def test_end_to_end_on_the_real_demolition_plan(demolition_pdf, tmp_path):
    out, issues = run_pipeline(
        demolition_pdf, StubClassifier(DEMOLITION_ROLES),
        tmp_path / "demolition.ifc")

    f = ifcopenshell.open(out)
    assert f.schema == "IFC4"

    walls = f.by_type("IfcWall")
    assert len(walls) >= 30, f"expected a realistic wall count, got {len(walls)}"

    for wall in walls:
        assert wall.Representation.Representations[0].Items[0].is_a() \
            == "IfcExtrudedAreaSolid"

    errors = [i for i in issues if i.severity == "error"
              and i.code == "scale_gate_failed"]
    assert errors == [], f"R1 scale gate failed: {errors}"
