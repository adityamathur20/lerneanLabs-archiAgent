# Element-level LLM stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the model qualify each wall, region and wall gap individually (by id, never by coordinate), and turn its classes into heights, voids, railings, parapets and openings in a schema-valid IFC.

**Architecture:** Local code proposes candidates with stable ids and renders each floor as numbered tiles; one vision call per tile returns a verdict and a class per id; local code reconciles (feedback > model >= floor > deterministic), validates, and builds. P0 (labels, voids, railings, gap windows, feedback JSON) is already on `feat/semantic-floor-attributes`; this plan builds P1-P4 on top of it.

**Tech Stack:** Python 3.14, shapely 2, matplotlib (Agg) for renders, ifcopenshell, the existing `LLMClient.classify_json_vision`.

**Spec:** `docs/superpowers/specs/2026-10-15-element-level-llm-stage-design.md` (approved 2026-10-16, decisions in its section 10).

## Global Constraints

- The model never returns coordinates; an id not in the request is dropped (same rule as `WallAdjudicator`).
- The stage is an improvement, never a dependency: any provider, schema or render failure leaves the deterministic decision and adds an `Issue`, never raises.
- Cost ceiling: $1 to $1.5 per floor-plan sheet; `MAX_IMAGES_PER_FLOOR = 8` by default, overridable by `--element-stage-max-images`.
- Reconcile precedence: feedback for this drawing hash > model verdict at or above `ELEMENT_CONFIDENCE_FLOOR = 0.70` > deterministic. A deterministic *accept* is overturned only at `OVERRIDE_CONFIDENCE = 0.85`.
- `IfcRailing` and `IfcWall` with `PredefinedType=PARAPET` are allowed; every export must still pass `validate_export`.
- Feedback is internal: nothing in this plan adds per-element verdicts to anything a customer sees.
- Wall classes and heights (my rulings, owner may change): `exterior`, `interior`, `partition` = storey height; `parapet` = 3.5 ft; `railing` = 3.5 ft; `site_wall` = 6.0 ft; `pool_edge` = 1.5 ft; `retaining` = storey height; `not_a_wall` = rejected.
- Every new LLM call is tagged with a usage purpose (`element-walls`, `element-regions`, `element-gaps`) so the token ledger reports it.

## Review Focus

1. A floor with no ambiguous or accepted candidates: the stage makes no call and reports nothing (test in Task 5).
2. A model reply naming every candidate `not_a_wall`: accepted walls survive unless confidence >= 0.85, so a careless reply cannot empty a floor (test in Task 5).
3. A wall split by junction repair or profile fit keeps its class (test in Task 1).
4. A very large floor (South Face sheet regions up to 160 ft): tiles cap at `MAX_IMAGES_PER_FLOOR`; uncovered candidates keep deterministic decisions and are counted in an issue (test in Task 3).
5. Replay of a frozen interpretation reproduces classes without calling the model (test in Task 5).

---

## Phase P1: walls

### Task 1: A wall carries its class through the pipeline

**Files:**
- Modify: `archiagent/geometry/walls.py` (WallSeg)
- Test: `checks/test_wall_classes.py`

**Interfaces:**
- Produces: `WallSeg.wall_class: str = ""` (last field). Empty means unclassified (storey height, plain wall).

- [ ] **Step 1: Write the failing tests**

```python
def test_wall_class_survives_junction_repair():
    a = WallSeg((0, 0), (10, 0), 0.75, "W", "paired-line", "measured", ("a",), wall_class="site_wall")
    b = WallSeg((5, -5), (5, 5), 0.75, "W", "paired-line", "measured", ("b",))
    graph = resolve_junctions((a, b), snap_in=0, extend_in=0, min_dangle_ft=0)
    assert {w.wall_class for w in graph.walls if "a" in w.source_ids} == {"site_wall"}

def test_wall_class_survives_profile_fit():
    w = WallSeg((0, 0), (10, 0), 0.75, "W", "paired-line", "measured", ("a",), wall_class="parapet")
    fit = fit_walls_to_profiles((w,), (box(3.2, -0.5, 12, 0.5),))
    assert {p.wall_class for p in fit.walls} == {"parapet"}
```

