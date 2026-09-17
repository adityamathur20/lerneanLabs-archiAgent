# IFC Wall Joins — Design

**Date:** 2026-09-15
**Status:** Approved design (brainstormed with the user 2026-09-15), pending
spec review. Revised the same day to include `IfcWallStandardCase`,
`IfcRectangleProfileDef` and `ConnectionGeometry` (§2.6).
**Branch:** `feature/generic-ifc-replay-blender` only.

**Predecessors:** commits `95041cd` and `51b01b8` (floating-point residue no
longer fails IFC export; 9 of 10 sample DXFs export and pass reopened-IFC
validation). Joining a sliver wall would fail, so those fixes come first.

**Replaces:** the out-of-tree `fix_ifc.py` post-processor, which never worked
(§2.1), and the storey note "Separate sweeps; physical joint overlaps are not
trimmed".

---

## 1. What this builds

Walls in the authored IFC meet the way an architect draws them. At every L,
T and X intersection one wall runs continuously through the intersection and
the other stops at its face. No wall overlaps another, and no corner is left
notched.

Straight walls are written as standard parametric walls:

- `IfcWallStandardCase`
- an Axis line and a Body extrusion
- an `IfcRectangleProfileDef` wherever the joined outline is a rectangle
- a material layer set centred on the axis
- `IfcRelConnectsPathElements` carrying an `IfcConnectionPointGeometry` at
  the axis crossing

Bonsai can re-join the walls correctly if a user edits them, and other BIM
tools receive every parametric cue IFC offers.

The reopened-IFC checker verifies the joined geometry exactly, and verifies
the class, the profile type and the connection points.

---

## 2. What measurement established

Everything here was measured on 2026-09-15 with IfcOpenShell 0.8.5 in
throwaway spikes, or against the generic-branch models of the sample DXFs.

### 2.1 `fix_ifc.py` could never work

It calls `ifcopenshell.api.run("geometry.edit_path", …)`. No such usecase
exists in 0.8.5 (`ModuleNotFoundError`). The script caught the error for all
67 connections and wrote a byte-identical copy. The real usecases are
`geometry.connect_path` / `connect_wall` and
`geometry.regenerate_wall_representation`.

### 2.2 Regeneration needs three things archiagent does not write

A wall is only regenerated if it has an **Axis** representation
(`Plan/Axis/GRAPH_VIEW` context), an **`IfcMaterialLayerSetUsage`**, and the
Plan/Axis context in the file. With all three, an L corner of two 0.23 m
walls was rewritten from two overlapping rectangles into joined outlines.

### 2.3 How IfcOpenShell decides a joint

| Connection types | Result | Source |
|---|---|---|
| `ATEND`/`ATSTART` both, equal layer priority | Mitre | `Regenerator.join`, "mitering behaviour" branch |
| `ATEND`/`ATSTART` both, one layer priority higher | Higher-priority wall runs to the far face; the other stops at its near face (butt) | same branch, priority comparison |
| stem `ATSTART`/`ATEND` → through wall `ATPATH` | Through wall untouched; stem stops at its face | `connection2 == "ATPATH"` branch |
| `ATPATH`/`ATPATH` | Nothing happens; walls stay overlapped | early `return` |
| Two parallel walls | Nothing happens | "Parallel" early `return` |

Two traps:

- **`connect_path` deletes an existing connection at the same wall end.** A
  T stored as three walls ending at one point keeps only one of its three
  requested joins, and the result is wrong: the stem and one half are mitred
  like an L while the other half is untouched.
- **`RelatingPriorities`/`RelatedPriorities` on the relationship crash
  regeneration** (`TypeError: 'PrioritisedLayer' object does not support item
  assignment`). Priority must be set on `IfcMaterialLayer.Priority` instead.

### 2.4 The required shapes are reproducible

Spike results against the user's sketch (§3), measured as overlap area and as
the symmetric difference between the union of regenerated outlines and the
sketch geometry:

