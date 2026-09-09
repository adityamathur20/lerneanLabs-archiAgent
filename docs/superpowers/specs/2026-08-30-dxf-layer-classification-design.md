# DXF Layer Classification — Design

**Date:** 2026-08-30
**Status:** Approved design. Implementation is **blocked** on the DXF ingest
front-end, which does not exist yet (§10).

**Predecessors:**
`docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md` (W1,
the PDF classifier this extends), `PLAN.md` §7 Stage 1.

**Scope:** deciding what each DXF layer is *for*. It does not cover DWG→DXF
conversion, DXF ingest, or floor/slab generation — three sibling pieces of
DWG support, each specced separately.

---

## 1. The problem, stated precisely

Given a DXF, decide each layer's `Role` so that `WALL_ROLES` selects wall
geometry and furniture, electrical, plumbing and annotation layers never
reach the geometry stages.

W1 solved this for PDF using layer names plus geometry statistics. DXF
carries far more per-layer information, and it also carries a failure mode
PDF does not have. Both are measured in §2.

---

## 2. What measurement established

Measured 2026-08-30 against the four DXFs in `input-floorplans/dxf/`.

### 2.1 The AIA/NCS layer standard is useless here — 2% conformance

Tested all 101 layers against the National CAD Standard name format
(`A-WALL-FULL`, i.e. `^[A-Z]{1,2}-[A-Z0-9]{4}(-[A-Z0-9]{1,4})*$`):

| File | Conformant |
|---|---|
| `Floor Plan.dxf` | 1 / 39 |
| `Manoj…plumbing.dxf` | 1 / 36 |
| `PLAN.dxf` | 0 / 17 |
| `VINAYAK APARTMENTS.dxf` | 0 / 9 |
| **Total** | **2 / 101 (2%)** |

A deterministic prefix matcher would classify almost nothing. **Do not build
one.** The names are nonetheless *human*-meaningful — `NEW WALLS`, `walls`,
`door`, `win`, `COLUM HATCH`, `FURNITURE`, `plumbing`, `BEAM` — which is
exactly the case W1's LLM-reads-the-vocabulary design already handles.

### 2.2 DXF carries a much richer feature vector than PDF

| Signal | Measured evidence |
|---|---|
| entity mix | `doors` = LINE:1111 + **ARC:147** (swings); `text` = MTEXT:136; `plumbing` = DIMENSION:124 + LEADER:21 |
| lineweight | `wall`=35, `COL`=40, `BEAM`=30 vs `doors`=5, `win`=5, `furnitur`=9 — walls are drafted heavy |
| linetype | `BEAM` = `HIDDEN`, correct for overhead elements |
| frozen/off | `GRID LINES` is frozen — the drafter's own "not needed" marker |
| color | discriminating in `Manoj…plumbing.dxf` (26/34/98/192); **useless** in `Floor Plan.dxf`, where every layer is colour 7 |

Colour and lineweight are informative in some files and flat in others. The
feature vector must therefore be *offered* to the model, never *relied on*.

### 2.3 The failure mode that motivates this whole design

In `Floor Plan.dxf`, **54.9% of all entities sit on layer `0`**, and that is
where the walls are:

| Wall layer used | Walls | Rooms |
|---|---|---|
| `WALLS` (the layer so named) | 57 | **0** |
| `0` (the default layer) | 748 | **27** |

No name-based method can catch this: `0` carries no meaning. This is
drawing-specific, not universal — layer `0` holds 54.9% of `Floor Plan.dxf`
but **0.0%** of the plumbing and Vinayak files — so it is a case to handle,
not the norm.

### 2.4 A rendered layer thumbnail resolves it immediately

Rendered with `ezdxf.addons.drawing.matplotlib.qsave`, in-process:

- **layer `0`** renders as two complete floor plans with grid bubbles,
  dimension chains, rooms, furniture and cars — and a legible title,
  "FIRST & SECOND FLOOR WORKING PLAN".
- **layer `WALLS`** renders as a sparse disconnected fragment.

The distinction is obvious at a glance and impossible from the name.

**Incidental finding, recorded for W3:** the layer-`0` thumbnail shows
readable text although the file contains **zero** live text entities. Outlined
glyphs render correctly even though `ezdxf` cannot read them as strings.
Rendering plus OCR is therefore a live route to W3's problem. Not solved here.

