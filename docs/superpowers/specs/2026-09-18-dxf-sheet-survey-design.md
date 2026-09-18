# DXF Sheet Survey — vision-derived plan regions

Date: 2026-09-18
Status: design, pending implementation plan

## Problem

`LayerStats` are computed across the entire modelspace. Real drawings in
`input-floorplans/dxf/` are sheets holding several drawings side by side — a
ground floor plan, a first floor plan, a front elevation, sometimes a plumbing
or electrical overlay. A `WALL` layer's `entity_share`, `bbox`, `extent_ratio`
and `length_p50` are therefore a blend of every view on the sheet.

Two costs follow:

1. **Stage 1 classifies on contaminated aggregates.** The escalation triggers
   `large_non_wall_layer` and `competing_wall_layers` both derive from those
   blended numbers, so stage 2 vision is invoked to resolve confusion the
   statistics themselves created.
2. **Geometry reconstruction consumes the whole sheet.** Without an explicit
   `--region`, elevations, sections and duplicate floors feed the same
   extraction as the plan.

The pipeline already solves both — but only when a human supplies the regions.
`propose_regions()` computes exact candidate bounds and states the gap in its
own docstring:

> `"""Spatial candidates only; titles/discipline/elevation need review."""`

This design automates that review step.

## Non-goals

- Per-element detection (furniture, fixtures, doors, windows, materials) and
  room-level semantics. Deferred; the classifier's output type is one role per
  *layer*, and element inventory is a different artifact with different
  consumers.
- Replacing stage 2. It remains in place as the safety net and is expected to
  fire less often, not never.
- Any vision-derived coordinate. See "Geometry safety" below.

## What this does NOT fix

This is a layer-classification and region-partitioning change. It does not
address wall detection, and must not be read as doing so.

The wall-detection failures analysed on `MR RAJEEV JI TWANI JI.dxf` — missing
outer walls in two of three plans, and a phantom wall built from a shower-head
fixture — are **granularity** failures, not accuracy failures:

- **Missing outer walls.** Three of Plan 1's and Plan 2's boundary faces are
  drawn on `furni`, a layer holding ~1900 genuine furniture polylines. No
  layer-level label is correct: `furniture` loses the walls, `wall_partition`
  turns 1900 furniture polylines into walls. Per-region statistics do not
  change this — `furni` is furniture-dominant inside every region. Neither does
  vision: stage 2 already renders per-layer PNGs, and a correct reading of a
  `furni` render still says "mostly furniture".
- **Phantom shower wall.** The `WALL` layer is classified correctly. The defect
  is a fixture glyph nested three blocks deep that happens to sit on it. Sheet
  level partitioning cannot see inside a block.

Both trace to `detect_walls_paired_lines` iterating `ps.by_layer(wall_layers)`:
entities on non-wall layers are never enumerated, and entities on wall layers
are never questioned. Improving the accuracy of a decision made at the wrong
granularity cannot fix an error *of* granularity.

The fix is geometry-first wall candidacy, specified separately in
`2026-09-18-geometry-first-wall-candidacy-design.md`. That work is
**independent of this one** and does not depend on regions existing: plan
separation on a real sheet is ≥11,449 drawing units while the junction
tolerance is 1 inch, so cross-plan connectivity and closure scoring are safe
without partitioning.

What this change does contribute to that analysis's recommendations is one item
of five — per-plan regions — plus the precondition for two others that remain
out of scope here: cross-plan corroboration, and a blocking outer-boundary
closure check, both of which need a defined notion of "this plan's boundary".

## Existing machinery this builds on

Already present and unchanged by this work:

| Component | Role |
|---|---|
| `regions.propose_regions(ps, units_per_foot, gap_ft=4.0)` | exact candidate bounds, `evidence="spatial-proposal"` |
| `regions.select_region(ps, region)` | clips a `PrimitiveSet` to a region |
| `regions.load_regions(path)` | reads a reviewed regions JSON |
| `semantic.PlanRegion` | id, bounds, kind, name, elevation_ft, origin, evidence, units_per_foot |
| `cli.py` region loop | iterates regions, calls `build_inventory(source)` **per region**, builds one model each |
| `pipeline._assemble(..., region=)` | stamps `region_id`, `storey_name`, `elevation_ft`, `source_region_bounds` |

