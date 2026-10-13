# Multi-floor runs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** A DXF/DWG holding several floor plans is detected, shown for approval floor by floor, run as one archiAgent run per approved floor (in parallel, each on only that floor's drawing), and delivered as one building IFC plus each floor's IFC, with a per-floor report.

**Spec:** `docs/superpowers/specs/2026-10-12-multi-floor-runs-design.md` (owner review 2026-10-13, section 14).

**Architecture:** archiAgent gains four small, separately testable commands: `--detect-floors`, `--cut-floors`, `--classification-file`, `--assemble-building`. The service gains a parent "building" job with child "floor" jobs. The Drawing view gains a Floors panel and a wall-height slider. Nothing changes for a drawing with no detected floors.

**Tech stack:** Python 3.14, ezdxf, shapely, ifcopenshell (archiAgent); FastAPI, RQ, SQLAlchemy (service); plain JS `cad/` panel.

## Global constraints

- Branches off `feat/room-gaps`; **archiAgent merges before archiViewer** (the worker image builds from archiAgent `main`).
- Elevation is never required. Wall height is one number for all floors.
- **Not in this plan** (owner, 2026-10-13): placing floors over one another, slabs/roofs per storey, double/triple height, stairs and voids between floors.
- Every task is test-first; the full suite must be green before the next task.
- Models in feet, DXF coordinates in source units; convert once at the boundary (`units_per_foot`).

## What the probe found (2026-10-13, the three multi-plan corpus drawings)

| Drawing | Clusters at gap 10 ft | Floor labels found |
|---|---|---|
| Baglow | 3 (stacked) | GROUND / FIRST / TERRACE FLOOR REVISED, each just below its plan |
| SANJANA Suresh | 2 (side by side) | GROUND FLOOR, first FLOOR |
| MB Panwar | 2 (side by side) | GROUND FLOOR PLAN, FIRST FLOOR PLAN, plus notes "UP TO SECOND FLOOR" inside the plans, which are not titles |

`propose_regions` at gap 4 ft over-splits (annotation fragments become clusters); at 10 ft all three are right. Notes such as "UP TO SECOND FLOOR" must lose to the caption.

## T1 — Floor detection (`archiagent/floors.py`)

**Produces:** `Floor(id, name, bounds, elevation_ft, elevation_source, label_text, label_center, confidence)`; `detect_floors(ps, units_per_foot, wall_height_ft=10.0, gap_ft=10.0) -> tuple[Floor, ...]`.

- [ ] Clusters from `propose_regions(ps, upf, gap_ft)`; a cluster smaller than 25 ft x 15 ft or with fewer than 20 primitives is dropped.
- [ ] Label vocabulary: ground, first..fourth, terrace, roof, basement, stilt, parking, mezzanine, with "floor"/"plan". Text matching `up to|upto|section|elevation|staircase|stair case` is a note, never a title.
- [ ] A label belongs to the cluster whose box it sits **below the lower edge of (within 15 ft), then inside, then above, then beside**; ties by text height. One name per cluster.
- [ ] Order floors bottom-up by name rank (basement, stilt/parking, ground, first, ..., terrace/roof); an unnamed cluster keeps its drawing order and is named "Unnamed plan N".
- [ ] Elevation: ground 0 and each next floor = previous + `wall_height_ft`, `elevation_source` "assumed". A level marker with an explicit unit (`+3.6m FIRST FFL`, `10'-6" GROUND`) and a floor name sets it, `elevation_source` "level-marker". Markers without a unit are ignored (the unit is a guess).
- [ ] Tests: the three corpus shapes built synthetically (stacked 3, side by side 2, notes inside); a drawing with one plan returns `()`; markers with and without units; names never duplicated.

## T2 — Cut a floor out (`archiagent/floor_cut.py`)

**Produces:** `cut_floor(src_dxf, floor, floors, out_path, units_per_foot) -> CutReport(kept, dropped_spanning, dropped_other_floor)`.

- [ ] Copy the DXF with ezdxf and delete modelspace entities not belonging to the floor. An entity belongs to the floor containing its bounding-box centre (INSERT by insertion point). An entity whose box overlaps more than one floor box belongs to none and is counted as dropped.
- [ ] Layers, blocks, styles and header are untouched, so block names stay stable for symbol matching.
- [ ] Tests: two boxes on a synthetic DXF; an entity across the gap is dropped from both and reported; the header `$INSUNITS` and layer table survive; the cut file reloads in `load_dxf`.

## T3 — CLI: `--detect-floors`, `--cut-floors`

**Files:** `archiagent/cli.py`, `checks/test_floors_cli.py`

