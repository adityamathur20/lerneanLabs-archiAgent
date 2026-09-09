# W4 — Filled-Body Wall Detection — Design

**Date:** 2026-08-28
**Status:** Approved design. Implementation is **blocked** on the
space-detection investigation (§9), by decision recorded 2026-08-28.

**Predecessors:** `PLAN.md` §7 Stage 3;
`docs/superpowers/specs/2026-08-23-phase1-continuation-roadmap.md` §2 W4

**Corrects:** `PLAN.md` §7 Stage 3, `PLAN.md` "Limitations" item 3, and
the roadmap's own W4 section. All three describe geometry that is not in
the source files. See §2.

---

## 1. What this builds

A second wall detector. It reads filled regions out of the drawing,
reduces each to a centerline and a measured thickness, and emits the same
`WallSeg` type the paired-line detector emits. The two wall sets merge
before junction resolution.

It exists to serve **R2** (all wall junctions handled properly). It does
not serve room coverage — see §2.3.

---

## 2. What measurement changed

Everything in this section was measured on 2026-08-28 against the three
sample drawings, not inferred. The probe is throwaway; its logic is
reproduced in §5 and §6.

### 2.1 There are no boundary polygons. Filled regions are triangle soup.

`PLAN.md` §7 Stage 3 specifies extracting "the hatch region's boundary
polygon" and computing its medial axis. **No such polygon exists in the
PDFs.** Every filled path in all three drawings is a triangle — the
exporter emits filled regions as triangulated meshes.

| Drawing | Layer | Filled paths | Decomposes into |
|---|---|---|---|
| Electrical | `WALLS` | 1,241 (`fs`) | 905 triangles + 947 degenerate slivers |
| Demolition | `HATCH-CONS` | 49 (`fs`) | 94 triangles |
| Ground floor | `COLUM HATCH` | 48 (`f`) | 48 triangles |

Individually these triangles are noise: the electrical triangles have a
median area of **0.013 sq ft**. The roadmap's claim that "filled wall
bodies already sit on layers we classify as walls" is true only after a
reassembly step neither document anticipated.

### 2.2 Unioned, they are excellent — and mostly rectangles.

Merging triangles that share an undirected edge:

| Drawing / layer | Triangles | Regions | Total area | Shape |
|---|---|---|---|---|
| Electrical `WALLS` | 905 | 132 | 267.0 sq ft | median 2 triangles; largest 100 tri / 18.5 sq ft |
| Demolition `HATCH-CONS` | 94 | 47 | 88.9 sq ft | **every** region exactly 2 triangles |

A 2-triangle region is a quad split on its diagonal — a rectangle. Fitting
a minimum-area rectangle and comparing its area to the region's:

| Drawing / layer | Regions | OBB fill-ratio p50 | Rect-like (>0.90) |
|---|---|---|---|
| Demolition `HATCH-CONS` | 47 | **1.000** | 47 (100%) |
| Electrical `WALLS` | 132 | 0.982 | 72 (55%) |

Of the demolition regions passing the full gate, median thickness is
**4.07 in** — matching the 4.0 in partition walls `PLAN.md` §4 measured
independently by pairing lines.

**Rectilinear decomposition is not needed.** On the electrical plan only
**5** regions are both non-rectangular and ≥ 1 sq ft, holding 6% of total
filled area; four of those five are ~1.9 sq ft blobs of 83–100 triangles,
i.e. curve approximations rather than walls. The other 55 non-rect-like
regions are all under 1 sq ft and the gate rejects them.

### 2.3 Filled bodies do not increase room count. They decrease it.

`PLAN.md` "Limitations" item 3 states room coverage is bounded by wall
coverage and names "the deferred hatch-body wall detector" as the binding
constraint. **A working detector was built and measured. The claim is
false.**

| Drawing | Baseline | + filled bodies (dedup 0.5 ft) |
|---|---|---|
| Demolition | 51 walls, **1 space**, 42 unresolved | 68 walls, **1 space**, 39 unresolved |
| Electrical | 155 walls, **2 spaces**, 69 unresolved | 161 walls, **0 spaces**, 64 unresolved |

Room count never rose, at any dedup tolerance, on either drawing. What did
improve is exactly the requirement W4 is for: unresolved junctions fell
**29%** on the demolition plan and 7% on the electrical plan.

`PLAN.md` item 3 must be amended when W4 is implemented, to record that the
detector was built and did not move room coverage.

### 2.4 Paired-line already finds most filled bodies.

Ingest converts every fill triangle's edges into `"line"` primitives, so
`_pair_family` has been pairing opposite faces of filled bodies all along.
After dedup, **49 of 55** electrical candidates and **28 of 45** demolition
candidates are duplicates of walls paired-line already emits. Deduplication
is therefore load-bearing, not a tidy-up.

---

## 3. Success criteria

Measured on the demolition and electrical plans:

