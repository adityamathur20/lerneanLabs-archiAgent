# Deployment readiness: what is built, what is specced, what is undecided

Date: 2026-10-07
Scope: `lerneanLabs-archiAgent` @ `ab67873` (`feat/llm-token-accounting`), `archiagent-viewer` @ `c8d8a42`

A status record, not a design. It exists so the gap between what the specs
describe and what the code does is written down rather than remembered.

---

## 1. Built, tested, committed

### 1.1 Symbol library — scale- and mirror-tolerant matching

| Component | Responsibility |
|---|---|
| `archiagent/classify/shapes.py` | Shared `Part`, `parts_from`, `measure` — one definition of a comparable shape for both matchers |
| `archiagent/classify/library_templates.py` | Matches stored library shapes at any uniform scale, optionally mirrored |
| `archiagent/classify/symbol_harvest.py` | Proposes candidates two ways: by block name, and by shape frequency |
| `archiagent/classify/templates.py` | `KINDS` gained `vegetation`; `_parts`/`_symbol` now delegate to `shapes.py` |
| `archiagent/data/symbol_library.json` | The reviewed library, shipped as package data |
| CLI | `--harvest-symbols`, `--symbol-library PATH`, `--no-symbol-library` |
| `archiagent/pipeline.py` | Library matching runs between `recognize_symbols` and the reviewed-template block |

**Measured.** Seeding from one `dasd` instance in `M.r Premg Agarwal Baglow 90x50.dxf`
and matching against `MR RAJEEV JI TWANI JI.dxf` produced **60 matches, 60 true
positives, 0 false positives**, covering 60 of that drawing's 72 instances, at
two distinct real sizes (1.373 ft and 1.805 ft) and including mirrored copies.
Harvesting `SANJANA SURESH JI.dxf` proposed 7 candidates, including a
40-instance glyph named `Chaukhat` — Hindi for door frame — found purely by
frequency, which no English keyword list reaches.

**Two assumptions died against real data and are recorded in the spec:** vertex
counts describe tessellation rather than shape (the same hatch boundary flattens
to 181 points in one drawing and 182 in another at identical length), and a
length prefilter broke on inconsistent hole-ring closure.

> **`symbol_library.json` ships with zero templates.** Until someone harvests and
> hand-approves entries, this code is inert. It is a mechanism, not a library.

### 1.2 Scale resolution

| Component | Responsibility |
|---|---|
| `archiagent/scale/extracted.py` | `extracted_scale` reads scale from native `DIMENSION` entities; `dimstyle_units_per_foot` infers the drawing's unit from `DIMLFAC` |
| `archiagent/scale/dimensions.py` | `parse_dimension_group` splits room-label pairs on `x`, `X`, `×` |
| `archiagent/scale/verify.py` | `scale_from_reviewed` — a single human assertion calibrates |
| `archiagent/cli.py` | `_resolve_scale` ladder, `_wall_length_measurements`, `_unit_factor_hint` |
| CLI added | `--scale-from-wall X1 Y1 X2 Y2 LENGTH`, `--trust-extracted-scale`, `--scale-tolerance-in` |
| CLI **removed** | `--units-per-foot` |

The ladder, highest priority first: a reviewed region's `units_per_foot` → an
asserted wall span → the drawing's own dimensions behind
`--trust-extracted-scale` → **never the file header alone**.

**Measured.**

- `PLAN.dxf`, whose header declares 304.8 units/foot for a drawing measured in
  inches, now **refuses** and prints the extracted estimate beside the header's
  claim, instead of silently building a wrong model at exit 0.
- `Aiims Road 3BHK Flats-vk.dxf` resolves **12.0 units/foot from 159 of its 424
  dimensions** via `DIMLFAC`, independently matching its header.
- Pair splitting took parseable dimension texts from **0 to 18–90 per drawing**
  across the corpus.

**Deliberately rejected, with the measurement that rejected it.** Feeding the
newly parsed labels to `resolve_scale`'s run matcher was tried. Against a known
truth of 12 units/foot it was right **once in seven drawings** — 0.936, 5.250,
6.700, 11.976, 4.863, 3.947, 4.526 — and every run reported `matched_count`
equal to its full text count, so the wrong answers arrive fully corroborated. A
room label states a room's clear interior, not the length of any drawn line, so
matching labels to runs is numerology. Not wired in.

### 1.3 Viewer and service

`StartRequest` and `build_command` take `scale_from_wall` and
`trust_extracted_scale` in place of `units_per_foot`. The upload form's
"Units per foot" number box became the checkbox **"Use the dimensions already in
this drawing"**, ticked by default.

### 1.4 Test status