- [ ] `--detect-floors` writes `<stem>.floors.json` (list of floors) beside `--outputDir`, prints a one-line summary, exits 0 with `[]` when none. Needs a resolved scale like `--list-regions` does.
- [ ] `--cut-floors FLOORS_JSON` writes `floor-<id>.dxf` for each floor the file marks approved (an `approved` field; default true) and a `floors.cut.json` report.
- [ ] Refused with `--replay-manifest` and with PDF input.
- [ ] Tests through the real command line on a generated two-plan DXF.

## T4 — CLI: `--classification-file`

**Files:** `archiagent/cli.py`, `archiagent/classify/cache.py` (reuse the existing decision encoding), `checks/test_classification_file.py`

- [ ] `--classify-only --classification-out PATH` writes the classification as JSON (same encoding the layer cache uses).
- [ ] `--classification-file PATH` reads it and wraps it in `_PrecomputedClassifier`; no provider call is made. A layer in the cut drawing that the file does not mention is `IGNORE` with source `default` (and warns), exactly like an unanswered layer today.
- [ ] Refused together with `--rules`, `--walls` and `--provider`.
- [ ] Tests: a run with the file makes **no** LLM call (stub provider that raises); same IFC as the run that produced it.

## T5 — CLI: `--assemble-building`

**Files:** `archiagent/cli.py`, `archiagent/ifc/author.py` (no change expected), `checks/test_assemble_building.py`

- [ ] `--assemble-building MANIFEST [MANIFEST ...] --outputDir DIR` reads the floors' interpretation manifests (existing replay reader), gives each model its floor's name, region id and elevation (from a `--floors-file` the service passes), and calls `author_building`.
- [ ] Writes `building.ifc` and `building.report.json` (per floor: walls, openings, symbols by kind, spaces, closed gaps, issues).
- [ ] Wall height is the one value used for every floor; refuses manifests built with different heights.
- [ ] Tests: two floors give two `IfcBuildingStorey` at 0 and 10 ft; assumed vs marker elevation recorded; duplicate ids refused.

## T6 — Wall height

**Files:** `archiagent-viewer/service/archiagent_service/api.py`, `pipeline.py`, `cad/src/scale-model.js`, `cad/src/scale-panel.js`, `cad/index.html`, `tests/scale-model.test.mjs`, `scripts/cad-check.mjs`

- [ ] `StartRequest.wall_height_ft: float | None` (1 to 50 ft, finite); `build_command` emits `--height`.
- [ ] Panel: a slider 1 to 50 ft, default 10, shown value in feet with the number editable; if the prepared drawing's floors carry level markers the default is the spacing they imply and a note says so.
- [ ] Tests: bounds, the emitted argv, the panel's options, dev-mode CLI line.

## T7 — Service: building and floor jobs

**Files:** `service/archiagent_service/{models,api,pipeline,worker}.py`, migrations, `service/tests`

- [ ] `jobs.parent_job_id`, `jobs.kind` (`single`|`building`|`floor`), `jobs.floor` (JSON).
- [ ] Prepare also runs `--detect-floors` and, when it finds floors, `--classify-only --classification-out`; stores `plan.floors.json` and `plan.classification.json`.
- [ ] `GET /v1/jobs/{id}/floors`; `POST /v1/jobs/{id}/floors/start` refused (409) unless every detected floor has a decision and one is approved; creates the building job and one floor job per approved floor; each floor job runs `--dxfFilePath floor-<id>.dxf --classification-file ...`.
- [ ] Building states: `running -> succeeded | partial | failed`; a failed floor can be retried alone; the assemble step runs when all floors are done.
- [ ] Worker service scaled to 3 processes in `docker-compose.prod.yml`; memory per run measured before raising.
- [ ] Tests: the 409 rule, parent/child states, partial, retry of one floor.

## T8 — Floors panel

**Files:** `cad/src/floors-panel.js` (new), `cad/src/main.js`, `cad/index.html`, `tests/floors-model.test.mjs`, `scripts/cad-check.mjs`

- [ ] One row per detected floor: name (editable), elevation (editable, marked "assumed"), Approve / Disapprove, a highlight of its box on the drawing.
- [ ] Convert stays disabled until every floor is decided and one is approved; a drawing with no detected floors is unchanged.
- [ ] Building page lists each floor's status, then the building IFC and per-floor IFCs and reports.
- [ ] Tests: the decision gate (pure model), and the cad-check path.

## T9 — Corpus acceptance, docs, release

- [ ] Detection on the five multi-plan drawings and Aiims Road (no floors) matches the spec's acceptance (section 11); a floor DXF contains no entity of another floor; building IFC has one storey per approved floor.
- [ ] Docs: CLI help, service README, the spec's status line; Playwright check on planto3d.in and .si after deploy; deploy only on the owner's go-ahead.

## Order and what runs without the others

T1 → T2 → T3 → T4 → T5 are archiAgent-only and testable offline. T6 is independent. T7 needs T3–T5. T8 needs T7's endpoints (it can be built against the JSON shape first).
