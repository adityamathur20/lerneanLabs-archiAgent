# Geometry-first wall candidacy

Date: 2026-09-18
Status: design, pending implementation plan

## Problem

Wall detection is gated on layer role. `pipeline._assemble` computes

```python
wall_layers = layers_for_roles(classification, WALL_ROLES,
                               min_confidence=WALL_CONFIDENCE_FLOOR)   # 0.70
```

and `detect_walls_paired_lines` then iterates `ps.by_layer(wall_layers)`
(`archiagent/geometry/walls.py:106`). Entities on any other layer are never
enumerated, so they are never offered for pairing. The layer decision is made
once, before any geometry is examined, and applied uniformly to every entity on
that layer.

This produces symmetric failures, both observed on
`input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf`:

**False negatives.** Three of the four outer boundary faces in Plan 1 and Plan 2
are drawn on `furni`, classified `furniture` at 0.90. They are geometrically
identical to Plan 3's boundary, which is drawn on `WALL` and detected
correctly — two parallel lines ~9in apart running the full plan length and
closing into a rectangle. Further misses sit on `PROJECTION` (`annotation`,
0.45) and `STAIR`. The same physical mid-plan wall is on `furni` in Plan 2 and
`STAIR` in Plan 3, confirming layer assignment is not systematic within the
sheet.

**False positives.** A shower-head glyph nested three blocks deep
(`1070/0/1:INSERT/0/0:LWPOLYLINE`) draws two concentric closed rectangles 0.5
units apart on layer `WALL`, plus a centre circle and a hex hatch. The detector
pairs the two rectangles at 12.3in and authors an `IfcWallStandardCase` roughly
1 ft long, unconnected to any junction, replicated at every fixture instance.

Neither is catchable by the current checks: `validate_export` verifies the
authored IFC against the pipeline's own wall list, never against the source
drawing. A wall never proposed cannot be reported missing, and a phantom wall
with real parallel faces and a real volume passes every geometric check.

## Root cause

Layer gating is the cause of both directions. A layer-level label has no correct
value for a mixed layer: `furni` holds ~1900 genuine furniture polylines *and*
three boundary walls. Calling it furniture loses the walls; calling it a wall
role turns 1900 furniture polylines into walls.

This is a granularity error, not an accuracy error, which is why no improvement
to layer classification can fix it — including vision. Stage 2 already renders
per-layer PNGs and sends them to the model; a correct reading of a `furni`
render still says "mostly furniture".

## Non-goals

- Blocking outer-boundary closure check. Closure is used here as a *scoring
  signal*; making an open envelope a blocking issue is source-fidelity
  validation and belongs in its own spec.
- Cross-plan corroboration (Plan 3's closed boundary versus Plans 1/2's open
  one). Needs per-plan regions; see the sheet-survey spec.
- General validation of the authored model against the source drawing.
- Curved and filled-body wall detection beyond what
  `detect_walls_filled_bodies` already does.

## Independence from the sheet survey

This change does **not** depend on
`2026-09-18-dxf-sheet-survey-design.md`, and should be built first.

Measured on the failing sheet: the narrowest gap between plans is 11,449
drawing units, while `resolve_junctions` uses `tol_in=1.0`
(`archiagent/geometry/junctions.py:167`). Cross-plan connectivity is therefore
impossible by four orders of magnitude. Loop closure is topological over
connected components, and the plans are disjoint components. `_interval_pairs`
only pairs faces active at the same span position, so paired-line generation
cannot straddle plans either. `reject_ladder_runs` already buckets by span in
1 ft groups specifically to handle multi-drawing sheets.

Modal wall thickness is computed sheet-wide, which yields more samples rather
than fewer.

## Architecture

`detect_walls_paired_lines` and `detect_walls_filled_bodies` are **unchanged**.
They already pair layer-locally and record `source_layer` on every `WallSeg`.
They simply receive a wider layer set. The gate moves from "which layers may be
paired" to "which candidates survive scoring".

```
classification
 └─ candidate_layers(classification)                     NEW
 └─ detect_walls_paired_lines(ps, candidate_layers, …)   unchanged
 └─ detect_walls_filled_bodies(ps, candidate_layers, …)  unchanged
 └─ combine_wall_hypotheses(…)                           unchanged
 └─ score_candidates(walls, classification, ctx)         NEW, deterministic
 └─ adjudicate_ambiguous(…)                              NEW, vision, optional
 └─ reject_ladder_runs(…)                                unchanged
 └─ (accepted, rejected+reasons) → junctions → IFC
              └─ provenance record
```

