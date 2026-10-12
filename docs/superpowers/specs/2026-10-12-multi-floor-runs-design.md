# Multi-floor runs: detect the floors, confirm them, run each in parallel, build one building

Date: 2026-10-12
Status: design, awaiting owner review
Related: `2026-10-07-staged-pipeline-design.md`, `2026-09-24-symbol-library-design.md`, `plans/2026-10-08-scale-tool-and-symbol-approval.md`

## 1. What the owner asked for

A drawing file often holds several floor plans ("GROUND FLOOR PLAN", "FIRST FLOOR REVISED", ...). Today the whole file runs as one storey, so floors land on top of each other. The tool should:

1. detect the floors in a DXF/DWG;
2. **show them on planto3d.in/.si for the user to approve or disapprove, each one**, with name, box and elevation;
3. as soon as every detected floor has been decided, **launch one archiAgent run per approved floor, in parallel, each with only that floor's drawing as input**; the rest of the algorithm is unchanged;
4. **classify layers once per file** and hand that result to every floor run;
5. deliver **one building IFC** (each floor a storey) and also each floor's own IFC;
6. report **per floor**: rooms and their types (bedroom, bathroom, kitchen, parking, living...), counts.

Decided by the owner on 2026-10-12: confirm-first (no auto-start); classify once; one building IFC plus per-floor IFCs; harvesting is a backend action, not offered to customers; interior elements are not yet authored into the IFC (later).

## 2. What exists today (verified)

| Fact | Where |
|---|---|
| A reviewed window with a name, kind `plan`, `elevation_ft`, registration `origin` and optional `units_per_foot` is a `PlanRegion`; `--regions-file` runs several and `author_building` stacks them as storeys | `semantic.py`, `regions.py`, `ifc/author.py` |
| `propose_regions` finds spatial clusters of geometry; it does not read titles, and says elevations "need review" | `regions.py` |
| `author_building` refuses multiple plans without explicit elevations and unique region ids, and never invents storey spacing | `ifc/author.py` |
| A precomputed layer classification can be handed to a run | `cli._PrecomputedClassifier` |
| A job is `pending -> preparing -> ready -> queued -> running -> succeeded|failed`; prepare stores `plan.dxf` and `plan.scale.json`; `/start` and `/retry` take options | service |
| The worker is one process at concurrency 1; the VPS has 2 vCPU and 8 GB | `docker-compose.prod.yml` |
| Every layer classification (text + vision) is repeated per run: ~41k of a ~55k-token whole-file run | measured 2026-10-11 |

Floor labels in the owner's drawings: text under each plan ("GROUND FLOOR PLAN", "FIRST FLOOR REVISED", "TERRACE FLOOR"), plans stacked vertically (Baglow) or in a row (MB Panwar, two SANJANA files, up to four). Aiims Road is one typical plan plus elevations with level marks (`+3.6 FIRST FFL`): not separate plans.

## 3. User flow

1. Upload; prepare as today (convert, scale evidence). Prepare now also **detects floors** and **classifies layers once**.
2. The Drawing view shows a **Floors** panel: one row per detected floor with its name, a highlighted box on the drawing, an elevation field, and **Approve / Disapprove**. Names and elevations are editable. A floor can also be added by drawing a box (v2).
3. The Convert button stays disabled until **every** detected floor is decided and at least one is approved. The scale and wall-thickness sections apply to all floors.
4. Convert creates one **building job** and one **floor job per approved floor**; floors run in parallel.
5. The building page shows each floor's status and, when all have finished, the building IFC and the per-floor IFCs and reports.

A file with no detected floor labels behaves exactly as today (one region, the whole drawing). Elevations and sections (`ELEVATION`, `SECTION`, `FFL` markers) are detected and listed as "not a floor" so they can be left out.

## 4. Floor detection (archiAgent, `--detect-floors`)

- Input: the prepared DXF. Output: `plan.floors.json`, a list of `{id, name, bounds, elevation_ft, elevation_source, label_text, label_position, confidence}`.
- Candidates are **spatial clusters** (`propose_regions`, with the drawing frame and annotation layers excluded) **named by floor labels**: TEXT/MTEXT matching a vocabulary (ground, first, second, third, fourth, terrace, roof, basement, stilt, parking, mezzanine, with "floor", "plan", "revised"). A label is assigned to the nearest cluster **below, then above, then beside** it, because the owner's drawings caption plans from beneath.
- A cluster with no label stays listed as "unnamed plan" for the user to name or disapprove. A label with no cluster is reported, not guessed.
- **Elevation**: read from a level marker when the file has one (`+3.6 FIRST FFL`, converted to feet by the scale); otherwise ground = 0 and each next floor = previous + the configured wall height, **marked assumed** and editable. Never silently invented: `elevation_source` says which.
- Detection proposes; the user's approval is the only thing that makes a floor real.

## 5. Per-floor DXF