1. The detector emits walls the paired-line detector does not — **≥ 15** on
   demolition, **≥ 5** on electrical, at 0.5 ft dedup tolerance.
2. Unresolved junction count **drops** on both drawings.
3. Space count does **not** regress on either drawing.
4. The R1 scale gate result — `units_per_foot` and `max_residual_in` — is
   **bit-identical** to today's on all three drawings.
5. Every rejected region ≥ 1 sq ft and every geometry-admitted region is
   reported as a model `Issue`; none is silently dropped or silently added.

Criterion 3 is currently unsatisfiable — see §9.

---

## 4. Architecture — a second layer channel

`pipeline.py:38` derives one `wall_layers` set that feeds both scale
resolution and wall detection. That single set is why `HATCH-CONS` had to
be roled `ANNOTATION`: excluding it from scale and excluding it from wall
detection were the same act. W4 separates them.

```
WALL_ROLES      = {WALL_STRUCTURAL, WALL_PARTITION}
WALL_BODY_ROLES = {WALL_FILL, COLUMN}          # new

candidate_runs(ps, WALL_ROLES)                  # unchanged — scale never
                                                # sees a body layer
detect_walls_paired_lines(ps, WALL_ROLES, …)    # unchanged
detect_walls_filled_bodies(ps, WALL_BODY_ROLES, …)   # new
```

Scale resolution is protected **by construction**, not by policy. This
matters because the 40% scale error recorded in `PLAN.md` was produced
precisely by letting hatch reach `candidate_runs`, and it passed the R1
gate with both a better match count and a lower residual than the correct
answer — neither gate criterion can catch it.

`WALL_FILL` is a new member of `Role`. The classifier's response schema
enumerates `Role` programmatically and `PROMPT_VERSION` is derived from the
prompt text plus the schema, so adding the member regenerates the schema
and invalidates stale cached classifications automatically. No cache
migration is required.

---

## 5. Components

### `archiagent/ingest/pdf_vector.py` — emit fills

`Primitive.kind` already documents `"fill"` as a legal value, but
`load_pdf` handles only item ops `"l"` and `"re"` and discards PyMuPDF's
per-drawing `type` field, so no `"fill"` primitive has ever been produced.