| Suite | Result |
|---|---|
| archiagent | **440 passed**, 12 skipped, 0 failed |
| service (live Postgres, Redis, S3Mock; `REQUIRE_SERVICES=1`) | **85 passed**, 3 skipped |
| archiagent-viewer | build ✓, **53 passed**, 10 skipped, 0 failed |

`REQUIRE_SERVICES=1` matters: it turns an unreachable dependency into a failure
rather than a skip, so the Postgres-backed API tests genuinely ran.

---

## 2. Specced, with no code behind it

| Spec | Missing | Blocked by |
|---|---|---|
| `2026-10-06-declared-wall-metrics-design.md` | The **entire** declared-thickness feature. No `--wall-thickness` flag exists. Its plan's Task 4 (`scale_from_reviewed`) is the only part delivered, via the scale work | — |
| `2026-10-07-vertical-treatment-design.md` | **The pool fix.** No `SUNKEN`/`VOID` treatment, no `Space.treatment` | Sub-project B |
| Sub-project B (room-label text as a classification gate) | Label-to-space association, OCR backfill, hard-constraint gating | — |
| `2026-10-07-staged-pipeline-design.md` | Project store, stage gates, resumption | — |
| Picker (`ieskudero/three-dxf-viewer`) | **Not a dependency, not installed, not referenced in any source file.** Only the decision is recorded | Nothing — the handle-join spike cleared it |
| Room-label → enclosure matching | Parsing is done; matching labels to enclosures is not | Needs space polygons |

Executable plans exist for `declared-wall-metrics` (7 tasks, none run) and
`scale-resolution` (4 tasks, **all run**).

---

## 3. Asked for and not delivered

- **LLM layer cap 20 → 55.** `escalate.ESCALATION_CAP` is still **20**.
  `--maxEscalation 55` works today with no upper clamp, so this is a default
  change only. Each escalated layer costs one rendered image in the vision call,
  so 55 will need `--max-tokens` raised and costs materially more per run.
- **Wall-thickness GUI.** Specced in detail — proposals pre-filled with their run
  counts, unit selector defaulting to inches, veto worded as its consequence —
  but neither the UI nor the CLI flags beneath it exist.

---

## 4. Decisions still open

### 4.1 The UI gap — the one that matters

After this deploys, a user who unticks "Use the dimensions already in this
drawing" on a drawing with no usable dimensions gets an honest refusal **and no
way to proceed**. `--scale-from-wall` is CLI-only; the API accepts
`scale_from_wall` but nothing in the browser produces one.

`PLAN.dxf`, `Floor Plan.dxf` and `SANJANA SURESH JI.dxf` therefore become
**unconvertible through the web UI**. That is a reach regression, traded for
never silently producing a mis-scaled model.

**Decide:** ship anyway and accept the gap, or build the picker first.

### 4.2 What happens to a fixture a room label rejects

Sub-project B treats room-label text as a hard constraint. If a fixture match is
blocked, does its geometry flow **back** into wall detection and resurrect the
phantom walls the symbol library removed? The argument on file is that "blocked"
should mean *reject the subtype, keep the exclusion*. **Not decided.**

### 4.3 Who authors the room-label compatibility table

`TOILET → {wc, washbasin, shower_head}`, `KITCHEN → {sink}`, and so on. Blocks
Sub-project B. Offered: draft one from the corpus for correction, or have it
written by hand.

### 4.4 Whether the staged pipeline's fixed stage order is too strict

`PLAN.dxf` is a site plan with nothing worth calling a wall, so under the staged
design it could never leave the `walls` stage and nothing else could run.

### 4.5 `npm test` is broken on Node 22

`node --test tests/` works on Node 20 (local) and fails on Node 22 with
`Cannot find module '/app/tests'`. The deploy uses `node:22-alpine`. With an
explicit glob (`node --test 'tests/*.test.mjs'`) all 63 tests run. **If CI runs
Node 22, it may be reporting success having executed nothing.**

---

## 5. Deploying what exists

`feat/llm-token-accounting` is already on GitHub at `ab67873`. Local `main` is
identical to it; `origin/main` is 42 behind and is **not** needed for a deploy
from the feature branch.

```bash
# The only required push:
cd archiagent-viewer && git push origin main
```

On the VPS, check out `feat/llm-token-accounting` in `lerneanLabs-archiAgent`,
pull `archiagent-viewer`, then follow `deploy/README.md` §7 — build, then
`alembic upgrade head` as a separate step, then `up -d`.

> **Both halves must deploy together.** `--units-per-foot` was removed on the
> archiAgent side and the caller changed on the viewer side; deploying one
> without the other breaks every conversion.

> **The pool fix is not in this deploy.** It is specced only, and blocked behind
> Sub-project B's label-to-space association.
