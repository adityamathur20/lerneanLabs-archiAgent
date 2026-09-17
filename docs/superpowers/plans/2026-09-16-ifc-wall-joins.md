# IFC Wall Joins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author walls that meet as drawn — at every L, T and X one wall runs through the intersection and the other stops at its face, with no overlaps — and verify that geometry by reopening the IFC.

**Architecture:** Collinear model walls are grouped into *runs* (`archiagent/ifc/wall_runs.py`), so a wall that passes through a junction is one IFC entity. Each run is authored as an `IfcWallStandardCase` with an Axis line, a centred material layer carrying a priority, and an `IfcRectangleProfileDef` body; `geometry.connect_path` records the joints and `geometry.regenerate_wall_representation` trims them. `archiagent/ifc/inspect.py` predicts each trimmed footprint independently and fails the run on any mismatch, overlap or gap.

**Tech Stack:** Python 3.12+, ifcopenshell 0.8.5 (`ifcopenshell.api.geometry`, `.material`, `.validate`), shapely 2.1, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-ifc-wall-joins-design.md`

## Global Constraints

- Branch: `feature/generic-ifc-replay-blender` only.
- Tests live in `checks/`; no client drawing or its coordinates beyond the two junction points already quoted in `checks/test_degenerate_export_geometry.py` may enter the repo.
- Run tests with the repo root on `PYTHONPATH` when working in a worktree: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q`. The venv's editable install points at the main checkout.
- Never set `RelatingPriorities`/`RelatedPriorities` on a relationship: it crashes regeneration in 0.8.5 (`TypeError: 'PrioritisedLayer' object does not support item assignment`). Priority goes on `IfcMaterialLayer.Priority`.
- Blue (the wall owning an intersection) is chosen by: greater thickness, then longer line, then the smaller `(min(start, end), max(start, end))` key.
- Geometry tolerances already in the codebase: `GEOMETRY_EPS_FT = 1e-5` (`archiagent/validate.py`), `PROFILE_TOL_FT = 1e-7` (`archiagent/ifc/profile_layout.py`), `BOUNDS_TOL_M = 1e-5`, `VOLUME_REL_TOL = 1e-5`, `VOLUME_ABS_TOL_M3 = 1e-8` (`archiagent/ifc/inspect.py`). Reuse them; do not invent new ones except the area tolerance `AREA_TOL_M2 = 1e-6` introduced in Task 7.
- `FT = 0.3048`. Models are in feet; IFC is in metres.
- Commits: conventional prefix (`feat:`, `fix:`, `test:`, `docs:`), and end every commit message with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

## File Structure

| File | Responsibility |
|---|---|
| `archiagent/ifc/wall_runs.py` (new) | Group model walls into runs, decide each junction's joint, rank walls, list connections and untrimmed junctions. Pure geometry; no ifcopenshell. |
| `archiagent/ifc/author.py` (modify) | Write runs as standard-case walls, connect, regenerate, restore rectangle profiles, re-apply styles, add connection points. |
| `archiagent/ifc/profile_layout.py` (modify) | `host_name` resolves a model wall index to its run name. |
| `archiagent/ifc/identity.py` (modify) | Treat `IfcWallStandardCase` as `IfcWall` when hashing stable GlobalIds. |
| `archiagent/ifc/inspect.py` (modify) | Predict each run's trimmed footprint; check class, parametric data, connection points, overlap and coverage. |
| `archiagent/validate.py` (modify) | Drop the `joint_solids_untrimmed` warning. |
| `checks/test_wall_runs.py` (new) | Run grouping and ranking. |
| `checks/test_wall_joins_ifc.py` (new) | Authored-and-reopened geometry for the sketch cases. |
| `checks/test_export_validation.py` (modify) | New checker failures. |

---

### Task 1: Group walls into runs

**Files:**
- Create: `archiagent/ifc/wall_runs.py`
- Test: `checks/test_wall_runs.py`

**Interfaces:**
- Consumes: `archiagent.model.BuildingModel`, `archiagent.ifc.profile_layout.wall_layout`.
- Produces:
  - `WallRun(id: str, members: tuple[int, ...], start: Pt, end: Pt, thickness_ft: float, source_layer: str, source_ids: tuple[str, ...], priority: int)`
  - `RunConnection(relating: int, related: int, relating_type: str, related_type: str, point: Pt)` — indices into `RunLayout.runs`; types are `"ATSTART" | "ATEND" | "ATPATH"`.
  - `RunLayout(runs: tuple[WallRun, ...], connections: tuple[RunConnection, ...], untrimmed: tuple[Pt, ...], run_of_wall: dict[int, int])`
  - `build_runs(model) -> RunLayout`

- [ ] **Step 1: Write the failing test**

```python
# checks/test_wall_runs.py
"""Walls are grouped so a wall passing through a junction is one IFC entity."""
import unittest

from archiagent.ifc.wall_runs import build_runs
from checks.test_wall_joins_fixtures import model_with


class WallRunChecks(unittest.TestCase):
    def test_collinear_walls_chain_and_a_thickness_change_breaks_the_chain(self):
        layout = build_runs(model_with([((0, 0), (4, 0), .5), ((4, 0), (9, 0), .5)]))
        self.assertEqual([r.members for r in layout.runs], [(0, 1)])
        self.assertEqual((layout.runs[0].start, layout.runs[0].end), ((0, 0), (9, 0)))
        self.assertEqual(layout.connections, ())

        stepped = build_runs(model_with([((0, 0), (4, 0), .5), ((4, 0), (9, 0), .75)]))
        self.assertEqual([r.members for r in stepped.runs], [(0,), (1,)])
        self.assertEqual(stepped.untrimmed, ((4, 0),))
        self.assertEqual(stepped.connections, ())

    def test_t_chains_the_through_pair_and_the_stem_joins_along_it(self):
        layout = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5),
                                        ((5, 0), (5, 4), .5)]))
        self.assertEqual([r.members for r in layout.runs], [(0, 1), (2,)])
        self.assertEqual([(c.relating, c.related, c.relating_type, c.related_type, c.point)
                          for c in layout.connections], [(1, 0, "ATSTART", "ATPATH", (5, 0))])

    def test_x_chains_only_the_blue_line_and_both_green_pieces_butt_into_it(self):
        walls = [((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                 ((5, -4), (5, 0), .5), ((5, 0), (5, 4), .5)]
        layout = build_runs(model_with(walls))
        self.assertEqual([r.members for r in layout.runs], [(0, 1), (2,), (3,)])
        self.assertEqual({(c.relating, c.related, c.related_type) for c in layout.connections},
                         {(1, 0, "ATPATH"), (2, 0, "ATPATH")})
        self.assertGreater(layout.runs[0].priority, layout.runs[1].priority)

    def test_l_ranks_by_thickness_then_length_then_position(self):
        thicker = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 4), .75)]))
        self.assertGreater(thicker.runs[1].priority, thicker.runs[0].priority)
        self.assertEqual([(c.relating, c.related, c.relating_type, c.related_type)
                          for c in thicker.connections], [(0, 1, "ATEND", "ATSTART")])

        longer = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 9), .5)]))
        self.assertGreater(longer.runs[1].priority, longer.runs[0].priority)

        tied = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 5), .5)]))
        self.assertGreater(tied.runs[0].priority, tied.runs[1].priority)

    def test_grouping_does_not_depend_on_input_order(self):
        walls = [((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5), ((5, 0), (5, 4), .5)]
        first = build_runs(model_with(walls))
        shuffled = build_runs(model_with([walls[2], walls[1], walls[0]]))
        self.assertEqual([r.members for r in first.runs], [(0, 1), (2,)])
        self.assertEqual([(r.start, r.end, r.thickness_ft) for r in first.runs],
                         [(r.start, r.end, r.thickness_ft) for r in shuffled.runs])


if __name__ == "__main__":
    unittest.main()
```

