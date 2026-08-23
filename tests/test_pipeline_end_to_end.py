import ifcopenshell

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.pipeline import run_pipeline

# StubClassifier looks up by EXACT LayerStats.name (case-sensitive), unlike
# PrimitiveSet.by_layer downstream, which is case-insensitive. The drawing's
# real layer name is "HATCH-CONS" (uppercase). "wall" (no layer by that
# exact name exists in this single-page drawing; only "walll" does) is kept
# here as a harmless no-op key for classifiers built against other drawings
# in the same family.
#
# Hatch is FILL INSIDE a wall body, not a wall face. Classifying it as
# wall_structural inflates candidate_runs and lets resolve_scale settle on a
# spurious scale that still passes the R1 gate -- measured at 8.36 pt/ft
# against a true ~11.67, a 40% error, with BOTH a higher match count (22 vs
# 21) and a lower residual (1.904in vs 1.568in) than the correct answer.
# HATCH1 alone is 6,534 diagonal fill strokes; pairing those as walls is
# spurious by construction. Hatch layers are therefore classified
# Role.ANNOTATION, not Role.WALL_STRUCTURAL -- do not "fix" this to chase a
# higher room/wall count; that was the exact mistake that produced the 40%
# scale error in the first place.
DEMOLITION_ROLES = {
    "wall": (Role.WALL_STRUCTURAL, 0.95),
    "walll": (Role.WALL_STRUCTURAL, 0.90),
    "HATCH-CONS": (Role.ANNOTATION, 0.75),
    "HATCH1": (Role.ANNOTATION, 0.70),
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


def test_extract_populates_model_issues(demolition_pdf):
    """Regression: BuildingModel.issues was always () from extract(), even
    though validate() found problems on the same model. run_pipeline must be
    able to rely on model.issues without a second validate() call."""
    from archiagent.pipeline import extract
    from archiagent.validate import validate

    model = extract(demolition_pdf, StubClassifier(DEMOLITION_ROLES))
    assert model.issues != ()
    assert model.issues == validate(model)


def test_no_wall_layers_names_the_cache_as_a_possible_cause(demolition_pdf):
    """A wall-less classification can be served from disk under a key that
    does not change on retry. The message has to mention --no-cache, or the
    user has no way to know the escape hatch exists."""
    import pytest

    from archiagent.pipeline import extract

    with pytest.raises(ValueError, match="--no-cache"):
        extract(demolition_pdf, StubClassifier({}))


def test_extract_from_primitives_does_not_reload_the_pdf(demolition_pdf):
    """The CLI needs the inventory BEFORE classifying (to check --walls
    names) and must not pay for load_pdf twice. extract_from_primitives takes
    the already-parsed PrimitiveSet -- and the inventory alongside it."""
    from archiagent.classify.inventory import build_inventory
    from archiagent.ingest.pdf_vector import load_pdf
    from archiagent.pipeline import extract, extract_from_primitives

    ps = load_pdf(demolition_pdf)
    stats = build_inventory(ps)
    a = extract_from_primitives(ps, StubClassifier(DEMOLITION_ROLES),
                                stats=stats)
    b = extract(demolition_pdf, StubClassifier(DEMOLITION_ROLES))

    assert a.scale == b.scale
    assert a.walls == b.walls
    assert a.source_path == b.source_path
    assert a.source_sha256 == b.source_sha256


def test_scale_resolves_to_the_drawings_true_scale(demolition_pdf):
    """Regression for a 40% scale error that PASSED the R1 gate.

    Classifying hatch layers as wall_structural inflates candidate_runs, and
    resolve_scale then settles on ~8.36 pt/ft against a true ~11.67 -- with BOTH a
    higher match count (22 vs 21) AND a lower residual (1.904 vs 1.568) than the
    correct answer. Neither of the gate's criteria can distinguish them, so the
    guard has to be upstream: only genuine wall faces may feed scale resolution.
    """
    from archiagent.pipeline import extract
    model = extract(demolition_pdf, StubClassifier(DEMOLITION_ROLES))
    assert 11.5 < model.scale.units_per_foot < 11.9
    assert model.scale.max_residual_in < 2.0