| Case | Method | Overlap | Difference from sketch |
|---|---|---|---|
| L, both 0.23 m | layer priority (blue 1, green 0) | 0 | 0 |
| L, green 0.23 / blue 0.115 | layer priority | 0 | 0 |
| L, green 0.115 / blue 0.23 | layer priority | 0 | 0 |
| T, through 0.23 / stem 0.115 | stem → through `ATPATH` | 0 | 0 |
| X, blue 0.23 / green 0.115 | two green pieces → blue `ATPATH` | 0 | 0 |

(An earlier run reported small differences for 0.115 m walls. That was the
spike's own 3-decimal rounding; the stored profile is exactly ±0.0575 m.)

### 2.5 What the models contain

Every junction in the model is between wall **ends**: `resolve_junctions`
splits walls at every node, so nothing passes through a junction.

| Drawing | Walls | L | T | X | Collinear | Distinct thicknesses |
|---|---|---|---|---|---|---|
| MR RAJEEV JI TWANI JI | 174 | 63 | 21 | 0 | 16 | 4 |
| VINAYAK APARTMENTS | 1,081 | 257 | 215 | 40 | 88 | 10 |

Timing: 46 walls with 529 joins created, connected and regenerated in
0.5 s total.

### 2.6 The three parametric cues work with the joins

A butt L of two `IfcWallStandardCase` walls was built with
`IfcRectangleProfileDef` bodies, layer priorities, and an
`IfcConnectionPointGeometry` passed to `connect_path`:

- **Joins:** `connect_path` and `regenerate_wall_representation` work
  unchanged on `IfcWallStandardCase`.
- **Profiles:** regeneration **rewrites both bodies to
  `IfcArbitraryClosedProfileDef`**, even though a perpendicular butt joint
  leaves both outlines rectangular. Keeping rectangles therefore requires
  converting them back after regeneration (§5 step 6). Any later Bonsai edit
  regenerates polygons again.
- **Connection geometry:** `connect_path` stores the connection geometry,
  and it survives regeneration. IfcOpenShell's wall code never reads it
  (only its space-boundary code uses `ConnectionGeometry`).
- **Validation:** `ifcopenshell.validate` with EXPRESS rules reports **0
  errors** (after naming the spike's `IfcProject`, which the rules require).

Schema facts, checked locally:

- `IfcWallStandardCase` is present in IfcOpenShell's IFC2X3, IFC4 and
  IFC4X3_ADD2 schemas.
- The bundled documentation carries no deprecation note.
- IfcOpenShell's API never creates it.
- The buildingSMART documentation pages returned HTTP 403, so deprecation
  status was not confirmed from the official site.
- An earlier draft of this spec called the class deprecated and removed; that
  claim is withdrawn.

---

## 3. Required geometry

Given by the user as a sketch. "Blue" is the wall that runs through the
intersection; "green" is the wall that stops.

- **L:** blue runs to the outer corner. Green stops at blue's inner face.
  Wrong: an empty corner square; both walls ending at the centreline.
- **T:** the through wall is always blue. The stem stops at blue's face.
- **X:** blue is continuous. Green is two pieces with a gap equal to blue's
  thickness.
- **No overlap anywhere.** The intersection square belongs entirely to blue.

**Choosing blue at L and X** (T has no choice), in order:

1. the thicker wall;
2. the longer **line**, where a line is the full chain of collinear,
   same-thickness, same-layer model walls through every junction, measured
   before any X decision;
3. the smaller position key: the lexicographically smaller
   `(min(start, end), max(start, end))` of that line.

The ranking is a total order, so every run produces the same IFC.

---

## 4. Runs — `archiagent/ifc/wall_runs.py` (new)

A **run** is the unit written as one wall entity: one or more model walls
chained end to end.

### 4.1 Inputs

`model.walls`, `model.junctions`, and `wall_layout(model)`. Walls mapped to an
accepted `WP:` wall profile never join a run and never join anything; they
are authored exactly as today, as `IfcWall`.

### 4.2 Straight-through partners

At a junction point, two member walls are partners when they share the
source layer, their thicknesses are equal (to 1e-9 ft), and their directions
leaving the point are opposite (cross product below 1e-7, the same
parallelism tolerance as wall detection).

### 4.3 Decisions per junction

| Junction | Partners | Decision |
|---|---|---|
| 2 walls, partners | — | chain (collinear joint, no connection) |
| 2 walls, parallel but not partners (thickness or layer steps) | — | **untrimmed**: IfcOpenShell ignores parallel walls |
| 2 walls, not parallel | — | **L**: rank the two walls' lines; blue gets the higher layer priority; `ATEND`/`ATSTART` connection |
| 3 walls | exactly one partner pair | **T**: chain the pair (blue); stem → blue `ATPATH` |
| 4 walls | two partner pairs | **X**: rank the two lines; chain the blue pair; each green piece → blue `ATPATH`; green pair is not chained |
| anything else | — | **untrimmed**: no connection; counted and reported |

"Anything else" includes a T whose through halves differ in thickness or
layer, Y-shaped and five-way junctions, and junctions touching a `WP:`
profile wall.

### 4.4 Output

Runs in a deterministic order. Each run records its member model-wall
indices in chain order, its axis start and end (the chain's outer
endpoints), thickness, source layer, merged source IDs, and its per-end joint
roles. It also records the list of connections with their junction points,
and the untrimmed junction points. Nothing in `BuildingModel` changes, so
frozen interpretations and replay are unaffected.

### 4.5 Naming and identity

A run is named `W{lowest member index:03d}` and carries
`ModelWallIndices` (JSON list) in `ArchiAgent_Provenance`. A wall that is not
merged keeps its current name.

Stable GlobalIds hash the entity class name (`identity.assign_stable_ids`).
Switching straight walls to `IfcWallStandardCase` would otherwise change the
GlobalId of **every** wall. The identity key therefore uses `IfcWall` for
`IfcWallStandardCase`, so an unmerged wall keeps its GlobalId. Merged members
other than the lowest no longer exist as separate wall entities.

---

## 5. Authoring — `archiagent/ifc/author.py`

Order within `_author_plan`:

1. **Context.** `author_building` adds `Plan` and `Plan/Axis/GRAPH_VIEW`
   contexts once per file.
2. **Material layer sets.** One `IfcMaterialLayerSet` per
   `(thickness, priority)` pair, with one layer of that thickness and
   `IfcMaterialLayer.Priority` set. The material is named after the wall
   presentation preset. Only walls involved in an L need a nonzero priority,
   where priority is the wall's rank from §3.
3. **Runs.** For each run:
   - an `IfcWallStandardCase` named per §4.5;
   - an Axis `IfcPolyline` from run start to run end;
   - a Body `IfcExtrudedAreaSolid` over an `IfcRectangleProfileDef`
     (`XDim` = run length, `YDim` = thickness, positioned so the axis is
     centred);
   - placement at the run start;
   - an `IfcMaterialLayerSetUsage` with `LayerSetDirection=AXIS2`,
     `DirectionSense=POSITIVE`, `OffsetFromReferenceLine=-thickness/2`;
   - provenance as today plus `ModelWallIndices`.
4. **Connections.** `geometry.connect_path` for every connection from §4.3.
   No relationship priorities.
5. **Regeneration.** `geometry.regenerate_wall_representation` on every run
   with at least one connection.
6. **Parametric profile restore.** For each regenerated Body item whose swept
   area is an `IfcArbitraryClosedProfileDef` with four distinct points that
   form a rectangle aligned with the wall's local axes (to 1e-9 m):
   - replace the swept area with an `IfcRectangleProfileDef` of the same
     extent;
   - remove the orphaned polyline profile.

   Outlines that are not rectangles, such as non-perpendicular joints, keep
   the polygon and are counted in the storey provenance.
7. **Presentation.** Re-apply the wall presentation style to the final Body
   item, because regeneration replaced the styled item.
8. **Connection geometry.** After regeneration, which may move placements,
   set each relationship's `ConnectionGeometry` to an
   `IfcConnectionPointGeometry`:
   - `PointOnRelatingElement`: the junction point in the relating wall's
     object coordinates, as an `IfcCartesianPoint` with z = 0;
   - `PointOnRelatedElement`: the same point in the related wall's
     coordinates.
9. **Openings.** Hosted on the run containing the opening's model host wall
   (`host_name` resolves model wall index → run name). Placement stays
   absolute, so regeneration does not move voids.
10. **Storey provenance.**
    - `JointGeometry` becomes `"Butt joints; intersection owned by one wall"`.
    - `UntrimmedJunctionsJSON` lists untrimmed junction points.
    - `NonRectangularWallProfiles` counts step 6's exceptions.

`validate.py` drops the `joint_solids_untrimmed` warning. Its own issue
entity labels (`W{idx:03d}`) refer to model walls and are unchanged.

Joins are always on; there is no CLI flag.

---

## 6. Checker — `archiagent/ifc/inspect.py`

### 6.1 Unchanged

The following stay as they are:

- schema validation, which now includes `IfcWallStandardCase`'s EXPRESS
  WHERE rules;
- entity counts (`by_type("IfcWall")` includes the subtype);
- containment and GlobalId uniqueness;
- opening void and filling relationships (the expected host is now the run
  name);
- exact volume and bounds for slabs, columns, beams, doors, windows and `WP:`
  walls.

### 6.2 Exact run expectations

For each run, the checker predicts its footprint from §3 and §4 in feet:

- **Green end at an L, T or X:** clip the run rectangle by the half-plane
  behind blue's near face.
- **Blue end at an L:** extend the run along its axis to green's far face,
  then clip by that face line.
- **Blue at a T or X, chained ends, and free ends:** no change.

Expected volume is footprint area × wall height minus the door and window
voids. Voids are computed along the run axis as today, including the flush
tolerance from `95041cd`. Expected bounds come from the footprint and the
void-adjusted z range. Existing tolerances and codes apply
(`solid_volume_mismatch`, `solid_bounds_mismatch`). The predictor is general
for non-perpendicular joints, because it clips by face lines rather than
assuming rectangles.

### 6.3 Parametric structure checks

- **`wall_class_mismatch`:** a run must be `IfcWallStandardCase`; a `WP:`
  wall must be plain `IfcWall`.
- **`wall_parametric_data_missing`:** every run must have both Axis and Body
  representations, and an `IfcMaterialLayerSetUsage` whose total layer
  thickness equals the run thickness and whose offset is −thickness/2.
- **`wall_profile_not_parametric`:** a run whose predicted footprint is a
  rectangle must have an `IfcRectangleProfileDef` Body with matching
  `XDim`/`YDim`.
- **`connection_geometry_mismatch`:** every `IfcRelConnectsPathElements`
  must carry an `IfcConnectionPointGeometry`. Both of its points, transformed
  to world coordinates, must lie within `BOUNDS_TOL_M` of the model junction
  point. The connection types must match §4.3.

### 6.4 Storey-wide checks

- **`wall_overlap`:** project each run solid's triangles to XY, union them
  per run, and intersect pairs found via an STRtree. Overlap above
  `1e-6` m² is an error unless it lies within the square of an untrimmed
  junction.
- **`wall_coverage_mismatch`:** the union of predicted run footprints must
  equal the model's ideal wall area. The ideal is each model wall's
  rectangle, extended along its axis at every junction end by half the
  thickness of the thickest other wall at that junction. This reproduces the
  sketch's union at L, T and X for any thickness combination, and does not
  depend on which wall is blue. (`spaces._wall_bodies` is not used: it
  buffers each thickness separately, so a mixed-thickness L would leave the
  outer corner quarter empty.) Any symmetric difference above `1e-6` m²
  outside untrimmed junction squares is an error. This catches a predictor or
  ranking bug that would leave a gap.

The report records the measured overlap and coverage areas and the untrimmed
junctions as warnings, so a failure shows where and by how much.

---

## 7. Deliberately not done

| Suggestion | Why not |
|---|---|
| Connection-level `RelatingPriorities`/`RelatedPriorities` | Crash in 0.8.5 (§2.3); layer priorities give the same joint |
| `IfcConnectionCurveGeometry` | Two axes meet at a point; the curve type describes a connection along a line |
| Post-processing the written IFC | The model already has exact centrelines; `WP:` walls are not rectangles |
| Mitred corners | Contradicts §3 |
| `IfcWallStandardCase` / rectangle profiles for `WP:` walls | Their outlines are arbitrary polygons with holes, which neither describes |

---

## 8. Testing

All tests use synthetic plans; no client drawings enter the repository.

**`checks/test_wall_runs.py`**
- Collinear walls chain; a thickness or layer change breaks the chain.
- A T chains the through pair; the stem stays separate.
- An X chains only the blue line; each rule of §3 decides blue in turn.
- `WP:`-mapped walls are excluded; mismatched T halves are untrimmed.
- Results are identical under permutation of `model.walls` input order.

**`checks/test_wall_joins_ifc.py`**
- One authored-and-reopened plan per sketch case: L (green thicker, blue
  thicker, equal), T and X. Assert each wall's XY footprint equals the sketch
  geometry and total overlap is 0.
- Runs are `IfcWallStandardCase`; `WP:` walls stay `IfcWall`.
- Every run has Axis and Body representations and a layer set usage with
  offset −thickness/2.
- Perpendicular joined runs have `IfcRectangleProfileDef` bodies with the
  sketch's dimensions; a non-perpendicular joint keeps a polygon and is
  counted.
- Every connection has an `IfcConnectionPointGeometry` whose points map to the
  junction.
- Wall presentation style survives regeneration and the profile restore.
- A door void still voids its run; unmerged walls keep names and GlobalIds.
- `ifcopenshell.validate` with EXPRESS rules reports no errors.

**`checks/test_export_validation.py` additions**
- A joined model passes.
- Each of the following fails with its matching error code:
  - a shifted run
  - a deleted run
  - a lengthened run
  - a duplicated overlapping wall
  - a run downgraded to `IfcWall`
  - a rectangle profile replaced by a polygon
  - a connection point moved off its junction

**Existing tests** whose expectations change legitimately (for example
the host wall volume in `test_semantic_ifc.py`) are updated with a one-line
reason in the commit.

---

## 9. Verification and done

- Full suite passes (baseline 253 passed, 8 skipped).
- All 10 sample DXFs re-run with the current settings (`--walls`,
  `--no_vision`, `--units-per-foot 12` for `PLAN` and `Floor Plan`). For each
  run, record:
  - the exit code and checker result;
  - joined vs untrimmed junction counts;
  - rectangle vs polygon run profiles;
  - total overlap, which must be 0.
- One Blender package built and a corner, a T and an X inspected against the
  sketch.

**Done:** all of the above pass, and 9 of 10 drawings produce an IFC that
passes the new checker.

---

## 10. Out of scope / follow-ups

- `Floor Plan.dxf` still stops on "wall 104 partially overlaps accepted wall
  profiles" (a segment wall partly covered by a `WP:` profile). Separate bug.
- Porting to `feature/dxf-layer-classification`.
- Joins involving `WP:` profile walls.
- Checking the files in Revit and Archicad. Not available in this
  environment; the parametric cues in §1 are included so those tools have
  what IFC defines, but their import behaviour is unverified.