Per-region de-contaminated statistics and multi-floor output therefore already
work. The only missing producer is the region list itself.

## Architecture

One new module, `archiagent/classify/survey.py`, and a third branch beside
`--region` / `--regions-file` in `cli.py`. No changes to `pipeline.py`,
`regions.py`, `prompt.py`, `llm_classifier.py` or `dxf_classifier.py`.

Ordering constraint: the survey runs **after** scale resolution, because
`propose_regions` requires `units_per_foot`.

### Data flow

```
ps  (whole modelspace)
 └─ propose_regions(ps, units_per_foot)      exact bounds, no LLM
 └─ render_survey(dxf_path, candidates)      1 sheet overview + 1 crop per candidate
 └─ client.classify_json_vision(...)         ONE call
 └─ survey_from_reply(reply, candidates)     pure; returns labelled PlanRegions
 └─ confidence gate                          buildable plans + Issues
 └─ existing CLI loop                        select_region → build_inventory → classify → extract
```

### Geometry safety

The vision model never emits a coordinate. It receives candidate regions that
already carry exact bounds and returns **ids and labels only**. Where it judges
two candidates to be one drawing, it returns `merge_with` ids and the merged
bounds are recomputed as the exact union of the originals.

This is deliberate: vision models do not produce reliable metric values, and a
bounding box off by a few percent clips a wall. Splitting a candidate is
therefore **not** supported — a split would require the model to supply a
dividing coordinate.

## The vision call

**One call per sheet, not one per region.** Region identity is comparative:
"which of these is the ground floor", "is `region-04` a duplicate of
`region-02`", "these two fragments are one plan with a detached porch" are all
unanswerable with a region viewed in isolation. This mirrors the reasoning
already encoded in `dxf_classifier._batches`, which keeps `competing_wall_layers`
rivals in the same batch for the same reason.

Volume is small — a sheet holds roughly 2–8 drawings. If
`len(candidates) > SURVEY_MAX_REGIONS` (12), degrade to labelling from the
overview image alone and report an Issue, rather than sending a 20-image call.

### Payload

**Images**, order-significant, matching the convention in
`classify_json_vision`:

1. `("sheet overview", <full modelspace render>)`
2. `("region-01", <crop>)` … one per candidate

Rendered in true DXF colors: color carries genuine discipline signal.

**Text** — one row per candidate: id, bounds in source units, size in feet,
entity count, the most common layer names present inside it, and whether
text/dimension layers fall inside.

This is the fusion the design depends on. The image shows what a drawing looks
like; the DXF rows show that a region is full of layers named `ELEC…`. Either
signal alone is weak for discipline identification; together they are strong.

### Response schema

```json
{"regions": [{
  "id": "region-01",
  "kind": "plan|elevation|section|detail|schedule|overlay|title_block|unknown",
  "discipline": "architectural|structural|electrical|plumbing|other|unknown",
  "title_text": "GROUND FLOOR PLAN",
  "storey_name": "Ground Floor",
  "elevation_ft": 0.0,
  "merge_with": ["region-03"],
  "confidence": 0.82,
  "reason": "..."
}]}
```

`title_text` records the literal text the model read from the drawing. It is
the audit hook for storey assignment: a wrong floor becomes traceable evidence
rather than an invisible inference. Empty string when no title is legible.

`elevation_ft` is nullable.

## Confidence gate

Two independent gates, both at `SURVEY_CONFIDENCE_FLOOR = 0.70` (matching the
existing `escalate.CONFIDENCE_FLOOR` convention):

- **kind gate.** Below the floor, the region is excluded from the build and an
  Issue `region_survey_uncertain` is reported. At or above, `kind` auto-drives
  reconstruction: regions whose kind is not `plan` are filtered out, so
  elevations and sections stop feeding extraction.
- **storey gate.** `storey_name` and `elevation_ft` are accepted only at or
  above the floor. Below it the region still builds as a plan, but as
  `"Unassigned plan"` with `elevation_ft=None` — today's behaviour.

The split exists because the two errors are not symmetric. Mistaking an
elevation for a plan produces a silently wrong building. Naming the first floor
"Ground Floor" produces a plausible model that is wrong in a way nothing
surfaces. Degrading the storey call to unnamed is strictly safer than guessing,
and costs only a label.