Add: for drawings whose `type` is `"f"` or `"fs"`, split the item list into
continuous subpaths (a chain breaks when an item's start point is not the
previous item's end point), and emit each closed ring as
`Primitive("fill", ring, layer, width, color)`.

Curves (`"c"`) and quads (`"qu"`) remain dropped, unchanged — that is
`PLAN.md` limitation 5 and belongs to M8.

### `archiagent/geometry/fills.py` — new

- `merge_fill_regions(ps, layers) -> tuple[FillRegion, ...]`
  Union-find over triangles sharing an undirected edge.
  `archiagent/geometry/junctions.py` already contains `_UnionFind`, with
  exactly the integer-indexed API this needs (`__init__(n)`, `find`,
  `union`). Move it to `archiagent/geometry/_unionfind.py` and import it
  from both call sites. Do **not** write a second copy — verbatim
  duplication of a logic block is a review defect in this project.
- `region_to_wall(region, units_per_foot) -> tuple[WallSeg | None, str]`
  Convex hull, then minimum-area enclosing rectangle by testing each hull
  edge as a candidate axis (rotating calipers). The long side becomes the
  centerline, the short side the thickness. Returns the wall and `""` on
  success, or `None` and a short machine-readable reason
  (`"fill_ratio"` / `"thickness"` / `"length"`) on rejection — the caller
  needs the reason to write a useful `Issue`, so a bare `None` is not
  enough.
- `detect_walls_filled_bodies(ps, body_layers, units_per_foot) -> tuple[tuple[WallSeg, ...], tuple[Issue, ...]]`
  The two above plus the gate, returning issues alongside walls.

### `archiagent/geometry/walls.py`

`WallSeg.detector` accepts `"filled-body"`. The type comment's
`"hatch-body"` is stale — the roadmap already reframed this away from
"hatch layers" toward filled bodies wherever they appear — and goes.

### `archiagent/pipeline.py`

One insertion between the existing line 46 and line 53: detect filled-body
walls, dedup against the paired-line set, concatenate, pass the union to
`resolve_junctions`.

---

## 6. The geometry gate

Every candidate region, whichever channel proposed it, must pass:

| Test | Threshold | Rationale |
|---|---|---|
| OBB fill ratio | > 0.90 | below this the region is not one rectangle. 100% of demolition regions pass; 72/132 electrical |
| thickness | 2–24 in | identical bounds to `detect_walls_paired_lines` |
| length | ≥ 1 ft | identical bound to `detect_walls_paired_lines` |

**Role-selected regions** get exactly this gate.

**Geometry-admitted regions** — those on layers the classifier did *not*
mark as a body role — may still be admitted, under a stricter fill ratio of
**> 0.98**. Every such admission emits an `Issue` naming the layer. This is
the recall path; making it visible is what keeps it from repeating the
shape of the 40% scale error, where wrong geometry entered the pipeline
with nothing in the output saying so.

**Rejected regions ≥ 1 sq ft** also emit an `Issue`. Measured: 5 on
electrical, 0 on demolition.

Issue codes, following the existing `validate.py` vocabulary
(`unresolved_junction`, `default_thickness`, `layer_unclassified`, …):

| code | severity | entity | when |
|---|---|---|---|
| `fill_region_rejected` | `warn` | the layer name | a region ≥ 1 sq ft failed the gate; `msg` names the failing test and its measured value |
| `fill_layer_inferred` | `warn` | the layer name | a region was admitted by geometry on a layer no body role selected |

Neither is an `error`: neither prevents a valid IFC from being authored,
and the CLI's exit status tracks only `scale_gate_failed`.

**Deduplication.** A filled-body wall is dropped when *both* its endpoints
lie within **0.5 ft** of an existing paired-line wall's centerline
(point-to-segment distance). Measured at that tolerance: 17 of 45 kept on
demolition, 6 of 55 on electrical. Tolerances of 0.25 ft and 1.0 ft were
also measured; 0.5 ft and 1.0 ft behave identically, 0.25 ft admits one
extra electrical wall.

---

## 7. Provenance and IFC

A filled-body wall carries `detector="filled-body"` and
`thickness_source="measured"` — the thickness comes from the region's own
short side, not from the 4 in / 8 in defaults. The existing per-wall
provenance Pset already serialises `detector`, so no IFC authoring change
is needed; the test asserting every wall carries its own provenance Pset
must be extended to cover the new value.

---

## 8. The implementation risk that must be measured, not assumed

**Making ingest emit fills must not perturb paired-line output.**

Fill-triangle edges are today ordinary `"line"` primitives that
`_pair_family` consumes (§2.4). If the new `"fill"` primitive closes a ring
where the old polyline did not, `segments()` yields one extra segment per
region and paired-line's output can shift — changing wall counts, junction
counts, and the scale residuals that success criterion 4 pins.

The implementation plan must, as a distinct step, capture
`detect_walls_paired_lines` output on all three drawings before the ingest
change and assert it identical after. **A difference is a stop, not a
footnote.**

---

## 9. Prerequisite: the space-detection investigation

Success criterion 3 — no space regression — **cannot be met today.** Adding
six filled-body walls takes the electrical plan from 2 spaces to 0, at
every dedup tolerance measured.

Six added walls collapsing room detection is not evidence that filled-body
walls are wrong. It is evidence that `space_boundary_graph` /
`detect_spaces` is fragile to added geometry, breaking rings it previously
closed. W4 exposed the defect; it did not cause it.

**Decision, 2026-08-28:** the space-detection investigation gets its own
brainstorm → spec → plan cycle and lands **before** W4 is implemented. W4's
measured R2 gain waits on it.

The rejected alternative — computing spaces from the paired-line wall set
only, while junctions and IFC receive the full set — would ship W4 sooner
at the cost of burying this finding under a special case.

---

## 10. Testing

**Unit** (no drawings required):
- `merge_fill_regions`: triangles sharing an edge merge; triangles sharing
  only a vertex do not; disjoint triangles stay separate; a single triangle
  is its own region.
- `region_to_wall`: axis-aligned rectangle; rectangle rotated 30°;
  L-shaped region rejected by fill ratio; each gate bound tested from both
  sides (1.99 in / 2.01 in, 23.99 in / 24.01 in, 0.99 ft / 1.01 ft).
- dedup: endpoints exactly at 0.5 ft kept vs dropped; a wall parallel to
  but offset beyond tolerance from an existing wall is kept.
- issue emission: a rejected ≥ 1 sq ft region produces an `Issue`; a
  geometry-admitted region produces an `Issue` naming its layer.

**Integration** against the real drawings, reached via `ARCHIAGENT_FIXTURES`
and skipped when absent — asserting §3's five criteria as explicit numbers,
so a regression names itself.

**Constraints carried from the project's standing rules:** no `.pdf`,
`.dwg`, `.dxf`, or `.ifc` file is ever committed; generated IFC goes to
`tmp_path` only; no test makes a live API call.

---

## 11. Explicitly not in this spec

- **Stroke-hatch bundles.** `HATCH1` (6,534 paths), `WALL HATCH` (1,385),
  `Hatch-3` (551) are all stroke-typed loose 45° strokes with no fill and
  no boundary. Inferring an envelope from a stroke bundle is a different
  algorithm; the roadmap already separates it and this spec keeps it
  separated.
- **Rectilinear decomposition** of non-rectangular regions — 5 regions, 6%
  of filled area, all plausibly curve artefacts (§2.2). Deferred until
  evidence demands it.
- **Room coverage.** Split out per §9.
- **Openings, columns as distinct IFC entities** — M8.
