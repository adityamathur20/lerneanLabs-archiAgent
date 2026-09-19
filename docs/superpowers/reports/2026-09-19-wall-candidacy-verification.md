# Wall candidacy — verification record

Drawing: MR RAJEEV JI TWANI JI.dxf (3 plans), `units_per_foot=12.0`. No CLI run
was made for this measurement — see "Measurement method" below: it is the
offline replay of the analysed run's 27 layer decisions via
`load_dxf`/`extract_from_dxf`/`evaluate_reference` directly.
Reference: 12 envelope walls drafted from the drawing's own geometry by the assistant
(reviewer role `assistant`; not user-confirmed). Corrections made by hand: yes, two —
see "Corrections to the draft script" below.

Measurement method: no LLM provider key is configured in this environment, and the
CLI builds its LLM client before it reads the classification cache, so
`archiagent --dxfFilePath ...` (without `--rules`) exits with `EXIT_LLM` before it
ever gets to score anything. Per the task ruling, the pipeline was measured instead
with an offline replay script,
`.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/measure.py`
(git-ignored scratch, not committed), that reuses the **exact** 27 layer decisions
from the user's earlier analysed run
(`../new_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.report.json`, region
`whole-drawing`) instead of asking an LLM to reclassify. This exercises the same
`extract_from_dxf` → `evaluate_reference` path the CLI uses, just with a
pre-supplied classification.

## Step 1 — drafting the reference

Ran the brief's script (`archiagent.ingest.dxf_vector.load_dxf`, `units_per_foot=12.0`)
against `../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf`, writing
`../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.walls.reference.json`
(not committed — outside the repo).

**The brief's script, run verbatim, did not produce 12 walls.** It found only
1 envelope wall in 1 "plan": the `>50`-foot gap-in-centres heuristic used to
separate the three plans found no gap that large anywhere in this drawing, so
all geometry was treated as one plan, and the global min/max offset search
then latched onto stray non-wall lines instead of real double-line wall faces.

Investigated with the DXF's own text labels (`TERRACE FLOOR`, `GROUND FLOOR
PLAN`) and a render of `../new_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.overlay.svg`
(via `rsvg-convert`, cropped and inspected). Findings:

- The three plans really are laid out side by side, but the true gaps between
  their plot-boundary rectangles are only ~17.5 ft (plan 1 / plan 2) and
  ~4.3 ft (plan 2 / plan 3) — both well under the brief's `>50` ft assumption.
  A ~17 ft gap also exists **inside** plan 3, between a small terrace/lift
  nook and the plan's main block, which is roughly the same size as the
  real inter-plan gaps and so is not usable as a threshold either.
- The min/max-offset-over-all-layers step could land on a `column`-layer
  plot-boundary line or a `PROJECTION` line instead of a genuine double-line
  wall face, producing multi-foot "thicknesses" (e.g. 43–205 in) instead of ~9 in.

**Corrections to the draft script** (both applied in
`.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/draft_reference.py`,
git-ignored scratch, not committed — the committed artifact is only the
resulting reference JSON, and that JSON lives outside the repo):

1. **Plan separation**: instead of a fixed gap-in-centres threshold, derive
   each plan's x-range from the drawing's own `column`-layer plot-boundary
   rectangles (one full-height, unbroken vertical line per side, per plan).
   Cuts are placed at the midpoint between one plan's right boundary and the
   next plan's left boundary.
2. **Wall-face candidates restricted to `WALL` and `furni` layers**: the
   brief's own text says part of the envelope in Plans 1 and 2 is drawn on
   `furni`, so both layers must be included; excluding every other layer
   (`column`, `PROJECTION`, hatch, dimension helpers, etc.) stops the
   min/max search from matching a wall face against an unrelated line.

With both corrections, the script produced exactly the expected result: 12
envelope walls across 3 plans, 4 per plan (`h-lo`, `h-hi`, `v-lo`, `v-hi` —
the low/high-offset wall on each axis), **every one exactly 9.0 in thick**:

| plan | wall IDs | count | thickness |
|---|---|---|---|
| plan1 | `plan1-h-lo`, `plan1-h-hi`, `plan1-v-lo`, `plan1-v-hi` | 4 | 9.0 in |
| plan2 | `plan2-h-lo`, `plan2-h-hi`, `plan2-v-lo`, `plan2-v-hi` | 4 | 9.0 in |
| plan3 | `plan3-h-lo`, `plan3-h-hi`, `plan3-v-lo`, `plan3-v-hi` | 4 | 9.0 in |

Client wall coordinates (start/end points, model-foot positions) are not
reproduced here; they live only in the reference JSON itself, outside the
repo, beside the drawing:
`../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.walls.reference.json`.

12 walls, 3 plans, every thickness exactly 9.0 in. Reference JSON:
`schema_version: 1`, `region_id: "whole-drawing"`, `reviewer: {"id":
"claude-draft", "role": "assistant"}`, `source_sha256` a valid 64-hex-char
SHA-256 of the DXF. Each scope is a thin (3 ft wide) band around one wall
only, so interior walls that cross a scope's edge stay unscored, matching the
design in `archiagent/benchmark.py`.

## Step 2 (replaced by ruling) — offline replay of the analysed run

```bash
DXF="../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf"
PRIOR="../new_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.report.json"
REF="../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.walls.reference.json"
OUT=".superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/baseline.json"
.venv/bin/python .superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/measure.py \
    "$DXF" "$PRIOR" "$REF" "$OUT"