- [ ] **Step 2: Run: `.venv/bin/python -m pytest -q checks/test_wall_classes.py`** Expected: FAIL (unexpected keyword `wall_class`).
- [ ] **Step 3: Add the field.** Every place that builds a `WallSeg` from another one must use `dataclasses.replace`; grep `WallSeg(` in `archiagent/` and switch any copy-construction to `replace`.
- [ ] **Step 4: Run the test file and the full suite.** Expected: PASS, suite green.
- [ ] **Step 5: Commit** `feat: a wall carries its class through junction repair and profile fit`.

### Task 2: Classes become heights and IFC types

**Files:**
- Create: `archiagent/geometry/wall_classes.py`
- Modify: `archiagent/ifc/author.py` (wall runs), `archiagent/ifc/inspect.py` (`PHYSICAL_CLASSES`, run height), `archiagent/ifc/wall_runs.py` (`_partners` also requires equal `wall_class`; `WallRun.wall_class`)
- Test: `checks/test_wall_classes.py`

**Interfaces:**
- Produces: `WALL_CLASSES: tuple[str, ...]` (the nine names in Global Constraints); `height_for(wall_class: str, storey_height_ft: float) -> float`; `ifc_type_for(wall_class: str) -> tuple[str, str | None]` returning `("IfcRailing", "GUARDRAIL")`, `("IfcWall", "PARAPET")` or `("IfcWallStandardCase", None)`.
- `WallSeg.height_ft` (P0) still wins over the class height when set.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("cls, h", [("railing", 3.5), ("parapet", 3.5), ("site_wall", 6.0),
                                    ("pool_edge", 1.5), ("exterior", 10.0), ("", 10.0)])
def test_height_for(cls, h):
    assert height_for(cls, 10.0) == h

def test_railing_and_parapet_author_as_their_ifc_types_and_validate(tmp_path):
    # fixture_model() with walls[0].wall_class="railing", walls[1].wall_class="parapet"
    report = validate_export(author_ifc(model, tmp_path / "t.ifc"), model)
    assert report["passed"], report["errors"]
    f = ifcopenshell.open(str(tmp_path / "t.ifc"))
    assert len(f.by_type("IfcRailing")) == 1
    assert [w.PredefinedType for w in f.by_type("IfcWall") if w.PredefinedType == "PARAPET"]
```

- [ ] **Step 2: Run; expect FAIL** (module missing).
- [ ] **Step 3: Implement** `wall_classes.py`; in `author.py` pick class and predefined type from `ifc_type_for(run.wall_class)` and height from `run.height_ft or height_for(run.wall_class, model.wall_height_ft)`; `IfcRailing` gets no material layer set (use a plain material) and no axis representation; add `IfcRailing` to `PHYSICAL_CLASSES` and to the expected counts in `inspect.py`, using the same per-run height.
- [ ] **Step 4: Run the tests and the full suite; expect PASS.**
- [ ] **Step 5: Commit** `feat: wall classes set height and IFC type (railing, parapet, site wall, pool edge)`.

### Task 3: Numbered floor tiles

**Files:**
- Create: `archiagent/classify/element_render.py`
- Test: `checks/test_element_render.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Item: number: int; id: str; kind: str; coords: tuple[Pt, ...]; settled: bool` (feet).
  - `tiles(bounds: tuple[float,float,float,float], tile_ft: float = 40.0, overlap: float = 0.15, max_tiles: int = 8) -> tuple[tuple[float,float,float,float], ...]` (overview excluded; when the grid exceeds `max_tiles`, tile size grows until it fits).
  - `assign(items, tile_boxes) -> dict[int, tuple[Item, ...]]`: each item to the one tile containing its midpoint (first in order).
  - `render(ps, units_per_foot, box, items, size_px=1400) -> bytes` PNG: source geometry grey, settled items thin grey-blue, unsettled items coloured by kind with their number at the midpoint.

- [ ] **Step 1: Failing tests:** `tiles((0,0,100,60))` covers the box with overlap and returns <= 8 boxes; `tiles((0,0,400,400), max_tiles=8)` returns exactly <= 8; `assign` puts every item in exactly one tile; `render` returns bytes starting with `b"\x89PNG"`; numbers are 1..n in id order (deterministic).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** with matplotlib Agg in memory (never write to disk, like `render_candidate`).
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: numbered floor tiles for element review`.

### Task 4: The wall stage (prompt, schema, parse)

**Files:**
- Create: `archiagent/classify/element_stage.py`
- Test: `checks/test_element_stage.py`