---

## 3. Architecture

```
DXF ─→ layer feature table ─→ ① text-only LLM call ─→ decisions + confidence
                                       │
                                       ├─ confident ───────────────→ done
                                       │
                                       └─ escalated (§5)
                                              ↓
                                       render thumbnails (in-process)
                                              ↓
                                       ② ONE vision call: reference image
                                          + up to 6 layer thumbnails
                                              ↓
                                          revised decisions
```

The `LayerClassifier` protocol and `LayerDecision` are unchanged, so
`pipeline.py` is untouched. Confidence and `source` continue to reach W2.

**Rejected: rendering layer combinations.** `Floor Plan.dxf` has 39 layers —
2³⁹ combinations, and even one-render-per-layer is 39 renders per drawing.
This design renders roughly four.

**Rejected: Blender as the renderer.** `ezdxf`'s matplotlib backend draws
in-process with no Blender startup and no IFC round-trip.

---

## 4. Stage 1 — the feature table

Per layer, all from DXF, no images:

- `name`
- `entity_mix` — counts by DXF entity type, top 5
- `entity_share` — fraction of modelspace entities on this layer
- `lineweight`, `linetype`, `color`
- `is_off`, `is_frozen`
- `extent_ratio` — layer bbox area ÷ modelspace bbox area

Rendered into the prompt as one row per layer. Layer names are JSON-quoted,
as in W1, because names contain spaces (`COLUM HATCH`, `DB TO SHAFT CONDUIT`).

**Carry over from W1's live testing, unchanged:** some models echo those
quotes back in the `name` field. Matching is exact-string against the
inventory, so an echoed quote silently defaults every layer to `ignore`.
Strip surrounding quotes before matching.

---

## 5. The escalation rule

Deterministic. Unit-testable with no LLM and no images. A layer escalates if
**any** of:

1. stage-1 confidence < 0.70
2. `entity_share` ≥ 0.10 **and** the stage-1 role is not in
   `WALL_ROLES ∪ {Role.COLUMN}` — the layer-`0` trap (§2.3). Stated as a set
   rather than as prose so the test can assert it directly.
3. the name is uninformative: `0`, `Defpoints`, purely numeric, or a single
   character
4. two or more layers were assigned a role in `WALL_ROLES` — the
   `WALL`-versus-`WALLS` disagreement

**Budget: at most 6 layers per drawing**, selected by descending
`entity_share`. The cap makes cost predictable and bounded. When the cap
truncates the list, the dropped layers each emit an `Issue`.

---

## 6. Stage 2 — the vision call

A single call containing, in order:

1. a whole-drawing reference render (all layers), because a layer only means
   something relative to the drawing it belongs to;
2. for each escalated layer: its thumbnail, its name, and its feature row.

The model returns a role, confidence and reason per escalated layer, under
the same JSON schema stage 1 uses. Stage-2 answers replace stage-1 answers
for those layers only.

**Renderer:** `ezdxf.addons.drawing.matplotlib.qsave` with a `filter_func`
selecting the layer. Adds `matplotlib` and `Pillow` as dependencies.

**Reply validation is unchanged from W1** — `decisions_from_reply` remains the
single validation boundary, and the layer name in every `LayerDecision` comes
from the inventory, never from the reply.

---

## 7. Privacy: vision is ON by default, disabled with `--no_vision`

**Original decision, 2026-08-30 — SUPERSEDED.** Stage 2 was opt-in, default
off, on the grounds that stage 1 transmits only layer names and statistics
while stage 2 transmits *pictures of a client's building*, a materially
different disclosure.

**Superseding decision, 2026-08-30.** Stage 2 runs **by default**. It is
disabled with the CLI flag `--no_vision`, or by setting
`ARCHIAGENT_VISION=0`. The flag wins over the environment variable when both
are present.

*Why the reversal:* accuracy beats disclosure-minimisation for this project's
users. The measured failure the escalation rule exists to catch — a drawing
whose walls sit on the unnamed default layer `0`, holding 54.9% of entities,
which stage 1 confidently classifies as `ignore` (§2.3) — is corrected only
by looking at the picture. Shipping that correction off by default makes the
common case the wrong case.