The floor job's input is a DXF containing only that floor. Built at prepare/confirm time with ezdxf by **deleting modelspace entities outside the floor's box** from a copy of the prepared DXF, so layers, blocks, styles and header (`$INSUNITS`) are carried exactly as ODA wrote them.

- An entity belongs to the floor containing the centre of its bounding box; an INSERT by its insertion point.
- Entities spanning more than one floor box (sheet frame, a dimension line across plans) belong to none and are listed in the floor's report as dropped.
- Unused blocks are left in place (harmless) so block names stay stable for symbol matching.
- Each floor DXF is stored as `floor-<id>.dxf` next to `plan.dxf`.

## 6. Classification once

Prepare runs the layer classification (text + vision) once on the whole file and stores `plan.classification.json`. Each floor run receives it with a new flag `--classification-file`, using the existing precomputed-classifier path. LLM cost for classification stays ~41k tokens for the file, not per floor. Wall adjudication is per floor and sums to about the same as one run.

Consequences: the layer gate (a wall layer needs 70% confidence) is decided once; if it fails there, **no floor runs**, which is cheaper than N failures.

## 7. Orchestration (service)

- New table or columns: `jobs.parent_job_id`, `jobs.kind` (`single` | `building` | `floor`), `jobs.floor` (JSON: id, name, bounds, elevation_ft, elevation_source). A building job owns its floors; a single-plan job is unchanged.
- States: building `ready -> running -> succeeded | partial | failed`; floors use the existing machine.
- Endpoints: `GET /v1/jobs/{id}/floors` (detected proposals); `POST /v1/jobs/{id}/floors/start` with `{floors: [{id, approved, name, elevation_ft}], ...scale/thickness options}`, refused with 409 unless **every detected floor has a decision**.
- **Parallelism needs more workers**: today one process at concurrency 1. The worker service is scaled to N processes (start with 3); `rq worker` instances are independent, so floors run in parallel up to N, the rest queue. The 2 vCPU box is adequate because runs wait on the LLM; memory per run is to be measured before raising N.
- **Failure**: floors are independent. If one fails the others finish; the building IFC is assembled from the floors that succeeded, the building job is `partial`, and the failed floor can be retried alone. A retry re-runs that floor only and re-assembles.
- Provider 504s hit a floor independently; the existing retry-from-stored-drawing path is reused per floor.

## 8. One building IFC, plus each floor's IFC

- Each floor run writes its own IFC and `*.interpretation.json` as today.
- A final **assemble** step (new, `python -m archiagent --assemble-building MANIFEST... --outputDir`) reads the floors' interpretation manifests, rebuilds their models by the existing replay path, and calls `author_building`. Elevations come from section 4 (user-confirmed); each floor's `origin` is its box's lower-left corner by default, so floors lie over one another. The alignment is shown to the user and flagged "assumed" until they correct it.
- Output: `building.ifc` (one `IfcBuildingStorey` per floor) and `floor-<id>.ifc` each.

## 9. Per-floor report

`building.report.json` has, per floor: walls, openings, symbols by kind, assumed elevation, issues, and **spaces by type**. Space type comes from the library's `space_types`/`space_evidence` carried on each matched symbol, weighted strong over weak, combined with room-label text in the floor ("M.BED ROOM", "TOILET"), the strongest evidence. The report lists rooms found, bathrooms, kitchens, bedrooms, parking. The type of a room is a proposal until reviewed.

Limitation, stated up front: today's conversions detect 0-2 closed spaces per drawing, so room typing is only as good as space detection, which is the weak step and is its own piece of work.

## 10. Cost and runtime

| | Today (whole file, one run) | Per floor, classification repeated | Per floor, classified once (this design) |
|---|---|---|---|
| LLM tokens (3 floors) | ~55k | ~137k | ~55k |
| Wall-clock | one run, 7-30 min (LLM latency) | about one run if parallel | about one run if parallel |

Parallel runs are faster than the same floors run one after another, not faster than today's single run, because a run is latency-bound. The gain is correctness (separate storeys), not speed.

## 11. Acceptance on the corpus

Detection, run on the five multi-plan drawings, must find: Baglow 3 floors (stacked), MB Panwar 2, SANJANA Giriraj 4, SANJANA Suresh 2, RAJEEV (ground + terrace), each with the right name and box; Aiims Road must report **no floors** and list its elevations as not-a-floor. A floor DXF must contain no entity from another floor's box. Building IFC must have one storey per approved floor at the confirmed elevations.

## 12. Not in this design

Authoring furniture/plumbing into the IFC; drawing a floor box by hand (v2); cross-floor stair/void continuity; per-floor scale or wall-thickness overrides; room typing as a gated acceptance check.

## 13. Decisions I made that you can overrule

1. Default elevation when the file has no level marker: ground 0, then + wall height, marked assumed.
2. Floors are aligned by each box's lower-left corner by default.
3. Entities spanning floors are dropped from every floor and reported.
4. A failed floor does not stop the others; the building is `partial`.
5. Worker count starts at 3.
