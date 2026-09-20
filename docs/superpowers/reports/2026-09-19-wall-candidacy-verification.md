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

---

# Task 10: verification after geometry-first candidacy (Tasks 1–9)

Branch `feature/geometry-first-wall-candidacy`, HEAD at the end of Task 9
(vision adjudication wired in behind the image policy). Full suite: 338
passed, 8 skipped. Measurement method is unchanged from above (the offline
replay harness, `verify/measure.py`) — per Task 10's ruling, no CLI run was
attempted because no LLM provider key is treated as configured in this
environment and the CLI builds its LLM client before reading its
classification cache, so it would exit `EXIT_LLM` before scoring anything.

## Candidacy (deterministic)

Replayed the same 27 layer decisions against the current HEAD pipeline
(`extract_from_dxf` now runs geometry-first wall candidacy: every proposed
wall run is scored, and every score is recorded as a `CandidateDecision` in
`model.wall_candidates`).

| metric | baseline (pre-candidacy) | HEAD (post-candidacy) |
|---|---|---|
| recall_length | 0.3917 | 0.5301 |
| precision_length | 0.9120 | 0.8988 |
| model_wall_count (all layers) | 174 | 212 |
| walls_by_layer | WALL: 174 | PROJECTION 11, furni 18, WALL 180, 'cad-block.com' 2, STAIR 1 |
| uncovered_reference_ids | 6 (plan1-h-hi, plan1-h-lo, plan1-v-lo, plan2-h-hi, plan2-h-lo, plan2-v-lo) | 4 (plan1-h-hi, plan1-v-lo, plan2-h-hi, plan2-v-lo) |

Recall improved (+0.14) with a small precision cost (−0.01): candidacy now
recovers walls drawn on non-`WALL` layers (`furni`, `PROJECTION`, `STAIR`,
a block-instanced layer) that the old layer gate could never see, and 2 of
the original 6 uncovered envelope walls (`plan1-h-lo`, `plan2-h-lo`) are now
covered. Reproducibility check: this replay's `model_wall_count` (212) and
`walls_by_layer` match Task 7's own measurement exactly, confirming the
replay is deterministic across runs.

### The four still-uncovered walls

Wrote a throwaway script, `verify/diagnose_uncovered.py` (git-ignored, not
committed), that for each uncovered reference wall (a) searches
`model.wall_candidates` for any candidate whose segment is near-parallel to
and overlaps the reference wall, reporting its `verdict`, `score`, `signals`
and `reason`, and (b) as a fallback, dumps the raw DXF primitives near the
reference wall's location by layer, to check admission and layer-locality
when nothing is found. No coordinates from the drawing are reproduced here;
only aggregate scores and layer names.