Also create the shared fixture helper it imports:

```python
# checks/test_wall_joins_fixtures.py
"""Small authored-from-scratch models for wall join checks. No client data."""
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.walls import WallSeg
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult


def model_with(segments, wall_height_ft=10., **kwargs):
    """segments: [(start, end, thickness_ft), ...] in feet, already noded."""
    walls = tuple(WallSeg(a, b, t, "walls", "paired-line", "measured", (f"cad:{i}",))
                  for i, (a, b, t) in enumerate(segments))
    graph = resolve_junctions(walls, snap_in=0, extend_in=0, min_dangle_ft=0)
    assert len(graph.walls) == len(walls), "fixture segments must already be noded"
    values = dict(walls=walls, junctions=graph.junctions, unresolved=graph.unresolved,
                  spaces=(), scale=ScaleResult(12., "clear", (), 0., 0), layer_decisions=(),
                  source_path="fixture.dxf", source_sha256="b" * 64,
                  wall_height_ft=wall_height_ft, region_id="plan-a", storey_name="Level 1",
                  elevation_ft=0., scale_verified=True, footprint_verified=True,
                  symbols_verified=True)
    values.update(kwargs)
    return BuildingModel(**values)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_runs.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.ifc.wall_runs'`

- [ ] **Step 3: Write minimal implementation**

```python
# archiagent/ifc/wall_runs.py
"""Group model walls into the runs authored as single IFC walls.

A junction only joins correctly in IFC when the wall passing through it is one
entity: `connect_path` keeps a single connection per wall end, so a T stored as
two halves plus a stem loses joins and mitres the wrong pair. Runs restore the
through wall, leaving the stopping wall to butt into it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from archiagent.ifc.profile_layout import wall_layout
from archiagent.primitives import Pt

PARALLEL_TOL = 1e-7
THICKNESS_TOL_FT = 1e-9
POINT_TOL_FT = 1e-9


@dataclass(frozen=True)
class WallRun:
    id: str
    members: tuple[int, ...]
    start: Pt
    end: Pt
    thickness_ft: float
    source_layer: str
    source_ids: tuple[str, ...]
    priority: int

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)


@dataclass(frozen=True)
class RunConnection:
    relating: int
    related: int
    relating_type: str
    related_type: str
    point: Pt


@dataclass(frozen=True)
class RunLayout:
    runs: tuple[WallRun, ...]
    connections: tuple[RunConnection, ...]
    untrimmed: tuple[Pt, ...]
    run_of_wall: dict[int, int] = field(default_factory=dict)


def _direction(wall):
    length = math.dist(wall.start, wall.end)
    return ((wall.end[0]-wall.start[0])/length, (wall.end[1]-wall.start[1])/length)


def _leaving(wall, point):
    """Unit direction of `wall` leaving `point`."""
    ux, uy = _direction(wall)
    return (ux, uy) if math.dist(point, wall.start) <= POINT_TOL_FT else (-ux, -uy)


def _partners(model, point, indices):
    """Pairs of wall indices that continue straight through `point`."""
    out = []
    for position, i in enumerate(indices):
        for j in indices[position+1:]:
            a, b = model.walls[i], model.walls[j]
            if a.source_layer != b.source_layer:
                continue
            if abs(a.thickness_ft - b.thickness_ft) > THICKNESS_TOL_FT:
                continue
            (ax, ay), (bx, by) = _leaving(a, point), _leaving(b, point)
            if abs(ax*by - ay*bx) < PARALLEL_TOL and ax*bx + ay*by < 0:
                out.append((i, j))
    return out


class _Groups:
    """Union-find over model wall indices."""

    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        a, b = self.find(i), self.find(j)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _chain_ends(model, members):
    """Outer endpoints of a chain of collinear walls."""
    counts = {}
    for i in members:
        for p in (model.walls[i].start, model.walls[i].end):
            counts[p] = counts.get(p, 0) + 1
    free = sorted(p for p, n in counts.items() if n == 1)
    if len(free) != 2:  # a closed ring of collinear walls cannot occur
        raise ValueError("wall chain must have two free ends")
    return free[0], free[1]


def _line_rank(model, groups, eligible):
    """Rank key per line: thicker first, then longer, then position."""
    lines = {}
    for i in eligible:
        lines.setdefault(groups.find(i), []).append(i)
    keys = {}
    for root, members in lines.items():
        thickness = model.walls[members[0]].thickness_ft
        length = sum(model.walls[i].length_ft for i in members)
        start, end = _chain_ends(model, members)
        keys[root] = (-thickness, -length, start, end)
    order = sorted(keys, key=lambda root: keys[root])
    return {root: position for position, root in enumerate(order)}


def build_runs(model) -> RunLayout:
    _, _, profile_mapping = wall_layout(model)
    eligible = [i for i in range(len(model.walls)) if i not in profile_mapping]
    usable = set(eligible)

    lines = _Groups(len(model.walls))
    for junction in model.junctions:
        indices = [i for i in junction.wall_indices if i in usable]
        for i, j in _partners(model, junction.point, indices):
            lines.union(i, j)
    rank = _line_rank(model, lines, eligible)

    chains = _Groups(len(model.walls))
    decisions, untrimmed = [], []
    for junction in sorted(model.junctions, key=lambda j: j.point):
        indices = sorted(i for i in junction.wall_indices if i in usable)
        pairs = _partners(model, junction.point, indices)
        if len(indices) != len(junction.wall_indices):
            untrimmed.append(junction.point)          # touches a WP: profile wall
        elif len(indices) == 2 and pairs:
            chains.union(*pairs[0])
        elif len(indices) == 2:
            (ax, ay), (bx, by) = (_leaving(model.walls[i], junction.point) for i in indices)
            if abs(ax*by - ay*bx) < PARALLEL_TOL:
                untrimmed.append(junction.point)      # collinear step: IfcOpenShell ignores it
            else:
                decisions.append(("L", junction.point, indices))
        elif len(indices) == 3 and len(pairs) == 1:
            chains.union(*pairs[0])
            decisions.append(("T", junction.point, indices))
        elif len(indices) == 4 and len(pairs) == 2:
            blue = min(pairs, key=lambda pair: rank[lines.find(pair[0])])
            chains.union(*blue)
            decisions.append(("X", junction.point, indices))
        else:
            untrimmed.append(junction.point)

    members = {}
    for i in eligible:
        members.setdefault(chains.find(i), []).append(i)
    runs, run_of_wall = [], {}
    for root in sorted(members, key=lambda r: min(members[r])):
        group = sorted(members[root])
        start, end = _chain_ends(model, group)
        first = model.walls[group[0]]
        run_index = len(runs)
        # Higher priority owns an L corner. Line ranks are unique, so no two walls tie.
        priority = len(rank) - rank[lines.find(group[0])]
        runs.append(WallRun(f"W{min(group):03d}", tuple(group), start, end,
                            first.thickness_ft, first.source_layer,
                            tuple(sorted({sid for i in group for sid in model.walls[i].source_ids})),
                            priority))
        for i in group:
            run_of_wall[i] = run_index
    runs = tuple(runs)

    connections = []
    for kind, point, indices in decisions:
        involved = sorted({run_of_wall[i] for i in indices})
        if kind == "L":
            blue = max(involved, key=lambda index: runs[index].priority)
            green = next(index for index in involved if index != blue)
            connections.append(RunConnection(green, blue, _end_type(runs[green], point),
                                             _end_type(runs[blue], point), point))
        else:
            blue = next(index for index in involved
                        if _end_type(runs[index], point) == "ATPATH")
            for green in involved:
                if green != blue:
                    connections.append(RunConnection(green, blue, _end_type(runs[green], point),
                                                     "ATPATH", point))
    connections.sort(key=lambda c: (c.point, c.relating, c.related))
    return RunLayout(runs, tuple(connections), tuple(sorted(untrimmed)), run_of_wall)


def _end_type(run, point):
    if math.dist(point, run.start) <= POINT_TOL_FT:
        return "ATSTART"
    if math.dist(point, run.end) <= POINT_TOL_FT:
        return "ATEND"
    return "ATPATH"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_runs.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the whole suite for regressions**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
Expected: 253 passed, 8 skipped (new file adds 5 → 258 passed)

- [ ] **Step 6: Commit**

```bash
git add archiagent/ifc/wall_runs.py checks/test_wall_runs.py checks/test_wall_joins_fixtures.py
git commit -m "$(cat <<'EOF'
feat: group collinear walls into runs for IFC joins