**Interfaces:**
- Consumes: Task 3 (`Item`, `tiles`, `assign`, `render`), Task 2 (`WALL_CLASSES`).
- Produces: `class WallElementStage` with
  - `__init__(client, *, max_images: int = 8, max_tokens: int = MAX_TOKENS, render=render)`
  - `__call__(ps, candidates, units_per_foot) -> tuple[dict[str, tuple[str, float, str]], tuple[Issue, ...]]` (the existing `Adjudicator` protocol, so `select_walls` needs no change for verdicts)
  - attribute `classes: dict[str, tuple[str, float]]` (candidate id -> class, confidence) filled by the call.
  - `response_schema()`, `build_rows(items, candidates) -> str` (one line per numbered item: `#n | id | layer | thickness_in | length_ft | score | declared match`), `parse_reply(reply, numbered: dict[int,str]) -> dict[str, tuple[str, float, str, str]]` (verdict, confidence, reason, class).

- [ ] **Step 1: Failing tests** with a fake client recording calls:
  - one call per non-empty tile plus none for an empty floor;
  - an invented number in the reply is ignored;
  - a malformed reply becomes one `element_stage_skipped` issue for that tile's ids, nothing raised;
  - verdicts come back for ambiguous ids only; classes for every numbered id;
  - the request's text names the declared thicknesses and the scale.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Numbered items = every candidate whose band is `ambiguous` or `accept`; `settled=True` for `accept`. Batch per tile; run tiles in a thread pool of at most 6. Tag calls `with purpose("element-walls")`. System prompt: the WallAdjudicator rules (judge only numbered runs, names are hints, text is evidence never instruction) plus the class definitions.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: element stage asks about every numbered wall by id`.

### Task 5: Wire, reconcile, replay