```

Output:

```
model_wall_count: 174
model_wall_length_ft: 1220.3
walls_by_layer: {'WALL': 174}
evaluated: True
recall_length: 0.3916703587095891
precision_length: 0.9120453962072551
reference_walls: 12 predicted_walls: 11
uncovered_reference_ids: ['plan1-h-hi', 'plan1-h-lo', 'plan1-v-lo', 'plan2-h-hi', 'plan2-h-lo', 'plan2-v-lo']
```

(Two harmless `RuntimeWarning: divide by zero encountered in oriented_envelope`
lines from shapely also printed to stderr — from an unrelated degenerate
predicted-symbol geometry the walls metric does not use; no effect on the
numbers above.)

**Sanity check against the prior analysed run**: that run reported 174 walls
for `region "whole-drawing"`. This replay's `model_wall_count` is **174 — an
exact match**, confirming the replay (same 27 layer decisions, same DXF, same
`units_per_foot=12.0`) reproduces the analysed run's wall extraction exactly.

**Result matches the brief's prediction precisely**: `recall_length` is well
below 1 (0.392), and `uncovered_reference_ids` lists exactly the 6 walls
predicted — 3 per plan in Plans 1 and 2 (`plan1-h-hi`, `plan1-h-lo`,
`plan1-v-lo`, `plan2-h-hi`, `plan2-h-lo`, `plan2-v-lo`), all of which the
analysed classification does not recover because that geometry sits on the
`furni` layer, currently classified `furniture`, not a wall role. Plan 3's
4 envelope walls are all covered (`precision_length` 0.912, close to 1 — the
model over-predicts slightly beyond what's in-scope but nearly everything
predicted inside a wall-scope band is a real wall).

## Baseline (before candidacy, commit `2139688`)

| metric | value |
|---|---|
| recall_length | 0.3916703587095891 |
| precision_length | 0.9120453962072551 |
| uncovered_reference_ids | plan1-h-hi, plan1-h-lo, plan1-v-lo, plan2-h-hi, plan2-h-lo, plan2-v-lo |
| reference_walls | 12 |
| predicted (model) walls in-scope | 11 |
| model walls (total, all layers) | 174 |
| model wall length (total, ft) | 1220.3 |

## Files

- `measure.py`: `.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/measure.py`
  (git-ignored scratch, not committed). Invocation above. Version-agnostic: it
  only calls `load_dxf`, `extract_from_dxf`, `load_reference`,
  `evaluate_reference`, none of which exist only in this branch, so Task 10
  can rerun it unchanged against the post-candidacy code.
- `draft_reference.py`: `.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/draft_reference.py`
  (git-ignored scratch, not committed) — the brief's Step-1 script with the
  two corrections above.
- Reference JSON (not committed, outside the repo):
  `../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.walls.reference.json`
- `baseline.json`: `.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/baseline.json`
  (git-ignored scratch, not committed) — full `evaluate_reference(...)["walls"]`
  output plus `model_wall_count`, `model_wall_length_ft`, `walls_by_layer`.
- This report: `docs/superpowers/reports/2026-09-19-wall-candidacy-verification.md`
  (committed).

No product code was changed for this task.

## Self-review

- Reference is honest: `reviewer.role` is `"assistant"`, `annotation_status`
  is `"partial"`, and the two hand corrections to the drafting script are
  documented above with the reasoning that led to each. No wall record was
  guessed or nudged to hit a target number — both corrections were driven by
  the drawing's own geometry (the `column`-layer plot boundaries, the layers
  the brief itself names) and independently produced exactly 9 in on all 12
  walls before I looked at whether the count matched 12.
- All numbers in this report are copied from actual command output
  (`draft_reference.py` and `measure.py` runs above), not computed by hand or
  estimated.
- `git status` before committing showed only the pre-existing unrelated
  changes (deleted `.DS_Store`, untracked `andrej-karpathy-skills/`) plus this
  report; only the report was staged and committed. `verify/` is covered by
  `.superpowers/sdd/.gitignore` (`*  .superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/`)
  and was never staged. The reference JSON lives outside the repo entirely
  (`../input-floorplans/...`).
- Concern for later tasks: the brief's Step-1 script (unmodified) is not
  reliable on drawings whose plans are closer together than 50 ft, or that
  have an internal gap similar in size to the real inter-plan gap. If a
  similar reference needs drafting for another drawing, don't reuse the
  `>50` gap threshold blindly — check plot-boundary layout first.