A junction joins correctly only when the wall passing through it is one IFC
entity: connect_path keeps one connection per wall end. Runs chain collinear
same-layer, same-thickness walls, choose the through wall at T and X, and rank
walls (thicker, then longer, then position) so an L corner has an owner.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Author runs as standard-case parametric walls (no joins yet)

**Files:**
- Modify: `archiagent/ifc/author.py:184-235` (the profile/wall/connection block of `_author_plan`)
- Modify: `archiagent/ifc/profile_layout.py:31-33` (`host_name`)
- Modify: `archiagent/ifc/identity.py:29-32` (stable id class key)
- Test: `checks/test_wall_joins_ifc.py`

**Interfaces:**
- Consumes: `build_runs`, `WallRun`, `RunLayout` from Task 1.
- Produces: in `author.py`, `_layer_set(f, thickness_m, priority, label)` returning a cached `IfcMaterialLayerSet`; run walls named `WallRun.id`; `host_name(model, wall_index)` returns the run id for a non-profile wall.

- [ ] **Step 1: Write the failing test**

```python
# checks/test_wall_joins_ifc.py
"""Authored walls are standard-case, parametric, and meet as drawn."""
import unittest
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element

from archiagent.ifc.author import author_ifc
from checks.test_wall_joins_fixtures import model_with


def author(segments, **kwargs):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "joins.ifc"
    author_ifc(model_with(segments, **kwargs), path)
    return ifcopenshell.open(str(path)), directory


class WallJoinAuthoringChecks(unittest.TestCase):
    def test_runs_are_standard_case_with_axis_and_centred_layer(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5)])
        self.addCleanup(directory.cleanup)
        walls = f.by_type("IfcWall")
        self.assertEqual([w.Name for w in walls], ["W000"])
        self.assertEqual(walls[0].is_a(), "IfcWallStandardCase")
        identifiers = {r.RepresentationIdentifier for r in walls[0].Representation.Representations}
        self.assertEqual(identifiers, {"Body", "Axis"})
        usage = ifcopenshell.util.element.get_material(walls[0])
        self.assertEqual(usage.is_a(), "IfcMaterialLayerSetUsage")
        self.assertAlmostEqual(usage.OffsetFromReferenceLine, -.25 * .3048)
        self.assertEqual([l.LayerThickness for l in usage.ForLayerSet.MaterialLayers],
                         [.5 * .3048])
        pset = ifcopenshell.util.element.get_psets(walls[0])["ArchiAgent_Provenance"]
        self.assertEqual(pset["ModelWallIndices"], "[0, 1]")

    def test_body_is_a_rectangle_profile(self):
        f, directory = author([((0, 0), (5, 0), .5)])
        self.addCleanup(directory.cleanup)
        body = next(r for r in f.by_type("IfcWall")[0].Representation.Representations
                    if r.RepresentationIdentifier == "Body")
        profile = body.Items[0].SweptArea
        self.assertEqual(profile.is_a(), "IfcRectangleProfileDef")
        self.assertAlmostEqual(profile.XDim, 5 * .3048)
        self.assertAlmostEqual(profile.YDim, .5 * .3048)

    def test_stable_ids_ignore_the_standard_case_subtype(self):
        """An unmerged wall must keep the GlobalId it had as a plain IfcWall."""
        import ifcopenshell.guid

        from archiagent.ifc.identity import assign_stable_ids

        model = model_with([((0, 0), (5, 0), .5)])
        ids = []
        for ifc_class in ("IfcWall", "IfcWallStandardCase"):
            f = ifcopenshell.file(schema="IFC4")
            product = f.create_entity(ifc_class, GlobalId=ifcopenshell.guid.new(), Name="W000")
            pset = f.create_entity(
                "IfcPropertySet", GlobalId=ifcopenshell.guid.new(), Name="ArchiAgent_Provenance",
                HasProperties=[
                    f.create_entity("IfcPropertySingleValue", Name="SourceSHA256",
                                    NominalValue=f.create_entity("IfcText", "b" * 64)),
                    f.create_entity("IfcPropertySingleValue", Name="RegionId",
                                    NominalValue=f.create_entity("IfcText", "plan-a"))])
            f.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
                            RelatedObjects=[product], RelatingPropertyDefinition=pset)
            assign_stable_ids(f, (model,))
            ids.append(product.GlobalId)
        self.assertEqual(ids[0], ids[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py`
Expected: FAIL — walls are named `W000`/`W001` (two entities, not one run), `is_a()` is `IfcWall`, and there is no Axis representation.

- [ ] **Step 3: Write minimal implementation**

In `archiagent/ifc/author.py`, add the imports and the Plan/Axis contexts. After the existing `body = run("context.add_context", …)` line in `author_building`:

```python
    plan = run("context.add_context", f, context_type="Plan")
    axis = run("context.add_context", f, context_type="Plan",
               context_identifier="Axis", target_view="GRAPH_VIEW", parent=plan)
```

Pass `axis` through to `_author_plan(f, body, axis, building, model, presentation)` and update its signature.

Add near the top of the module:

```python
import ifcopenshell.api.material
import ifcopenshell.util.element

from archiagent.ifc.wall_runs import build_runs
```

Inside `_author_plan`, add the layer-set cache (walls with the same thickness and priority share one set):

```python
    layer_sets = {}

    def layer_set(thickness_m, priority):
        key = (round(thickness_m, 9), priority)
        if key not in layer_sets:
            label = PRESENTATION_PALETTE["IfcWall"][0]
            material_set = run("material.add_material_set", f,
                               name=f"{label} {thickness_m*1000:.0f}mm P{priority}",
                               set_type="IfcMaterialLayerSet")
            layer = run("material.add_layer", f, layer_set=material_set,
                        material=run("material.add_material", f, name=label))
            run("material.edit_layer", f, layer=layer,
                attributes={"LayerThickness": thickness_m, "Priority": priority})
            layer_sets[key] = material_set
        return layer_sets[key]
```

Replace the `for idx, w in enumerate(model.walls):` block (author.py:196-212) with runs:

```python
    layout = build_runs(model)
    wall_entities = {}
    for index, wall_run in enumerate(layout.runs):
        angle = math.atan2(wall_run.end[1]-wall_run.start[1], wall_run.end[0]-wall_run.start[0])
        length_m, thickness_m = wall_run.length_ft * FT, wall_run.thickness_ft * FT
        wall = entity("IfcWallStandardCase", wall_run.id,
                      rectangle_solid(length_m, thickness_m, height_m),
                      placement(wall_run.start[0]*FT, wall_run.start[1]*FT, angle=angle))
        run("geometry.assign_representation", f, product=wall,
            representation=f.createIfcShapeRepresentation(
                axis, "Axis", "Curve2D",
                [f.createIfcPolyline([f.createIfcCartesianPoint((0., 0.)),
                                      f.createIfcCartesianPoint((length_m, 0.))])]))
        run("material.assign_material", f, products=[wall],
            type="IfcMaterialLayerSetUsage", material=layer_set(thickness_m, wall_run.priority))
        usage = ifcopenshell.util.element.get_material(wall)
        usage.OffsetFromReferenceLine = -thickness_m / 2
        member = model.walls[wall_run.members[0]]
        provenance(wall, {"SourceLayer": wall_run.source_layer, "Detector": member.detector,
                          "SourceIds": json.dumps(list(wall_run.source_ids)),
                          "ModelWallIndices": json.dumps(list(wall_run.members)),
                          "ThicknessSource": member.thickness_source,
                          "HeightFt": model.wall_height_ft,
                          "HeightEvidence": "See storey assumptions; height is not independently verified",
                          "ThicknessIn": wall_run.thickness_ft * 12,
                          "ScaleMaxResidualIn": model.scale.max_residual_in})
        for i in wall_run.members:
            wall_entities[i] = wall
```

`rectangle_solid` already builds `sb.rectangle(...)`, which is a polyline profile. Replace its body (author.py:160-163) so runs are parametric:

```python
    def rectangle_solid(length, thickness, height):
        profile = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                                  Position=f.createIfcAxis2Placement2D(
                                      f.createIfcCartesianPoint((length/2, 0.)), None),
                                  XDim=length, YDim=thickness)
        return sb.extrude(profile, magnitude=height, extrusion_vector=(0., 0., 1.))
```

In `archiagent/ifc/profile_layout.py`, make `host_name` follow runs:

```python
def host_name(model, wall_index):
    profiles, _, mapping = wall_layout(model)
    if wall_index in mapping:
        return f"WP:{profiles[mapping[wall_index]].id}"
    from archiagent.ifc.wall_runs import build_runs
    layout = build_runs(model)
    return layout.runs[layout.run_of_wall[wall_index]].id
```

In `archiagent/ifc/identity.py`, keep GlobalIds stable across the class change, inside `assign_stable_ids`:

```python
        ifc_class = "IfcWall" if product.is_a("IfcWall") else product.is_a()
        keys[product.id()] = (ifc_class, props.get("SourceSHA256", ""),
                              props.get("RegionId", ""), product.Name or "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole suite and record which existing tests now fail**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
Expected: failures in `checks/test_semantic_ifc.py` and `checks/test_export_validation.py`, because wall names and counts changed and `inspect._expectations` still predicts per model wall. Do not fix them here; Task 6 replaces the predictor and Task 8 updates the existing expectations. Record the list in the commit message.

- [ ] **Step 6: Commit**

```bash
git add archiagent/ifc/author.py archiagent/ifc/profile_layout.py archiagent/ifc/identity.py checks/test_wall_joins_ifc.py
git commit -m "$(cat <<'EOF'
feat: author wall runs as parametric standard-case walls

Each run becomes one IfcWallStandardCase with an Axis line, a rectangle profile
body and a material layer centred on the axis and carrying its join priority.
Stable GlobalIds key the subtype as IfcWall so unmerged walls keep their IDs.

Export validation and the semantic IFC expectations still predict per model
wall and fail until the predictor follows runs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Connect and regenerate — the joints

**Files:**
- Modify: `archiagent/ifc/author.py` (`_author_plan`, the connection block at author.py:214-234)
- Test: `checks/test_wall_joins_ifc.py`

**Interfaces:**
- Consumes: `RunLayout.connections` from Task 1; `wall_entities` from Task 2.
- Produces: `_footprint(wall)` helper in `checks/test_wall_joins_fixtures.py` returning a shapely `Polygon` of a wall's Body outline in world feet, used by Tasks 3–7 tests.

- [ ] **Step 1: Write the failing test**

Add to `checks/test_wall_joins_fixtures.py`:

```python
def footprint(wall):
    """World-coordinate XY outline of a wall's Body, in feet."""
    import ifcopenshell.util.placement as placement
    import numpy as np
    from shapely.geometry import Polygon

    matrix = placement.get_local_placement(wall.ObjectPlacement)
    body = next(r for r in wall.Representation.Representations
                if r.RepresentationIdentifier == "Body")
    item = body.Items[0]
    while item.is_a("IfcBooleanResult"):
        item = item.FirstOperand
    profile = item.SweptArea
    if profile.is_a("IfcRectangleProfileDef"):
        x, y = profile.Position.Location.Coordinates if profile.Position else (0., 0.)
        points = [(x-profile.XDim/2, y-profile.YDim/2), (x+profile.XDim/2, y-profile.YDim/2),
                  (x+profile.XDim/2, y+profile.YDim/2), (x-profile.XDim/2, y+profile.YDim/2)]
    else:
        curve = profile.OuterCurve
        points = (list(curve.Points.CoordList) if curve.is_a("IfcIndexedPolyCurve")
                  else [p.Coordinates for p in curve.Points])
    world = [(matrix @ np.array([p[0], p[1], 0., 1.]))[:2] / .3048 for p in points]
    return Polygon(world)
```

Add to `checks/test_wall_joins_ifc.py`:

```python
from shapely.geometry import box
from shapely.ops import unary_union

from checks.test_wall_joins_fixtures import footprint


class WallJointGeometryChecks(unittest.TestCase):
    def assert_matches_sketch(self, f, expected):
        walls = f.by_type("IfcWall")
        shapes = [footprint(w) for w in walls]
        overlap = sum(a.intersection(b).area for i, a in enumerate(shapes) for b in shapes[i+1:])
        self.assertLess(overlap, 1e-9, "walls must not overlap")
        self.assertLess(unary_union(shapes).symmetric_difference(expected).area, 1e-9)

    def test_l_corner_is_butted_with_the_thicker_wall_owning_it(self):
        # green (0,0)-(5,0) 0.5ft stops at blue's face; blue (5,0)-(5,-4) 0.75ft
        # runs to green's outer face.
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.25, 5-.375, .25), box(5-.375, -4, 5+.375, .25)]))

    def test_t_stem_stops_at_the_through_wall_face(self):
        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, 0), (5, -4), .5)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.375, 10, .375), box(5-.25, -4, 5+.25, -.375)]))

    def test_x_splits_the_thinner_wall_around_the_thicker_one(self):
        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, -4), (5, 0), .5), ((5, 0), (5, 4), .5)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.375, 10, .375), box(5-.25, -4, 5+.25, -.375), box(5-.25, .375, 5+.25, 4)]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py -k JointGeometry`
Expected: FAIL on "walls must not overlap" — untrimmed rectangles still overlap at every junction.

- [ ] **Step 3: Write minimal implementation**

Replace the junction loop in `_author_plan` (author.py:214-234) with run connections, then regenerate:

```python
    for connection in layout.connections:
        run("geometry.connect_path", f,
            relating_element=wall_entities[layout.runs[connection.relating].members[0]],
            related_element=wall_entities[layout.runs[connection.related].members[0]],
            relating_connection=connection.relating_type,
            related_connection=connection.related_type)

    joined = {c.relating for c in layout.connections} | {c.related for c in layout.connections}
    for index in sorted(joined):
        wall = wall_entities[layout.runs[index].members[0]]
        run("geometry.regenerate_wall_representation", f, wall=wall)
        body = next(r for r in wall.Representation.Representations
                    if r.RepresentationIdentifier == "Body")
        presentation.assign("IfcWall", body.Items[0])
```

Delete the now-unused `connection_type` helper and the `connections` set above it; `itertools` stays in use for nothing else, so drop that import too if no other use remains.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py`
Expected: PASS (6 tests) — the three sketch cases match exactly with zero overlap.

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/author.py checks/test_wall_joins_ifc.py checks/test_wall_joins_fixtures.py
git commit -m "$(cat <<'EOF'
feat: trim wall joints with IfcOpenShell path connections

Runs are connected end-to-end at corners and end-to-path at T and X, then
regenerated so the owning wall keeps the intersection and the other stops at
its face. Presentation style is re-applied because regeneration replaces the
styled item. Matches the reviewed sketch exactly with zero overlap.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Restore rectangle profiles and record joint provenance

**Files:**
- Modify: `archiagent/ifc/author.py` (after the regeneration loop from Task 3; storey provenance at author.py:132-143)
- Test: `checks/test_wall_joins_ifc.py`

**Interfaces:**
- Consumes: `layout` from Task 1, the regeneration loop from Task 3.
- Produces: `_restore_rectangle(f, wall) -> bool` in `author.py`, returning whether the Body became an `IfcRectangleProfileDef`.

- [ ] **Step 1: Write the failing test**

```python
    def test_joined_walls_keep_rectangle_profiles_and_report_exceptions(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        self.addCleanup(directory.cleanup)
        for wall in f.by_type("IfcWall"):
            body = next(r for r in wall.Representation.Representations
                        if r.RepresentationIdentifier == "Body")
            self.assertEqual(body.Items[0].SweptArea.is_a(), "IfcRectangleProfileDef")
        storey = ifcopenshell.util.element.get_psets(
            f.by_type("IfcBuildingStorey")[0])["ArchiAgent_Provenance"]
        self.assertEqual(storey["JointGeometry"], "Butt joints; intersection owned by one wall")
        self.assertEqual(storey["NonRectangularWallProfiles"], 0)
        self.assertEqual(storey["UntrimmedJunctionsJSON"], "[]")

    def test_angled_joint_keeps_a_polygon_and_is_counted(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (8, 3), .5)])
        self.addCleanup(directory.cleanup)
        storey = ifcopenshell.util.element.get_psets(
            f.by_type("IfcBuildingStorey")[0])["ArchiAgent_Provenance"]
        self.assertGreater(storey["NonRectangularWallProfiles"], 0)

    def test_voids_styles_and_profile_walls_survive_regeneration(self):
        import ifcopenshell.validate

        from archiagent.geometry.profiles import WallProfile
        from archiagent.semantic import Opening, SymbolInstance

        profile = WallProfile("wp-test", ((6, 2), (6, 6), (6.5, 6), (6.5, 3), (8, 3), (8, 2), (6, 2)),
                              (), "walls", ("cad:p",), "native-wall-face", "accepted_by_rule")
        f, directory = author(
            [((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)],
            symbols=(SymbolInstance("d1", "door", (2.5, 0.), 2., .5, source_ids=("cad:d",),
                                    evidence="block", confidence=1.),),
            openings=(Opening("o1", "door", 0, (1.5, 0.), (3.5, 0.), 7., symbol_id="d1",
                              assumed_height=False),),
            wall_profiles=(profile,))
        self.addCleanup(directory.cleanup)
        host = f.by_type("IfcOpeningElement")[0].VoidsElements[0].RelatingBuildingElement
        self.assertEqual(host.Name, "W000")
        outline_wall = next(w for w in f.by_type("IfcWall") if w.Name.startswith("WP:"))
        self.assertEqual(outline_wall.is_a(), "IfcWall")
        styled = {item.Item for item in f.by_type("IfcStyledItem")}
        for wall in f.by_type("IfcWall"):
            body = next(r for r in wall.Representation.Representations
                        if r.RepresentationIdentifier == "Body")
            self.assertIn(body.Items[0], styled)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f, logger, express_rules=True)
        self.assertEqual([s for s in logger.statements
                          if str(s.get("level", "")).lower() in {"error", "critical"}], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py -k rectangle_profiles`
Expected: FAIL — regeneration leaves `IfcArbitraryClosedProfileDef`, and the storey has no `NonRectangularWallProfiles`.

- [ ] **Step 3: Write minimal implementation**

Add to `author.py`:

```python
def _restore_rectangle(f, wall):
    """Rewrite a regenerated outline as a rectangle profile when it still is one.

    Regeneration always emits a polyline profile, even for a butt joint between
    perpendicular walls, which drops the wall's parametric length/thickness.
    """
    body = next(r for r in wall.Representation.Representations
                if r.RepresentationIdentifier == "Body")
    item = body.Items[0]
    if not item.is_a("IfcExtrudedAreaSolid"):
        return False
    profile = item.SweptArea
    if not profile.is_a("IfcArbitraryClosedProfileDef"):
        return profile.is_a("IfcRectangleProfileDef")
    curve = profile.OuterCurve
    points = ([tuple(p) for p in curve.Points.CoordList] if curve.is_a("IfcIndexedPolyCurve")
              else [tuple(p.Coordinates) for p in curve.Points])
    if points and math.dist(points[0], points[-1]) < 1e-9:
        points = points[:-1]
    xs, ys = {round(p[0], 9) for p in points}, {round(p[1], 9) for p in points}
    if len(points) != 4 or len(xs) != 2 or len(ys) != 2:
        return False
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    item.SweptArea = f.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA",
        Position=f.createIfcAxis2Placement2D(
            f.createIfcCartesianPoint(((x0+x1)/2, (y0+y1)/2)), None),
        XDim=x1-x0, YDim=y1-y0)
    ifcopenshell.util.element.remove_deep2(f, profile)
    return True
```

Import `ifcopenshell.util.element` is already added in Task 2. In the regeneration loop from Task 3, count the exceptions:

```python
    polygons = 0
    for index in sorted(joined):
        wall = wall_entities[layout.runs[index].members[0]]
        run("geometry.regenerate_wall_representation", f, wall=wall)
        polygons += 0 if _restore_rectangle(f, wall) else 1
        body = next(r for r in wall.Representation.Representations
                    if r.RepresentationIdentifier == "Body")
        presentation.assign("IfcWall", body.Items[0])
```

`polygons` and `layout` are computed before `provenance(storey, …)` runs, so move the storey provenance call to after this loop and replace its joint entries:

```python
        "JointGeometry": "Butt joints; intersection owned by one wall",
        "UntrimmedJunctionsJSON": json.dumps([list(p) for p in layout.untrimmed]),
        "NonRectangularWallProfiles": polygons,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/author.py checks/test_wall_joins_ifc.py
git commit -m "$(cat <<'EOF'
feat: keep rectangle profiles after joint regeneration