*Consequence, accepted:* rendered images of client buildings are transmitted
to the configured LLM provider on every DXF run unless `--no_vision` is
passed. This must be stated plainly in `--help` and the README so it is never
a surprise. Escalations are reported as `Issue`s either way — judged by
picture, or skipped.


---

## 8. Error handling

| Failure | Behaviour |
|---|---|
| `matplotlib`/`Pillow` missing | fall back to stage 1; `Issue` naming the missing dependency |
| a render raises | skip that layer's thumbnail, keep the rest; `Issue` per skipped layer |
| no vision-capable model configured | fall back to stage 1; `Issue` |
| stage-2 reply invalid | keep stage-1 decisions; `Issue`; **never** fail the run |

The rule throughout: stage 2 is an improvement, never a dependency. A run
that could not escalate produces a model, and says so.

New issue codes, following `validate.py`'s existing vocabulary:

| code | severity | when |
|---|---|---|
| `layer_escalation_skipped` | `warn` | a layer met an escalation trigger but was not escalated |
| `layer_role_revised` | `info` | stage 2 changed a stage-1 role |

---

## 9. Testing

**Unit, no LLM and no images:** feature extraction from a synthetic
`ezdxf.new()` document; each escalation trigger from both sides of its
threshold; the 6-layer cap and its ordering; quote-stripping in name
matching; every row of the §8 table.

**Integration**, via `ARCHIAGENT_FIXTURES`, skipped when absent: on
`Floor Plan.dxf`, assert that layer `0` is escalated by trigger 2 and that
`WALLS` and `0` together trigger rule 4.

**Security constraints.** Rendered thumbnails are pictures of confidential
client drawings and are exactly as sensitive as the DXF. They are written to
`tmp_path` or a git-ignored cache and are **never committed**. `.gitignore`
already covers `*.pdf`, `*.dwg`, `*.dxf` and `input-floorplans/`, but **not
`*.ifc` and not rendered `*.png`** — both must be added as part of this work.
No test makes a live API call.

---

## 10. Prerequisite

This spec assumes a DXF front-end that yields a modelspace and a layer table.
That front-end does not exist; only a throwaway spike does. **Implementation
is blocked until DXF ingest lands.**

---

## 11. Explicitly not in this spec

- DWG→DXF conversion. The ODA File Converter runs headless
  (`ODAFileConverter <in_dir> <out_dir> ACAD2018 DXF 0 1 "*.DWG"`, verified
  2026-08-30, exit 0 in 3.4 s) and takes directories rather than files. Its
  wrapper is a sibling spec.
- DXF ingest itself — the sibling spec this one depends on.
- Floor/slab generation. `author.py` emits `IfcProject`, `IfcSite`,
  `IfcBuilding`, `IfcBuildingStorey`, `IfcWall` and `IfcSpace`, and **no
  `IfcSlab`** — there is no floor. Independent of this work.
- W3 text recovery, despite §2.4's lead.
- Symbol recognition from `INSERT` block names.


---

## 12. CLI shape (amended 2026-08-30)

The original CLI took the input PDF and the output IFC as positional
arguments. With `--dxfFilePath` added that became unsafe: argparse cannot
distinguish `PDF --dxfFilePath X` (a usage error) from `--dxfFilePath X
OUT_IFC` (valid) — both present as one DXF plus one stray positional. The
stray was assumed to be the output path, so
`archiagent myplan.pdf --dxfFilePath drawing.dxf` exited 0 and **overwrote
myplan.pdf with the authored IFC**. Reproduced 2026-08-30.

**Amendment: every input and output is a named flag. No positionals.**

| Flag | Meaning |
|---|---|
| `--dxfFilePath PATH` | input DXF |
| `--pdfFilePath PATH` | input PDF |
| `--outputDir DIR` | directory to write into; filename derived from the input stem (`drawing.dxf` -> `<DIR>/drawing.ifc`) |
| `--no_vision` | disable the vision stage (§7) |

Exactly one of `--dxfFilePath` / `--pdfFilePath` must be given. Neither, and
both, are usage errors that name both options.

`--outputDir` being a directory rather than a filename is the structural fix:
the tool derives its own output name and can only write inside a directory the
user named, so no input file can be targeted by accident.