This mirrors precedent already in the codebase: `reject_ladder_runs` proposes
then rejects geometrically and returns `(kept, rejected)`. This work
generalises that shape and widens what gets proposed.

### `candidate_layers(classification)`

Returns every layer except those held at high confidence in one of four roles:
`dimension`, `text_label`, `title_block`, `grid`. These are the roles where
paired parallel lines are systematically not walls and are numerous — dimension
witness lines in particular run parallel at wall-like spacings and would flood
the candidate set.

The exclusion applies **only at or above `WALL_CONFIDENCE_FLOOR`**. A layer
shakily called `dimension` is still paired, so the class of bug this work exists
to remove cannot reappear through the exclusion list.

Every other role is paired: `furniture`, `annotation`, `stair`, `electrical`,
`plumbing`, `ignore`, and unclassified layers. All three known miss layers
(`furni`, `PROJECTION`, `STAIR`) are covered.

## Scoring

`score_candidates` is pure and takes the **whole candidate set**, not one
candidate at a time: connectivity, loop closure and modal thickness are all
set-level properties. It returns a score and a signal breakdown per candidate.

Layer role is one weighted input, never a gate.

| Signal | Reads on the known cases |
|---|---|
| layer role + confidence | `furni` mild negative; `WALL` strong positive |
| run length | 780-unit boundary strong; ~1 ft shower weak |
| end connectivity | boundary meets accepted `WALL` at both corners; shower meets nothing |
| loop closure participation | boundary closes a plan rectangle; shower closes only itself |
| thickness vs. modal wall thickness | boundary ~9in matches; shower 12.3in also plausible — **cannot reject alone** |
| repeated block instancing | shower's `source_ids` nesting path shows one block inserted 5× |
| enclosed glyph content | shower loop encloses a circle and a hex hatch |
| nested concentric closed faces | shower's two faces are a closed rectangle inside another closed rectangle — an outline, not two wall faces |

The last signal is the sharpest rejector for the phantom wall and is purely
structural. Thickness agreement is explicitly insufficient on its own and is
documented as such, because the phantom wall's measured thickness is plausible.

Modal wall thickness is derived from candidates whose layer holds a wall role at
or above `WALL_CONFIDENCE_FLOOR`, so the reference distribution comes from
uncontested walls.

### Why no layer-name prior is needed

The analysis recommended decoupling obvious layers from LLM confidence swings —
"a layer literally named WALL shouldn't be one bad classification run away from
disappearing". This spec deliberately adds **no** name-keyword prior:

- `prompt.py` explicitly warns that abbreviations and spelling variants are
  hypotheses, not a universal keyword dictionary.
- `S-WALL` on this very drawing is the counter-example: it hosts the shower-head
  inserts, and a name prior would have strengthened the phantom wall.

Under geometry-first candidacy the concern dissolves without a dictionary. A
layer named `WALL` is paired regardless of its assigned role, and geometry
decides. The recommendation is satisfied structurally rather than lexically.

## Thresholds and the ambiguous band

The score is a weighted sum of the signals above, each normalised to `[-1, 1]`,
with the total normalised to `[0, 1]`. Weights and thresholds are module
constants with these **starting values, to be tuned against the wall-coverage
metrics** once the reference sheets are annotated — they are not claimed to be
calibrated:

```
ACCEPT_FLOOR   = 0.65     # score >= this  -> accept
REJECT_CEILING = 0.35     # score <= this  -> reject
```

`ACCEPT_FLOOR > REJECT_CEILING` is required; the bands must not overlap, and a
candidate exactly on either boundary takes the decisive verdict (`>=` accepts,
`<=` rejects) rather than falling into the ambiguous band.

Initial weights give connectivity, loop closure and nested-concentric-faces the
largest magnitudes, since those are the signals that separate the two known
defects; layer role is deliberately mid-weight so it can be outvoted by
geometry, which is the entire point of the change.

Ambiguous candidates escalate to vision adjudication when enabled. Those that
remain unadjudicated — adjudication disabled, over the batch cap, or any failure
path — **default to reject**, and are always recorded in provenance and reported
as an Issue at `warn`.

Rationale: the project's stated principle is that an omission should be
conspicuous, not absorbed (`layers.py`, on `source="default"`). A rejected
candidate with a full score breakdown and a warning is conspicuous; a phantom
wall in the IFC is not.