Regeneration emits a polyline profile even when a butt joint leaves the outline
rectangular, losing the wall's parametric length and thickness. Rectangular
outlines are rewritten as IfcRectangleProfileDef; the rest are counted in the
storey provenance alongside the untrimmed junctions.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Connection point geometry

**Files:**
- Modify: `archiagent/ifc/author.py` (after the regeneration loop)
- Test: `checks/test_wall_joins_ifc.py`

**Interfaces:**
- Consumes: `layout.connections`, the regenerated walls.
- Produces: nothing new; sets `IfcRelConnectsPathElements.ConnectionGeometry`.

- [ ] **Step 1: Write the failing test**

```python
    def test_every_connection_carries_its_junction_point(self):
        import ifcopenshell.util.placement as placement
        import numpy as np

        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, 0), (5, -4), .5)])
        self.addCleanup(directory.cleanup)
        relationships = f.by_type("IfcRelConnectsPathElements")
        self.assertEqual(len(relationships), 1)
        for relationship in relationships:
            geometry = relationship.ConnectionGeometry
            self.assertEqual(geometry.is_a(), "IfcConnectionPointGeometry")
            for element, point in ((relationship.RelatingElement, geometry.PointOnRelatingElement),
                                   (relationship.RelatedElement, geometry.PointOnRelatedElement)):
                matrix = placement.get_local_placement(element.ObjectPlacement)
                world = (matrix @ np.array([*point.Coordinates[:2], 0., 1.]))[:2] / .3048
                self.assertAlmostEqual(world[0], 5, places=6)
                self.assertAlmostEqual(world[1], 0, places=6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py -k junction_point`
Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'is_a'` — `ConnectionGeometry` is empty.

- [ ] **Step 3: Write minimal implementation**

Keep the relationship returned by `connect_path` so it can be completed after regeneration. Change the connection loop from Task 3 to collect them:

```python
    relationships = []
    for connection in layout.connections:
        relationships.append((connection, run("geometry.connect_path", f,
            relating_element=wall_entities[layout.runs[connection.relating].members[0]],
            related_element=wall_entities[layout.runs[connection.related].members[0]],
            relating_connection=connection.relating_type,
            related_connection=connection.related_type)))
```

After the regeneration loop, which can move placements:

```python
    import ifcopenshell.util.placement

    def local_point(element, point):
        inverse = numpy.linalg.inv(
            ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement))
        local = inverse @ numpy.array([point[0]*FT, point[1]*FT, z, 1.])
        return f.createIfcCartesianPoint(tuple(float(v) for v in local[:3]))

    for connection, relationship in relationships:
        if relationship is None:
            continue
        relationship.ConnectionGeometry = f.create_entity(
            "IfcConnectionPointGeometry",
            PointOnRelatingElement=local_point(relationship.RelatingElement, connection.point),
            PointOnRelatedElement=local_point(relationship.RelatedElement, connection.point))
