# Scale from a wall, and symbol harvesting with owner approval

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (A) a user uploads a DXF or DWG, sees the drawing at once, and either accepts the scale the drawing's own dimensions imply or clicks one wall and types its length, and only then is the model built; (B) every conversion proposes symbol templates from the drawing, the owner approves or rejects them on one page, and every later conversion matches the approved ones.

**Why these two first:** they are the two inputs that decide whether a model is right. Without (A), drawings with no usable dimensions (`PLAN.dxf`, `Floor Plan.dxf`, SANJANA SURESH) cannot be converted through the web at all (deployment-readiness §4.1). Without (B), `symbol_library.json` ships empty, so fixture and furniture linework stays in wall detection as phantom walls (deployment-readiness §1.1: "a mechanism, not a library").

**Architecture:** A job gains a *prepare* step before it runs: the worker converts a DWG with ODA and reads the drawing's scale evidence, stores `plan.dxf` and `plan.scale.json`, and the job waits in `ready`. The Drawing view reads both, offers the two scale choices, and starts the run. Each run also harvests symbol candidates into `plan.symbols.json`; the service collects them into a table the owner reviews at `/admin/symbols/`; the worker hands the approved set to every run as `--symbol-library`.

**Tech Stack:** archiAgent (Python, ezdxf), archiViewer service (FastAPI, SQLAlchemy, Alembic, RQ), Drawing view (`cad/`, mlightcad `cad-simple-viewer` 1.7.4), the 3D view (vanilla JS).

**Specs this implements:** `docs/superpowers/specs/2026-10-07-staged-pipeline-design.md` §2 (the scale gate writes reviewed data), `2026-09-24-symbol-library-design.md` §4 (harvesting; "only a reviewer moves it into the library").

---

## Global constraints

- **The browser never receives a DWG**, and never a GPL package (`tests/licence-boundary.test.mjs`).
- **A scale is never guessed.** The two ways forward are the drawing's own dimensions or one asserted wall; the header alone is never used (scale-resolution design).
- **The service never imports archiagent**; it runs the CLI as a subprocess.
- **Release order:** push archiAgent `main` before archiViewer `main` — the worker image is built from archiAgent `main`.
- **Symbol templates are shared across tenants.** They are normalised glyphs (centred, unit size) with hash-only provenance, never file names or raw block names. **Open question for the owner:** harvest from every tenant's uploads into one shared library (assumed by this plan), or keep a library per tenant.

---

## Part A — Scale from a wall

> **Built 2026-10-08, not yet deployed.** archiAgent `feat/scale-prepare` (A1, A2, help-text fix); archiViewer `feat/scale-gate` (A3–A5, plus a killed-work-horse fix found end to end). Verified in Chrome through the local API, worker and ODA on a corpus DWG. Deploy order: merge archiAgent first.

### Task A1: a DXF with no units header must still run (archiAgent)

`load_dxf` raises `DxfUnitsError` when `$INSUNITS` is absent or unitless, *before* `_resolve_scale` can use an asserted wall. A drawing whose header says nothing is exactly the one that needs an asserted wall.

**Files:** `archiagent/ingest/dxf_vector.py`, `archiagent/cli.py`, `checks/test_scale_ladder.py`

- [ ] `load_dxf` returns `declared=None` instead of raising when the header has no unit; geometry tolerances use a provisional factor (1.0) that the resolved scale replaces.
- [ ] `_resolve_scale` is unchanged: no header and no assertion and no usable dimensions still refuses, with the same message.
- [ ] Test: a unitless DXF + `--scale-from-wall` builds; a unitless DXF alone refuses.

### Task A2: `--prepare` — convert, read the scale evidence, stop (archiAgent)

**Files:** `archiagent/cli.py`, `checks/test_prepare.py`

`--prepare` with `--outputDir`: for a DWG, convert (ODA, as today) and keep `plan.dxf`; then write `plan.scale.json` and exit 0 without classifying or authoring.

```json
{"schema_version": 1,
 "header": {"insunits": 1, "units_per_foot": 12.0},
 "extracted": {"units_per_foot": 12.0, "support": 159, "considered": 424} | null,
 "extents": {"min": [x, y], "max": [x, y]}}
```

- [ ] `extracted` is `extracted_scale(ps, tolerance)` exactly as the ladder sees it, so the UI can never offer a scale the run would then reject.
- [ ] Test on a fixture with native dimensions, one without, one unitless.

### Task A3: the job waits for its scale (service)

**Files:** `service/archiagent_service/{api,worker,pipeline,models}.py`, a migration, tests

States: `pending` → **`preparing`** → **`ready`** → `queued` → `running` → `succeeded`|`failed`.

