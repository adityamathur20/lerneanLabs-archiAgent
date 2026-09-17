# IFC Wall Joins — Verification Record

**Date:** 2026-09-17
**Branch:** `feature/generic-ifc-replay-blender`
**Plan:** `docs/superpowers/plans/2026-09-16-ifc-wall-joins.md`
**Spec:** `docs/superpowers/specs/2026-09-15-ifc-wall-joins-design.md`

**Suite:** 285 passed, 8 skipped, 6 subtests passed (48.6s). No provider call was
made anywhere in this work: every drawing below was classified by `--walls`.

---

## 1. All ten DXFs, end to end

```bash
python -m archiagent --dxfFilePath "<file>" --outputDir <dir> \
    --walls <layers...> [--units-per-foot 12] --no_vision
```

| Drawing | Result | Walls | Joints | Untrimmed | Polygon profiles | Max priority |
|---|---|---|---|---|---|---|
| `Aiims Road 3BHK Flats-vk.dxf` | **PASS** | 409 | 348 | 0 | 0 | 6 |
| `M.r Premg Agarwal Baglow 90x50.dxf` | **PASS** | 246 | 123 | 10 | 18 | 4 |
| `MB Panwar JI Revision 2.dxf` | **PASS** | 75 | 43 | 2 | 0 | 4 |
| `MR RAJEEV JI TWANI JI.dxf` | **PASS** | 139 | 82 | 2 | 44 | 3 |
| `Manoj JI Ladnu shyam nagar plumbing.dxf` | **PASS** | 193 | 92 | 0 | 0 | 3 |
| `PLAN.dxf` (units 12) | **PASS** | 11 | 0 | 0 | 0 | 0 |
| `SANJANA Giriraj Ji plan for structures.dxf` | **PASS** | 80 | 66 | 0 | 1 | 3 |
| `SANJANA SURESH JI.dxf` | **PASS** | 46 | 39 | 0 | 5 | 3 |
| `VINAYAK APARTMENTS.dxf` | **PASS** | 802 | 442 | 90 | 0 | 5 |
| `Floor Plan.dxf` (units 12) | fails earlier | — | — | — | — | — |

"PASS" means the reopened IFC satisfied every check in `validate_export` with
**zero errors**: schema and EXPRESS rules, element counts, containment, exact
solid volumes and bounds per run, opening void/filling relationships, wall
class, axis and layer set, rectangle profiles, connection points, pairwise
overlap and whole-storey coverage.

`Floor Plan.dxf` stops before authoring on `wall 104 partially overlaps
accepted wall profiles`. That predates this work and is out of scope (§5).

Every `IfcMaterialLayer.Priority` is within IFC's legal 0-100; the drawings
exercise 0-6.

## 2. Joint geometry matches the reviewed sketch

`checks/test_wall_joins_ifc.py` authors one plan per case, reopens it, and
compares each wall's XY footprint against the sketch geometry:

| Case | Assertion | Overlap |
|---|---|---|
| L, green 0.5 / blue 0.75 | blue owns the corner, green stops at its face | 0 |
| T, through 0.75 / stem 0.5 | through wall untouched, stem stops at its face | 0 |
| X, through 0.75 / green 0.5 | green split in two, gap = through thickness | 0 |
| Oblique L (~50°) | owner reaches the far face, end cut on a slant | 0 |

## 3. Blender package

`--blender-package` on `MR RAJEEV JI TWANI JI.dxf` wrote `source.ifc`,
`mesh_manifest.json` and `build_blender.py`: 278 meshes, 139 of them walls, all
`IfcWallStandardCase`, in world metres. Launching Blender itself was **not**
exercised — no Blender binary is available in this environment.

## 4. What the sample drawings exposed

Six defects reached only by real geometry, each fixed test-first:

| Defect | Symptom | Fix |
|---|---|---|
| Drawing-wide priority ranks | 662 schema violations on VINAYAK; ranks reached 743 against a 0-100 limit | Smallest levels satisfying each joint's ownership |
| Stem outranking its through wall | Through wall notched, or split into a composite profile | Ownership at T/X follows geometry, not ranking |
| Corner extended by thickness/2 | Oblique corners mispredicted; volume, bounds and profile errors | Cut by the neighbour's face lines |
| Wall running along the wall it meets | Prediction deleted the wall; phantom overlaps | Recorded untrimmed, matching IfcOpenShell |
| Absolute 1e-12 rectangle tolerance | Rectangles written as polygons, then reported not parametric | One relative `is_rectangular` shared by authoring and validation |
| Void past a trimmed end | Up to 19% of a lintel's volume subtracted twice | Opening spans clipped to the trimmed footprint |
| Near-collinear corner | MB Panwar authored a wall spanning 7.7km | Joint refused beyond twenty thicknesses of reach |

## 5. Performance

One real regression was found and fixed: `host_name` rebuilt both the profile
layout and the run layout on **every call**, and validation calls it once per
opening — VINAYAK rebuilt them 48 times over 1081 walls. `host_names(model)`
now resolves every wall in one pass and `build_runs` reuses a model's layout
(`82d1d01`).

After that fix, profiles of the same drawing before and after this work show
the same work being done. `MR RAJEEV JI TWANI JI.dxf`, baseline `7d82550`
versus `82d1d01`, call counts identical to the digit:

| Call | Baseline | Now |
|---|---|---|
| `Vec3.__add__` | 2,687,579 | 2,687,579 |
| `Vec3.__mul__` | 2,006,962 | 2,006,962 |
| `Vec3.__sub__` | 1,100,030 | 1,100,030 |
| `json.iterencode` | 3,549 | 3,549 |

On `VINAYAK APARTMENTS.dxf`, `recognition.host_openings` dominates both
versions — 162,573,519 `length_ft` calls at baseline versus 162,522,712 now
(119.5s versus 127.7s cumulative). It is O(symbols x walls^2) and predates this
work; the joints add roughly 4.5s of authoring and 6.3s of checks.

**Wall-clock timings from this machine are not reported.** Repeated runs of the
same drawing at the same commit varied between 17s and 709s under ordinary
desktop load (VS Code, Chrome, Notion; load average 6.6), and an 11-wall
drawing once measured 1065s. Those numbers say nothing about the code. Call
counts are load-independent and are what §5 rests on. A clean-machine
measurement is still outstanding.

## 6. Out of scope

- `Floor Plan.dxf`: a segment wall partially covered by a `WP:` profile stops
  authoring before any joint work runs.
- Joints involving `WP:` outline walls: IfcOpenShell cannot join a wall with no
  axis and no uniform thickness; those junctions are recorded untrimmed.
- Porting to `feature/dxf-layer-classification`.
- Revit and Archicad import behaviour: not available here, unverified.
