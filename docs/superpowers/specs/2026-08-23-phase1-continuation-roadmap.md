# Phase 1 Continuation — Roadmap

**Date:** 2026-08-23
**Status:** Decomposition only. Each workstream below gets its own spec →
plan → implementation cycle. This document decides *what* and *in what
order*, not *how*.

**Predecessor:** `PLAN.md` (Phase 1 design, M1–M5 complete and merged)

---

## 1. Where the project actually stands

M1–M5 is merged: 111 tests, a deterministic vector pipeline producing
parametric IFC4. On the three sample drawings:

| Drawing | Result |
|---|---|
| `ELECTRICAL FINAL l PLAN.pdf` | 258 walls, 14 spaces, 878 junction connections. 61/63 dimensions matched at 0.984 in |
| `DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf` | 51 walls, 1 space, 36 connections. 21/24 dimensions at 1.568 in |
| `GROUND FLOOR PLAN_WORKING REVISED.pdf` | **Fails entirely** — no live text, so scale cannot resolve |

The geometry engine works. What does not yet exist is a *tool*.

### The gap, stated plainly

To run this today you must write Python, and you must hand-author a
dictionary mapping that specific drawing's layer names to roles:

```python
roles = {"walll": (Role.WALL_STRUCTURAL, 0.95), ...}
run_pipeline(pdf, StubClassifier(roles), "out.ifc")
```

There is **no CLI**, and there is **no LLM-backed classifier** — the one
place the design allocates to the LLM is still a stub. Everything in this
roadmap follows from closing that gap and then widening what the pipeline
can read.

---

## 2. The four workstreams

### W1 — Automatic layer classification

Replace `StubClassifier` with the LLM-backed classifier the design always
specified (`PLAN.md` §7 Stage 1). Input is the existing `LayerStats`
inventory; output is a role and confidence per layer.

*Why first:* nothing else can be automatic while a human must hand-author
the role map. It is also the thing W2 displays — a review surface needs
interpretations to review.

*Delivers:* `run_pipeline(pdf, out)` works on an unseen drawing without
anyone writing Python.

*Size:* small. The interface, the fixed role vocabulary, and the
deterministic statistics all exist.

### W2 — Review & correction GUI

A visual surface shown after parsing and before the model is built,
displaying every interpretation the system made — layer roles now, glyph
shapes and symbols as W3 and W4 land — each with its confidence, and a
correction affordance.

*Why it matters beyond convenience:* it is the write-back gate for W3's
persistent shape library. Nothing enters that library on an LLM's word
alone.

**This reverses a documented decision.** `PLAN.md` §13 lists "a custom
review UI" as a non-goal, on the earlier basis that "the review surface is
Blender plus conversation". That non-goal is superseded here and
`PLAN.md` must be amended when W2 is specced, not left contradicting this
document.

*Design references the spec must honour:* `impeccable`
(github.com/pbakaus/impeccable) and Anthropic's `frontend-design` skill.
Neither prescribes a stack; both demand the interface not be a generic
dashboard. The subject matter has its own visual vocabulary — dimension
chains, hatch, line weights, grid bubbles — and that is where the design
language should come from.

*Open questions for its spec:* delivery (local web app? desktop? served
by the CLI?); whether it blocks the pipeline or annotates a completed run;
how it coexists with the Blender + MCP workflow chosen in `PLAN.md` §8;
whether corrections persist per-drawing or per-CAD-office.

*Size:* largest of the four. Its own spec, unambiguously.

### W3 — Text recovery from outlined glyphs

Some CAD exports convert text to vector outlines, leaving no readable
strings. The ground-floor plan is entirely blocked on this.

*Approach (spiked 2026-08-23, feasible):* cluster identical filled glyph
outlines, identify each distinct shape once, cache to a persistent shape
library, fall back to OCR for shapes that fail to cluster. Library is
consulted first; misses invoke the LLM; confirmed results are written
back.

**Spike evidence:**
- Text renders crisply at 600 DPI — `50'-0"`, `18'-7½"`, `6'-4½"`,
  `15'-0"`, `10'-0"`. OCR would also work.
- Glyphs cluster: `OVERALL DIM` is 366 filled paths → **54 distinct
  shapes**, top 40 covering 92%.
- **`parse_dimension` rejects fractional inches.** `18'-7½"` and `6'-4½"`
  both fail today. This is a defect in shipped code, independent of any
  OCR work, and it loses 2 of the 5 dimensions in a single sampled crop.