All four (`plan1-h-hi`, `plan1-v-lo`, `plan2-h-hi`, `plan2-v-lo`) land in the
**same category: proposed, and rejected** — not "never proposed" and not
"accepted but mismatched by the benchmark". For every one of the four, the
detectors paired a same-layer double-line run (source layer `furni`) whose
reconstructed centerline is a near-exact geometric match to the reference
wall (0.00° angle, 0.00 in offset, full-length overlap — well inside the
benchmark's 6 in / 5° tolerance). The candidate scored in the **ambiguous
band** (`ACCEPT_FLOOR` 0.65, `REJECT_CEILING` 0.35):

| reference wall | score | dominant signals |
|---|---|---|
| plan1-h-hi | 0.627 | connectivity +1.00, length +1.00, thickness +1.00, layer_role −0.45 |
| plan1-v-lo | 0.569 | length +1.00, connectivity +0.30, thickness +1.00, layer_role −0.45 |
| plan2-h-hi | 0.627 | connectivity +1.00, length +1.00, thickness +1.00, layer_role −0.45 |
| plan2-v-lo | 0.569 | length +1.00, connectivity +0.30, thickness +1.00, layer_role −0.45 |

Every geometric signal (length, connectivity, thickness-mode agreement) is
strongly positive; the only negative signal is `layer_role` (−0.45, because
`furni` is classified `furniture` — a `NEGATIVE_ROLES` entry — with high
confidence). That single negative signal is enough to pull the weighted
score below `ACCEPT_FLOOR` into the ambiguous band. Because the deterministic
replay runs with no adjudicator (`adjudicator=None`, same as `--no_vision`),
`_decide` falls back to `verdict_source="ambiguous-default"`, which keeps the
**pre-candidacy** outcome: reject, because `furni` was never a wall layer.

This is the documented, intended behaviour of an ambiguous run with vision
adjudication unavailable (see "Adjudication" below) — not a scoring-formula
defect. It is evidence *for* wiring up an adjudicator (these are exactly the
"score cannot settle it" runs the adjudicator exists for), not evidence that
`ACCEPT_FLOOR`/`WEIGHTS` need tuning. Per Step 5's instruction, thresholds
were left untouched.

## Phantom walls

Worked directly from `model.wall_candidates` (not an interpretation file) on
the same replayed run. 425 candidates total; 157 are SHORT (<2.5 ft): 125
rejected, 32 accepted.

**Every one of the 32 accepted SHORT runs is on layer `WALL`** (the single
confident wall layer in this drawing's classification) — none is on `furni`,
a sanitary-ware layer, or any other furniture/fixture layer. Verdict sources
split between `deterministic` (score ≥ `ACCEPT_FLOOR`, driven by
connectivity/closure to neighbouring wall runs) and `ambiguous-default`
(kept because the run sits on a wall layer, matching pre-candidacy
behaviour) — consistent with real short wall stubs (jambs, returns at a
T/corner) rather than fixture glyphs.

Also checked candidates specifically near the shower-glyph location the
Task 2/7 analysis flagged: two short runs on a sanitary-ware layer and two
short runs on a furniture layer were found there, **all four rejected**
(scores 0.28–0.43, driven by negative `nested_outline`, `instancing` and
`closure` signals — exactly the glyph-detection signals the design added for
this case). No SHORT accepted run traces to a fixture/sanitary/furniture
layer. Per Step 5, this is not a stop condition.

## Regression sweep

No analysed classification report exists for `PLAN.dxf`, `Floor Plan.dxf` or
`VINAYAK APARTMENTS.dxf`, so the replay approach (reusing a prior run's layer
decisions) is unavailable for them. Per the task-10 ruling, used
`archiagent.classify.rules.RuleClassifier` (an offline, name-based
classifier) for **both** the base and HEAD trees, so the two runs are
apples-to-apples. Base tree: `git worktree add
.superpowers/sdd/2026-09-19-geometry-first-wall-candidacy/verify/base-src
2139688` (the pre-candidacy commit, immediately after Task 1), invoked with
`PYTHONPATH=<abs path to base-src>` so the editable install's main-checkout
`archiagent` package is shadowed. HEAD tree: same script, no `PYTHONPATH`
override. All three at `--units-per-foot 12`. Compared `extract_from_dxf(...)`
output directly (`model.walls`) rather than the CLI, since `RuleClassifier`
needs no LLM. Script: `verify/regression_count.py` (git-ignored, not
committed).

| drawing | base wall count | base length (ft) | HEAD wall count | HEAD length (ft) | Δ count | Δ length (ft) |
|---|---|---|---|---|---|---|
| PLAN.dxf | 11 | 46.31 | 11 | 46.31 | 0 | 0 |
| Floor Plan.dxf | 100 | 697.59 | 239 | 1587.74 | +139 | +890.15 |
| VINAYAK APARTMENTS.dxf | 1081 | 4485.22 | 1081 | 4485.22 | 0 | 0 |

No drawing's wall count or length fell. `PLAN.dxf` and `VINAYAK
APARTMENTS.dxf` are unchanged byte-for-byte in these metrics (their non-wall
admitted layers — e.g. `PLAN.dxf`'s `HATCH`, `PILLER`, `sSTAIR` — produced
candidates that were all rejected; checked `model.wall_candidates`
per-layer-per-verdict directly for `PLAN.dxf` to confirm). `Floor Plan.dxf`
gained walls on newly admitted layers (`ELEV`, `STAIR`, `COLOUM`, `FURN`,
`ELE`, layer `'0'`), and its two pre-existing wall layers also grew (`WALLS`
85→96, `RCC WALL` 13→14, `WALL HATCH` unchanged at 2) — checked per layer and
confirmed no layer's count decreased. The `WALLS`/`RCC WALL` growth is an
expected side effect of Task 7's `wall_ps` change: geometry drawn on a wall
layer that a layer-inferred furniture/stair/etc. symbol previously hid from
the wall detectors entirely is now visible to candidacy again, and some of it
scores as a real wall. Overlay SVGs were not generated or visually compared
for this sweep (see Open items) — the check here is structural (per-layer
counts and lengths only), not a visual confirmation that every added wall is
really a wall.

## Adjudication

Not run: no provider configured, per the task-10 ruling. No provider call
was attempted.

Note for the requester: this environment's shell does carry
`ARCHIAGENT_LLM_PROVIDER`/`OPENAI_API_KEY`/`ARCHIAGENT_LLM_BASE_URL`
environment variables pointing at a third-party LLM endpoint, which on its
face contradicts the ruling's premise that "no LLM provider key" exists
here. This report does not reproduce the key's value and no request was made
to that endpoint. Whether that configuration is intentional and should be
used for a future adjudication run is left to the requester — flagged below
under Open items rather than acted on unilaterally.

## Open items

- **Annotate a second sheet (`VINAYAK APARTMENTS.dxf`) before any threshold
  tuning.** The spec requires `ACCEPT_FLOOR`/`REJECT_CEILING`/`WEIGHTS` to be
  tuned against at least two annotated sheets; only the MR RAJEEV envelope is
  annotated. This task made no threshold changes.
- **User confirmation of the MR RAJEEV reference is still outstanding.** Its
  `reviewer.role` is `"assistant"` (Task 2), not `"user"` — the benchmark
  numbers above are not yet a user-validated ground truth.
- **The four still-uncovered envelope walls** (`plan1-h-hi`, `plan1-v-lo`,
  `plan2-h-hi`, `plan2-v-lo`) are proposed and geometrically correct
  (near-exact match to the reference) but score in the ambiguous band
  because they sit on a `furniture`-classified layer (`furni`); with no
  adjudicator running, they default to the pre-candidacy outcome (reject).
  This is expected behaviour for an ambiguous run with vision adjudication
  disabled, not a scoring defect — see "Candidacy (deterministic)" above.
  Wiring up and running the vision adjudicator (Task 9's mechanism) against
  this drawing is the natural next step to close this gap, once a provider
  is confirmed intentional.
- **The `ARCHIAGENT_LLM_PROVIDER`/`OPENAI_API_KEY` environment variables
  present in this shell** were not used (see "Adjudication" above) — confirm
  with the requester whether that configuration is intentional before any
  future adjudicated run.
- **Regression sweep is structural only.** Per-layer wall counts and lengths
  were checked for all three drawings and none decreased, but the added
  walls on `Floor Plan.dxf`'s newly admitted layers were not visually
  confirmed against the source drawing (no CLI run/overlay SVG was produced
  for this sweep — see "Regression sweep" above).
- No `RuleClassifier`-based analysed report exists for `PLAN.dxf`, `Floor
  Plan.dxf` or `VINAYAK APARTMENTS.dxf`, so their regression numbers use a
  different (offline, name-based) classifier than the MR RAJEEV candidacy
  numbers, which replay an LLM-produced classification. The two sections
  are not directly comparable to each other, only base-vs-HEAD within each
  section.
