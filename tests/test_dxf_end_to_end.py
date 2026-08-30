"""DXF pipeline against the real client drawings.

These skip unless ARCHIAGENT_FIXTURES points at the drawings directory. The
drawings are confidential and are never committed; neither is any IFC these
tests author -- everything goes to tmp_path.

Counts are asserted with headroom rather than pinned exactly, so refactoring
that changes a wall count slightly does not fail the suite, while a real
regression (walls collapsing, rooms vanishing) still does.
"""

from __future__ import annotations

import pytest

from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.classify.escalate import escalation_candidates
from archiagent.classify.layers import LayerDecision, StubClassifier
from archiagent.classify.roles import Role
from archiagent.ingest.dxf_vector import load_dxf, units_from_header
from archiagent.pipeline import extract_from_dxf
from tests.conftest import fixtures_dir


@pytest.fixture(scope="session")
def dxf_dir():
    d = fixtures_dir() / "dxf"
    if not d.is_dir():
        pytest.skip(f"{d} not present")
    return d


@pytest.fixture(scope="session")
def floor_plan(dxf_dir):
    p = dxf_dir / "Floor Plan.dxf"
    if not p.exists():
        pytest.skip(f"{p} not present")
    return p


def _walls(*names):
    return StubClassifier({n: (Role.WALL_STRUCTURAL, 0.95) for n in names})


# --- the layer-0 trap -------------------------------------------------------

def test_layer_zero_holds_most_of_the_drawing(floor_plan):
    """54.9% of this drawing sits on the unnamed default layer."""
    ps, _ = load_dxf(floor_plan, units_per_foot=12.0)
    stats = {s.name: s for s in build_dxf_inventory(floor_plan, ps)}
    assert stats["0"].entity_share > 0.5


def test_layer_zero_escalates_on_its_share_not_its_name(floor_plan):
    """Its name says nothing, so only the share trigger can catch it."""
    ps, _ = load_dxf(floor_plan, units_per_foot=12.0)
    stats = build_dxf_inventory(floor_plan, ps)
    decisions = tuple(LayerDecision(s.name, Role.IGNORE, 0.95, "", "llm")
                      for s in stats)
    assert dict(escalation_candidates(decisions, stats))["0"] == \
        "large_non_wall_layer"


def test_the_layer_named_walls_is_the_wrong_answer(floor_plan):
    """Naming alone picks WALLS, which yields no rooms at all. This is the
    measured reason the vision escalation exists."""
    ps, upf = load_dxf(floor_plan, units_per_foot=12.0)
    named = extract_from_dxf(ps, _walls("WALLS"), units_per_foot=upf)
    real = extract_from_dxf(ps, _walls("0", "WALLS"), units_per_foot=upf)
    assert len(named.spaces) == 0
    assert len(real.spaces) >= 20
    assert len(real.walls) > 10 * len(named.walls)


# --- units ------------------------------------------------------------------

def test_declared_units_are_not_trustworthy(floor_plan):
    """$INSUNITS says feet; the columns measure 12.04 x 24.07, i.e. inches."""
    import ezdxf
    doc = ezdxf.readfile(str(floor_plan))
    assert units_from_header(doc) == 1.0            # header claims feet
    cols = [e for e in doc.modelspace().query("LWPOLYLINE")
            if e.dxf.layer == "COLOUM"]
    assert cols, "COLOUM layer missing from the fixture"
    pts = [(v[0], v[1]) for v in cols[0]]
    short = min(max(x for x, _ in pts) - min(x for x, _ in pts),
                max(y for _, y in pts) - min(y for _, y in pts))
    assert 10 < short < 32, f"column short side {short} is not inches"


def test_explicit_units_override_the_lying_header(floor_plan):
    _, upf = load_dxf(floor_plan, units_per_foot=12.0)
    assert upf == 12.0


# --- the whole point --------------------------------------------------------

def test_no_scale_gate_failure_on_the_dxf_path(floor_plan):
    """The PDF twin of this drawing fails the R1 scale gate outright, because
    it has no readable text. The DXF path skips scale resolution entirely, so
    that failure cannot occur."""
    ps, upf = load_dxf(floor_plan, units_per_foot=12.0)
    model = extract_from_dxf(ps, _walls("0", "WALLS"), units_per_foot=upf)
    assert [i for i in model.issues if i.code == "scale_gate_failed"] == []
    assert model.scale.units_per_foot == 12.0
    assert model.scale.max_residual_in == 0.0


def test_authors_a_readable_ifc(floor_plan, tmp_path):
    import ifcopenshell
    from archiagent.ifc.author import author_ifc
    ps, upf = load_dxf(floor_plan, units_per_foot=12.0)
    model = extract_from_dxf(ps, _walls("0", "WALLS"), units_per_foot=upf)
    out = author_ifc(model, tmp_path / "floor.ifc")
    f = ifcopenshell.open(out)
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcWall")) > 500


# --- the other three drawings ----------------------------------------------

@pytest.mark.parametrize("name,upf,layers,min_walls", [
    ("Manoj JI Ladnu shyam nagar plumbing.dxf", 12.0, ("wall", "boundary wall"), 150),
    ("VINAYAK APARTMENTS.dxf", 12.0, ("walls", "NEW WALLS"), 500),
])
def test_other_drawings_produce_walls(dxf_dir, name, upf, layers, min_walls):
    p = dxf_dir / name
    if not p.exists():
        pytest.skip(f"{p} not present")
    ps, resolved = load_dxf(p, units_per_foot=upf)
    model = extract_from_dxf(ps, _walls(*layers), units_per_foot=resolved)
    assert len(model.walls) >= min_walls


def test_every_dxf_loads_and_inventories(dxf_dir):
    """No drawing may crash ingest or inventory, whatever its contents."""
    found = sorted(dxf_dir.glob("*.dxf"))
    if not found:
        pytest.skip("no DXFs present")
    for p in found:
        ps, upf = load_dxf(p, units_per_foot=12.0)
        stats = build_dxf_inventory(p, ps)
        assert stats, f"{p.name} produced an empty inventory"
        assert upf == 12.0