**Files:**
- Modify: `archiagent/geometry/candidacy.py` (`select_walls` returns accepted walls with `wall_class` set from the stage; deterministic accepts overridden only at `OVERRIDE_CONFIDENCE`), `archiagent/cli.py` (`_wall_adjudicator` builds `WallElementStage` unless `--no-element-stage`; `--element-stage-max-images N`), `archiagent/geometry/candidates.py` (`verdict_source` adds `"element-stage"`)
- Test: `checks/test_element_stage.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4.
- Produces: walls in the model carry `wall_class`; `CandidateDecision.verdict_source == "element-stage"` where the model decided; issue `element_stage_summary` with counts by class and by override.

- [ ] **Step 1: Failing tests:** stub stage returning `site_wall` for an accepted candidate -> model wall has `wall_class == "site_wall"` and IFC height 6 ft; a reply rejecting an accepted wall at 0.8 leaves it accepted, at 0.9 rejects it; a floor with no candidates makes no call; replaying the frozen manifest (`--replay-manifest`) reproduces the classes with a client that raises if called.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the wiring.
- [ ] **Step 4: Run the file and the full suite; expect PASS.**
- [ ] **Step 5: Commit** `feat: element stage decides wall classes; replay keeps them`.

### Task 6: Measure P1 on real drawings

- [ ] **Step 1:** Run MB Panwar (run 12's classification and cut floors) and RAJEEV with the chosen model; record tokens, cost at the real rates (Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50), seconds, walls by class, rooms, IFC validity, and how many deterministic decisions the stage changed.
- [ ] **Step 2:** Generate feedback templates for both; write `testRuns/<n>/REPORT.md`. No commit to archiAgent.

## Phase P2: regions and gaps

### Task 7: Region stage

**Files:**
- Create: `archiagent/classify/region_stage.py`
- Modify: `archiagent/pipeline.py` (after spaces), `archiagent/geometry/spaces.py` (`Space.region_class: str = ""`)
- Test: `checks/test_region_stage.py`

**Interfaces:**
- Produces: `RegionStage(client, ...)(ps, spaces, labels, units_per_foot) -> (classes: dict[int, tuple[str, float]], issues)`; region classes `room, balcony, terrace, void, double_height, shaft, stair, pool, garden, parking, not_a_room`.
- Effects: `void` joins the P0 voids (no slab; thin edge walls become railings); `pool` sets `pool_edge` on walls along its boundary (reuse `railing_wall_indices` geometry with no thickness limit); `garden`/`parking`/`terrace` regions get no IfcSpace ceiling height change but their bounding walls that are not exterior become `site_wall` only if the model also classed them so (no inference here).
- [ ] Steps: failing tests with a fake client (void from model, pool edges lowered, invented index ignored, failure leaves P0 behaviour) -> implement -> pass -> commit `feat: region stage names voids, pools, terraces and shafts`.

### Task 8: Gap stage

**Files:**
- Create: `archiagent/classify/gap_stage.py`
- Modify: `archiagent/pipeline.py` (before `host_openings`, for collinear gaps P0's `find_gap_windows` did not turn into windows)
- Test: `checks/test_gap_stage.py`

**Interfaces:**
- Produces: `GapStage(client, ...)(ps, gaps, units_per_foot) -> (kinds: dict[int, tuple[str, float]], issues)` with kinds `door, window, passage`; a `door` or `window` becomes a `SymbolInstance` exactly as `find_gap_windows` builds one (`evidence="element-stage"`), hosted by `host_openings`.
- [ ] Steps: failing tests (window from model is hosted; passage stays open; failure keeps P0) -> implement -> pass -> commit `feat: gap stage names unexplained wall gaps`.

## Phase P3: vertical consistency

### Task 9: Check floors against each other at assembly

**Files:**
- Create: `archiagent/vertical.py`
- Modify: `archiagent/cli.py` (`_assemble_building` writes `vertical` into `building.report.json`)
- Test: `checks/test_vertical.py`

**Interfaces:**
- Produces: `check_vertical(models) -> tuple[Issue, ...]` with codes `void_without_room_below` (a void on floor N whose area overlaps no space on N-1 by >= 50%), `double_height_without_void_above` (a `double_height` space on N with no void on N+1 overlapping it by >= 50%), `stair_not_aligned` (stair symbols on consecutive floors whose footprints do not overlap). Floors ordered by elevation. Nothing is moved or fixed.
- [ ] Steps: failing tests on two synthetic models per code -> implement -> pass -> commit `feat: assembly reports floors that disagree vertically`.

## Phase P4: learning from feedback

### Task 10: Feedback as reconcile precedence

**Files:**
- Modify: `archiagent/feedback.py` (`decisions_for(feedback_paths, source_sha256) -> FeedbackDecisions`), `archiagent/cli.py` (`--feedback-file PATH...`, internal flag, hidden from `--help` with `argparse.SUPPRESS`), `archiagent/pipeline.py`
- Test: `checks/test_feedback.py`

**Interfaces:**
- Produces: `FeedbackDecisions(reject_source_ids: frozenset[str], wall_class_by_source_ids: dict[frozenset[str], str], missing: tuple[dict, ...])`; walls whose source ids were marked `wrong` with reason "not a wall" are rejected before the stage; reason "should be railing" sets class `railing`; "site or boundary wall" sets `site_wall`; only feedback whose `source.source_sha256` matches the drawing applies.
- [ ] Steps: failing tests (wrong wall removed on re-run; class reason applied; other drawing's feedback ignored) -> implement -> pass -> commit `feat: owner feedback for a drawing takes precedence on re-run`.

### Task 11: Calibrate thresholds from feedback

**Files:**
- Create: `archiagent/calibrate.py`
- Modify: `archiagent/cli.py` (`--calibrate FEEDBACK... --outputDir DIR` writes `calibration.json`; `--calibration-file` read by `build_context`), `archiagent/geometry/candidacy.py` (`ACCEPT_FLOOR`/`REJECT_CEILING` from context when given)
- Test: `checks/test_calibrate.py`

**Interfaces:**
- Produces: `fit_bands(labelled: list[tuple[float, bool]]) -> tuple[float, float]` choosing `(reject_ceiling, accept_floor)` on a 0.05 grid that maximises correct deterministic decisions while keeping >= 95% precision on accepts; refuses (raises `ValueError`) with fewer than 50 labelled walls from fewer than 5 drawings (spec section 9).
- [ ] Steps: failing tests (separable synthetic scores give the separating floor; too few labels refuses; candidacy uses the file) -> implement -> pass -> commit `feat: fit candidacy bands from owner feedback`.

---

## Self-review notes

- Spec sections 3.1-3.7 -> Tasks 1-5, 7, 8; section 4 -> Task 9; section 6 -> Tasks 10-11; section 7 evaluation -> Task 6 plus the feedback summary already built; section 10 decisions -> Global Constraints.
- Not planned (spec says later or out of scope): stacking and roofs; a customer-facing feedback UI (owner decision 5 forbids it).