- [ ] `POST /v1/jobs/{id}/prepare` (after the upload PUT): queues `prepare_job`, which runs `--prepare`, stores `plan.dxf` and `plan.scale.json`, sets `ready` (or `failed` with ODA's message).
- [ ] `POST /v1/jobs/{id}/start` accepts `pending` (unchanged, for API callers) or `ready`. From `ready` the worker passes `--dxfFilePath plan.dxf`, so a DWG is converted once, and `converted_from_dwg` stays true.
- [ ] `POST /v1/jobs/{id}/retry` with new scale options: a new job from the same stored source (server-side copy, no re-upload). A job that failed on scale is retried with a measured wall, not re-uploaded.
- [ ] Tests: state machine (illegal transitions are 409), retry copies the source and nothing else.

### Task A4: set scale in the Drawing view (cad/)

**Files:** `cad/src/set-scale.js`, `cad/src/main.js`, `cad/index.html`, `src/source.js`, `scripts/cad-check.mjs`

- [ ] "Set scale" mode: the user clicks a line. The span comes from the **entity's own vertices** (a LINE's endpoints; for an LWPOLYLINE, the straight segment nearest the click; arcs refused), never from where the cursor landed. Selected segment highlighted.
- [ ] Length input accepts what archiAgent accepts (`10`, `10ft`, `10'-6"`, `3.05m`, `3050mm`, `120in`), parsed by a port of `parse_explicit_length` with a shared fixture file so the two cannot drift.
- [ ] Live readout: implied units per foot, beside the drawing's own dimensions (`plan.scale.json`) and the header; a disagreement shows the factor hint (12 = feet read as inches, 25.4, 304.8 …) as archiAgent words it.
- [ ] "Convert with this scale" → `/start` (or `/retry`) with `scale_from_wall`, then the 3D view, polling.
- [ ] When `extracted` exists, the first offer is "Use the drawing's dimensions (N agree: X units/ft)".
- [ ] cad-check: select a wall on a corpus drawing, type its length, assert the request body's points are that entity's vertices.

### Task A5: upload goes through the Drawing view (3D view)

- [ ] Upload → prepare → poll until `ready` → open `/cad/?id=…&step=scale`. The "Use the dimensions already in this drawing" checkbox goes: the choice is made on the drawing, with its evidence shown.
- [ ] A job in `ready` is listed in both views as "waiting for scale".

---

## Part B — Symbol harvesting and owner approval

### Task B1: harvest with the run's own scale (archiAgent)

`--harvest-symbols` sizes candidates (`size_ft`) with the **header** units today. On a drawing whose header is wrong, every size range is wrong by that factor.

**Files:** `archiagent/cli.py`, `archiagent/classify/symbol_harvest.py`, `checks/test_symbol_harvest.py`

- [ ] `--harvest-symbols-to PATH` runs as part of a normal run, after scale is resolved, and writes candidates (geometry, size range, instances, found_by) to PATH. No previews: the review page draws the normalised geometry as SVG.
- [ ] Test: the same drawing with header 304.8 and asserted 12 gives sizes in feet from the asserted scale.

### Task B2: collect candidates; one table, one decision per shape (service)

**Files:** `service/archiagent_service/{models,symbols}.py`, migration, tests

- [ ] Table `symbol_templates`: `id` (archiAgent's shape digest id, so the same glyph from two tenants is one row), `kind`, `subtype`, `label`, `geometry` (JSON), `size_min_ft`, `size_max_ft`, `mirror_allowed`, `rotations_deg`, `status` (`candidate`/`approved`/`rejected`), `found_by`, `instances` (summed), `jobs` (count), `reviewed_by`, `reviewed_at`, timestamps.
- [ ] Worker passes `--harvest-symbols-to plan.symbols.json` on every DXF/DWG run and upserts: new shapes become candidates; known shapes add instances and widen the size range; **a rejected shape stays rejected**.

### Task B3: the owner's review page

**Files:** `service/archiagent_service/{api,auth,config}.py`, `admin/index.html`, `admin/src/main.js`, tests

- [ ] `ARCHIAGENT_SERVICE_ADMIN_TENANTS`: tenant ids whose keys may review. `GET /v1/admin/symbols?status=candidate`, `POST /v1/admin/symbols/{id}` with `approve`/`reject` and the reviewed fields. Everyone else gets 404, not 403.
- [ ] `/admin/symbols/`: a grid of candidates, most instances first; each card shows the glyph (SVG from the normalised geometry), instance and drawing counts, found by name or frequency, a kind picker limited to archiAgent's `KINDS`, subtype, label, size range, mirror allowed. Approve / Reject, one click each.
- [ ] Approval validates exactly as `library_templates._validated` would, server-side, so an approved row can never fail to load in a run.

### Task B4: every run uses the approved library (worker)

- [ ] Before each run the worker writes the approved rows as `{"schema_version": 1, "templates": [... status: "reviewed"]}` and passes `--symbol-library`. Recorded on the job as `symbol_library: {count, digest}` so a result can be traced to the library it used.
- [ ] Measured on the corpus before/after approving the first templates: walls, phantom walls removed, symbols matched (the 2026-10-08 comparison tooling).

---

## Order

A1 → A2 → A3 → A4 → A5, then B1 → B2 → B4 → B3. A first: it unblocks drawings that cannot be converted at all. B4 before B3 so approved rows are used the moment the page exists.

## Risks

| Risk | Mitigation |
|---|---|
| A user picks a non-wall line, or an arc segment | Only straight segments are selectable; the implied scale is shown against the drawing's own dimensions before converting |
| Clicking during mlightcad's progress overlay is lost | Tools stay disabled until it hides (already in Stage A) |
| A bad approval spreads to every tenant's runs | Approval validates like the loader; each job records the library digest it used; a template can be rejected again |
| Candidates from customer drawings shared across tenants | Normalised glyphs, hash-only provenance; sharing itself is an open owner decision |