Regions produced by the survey carry `evidence="vision-survey"`, distinct from
`"reviewed"`, `"user-window"` and `"spatial-proposal"`.

## CLI

On by default for DXF input; `--no-survey` disables. Rationale: it mirrors the
shipped stage-2 policy (vision on by default, `--no_vision` to disable).

Engagement conditions — the survey runs only when **all** hold:

- input is DXF
- neither `--region` nor `--regions-file` was given
- `--no-survey` was not given
- `propose_regions` returned at least one candidate

There is no `--survey` flag; `--no-survey` is the only control. An explicit
`--region` or `--regions-file` simply takes precedence and disengages the
survey — not an error, since supplying regions is the more specific
instruction. The existing mutual exclusion between `--region` and
`--regions-file` is unchanged.

### Behavioural change

On a multi-view drawing previously built as `region_id="whole-drawing"`, output
will now differ — this is the intended improvement, but it changes existing
runs. Blast radius is bounded: single-candidate sheets behave as before, and any
survey failure falls back to today's path.

### Required fix

`cli.py:640` currently raises `ValueError` when `region.kind != "plan"`. On the
survey path, elevations and sections are an expected result rather than user
error, so survey-produced non-plan regions are **filtered with an Issue**. The
existing raise stays for `--regions-file`, where a non-plan kind does indicate
an unreviewed input.

## Error handling

The survey is an improvement, never a dependency — the stance already taken by
`dxf_classifier.py` for stage 2. Every failure mode is caught, reported as an
Issue, and falls back to the pre-survey path:

| Failure | Result |
|---|---|
| matplotlib/Pillow absent (`RenderUnavailable`) | Issue, no survey, whole-drawing build |
| overview or crop render raises | Issue, no survey |
| transport/auth/quota (`LLMUnavailable`) | Issue, no survey |
| malformed or non-conforming reply (`LLMSchemaError`) | Issue, no survey |
| `merge_with` naming an unknown id | that merge dropped, Issue, other regions kept |
| `merge_with` cycle or self-reference | cycle broken deterministically, Issue |
| reply omits a candidate id | that candidate treated as `kind="unknown"`, below gate |
| reply names a candidate not proposed | dropped, mirroring `decisions_from_reply` |

The survey can never fail a build.

## Caching

Keyed on `source_sha256` + survey prompt version + model + provider + base_url,
following `CachingClassifier`. The prompt version is derived from prompt and
schema content by hash, as `prompt.PROMPT_VERSION` already is, so editing the
prompt invalidates stale entries automatically.

Renders land under the existing `.archiagent-cache/thumbnails/` convention,
anchored to the input DXF's own directory — these are pictures of confidential
client drawings and must not land under the repo or a shared cache.

## Testing

Mirroring `decisions_from_reply`, all validation lives in a pure function
`survey_from_reply(reply, candidates)` testable with plain dicts and no client:

- one label per candidate; missing, extra, and duplicate ids
- `merge_with`: union bounds exactness, unknown ids, self-merge, two-cycle,
  three-cycle, chained merges
- both gates at their boundaries (0.69 / 0.70 / 0.71) and independently
- non-plan kinds filtered, not raised
- every failure row above produces an Issue and preserves prior behaviour
- `SURVEY_MAX_REGIONS` overflow degrades to overview-only
- integration: region counts and kinds on `PLAN.dxf`,
  `VINAYAK APARTMENTS.dxf`, and a `Jiju_dxf/` sheet

## Expected effect

`large_non_wall_layer` and `competing_wall_layers` escalations should fall,
since both derive from `entity_share` and wall-role counts previously blended
across views. `low_confidence` and `uninformative_name` will **not** fall —
those are naming problems, not contamination.

Net call count per drawing: +1 survey call, −(1 to 5) stage-2 batch calls.

## Render settings

Module constants, tunable without touching call sites:

- overview: `size_inches=(10.0, 10.0)`, `dpi=140`
- crop: `size_inches=(7.0, 7.0)`, `dpi=160` — higher than
  `thumbnails.render_layer`'s default because the survey reads title text off
  the crop, which stage 2 never had to do

Crops render the region's bounds with a small margin so a title sitting just
outside the clustered geometry is not cut off.

## Scope of `discipline`

Recorded on the region and reported; it drives nothing in this change. It
exists so that a later element-level or overlay-merging change has the field
already populated and audited, without re-running vision.