```

Add `import numpy` and `import ifcopenshell.util.placement` to the module imports at the top of `author.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_wall_joins_ifc.py`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/author.py checks/test_wall_joins_ifc.py
git commit -m "$(cat <<'EOF'
feat: record the junction point on every wall connection

Each IfcRelConnectsPathElements carries an IfcConnectionPointGeometry holding
the axis crossing in both walls' own coordinates. Written after regeneration,
which may move a wall's placement.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Predict trimmed run geometry in the checker

**Files:**
- Modify: `archiagent/ifc/inspect.py:60-142` (`_expectations`)
- Test: `checks/test_export_validation.py`

**Interfaces:**
- Consumes: `build_runs`, `RunLayout` (Task 1).
- Produces: `run_footprints(layout, model) -> dict[int, shapely.Polygon]` in `archiagent/ifc/wall_runs.py`, keyed by run index, in feet. Used by `inspect._expectations` and by Task 7's coverage check.

- [ ] **Step 1: Write the failing test**

```python
    def test_joined_walls_pass_reopened_validation(self):
        from checks.test_wall_joins_fixtures import model_with
        model = model_with([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        path = Path(self.directory.name) / "joined.ifc"
        author_ifc(model, path)
        report = validate_export(path, (model,))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["counts"]["IfcWall"], {"expected": 2, "actual": 2})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_export_validation.py -k joined_walls`
Expected: FAIL with `solid_volume_mismatch` and `missing_product`/`unexpected_product` — the predictor still expects one untrimmed rectangle per model wall, named `W000`, `W001`.

- [ ] **Step 3: Write minimal implementation**

Add to `archiagent/ifc/wall_runs.py`:

```python
def run_footprints(layout, model):
    """Trimmed XY outline of every run, in feet.

    A run is its rectangle, extended at an L end to the far face of the wall it
    owns, minus the rectangles of the walls it butts into.
    """
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    def rectangle(index, extra_start=0., extra_end=0.):
        wall_run = layout.runs[index]
        ux = (wall_run.end[0]-wall_run.start[0]) / wall_run.length_ft
        uy = (wall_run.end[1]-wall_run.start[1]) / wall_run.length_ft
        start = (wall_run.start[0]-ux*extra_start, wall_run.start[1]-uy*extra_start)
        end = (wall_run.end[0]+ux*extra_end, wall_run.end[1]+uy*extra_end)
        return LineString((start, end)).buffer(wall_run.thickness_ft/2, cap_style=2)

    extensions = {index: [0., 0.] for index in range(len(layout.runs))}
    for connection in layout.connections:
        if connection.related_type == "ATPATH":
            continue  # T and X: the owning wall is untouched
        green = layout.runs[connection.relating]
        end = 0 if connection.related_type == "ATSTART" else 1
        extensions[connection.related][end] = max(extensions[connection.related][end],
                                                  green.thickness_ft/2)
    shapes = {index: rectangle(index, *extensions[index]) for index in range(len(layout.runs))}
    for connection in layout.connections:
        shapes[connection.relating] = shapes[connection.relating].difference(
            shapes[connection.related])
    return {index: shape for index, shape in shapes.items()}
```

In `inspect.py`, replace the per-model-wall loop inside `_expectations` (inspect.py:87-106) with runs:

```python
        from archiagent.ifc.wall_runs import build_runs, run_footprints
        layout = build_runs(model)
        shapes = run_footprints(layout, model)
        for index, wall_run in enumerate(layout.runs):
            if any(i in mapping for i in wall_run.members):
                continue
            shape = shapes[index]
            length = wall_run.length_ft
            ux = (wall_run.end[0]-wall_run.start[0]) / length
            uy = (wall_run.end[1]-wall_run.start[1]) / length
            cuts = []
            for op in model.openings:
                if op.host_wall_index not in wall_run.members:
                    continue
                a, b = sorted((p[0]-wall_run.start[0])*ux + (p[1]-wall_run.start[1])*uy
                              for p in (op.start, op.end))
                a = 0.0 if a <= GEOMETRY_EPS_FT else a
                b = length if b >= length-GEOMETRY_EPS_FT else b
                cuts.append(box(a, op.sill_ft, b, op.sill_ft+op.height_ft))
            section = box(0, 0, length, model.wall_height_ft).difference(unary_union(cuts))
            if section.is_empty:
                raise ValueError(f"wall run {wall_run.id} has no material after opening subtraction")
            _, zlo, _, zhi = section.bounds
            void_area = box(0, 0, length, model.wall_height_ft).area - section.area
            volume = shape.area*model.wall_height_ft - void_area*wall_run.thickness_ft
            bounds = shape.bounds
            add("IfcWall", wall_run.id, model, volume,
                (bounds[0]*FT, bounds[1]*FT, (z+zlo)*FT, bounds[2]*FT, bounds[3]*FT, (z+zhi)*FT))
```

Add the imports `from archiagent.validate import GEOMETRY_EPS_FT` (already present from commit `95041cd`) and keep `box`/`unary_union` which are already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_export_validation.py`
Expected: PASS for the new test; the pre-existing tests in this file may still fail until Task 8 updates their expected volumes.

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/wall_runs.py archiagent/ifc/inspect.py checks/test_export_validation.py
git commit -m "$(cat <<'EOF'
feat: predict trimmed run geometry in reopened-IFC validation

Expectations follow wall runs and their butt joints: a run is its rectangle,
extended where it owns a corner, minus the walls it butts into, less its
opening voids. Exact volume and bounds checks are kept.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Parametric, overlap and coverage checks

**Files:**
- Modify: `archiagent/ifc/inspect.py` (`validate_export`, after the per-product loop)
- Test: `checks/test_export_validation.py`

**Interfaces:**
- Consumes: `build_runs`, `run_footprints`, the report structure of `validate_export`.
- Produces: error codes `wall_class_mismatch`, `wall_parametric_data_missing`, `wall_profile_not_parametric`, `connection_geometry_mismatch`, `wall_overlap`, `wall_coverage_mismatch`; module constant `AREA_TOL_M2 = 1e-6`.

- [ ] **Step 1: Write the failing test**

```python
    def _joined(self):
        from checks.test_wall_joins_fixtures import model_with
        model = model_with([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        path = Path(self.directory.name) / "mutate.ifc"
        author_ifc(model, path)
        return model, path

    def test_downgraded_class_and_lost_parametric_data_fail(self):
        model, path = self._joined()
        f = ifcopenshell.open(str(path))
        wall = f.by_type("IfcWall")[0]
        f.create_entity("IfcWall", **{k: v for k, v in wall.get_info().items()
                                      if k not in {"id", "type"}})
        f.remove(wall)
        f.write(str(path))
        codes = {e["code"] for e in validate_export(path, (model,))["errors"]}
        self.assertIn("wall_class_mismatch", codes)

    def test_polygon_profile_and_moved_connection_point_fail(self):
        model, path = self._joined()
        f = ifcopenshell.open(str(path))
        item = next(r for r in f.by_type("IfcWall")[0].Representation.Representations
                    if r.RepresentationIdentifier == "Body").Items[0]
        rectangle = item.SweptArea
        item.SweptArea = f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA",
            OuterCurve=f.createIfcPolyline([
                f.createIfcCartesianPoint((0., 0.)), f.createIfcCartesianPoint((1., 0.)),
                f.createIfcCartesianPoint((1., 1.))]))
        f.write(str(path))
        codes = {e["code"] for e in validate_export(path, (model,))["errors"]}
        self.assertIn("wall_profile_not_parametric", codes)

        f = ifcopenshell.open(str(path))
        relationship = f.by_type("IfcRelConnectsPathElements")[0]
        relationship.ConnectionGeometry = None
        f.write(str(path))
        codes = {e["code"] for e in validate_export(path, (model,))["errors"]}
        self.assertIn("connection_geometry_mismatch", codes)

    def test_duplicated_wall_is_reported_as_overlap(self):
        model, path = self._joined()
        f = ifcopenshell.open(str(path))
        wall = f.by_type("IfcWall")[0]
        import ifcopenshell.util.element
        ifcopenshell.util.element.copy_deep(f, wall)
        f.write(str(path))
        codes = {e["code"] for e in validate_export(path, (model,))["errors"]}
        self.assertTrue({"wall_overlap", "duplicate_product", "entity_count_mismatch"} & codes)

    def test_missing_wall_is_reported_as_a_coverage_gap(self):
        model, path = self._joined()
        f = ifcopenshell.open(str(path))
        wall = f.by_type("IfcWall")[1]
        for inverse in list(f.get_inverse(wall)):
            f.remove(inverse)
        f.remove(wall)
        f.write(str(path))
        codes = {e["code"] for e in validate_export(path, (model,))["errors"]}
        self.assertIn("wall_coverage_mismatch", codes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_export_validation.py -k "class or parametric or overlap or coverage"`
Expected: FAIL — those codes do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add to `inspect.py` after `BOUNDS_TOL_M`:

```python
AREA_TOL_M2 = 1e-6
```

Add a helper and call it from `validate_export` just before `report["passed"] = not report["errors"]`:

```python
def _check_joins(f, models, error):
    """Verify the parametric wall structure, and that solids tile the model."""
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    for model in models:
        from archiagent.ifc.wall_runs import build_runs, run_footprints
        layout = build_runs(model)
        shapes = run_footprints(layout, model)
        by_name = {p.Name: p for p in f.by_type("IfcWall")
                   if _product_scope(p) == _scope(model)}
        footprints = {}
        for index, wall_run in enumerate(layout.runs):
            product = by_name.get(wall_run.id)
            if product is None:
                continue
            label = f"IfcWall #{product.id()} {product.Name}"
            if not product.is_a("IfcWallStandardCase"):
                error("wall_class_mismatch", label, "a straight wall run must be IfcWallStandardCase")
            usage = ifcopenshell.util.element.get_material(product)
            identifiers = {r.RepresentationIdentifier for r in product.Representation.Representations}
            thickness = wall_run.thickness_ft*FT
            if (not usage or not usage.is_a("IfcMaterialLayerSetUsage") or {"Axis", "Body"} - identifiers
                    or abs(sum(l.LayerThickness for l in usage.ForLayerSet.MaterialLayers) - thickness) > BOUNDS_TOL_M
                    or abs(usage.OffsetFromReferenceLine + thickness/2) > BOUNDS_TOL_M):
                error("wall_parametric_data_missing", label,
                      "run needs Axis and Body representations and a layer set centred on its axis")
            item = next(r for r in product.Representation.Representations
                        if r.RepresentationIdentifier == "Body").Items[0]
            rectangular = len(shapes[index].exterior.coords) == 5
            if rectangular and not item.SweptArea.is_a("IfcRectangleProfileDef"):
                error("wall_profile_not_parametric", label,
                      "a rectangular run must use IfcRectangleProfileDef")
            try:
                shape = ifcopenshell.geom.create_shape(settings, product)
                verts = list(shape.geometry.verts)
                faces = list(shape.geometry.faces)
                triangles = [Polygon([(verts[i*3]/FT, verts[i*3+1]/FT) for i in faces[t:t+3]])
                             for t in range(0, len(faces), 3)]
                footprints[wall_run.id] = unary_union([t for t in triangles if t.is_valid and t.area > 0])
            except Exception as exc:
                error("solid_geometry_failed", label, exc)

        allowance = unary_union([Polygon(((p[0]-s, p[1]-s), (p[0]+s, p[1]-s),
                                          (p[0]+s, p[1]+s), (p[0]-s, p[1]+s)))
                                 for p in layout.untrimmed
                                 for s in (max((r.thickness_ft for r in layout.runs), default=0.),)])
        names = sorted(footprints)
        tree = STRtree([footprints[n] for n in names])
        for position, name in enumerate(names):
            for other in tree.query(footprints[name]):
                other = int(other)
                if other <= position:
                    continue
                overlap = footprints[name].intersection(footprints[names[other]]).difference(allowance)
                if overlap.area*FT*FT > AREA_TOL_M2:
                    error("wall_overlap", f"{name}/{names[other]}",
                          f"walls share {overlap.area*FT*FT:.3g}m2 outside any untrimmed junction")
        ideal = unary_union([shapes[i] for i in range(len(layout.runs))])
        got = unary_union(list(footprints.values()))
        difference = got.symmetric_difference(ideal).difference(allowance)
        if difference.area*FT*FT > AREA_TOL_M2:
            error("wall_coverage_mismatch", _scope(model)[0] or "model",
                  f"authored walls differ from the model wall area by {difference.area*FT*FT:.3g}m2")

    for relationship in f.by_type("IfcRelConnectsPathElements"):
        label = f"IfcRelConnectsPathElements #{relationship.id()}"
        geometry = relationship.ConnectionGeometry
        if geometry is None or not geometry.is_a("IfcConnectionPointGeometry"):
            error("connection_geometry_mismatch", label, "connection must carry its junction point")
            continue
        points = []
        for element, point in ((relationship.RelatingElement, geometry.PointOnRelatingElement),
                               (relationship.RelatedElement, geometry.PointOnRelatedElement)):
            matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
            coordinates = list(point.Coordinates) + [0.0, 0.0]
            points.append((matrix @ numpy.array([coordinates[0], coordinates[1], coordinates[2], 1.]))[:3])
        if math.dist(points[0][:2], points[1][:2]) > BOUNDS_TOL_M:
            error("connection_geometry_mismatch", label,
                  "the two connection points do not coincide in world coordinates")
```

Add `import numpy` and `import ifcopenshell.util.placement` to `inspect.py`, and call the helper inside `validate_export`:

```python
    _check_joins(f, models, error)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q checks/test_export_validation.py`
Expected: PASS for the new tests.

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/inspect.py checks/test_export_validation.py
git commit -m "$(cat <<'EOF'
feat: verify parametric walls, overlaps and coverage on reopen

Every run must be a standard-case wall with an axis, a centred layer set and a
rectangle profile where its outline is rectangular; every connection must carry
its junction point. Authored wall solids must tile the model wall area without
overlapping outside a recorded untrimmed junction.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Update existing expectations, drop the stale warning, verify on the drawings

**Files:**
- Modify: `archiagent/validate.py:96-98` (remove `joint_solids_untrimmed`)
- Modify: `checks/test_semantic_ifc.py` (wall volume and host name), `checks/test_degenerate_export_geometry.py` (door-host expectation key), any other failing existing test
- Create: `docs/superpowers/reports/2026-09-16-wall-joins-verification.md`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: the verification record.

- [ ] **Step 1: Remove the stale warning and run the full suite to list what fails**

In `archiagent/validate.py`, delete:

```python
    if model.junctions:
        add("warn", "junctions", "joint_solids_untrimmed",
            "IFC path relationships connect separate sweeps; physical overlaps are not trimmed")
```

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
Expected: a short list of failures in `checks/test_semantic_ifc.py` and `checks/test_degenerate_export_geometry.py`.

- [ ] **Step 2: Update each failing expectation with its reason**

In `checks/test_semantic_ifc.py::test_ifc_voids_holes_types_provenance_and_elevation`, the fixture's four walls now butt at four corners, so the host wall is shorter and the assertion becomes:

```python
    # W000 now stops at the corner walls it butts into, so its volume is the
    # trimmed footprint (12 - 0.25 - 0.25 long) times the height, less the door.
    assert ifcopenshell.util.shape.get_volume(wall_shape.geometry) == pytest.approx(
        ((12 - .5) * .5 * 10 - 3 * .5 * 7) * FT ** 3)
```

In `checks/test_degenerate_export_geometry.py::test_door_filling_its_whole_host_keeps_only_the_lintel`, the expectation key is the run id:

```python
        from archiagent.ifc.wall_runs import build_runs
        layout = build_runs(model)
        name = layout.runs[layout.run_of_wall[door.host_wall_index]].id
        bounds = expected[("IfcWall", name, _scope(model))]["bounds_m"]
```

- [ ] **Step 3: Run the whole suite**

Run: `PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
Expected: all pass (253 baseline + the new tests), 8 skipped.

- [ ] **Step 4: Re-run all ten sample drawings**

```bash
S=/tmp/wall-joins; D=/Users/adityamathur/Desktop/blender-experiment/input-floorplans/dxf
run() { f=$1; upf=$2; shift 2; n=$(basename "$f" .dxf); extra=(); [ "$upf" != - ] && extra=(--units-per-foot "$upf")
  out=$(PYTHONPATH=$PWD .venv/bin/python -m archiagent --dxfFilePath "$D/$f" --outputDir "$S/$n" \
        --walls "$@" --no_vision "${extra[@]}" 2>&1); code=$?
  echo "[$n] exit=$code | $(echo "$out" | grep -E '^\s+(walls|status|issues)|^error' | tr -s ' ' | tr '\n' '|')"
  PYTHONPATH=$PWD .venv/bin/python -c "
import json,sys,glob
p=glob.glob(sys.argv[1]+'/*.report.json')
v=json.load(open(p[0]))['ifc_validation'] if p else 'no report'
print('   validation:', v if isinstance(v,str) else f'passed={v[\"passed\"]} errors={len(v[\"errors\"])}')" "$S/$n"
}
run "Jiju_dxf/MR RAJEEV JI TWANI JI.dxf" - WALL
run "Jiju_dxf/SANJANA SURESH JI.dxf" - "AKDA WALL"
run "VINAYAK APARTMENTS.dxf" - walls "NEW WALLS"
run "Floor Plan.dxf" 12 0 WALLS
run "PLAN.dxf" 12 WALL
run "Jiju_dxf/M.r Premg Agarwal  Baglow 90x50.dxf" - WALL
run "Jiju_dxf/SANJANA Giriraj Ji plan for structures.dxf" - wall
run "Jiju_dxf/MB Panwar JI Revision 2.dxf" - Wall WALLS
run "Manoj JI Ladnu shyam nagar plumbing.dxf" - wall "boundary wall"
run "Jiju_dxf/Aiims Road 3BHK Flats-vk.dxf" - BDC-WALLS
```

Expected: 9 of 10 exit 0 with `passed=True`; `Floor Plan` still fails at "wall 104 partially overlaps accepted wall profiles" (known, out of scope).

- [ ] **Step 5: Record the verification**

Write `docs/superpowers/reports/2026-09-16-wall-joins-verification.md` containing, for every drawing: exit code, validation result, wall run count, untrimmed junction count, non-rectangular profile count, and the reported overlap area. Read the last three from each output's `.report.json` and the storey provenance.

- [ ] **Step 6: Commit**

```bash
git add archiagent/validate.py checks docs/superpowers/reports/2026-09-16-wall-joins-verification.md
git commit -m "$(cat <<'EOF'
test: update expectations for butt-jointed walls and record verification

Wall solids are now trimmed at joints, so the fixture host wall's volume is its
trimmed footprint and expectation keys follow run ids. The joint_solids_untrimmed
warning no longer applies. Records the ten-drawing verification run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Notes for the executor

- **If `regenerate_wall_representation` leaves a wall unchanged**, check that it has an Axis representation, an `IfcMaterialLayerSetUsage`, and that the file has a `Plan/Axis/GRAPH_VIEW` context. Without all three it returns silently.
- **If a T comes out mitred**, the through wall was written as two entities. `build_runs` must chain that pair; check `_partners` tolerances.
- **If two walls at one point lose a connection**, more than one connection was created on a single wall end. Only one is allowed; the second deletes the first.
- **Do not** set priorities on the relationship.