- **Dimension chains sum to their printed overall**
  (18.625 + 6.375 + 15 + 10 = 50.0). An unused, independent validation
  signal — and precisely the evidence that would have caught the 40% scale
  error recorded in `PLAN.md`.

*Depends on:* W2, for the human confirmation gate before library
write-back.

*Size:* medium. Three separable parts — clustering, identification +
library, fraction-aware parsing.

### W4 — Filled-body wall detection

A second wall detector emitting the same `WallSeg` type, targeting filled
closed polygons. Merges with the paired-line detector before junction
resolution.

**A correction to `PLAN.md` §7 Stage 3.** The spec describes a
"hatch-body detector" that extracts "the hatch region's boundary polygon"
and computes its medial axis. Measured against the actual drawings, that
describes only the rarer case:

| Layer | Drawing | Reality |
|---|---|---|
| `HATCH1` | Demolition | 6,504 of 6,534 segments are **zero-length points** — 99.5% junk |
| `WALL HATCH` | Ground floor | 1,385 loose 45° strokes — **no boundary polygon exists** |
| `Hatch-3` | Electrical | 551 loose 45° strokes |
| `HATCH-CONS`, `COLUM HATCH` | Demolition, Ground floor | Filled polygons — 49 and 48 paths |

A bundle of loose diagonal strokes has no boundary to take a medial axis
of. Those are two different algorithms and the spec anticipated one.

*Reframing:* target **filled closed polygons wherever they appear**, not
"hatch layers". The electrical plan's `WALLS` layer is itself 1,241 filled
paths out of 1,489 — filled wall bodies already sit on layers we classify
as walls. Stroke-hatch envelope inference is a harder, separable second
case.

*Depends on:* nothing. Testable today on the electrical plan.

*Size:* medium.

---

## 3. Recommended order

```
W1  automatic layer classification     ── no dependencies, unblocks "tool"
 └─ W2  review & correction GUI        ── displays W1; gates W3
     └─ W3  text recovery              ── needs W2's confirmation gate
W4  filled-body detection              ── independent, run any time
```

**W1 → W2 → W3, with W4 parallel.**

*Why W1 first:* it is small, it has no dependencies, and it converts "a
library you write Python against" into "a tool you point at a PDF". It
also produces the interpretations W2 exists to display — building W2 first
would mean designing a review surface for output that does not yet exist.

*Why W2 before W3:* your own design requires human confirmation before a
shape enters the persistent library. Building W3 first would mean either a
throwaway CLI confirmation step or an unguarded library.

*Why W4 can go any time:* no dependencies, and its target drawing already
works end to end. Reasonable to run alongside W2, which is long.

### Immediate, independent of all four

**Fraction-aware `parse_dimension`.** A bug in shipped code, ~an hour,
required by W3 and beneficial regardless. Should not wait for a
workstream.

---

## 4. Explicitly not in this roadmap

Carried forward from `PLAN.md` and still deferred:

- Opening detection (doors/windows) — needs arc geometry that ingest
  currently discards
- Spatial dimension-to-dimension-line association — the structural fix for
  the R1 gate hole; **required** before that gate can be trusted
- Genuine mid-run X-crossing junctions — zero occurrences observed
- The second `extend_to_intersections` order-dependence — parked with a
  ruling, documented in code
- IFC → model re-import and the `edit_model` correction loop
- Blender bpy scene dump
- Phases 2 (interior design variants) and 3 (MEP) — both consume this
  output and neither is blocked by anything above

---

## 5. What each spec must settle

Recorded here so the questions are not rediscovered:

**W1** — What does the classifier see: statistics only, or a rendered
thumbnail per layer? How is confidence derived, and what floor gates a
layer out? What happens when no layer classifies as a wall?

**W2** — Delivery and stack. Blocking checkpoint or post-hoc annotator?
How does it coexist with the Blender + MCP workflow? Do corrections
persist per drawing or per CAD office? What is the signature design
element, per `frontend-design`'s "spend your boldness in one place"?

**W3** — Shape signature normalisation: are size and rotation variants one
cluster or several? Where does the library live and what is its format?
What exactly does chain-sum validation gate? How do OCR and clustering
reconcile when they disagree?

**W4** — How do two detectors merge without double-counting a wall found
by both? Medial axis or minimum-area-rectangle for the centerline? Does a
filled body carry different provenance from a paired-line wall?