This is the setting most likely to need revisiting once wall-coverage metrics
exist, since the opposite argument is also real: a phantom wall is visible in
the overlay while a missing wall is invisible until someone re-reads the DXF.
It is called out here so the choice is deliberate rather than inherited.

## Vision adjudication

Only candidates in the ambiguous band, and only when enabled.

Each batch renders the candidate highlighted in its local context and calls the
existing `LLMClient.classify_json_vision`. Batching follows
`dxf_classifier._batches`; the cap is `ADJUDICATION_MAX_CANDIDATES`. Beyond the
cap, the deterministic verdict stands and an Issue is reported.

Reply schema, one record per candidate id:

```json
{"candidates": [{"id": "cand-0007", "verdict": "wall|not_wall",
                 "confidence": 0.0, "reason": "..."}]}
```

Adjudication is an improvement, never a dependency — the stance already taken
for stage 2. Renderer unavailable, render failure, transport error, malformed
reply, unknown or omitted ids: each falls back to the deterministic verdict,
reports an Issue, and never fails the build.

Adjudication is **on by default** for DXF input, matching the shipped stage-2
vision policy, and is disabled by `--no-wall-adjudication`. With it disabled the
pipeline stays fully deterministic.

## Provenance

Every candidate, accepted and rejected, is recorded: `source_ids`, layer, role
and confidence, thickness, length, each signal's computed value, total score,
verdict, verdict source (`deterministic` or `adjudicated`), and reason.

Written into the interpretation JSON as `wall_candidates`, alongside the
existing `walls`. This makes the next investigation a lookup rather than the
raw-DXF archaeology this analysis required.

## Measurement

`benchmark.py` already provides scoped precision/recall with IoU matching and
partial-annotation handling, but its docstring states: *"Wall-coverage metrics
are not implemented."* That gap is closed here.

1. Extend the reference JSON schema with wall records (centerline endpoints,
   thickness, status), following the existing `symbols` record conventions.
2. Implement wall-coverage metrics in `evaluate_reference`, honouring the
   existing scope semantics: only complete, explicitly reviewed scopes enable
   precision/recall, and predictions outside reviewed scopes are never counted
   as false positives.
3. Annotate the outer boundary loops of all three plans in
   `MR RAJEEV JI TWANI JI.dxf` as one complete scope — roughly 12 wall runs,
   not the whole drawing.
4. Annotate a second sheet, `VINAYAK APARTMENTS.dxf`, before finalising
   thresholds.

Thresholds are tuned against precision/recall, not against an overlay.

## Changes to existing code

| File | Change |
|---|---|
| `archiagent/classify/layers.py` | add `candidate_layers()`; `layers_for_roles` and `WALL_CONFIDENCE_FLOOR` retained |
| `archiagent/geometry/candidacy.py` | new; scoring and verdicts, pure |
| `archiagent/classify/wall_adjudicator.py` | new; vision adjudication of the ambiguous band |
| `archiagent/pipeline.py` | pass `candidate_layers` to the detectors; insert scoring before junctions |
| `archiagent/benchmark.py` | wall records and wall-coverage metrics |
| `archiagent/cli.py` | `--no-wall-adjudication` |

`_assemble`'s `_no_wall_layers()` guard must keep meaning "no layer was
*classified* as a wall", not "no candidate layers exist" — it continues to key
off `layers_for_roles`, not `candidate_layers`.

## Testing

Scoring lives in a pure function testable with constructed `WallSeg` values and
no client, mirroring how `decisions_from_reply` isolates validation.

- each signal independently, on synthetic geometry
- the three known cases as regressions: `furni` boundary accepted, shower head
  rejected, `PROJECTION` lands in the ambiguous band
- threshold boundaries, and that a candidate exactly at a threshold is handled
  in one documented direction
- `candidate_layers`: each excluded role at, above and below the confidence
  floor
- adjudication failure paths: each falls back to the deterministic verdict and
  reports an Issue
- provenance completeness: every candidate appears exactly once with a verdict
- reference harness: wall precision/recall on both annotated sheets

## Risks

- **Overfitting to one drawing.** Mitigated by annotating a second sheet before
  finalising thresholds.
- **Candidate volume and runtime** on a three-plan sheet. To be measured during
  implementation, not assumed.
- **Existing test diffs.** More walls will be detected; the diffs must be read
  rather than rubber-stamped, since this change is capable of adding wrong walls
  as well as right ones.
- **Ambiguous-band default.** See the rationale above; revisit once metrics
  exist.
