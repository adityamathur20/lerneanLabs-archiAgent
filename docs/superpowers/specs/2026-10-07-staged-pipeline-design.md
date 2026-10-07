# The staged pipeline: a building assembled in gated steps

Date: 2026-10-07
Status: design, pending implementation plan
Related: `2026-10-06-scale-resolution-design.md`, `2026-10-07-vertical-treatment-design.md`, `2026-10-06-declared-wall-metrics-design.md`

## Problem

Today a run is one process: a DXF goes in, an IFC comes out, and every decision
in between — scale, layer roles, which runs are walls, where the openings are —
is made without the user. When one of those is wrong, the only feedback is a
wrong building, and the only remedy is to re-run the whole thing with different
flags.

The product this wants to be is a sequence the user walks through:

1. create a project, or open an existing one
2. upload the DXF
3. select a line and state its length — scale
4. generate walls, review them, **then** proceed to openings
5. slabs and flooring
6. interior elements
7. roof and terrace

Each step shows its result and waits. That is not a different pipeline; it is
the pipeline it already is, with gates and the ability to stop and resume.

## What already exists

The stages are real and already separated inside `_assemble`
([pipeline.py:24](../../../archiagent/pipeline.py#L24)):

| User step | Existing stage |
|---|---|
| 3 scale | `_resolve_scale`, `extracted_scale`, `scale_from_reviewed` |
| 4 walls | `recognize_symbols` → `match_library_templates` → `exclude_symbol_geometry` → `detect_wall_profiles` → `select_walls` → `resolve_junctions` |
| 4 openings | `host_openings`, `contextualize_openings`, `remap_openings` |
| 5 slabs | `detect_spaces`, `detect_footprints` |
| 6 interiors | `SymbolsJSON` is already retained with subtype and template id, authored by nothing |
| 7 roof | not implemented |

Resumption also partly exists. `--freeze-only` writes the interpretation,
review and overlays without authoring IFC, and `--replay-manifest` rebuilds from
a frozen interpretation **after checking the input checksum**. That pair is the
foundation: a frozen interpretation is already a resumable snapshot of a run.

And the hosting exists: `archiagent-viewer` is a deployed app with an upload
form, a job API, a worker that invokes the CLI, and a fragments renderer.

So this spec is mostly about **gates, state and resumption**, not new geometry.

## Scope

In: a project that persists across steps; a stage boundary that can be stopped
at, reviewed and resumed from; the decisions a user makes at each gate being
recorded as reviewed data rather than flags.

Out: new geometry algorithms. Roof authoring (step 7) is named here so the
stage list is complete, and specified separately.

## 1. A project is a sequence of accepted stages

```
project
  source        the DXF, by checksum -- never re-uploaded silently
  stages        ordered, each: name, status, inputs, outputs, reviewer, at
  current       the first stage not yet accepted
```

Stage status is one of `pending`, `running`, `awaiting_review`, `accepted`,
`failed`. A stage can only run when every stage before it is `accepted`.

The stage list is fixed and ordered: `scale`, `walls`, `openings`, `slabs`,
`interiors`, `roof`. Fixed because the dependencies are real — openings host
onto walls, slabs bound to spaces that walls enclose — not because the sequence
is a convenience.

## 2. The gate is what a stage's inputs are made of

A user's decision at a gate must survive into every later run, which means it
cannot be a command-line flag typed once. Each gate writes **reviewed data** in
the form the pipeline already accepts:

| Gate | What the user decides | Where it is written |
|---|---|---|
| scale | the asserted span, or trust the drawing's dimensions | a `measurements` record (`cli-wall-length`) |
| walls | wall thicknesses; which candidate runs are walls | `--wall-thickness` set; `review["wall_profiles"]` |
| openings | which openings are real, and their kind | `review["symbols"]` |
| slabs | vertical treatment per space | the room-label vocabulary plus per-space overrides |
| interiors | which library symbols are correct | `symbol_library.json`, `review["symbols"]` |
| roof | terrace versus roof slab | per-space, as for slabs |

This is the design's load-bearing idea: **the gates produce the review documents
the pipeline already reads**. Nothing new is invented to carry a decision, and a
project can be replayed start to finish from its accumulated review data with no
user present — which is also how it becomes testable.

## 3. Resumption rests on the frozen interpretation

Running a stage means: replay the accepted stages from the frozen
interpretation, run the next stage, freeze again.

`--replay-manifest` already refuses to combine replay with flags that would
change geometry (`--height`, `--rules`, `--scale-from-wall` and others), which
is exactly the property a stage boundary needs: a later stage must not silently
re-decide an earlier one. That refusal becomes the mechanism rather than a
guard.

The checksum check is equally load-bearing. If the source DXF changes, every
accepted stage is invalidated, because a wall the user accepted may no longer
exist. The project records the source checksum and refuses to resume against a
different file, offering to start a new project from it.

## 4. What the user sees at each gate

The viewer already renders authored IFC. A gate needs less than that: the
**result of the stage just run**, and the controls for the decision.

- **scale** — the DXF in the picker, the implied scale shown live as a length is
  typed, and what the drawing's own dimensions imply beside it.
- **walls** — the wall model in 3D, plus the thickness classes with their run
  counts. Rejected candidates are visible, since an over-eager veto is otherwise
  invisible.
- **openings** — walls with openings cut, and a list of unhosted openings, which
  are the ones that are usually wrong.
- **slabs** — the floor plate with voids subtracted, which is where a pool or a
  double-height space shows up as either right or obviously wrong.
- **interiors** — matched symbols with their template id and confidence.
- **roof** — last, and separately specified.

Each gate offers accept, or go back. Going back to stage *n* discards the
accepted state of every stage after it, because they were derived from it. That
is stated plainly in the UI rather than silently done.

## 5. Why stages are not re-run automatically

A tempting design runs every stage on every change and shows the final model
continuously. This spec does not, for a reason measured earlier: a full run on
Aiims Road takes minutes, dominated by `load_dxf` at 10.9 s plus classification
and candidacy. Re-running six stages on every edit makes the loop unusable.

More importantly, automatic re-running would re-decide what the user already
accepted. The point of a gate is that it holds.

## 6. Error handling

| Condition | Result |
|---|---|
| a stage runs when an earlier one is not accepted | refused, naming the first unaccepted stage |
| the source file's checksum changes | every stage invalidated; offer a new project from the new file |
| a stage fails | status `failed` with the pipeline's own message, which the viewer already surfaces verbatim; earlier stages keep their accepted state |
| the user goes back to an earlier stage | later stages are discarded, stated explicitly before it happens |
| replaying a project whose archiAgent version changed | the existing changed-version reporting applies, and the stage is re-run rather than trusted |

## 7. Testing

- a project cannot run `walls` before `scale` is accepted
- accepting a stage writes review data that a flagless replay reproduces exactly
- a full project replays start to finish from its review documents with no user
  input, producing a byte-identical IFC
- changing the source DXF invalidates every accepted stage
- going back to `walls` discards `openings` and everything after it
- a failed stage leaves earlier accepted stages intact
- the scale gate's output is a `measurements` record the existing
  `--measurements` path reads unchanged

## 8. Risks

- **The stage list hard-codes a building order that may not hold.** A drawing
  with no walls worth building still has a site plan worth placing. The ordering
  is justified by real dependencies, but a project that cannot get past `walls`
  can do nothing at all, which may be too strict for site or landscape drawings.
  `PLAN.dxf` in this corpus is exactly such a file.
- **Accumulated review data can contradict a later algorithm change.** A wall
  the user accepted under one candidacy version may not be proposed by the next.
  The existing changed-version reporting makes this visible; it does not resolve
  it, and the honest answer is that some projects will need re-reviewing after
  an upgrade.
- **Going back is destructive.** Discarding later stages is correct but costly
  for a user who has reviewed a hundred openings. A later refinement could
  preserve decisions that do not depend on what changed; this spec does not
  attempt it, because determining that dependency correctly is harder than it
  looks and getting it wrong silently keeps a stale decision.
- **This is a product shape, not only a refactor.** The project store, the job
  model per stage and the gate UIs are real additions to a deployed service.
  Sequencing them behind the stages that already exist — scale first, since it
  is built — keeps each step shippable.
