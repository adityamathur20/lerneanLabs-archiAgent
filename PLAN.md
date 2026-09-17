# 2D Floorplan → 3D BIM Pipeline — Design & Plan

> Historical design. The current semantic reconstruction implementation,
> acceptance behavior and remaining limitations are documented in
> [SEMANTIC_PIPELINE.md](SEMANTIC_PIPELINE.md). In particular, a draft export
> does not establish dimensional or semantic acceptance.

**Project:** lerneanLabs-archiAgent
**Date:** 2026-08-21
**Status:** Phase 1 design approved. Vertical slice proven end-to-end (§4).
Phases 2–3 scoped only.

---

## 1. Goal

Take a 2D floorplan and produce a to-scale 3D architectural model in an
industry-standard format, using open-source CAD and rendering software
augmented by an LLM.

### Phase decomposition

The original brief spans three independent subsystems. Each gets its own
spec → plan → implementation cycle.

| Phase | Output | Status |
|---|---|---|
| **1** | To-scale 3D model in IFC4 | **This document** |
| **2** | Alternative 3D models with interior design from user design intent | Scoped only |
| **3** | MEP plans for an input 3D model, or generated with the model from 2D | Scoped only |

Phase 1 is load-bearing — Phases 2 and 3 both consume its output.

---

## 2. The problem with the previous approach

The earlier pipeline (`floorplan_3d.py`, `floorplan_3d_commercial.py`,
the `read-floorplan` skill) rasterized input to PNG and asked an LLM to
read wall coordinates off pixels. Output was per-room overlapping boxes,
no wall network, no openings — errors on the order of **feet**.

**Root cause is not "the LLM has poor spatial understanding."** It is
that a language model was assigned *metric localization* — estimating
coordinates from an image — the one task it is structurally worst at.
The fix is not better prompting or a fine-tuned vision model. The fix is
to stop asking it for coordinates.

### Guiding principle

> **Deterministic code owns geometry. The LLM owns vocabulary.**

The LLM never emits a coordinate and never writes IFC or `bpy` code. It
classifies, names, disambiguates, and proposes edits.

### Primary requirements

Two requirements outrank everything else in this document. Where any
other goal conflicts with these, these win.

**R1 — Dimensional accuracy: every printed dimension reproduced within
2 inches.** A hard gate, not a target. The pipeline refuses to emit a
model that fails it.

**R2 — Every wall junction correctly resolved.** Walls must be
*connected*, not merely adjacent — topologically in the graph,
geometrically in the solids, and explicitly in the IFC relationships
(§7 Stage 3b). Room detection is entirely downstream of this, so a
junction failure is never a local defect.

#### Error budget for R1

| Source | Contribution | Control |
|---|---|---|
| Source vector geometry | **0** — exact by construction | vector-first ingest |
| Scale factor | dominant term | consensus across ≥3 dimensions; clear-basis; residual table |
| Endpoint snapping | ≤ ½ × snap tolerance = **0.5 in** | tolerance in inches, configurable |
| Junction extension | ≤ extension budget = **6 in**, but only where a junction genuinely exists | reported per-junction |
| Thickness defaults | flagged, not silent | `thickness_source` in provenance |

Scale is the term that matters, because its error is *proportional*:
across a 100 ft span, a 2 in budget means the factor must be accurate
to roughly **0.17%**. This is why the residual table (§7 Stage 2) is the
pipeline's primary correctness signal and its main regression metric —
it is the only thing that measures the dominant error term directly.

---

## 3. Evidence (spikes, 2026-08-21)

### 3.1 CAD layers survive into the PDFs

All three files in `input-floorplans/` are vector CAD exports preserving
named CAD layers as PDF Optional Content Groups. PyMuPDF tags every
path with its layer name.

| File | Layers | Vector paths | Live text words |
|---|---|---|---|
| `GROUND FLOOR PLAN_WORKING REVISED.pdf` | 37 | 16,432 | 0 (text outlined) |
| `ELECTRICAL FINAL l PLAN.pdf` | 53 | 25,596 | 508 |
| `DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf` | 16 | 14,006 | 78 |

Layers in the ground-floor plan include:

```
WALLS · WALL HATCH · RCC WALL · BEAM · COLOUM · COLUM HATCH
door · window · STAIR · DIM · OVERALL DIM · WORK DIM · ELECT DIM
TEXT · GRID TEXT · ELECTRICAL TEXT · LVL TEXT · SILL TEXT
GRID LINES · GRID CIRCLE · FURN · FURNI · CARS · C-LINE · PROPERTY LINE
```

Filtering to structural layers alone reproduces a clean architectural
plan — **no vision, no OCR, no computer vision**.

### 3.2 Layer names are a per-drawing vocabulary, not a standard

Walls appear as `WALLS`, `WALL`, `Wall`, `walll`, `A-Wall` across the
three files. Layer `0` is a CAD junk drawer — in the ground-floor plan
it holds the parked cars. `BEAM` looks structurally like a wall but is
**overhead** and must be excluded.

This is the correct LLM task: classify 16–53 layer *names* per drawing
instead of estimating thousands of coordinates.

### 3.3 Scale is recoverable to sub-inch

**Provisional — superseded.** The 11.861 pt/ft figure below was derived
from a single-dimension anchor on an early spike, before consensus scale
resolution existed. The M1–M5 implementation's 21-dimension consensus
value for this drawing is **11.6667 pt/ft** (max residual 1.568in); treat
that as the ground truth, not the anchor below. See "Limitations
discovered during M1–M5 implementation" for how badly a single- or
few-dimension anchor can mislead.

Anchoring on one printed dimension and predicting seven others against
actual vector geometry:

| Printed | Predicted | Actual | Error |
|---|---|---|---|
| 13'-10" | 164.1 pt | 164.3 pt | **0.2 in** |
| 3'-0" | 35.6 pt | 35.6 pt | **0.0 in** |
| 4'-10" | 57.3 pt | 57.8 pt | 0.5 in |
| 7'-0" | 83.0 pt | 82.3 pt | 0.7 in |
| 9'-7" | 113.7 pt | 114.4 pt | 0.7 in |
| 5'-0" | 59.3 pt | 57.8 pt | 1.5 in |
| 9'-10" | 116.6 pt | 114.4 pt | **2.3 in** ← fails the <2 in bar |

The 2.3 in outlier is **systematic, not noise**: it is the
centerline-vs-clear ambiguity, and the discrepancy *is* a wall
thickness. See §6 Stage 2 for the handling this requires.

### 3.4 `ifcopenshell.api` authors parametric IFC with no Blender

Verified with `bpy` confirmed un-importable:

```
representation item type: IfcExtrudedAreaSolid   ← parametric, not a mesh
extrude depth: 3.6576 m = 12.0 ft
openings: 1 | doors: 1 | spaces: 1
void rel: W001                                   ← real IfcRelVoidsElement
pset roundtrip: ['WALLS', 'paired-line', 0.98, 0.2]
```

This resolves the "less-tested path" concern in the safer direction:
headless authoring is the **proven** path; in-Blender construction
carries the operator-context risk. The API calls are identical either
way — it is the same library.

### 3.5 Text availability is mixed

Two of three files carry live text with exact bounding boxes. The
ground-floor plan has text converted to outlines — geometry still exact,
labels require OCR. The `TEXT` / `GRID TEXT` / `ELECTRICAL TEXT` layers
isolate exactly where text lives, so OCR runs on clean cropped glyphs.

### 3.6 Wall representation is mixed

Walls appear both as **paired parallel lines** and as **hatch-filled
bodies**; columns hatched separately. Both must be supported.

---

## 4. Proven vertical slice

A throwaway end-to-end run on `DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf`:
PDF → layer filter → paired-line detection → parametric IFC4 → Bonsai
load in Blender. Output committed as
`reference/demo_demolition_walls.ifc`.

| | Value |
|---|---|
| Raw segments on `wall`/`walll` | 187 |
| Walls after paired-line detection | 49 (25 horizontal, 24 vertical) |
| Detected thickness | median **4.0 in**, max 13.1 in |
| Building envelope | 44.4 × 40.0 ft |
| Blender/Bonsai | v5.1.2, Bonsai enabled, ifcopenshell 0.8.5 |

The 4.0 in median is **measured from the drawing, not assumed**, and
independently matches the specified interior default — corroborating
the paired-line detector.

The demo deliberately skips junction healing (Stage 3) and opening
detection (Stage 5); the visible wall gaps are those two stages, not
defects in the proven parts.

### Implementation traps discovered

1. **`ShapeBuilder.rectangle` anchors the profile at its corner, not its
   center.** Placing walls at their midpoint produced 49 scattered
   floating slabs. Fix: offset the profile by `-thickness/2` and place
   at `p0`. **The builder's unit tests must cover this.**
2. **`ifcopenshell.api.void.*` was renamed `feature.*` in 0.8.x** —
   `feature.add_feature`, `feature.add_filling`.
3. **`ShapeBuilder.rectangle` yields `IfcArbitraryClosedProfileDef`.**
   Construct `IfcRectangleProfileDef` directly for cleaner BIM.
4. **`IfcSpace` uses `aggregate.assign_object`, not
   `spatial.assign_container`.**
5. **PDF y-axis grows downward** — must be flipped to building
   coordinates.

---

## 5. Why not CubiCasa5K / DeepFloorplan / Raster-to-Vector / FloorplanToBlender3d

Considered and deferred. Recorded so it is not relitigated.

- **Not a hardware issue.** Inference is a single forward pass on a
  1–4 MP image — CPU-seconds. Only *fine-tuning* needs a GPU.
- **Wrong input modality.** All are raster-input methods whose purpose is
  recovering structure destroyed by rasterization. Our inputs still have
  that structure. Using them means rasterizing exact geometry and then
  statistically approximating what we already had exactly.
- **Domain gap, unfixable by hardware.** Trained on clean stylized
  residential plans (Finnish apartments in CubiCasa5K; R2V/R3D). Our
  inputs are dense construction documents where dimension chains and
  grid lines are visually indistinguishable from walls to a segmentation
  network.
- **Maintenance cost.** DeepFloorplan is TensorFlow 1.x (effectively
  dead); Raster-to-Vector's original implementation is Torch/Lua.
- **FloorplanToBlender3d** is classical OpenCV (threshold → findContours
  → extrude), not ML. Works on clean simple plans, collapses on dense
  CAD drawings, and targets Blender meshes rather than IFC.

**Where they earn a place later:**

1. **Raster degradation path.** With a genuine scan there is no exact
   data to lose — that is where a learned model beats hand-tuned OpenCV,
   and where CubiCasa's pretrained weights are the first thing to try.
2. **CubiCasa5K as a benchmark, not a model.** 5,000 plans with
   ground-truth SVG measures accuracy objectively. *Verify the licence
   before any commercial use — believed non-commercial research.*
3. **Evaluation methodology** — junction/room IoU metrics.

### On RAG over CubiCasa5K

Retrieval helps when the missing ingredient is **knowledge**. Our
failure was **perception**. Retrieving similar floorplans does not
improve measurement — it supplies *priors*, which here are actively
harmful: they bias output toward the retrieved building rather than the
drawing in hand (hallucination-by-analogy). Retrieval helps when the
answer is in the corpus; our answer is in the input file and nowhere
else.

**Legitimate uses, all in the semantic layer:**

1. Corpus-derived **symbol dictionary** (upgrading
   `references/symbols.md`). Caveat: CubiCasa's icon vocabulary is ~12
   classes and residential-biased.
2. **Statistical priors as validators only** — "bedrooms run 9–15 ft;
   this says 40 ft, flag it." Safe because it only flags, never fills in.
3. **Few-shot examples** for room-type classification.

`Data-base/floorplan/` (RoomSketcher symbol and plan-reading pages) is
already a better-shaped RAG corpus than CubiCasa5K, being
knowledge-as-text.

---

## 6. Architecture

```
┌─ Stage 0   Ingest — layer-separated vector PDF / DXF / DWG
│                                → Normalized Primitive Set
├─ Stage 1   Layer classification ........................ [LLM]
├─ Stage 2   Scale resolution (clear-dimension default)
├─ Stage 3   Wall network extraction + junction healing
├─ Stage 4   Space (room) detection
├─ Stage 5   Opening detection
├─ Stage 6   Semantic labelling & symbol naming .......... [LLM]
├─ Stage 7   Model assembly + validation
├─ Stage 8   IFC authoring — HEADLESS ifcopenshell.api
│                                → model.ifc  ← SOURCE OF TRUTH
├─ Stage 9   Load into Blender via Bonsai
└─ Stage 10  Dump bpy script from the loaded scene

        ┌──────────── correction loop ────────────┐
        │  IFC ──project──▶ Model View (JSON)     │
        │   ▲                     │               │
        │   │                     ▼               │
        │  apply ops ◀──── LLM proposes edits     │
        └─────────────────────────────────────────┘
```

Only Stages 1 and 6 involve the LLM. Both are classification over a
bounded vocabulary. No stage asks the LLM for a coordinate, for IFC, or
for `bpy`.

---

## 7. Stage detail

### Stage 0 — Ingest

**Purpose:** normalize every input to one internal representation.

| Input | Library | Status |
|---|---|---|
| Layer-separated vector PDF | PyMuPDF | **v1 target** |
| DXF | ezdxf | v1 target |
| DWG | ODA File Converter / LibreDWG → DXF | v1, external binary |
| Merged/flattened PDF | — | **deferred, fail fast** |
| Raster image / scan | — | **deferred** |

**Scope decision:** v1 supports **layer-separated PDFs only**. If a PDF
has no OCGs and no usable layer signal, the pipeline **fails fast with a
clear message** rather than silently degrading. Merged PDFs and raster
images are deliberate future work.

**Output — Normalized Primitive Set:**

```python
Primitive(kind="line"|"rect"|"curve"|"fill",
          coords=[...],          # source units (PDF points / DXF units)
          layer="WALLS", stroke_width=0.72, color=(r,g,b), fill=None)

TextItem(text="14'-5\"", bbox=(x0,y0,x1,y1), layer="DIM", font="ArialMT")
```

This abstraction is where the deferred raster front-end will plug in
without touching Stages 1–10.

**Note:** PDF y-axis grows downward; flip to building coordinates here,
once, so no downstream stage has to think about it.

### Stage 1 — Layer classification `[LLM]`

**Purpose:** map this drawing's private layer vocabulary onto a fixed
role vocabulary.

**Input:** compact layer inventory — per layer: name, path count,
stroke-width histogram, dominant colors, bbox coverage, axis-aligned
fraction, segment-length percentiles. Optionally a thumbnail of that
layer alone.

**Role vocabulary (fixed):**

```
wall_structural · wall_partition · column · beam_overhead
door · window · stair · railing
dimension · text_label · grid · annotation · title_block
furniture · electrical · plumbing · vehicle · landscape · ignore
```

**Output:** `{layer_name: (role, confidence)}`, persisted and
independently editable.

**Leverage:** a wrong wall is usually a wrong *layer*. Correcting one
layer role fixes dozens of walls at once — which is why provenance
(§8) is mandatory.

Implemented in W1 as `LLMLayerClassifier`
(`archiagent/classify/llm_classifier.py`), backed by the `LLMClient` port in
`archiagent/llm/`. `StubClassifier` is retained for tests and for the CLI's
`--walls` bypass. Default model `claude-haiku-4-5`; the provider is
configurable, and `--inspect` / `--walls` work with no credentials at all.
See `docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md`
and the measured results in
`docs/superpowers/reports/2026-08-23-w1-verification.md`. Live-provider
verification is outstanding — no API credentials were available when that
report was written — see its "Outstanding: live verification" section for
the commands still needed to close Task 8.

### Stage 2 — Scale resolution

**Purpose:** establish the exact source-unit → foot factor.

**Hard acceptance criterion: agreement within 2 inches across at least
three independent dimensions.** This is a gate, not a goal — the
pipeline refuses to proceed below it.

**Dimension convention: printed dimensions default to CLEAR
(face-to-face).** Room labels such as `14'-5" X 13'-10"` denote internal
clear dimensions. The resolver therefore matches printed values against
**inner wall faces**, not centerlines:

```
centerline_distance = clear_distance + t1/2 + t2/2
```

This is what eliminates the 2.3 in outlier in §3.3 — that residual was
exactly an uncompensated wall thickness.

**Method:**
1. Extract dimension strings from `TextItem`s on `dimension`-role
   layers. Parse feet-inches (`14'-5"`, `13'-10"`), metric, and mixed.
2. Associate each string with its dimension-line geometry.
3. Compute a candidate factor per dimension, **assuming clear**.
4. Robust consensus (median / RANSAC).
5. **Emit the residual table** — predicted vs. actual for every
   dimension. This is the primary correctness signal and a regression
   metric (§11).
6. If a dimension's residual improves markedly under a centerline
   assumption, flag it as centerline and note the exception rather than
   silently switching.

**Fallbacks, in order:** OCR of text-layer crops (outlined text); scale
bar geometry; drawing-ratio note in the title block; ask the user.

### Stage 3 — Wall network extraction

**Purpose:** a topologically clean wall centerline graph with true
thicknesses.

Two detectors, merged and deduplicated:

**(a) Paired-line detector** *(proven in §4)*. On `wall_*`-role layers,
find parallel, overlapping segments separated by a plausible thickness
(3–24 in). Emit centerline + measured thickness.

**(b) Hatch-body detector.** On wall-hatch layers, take the hatch region
boundary polygon, compute its medial axis, emit centerline + thickness.

**Then build the graph:** nodes = junctions, edges = wall runs
(`networkx`). Drop dangles below a minimum length; merge collinear runs;
classify exterior vs. interior by envelope adjacency.

#### Stage 3b — Junction resolution *(critical path)*

Junctions are the single highest-risk part of the pipeline. Stage 4
(rooms) is *entirely* downstream of them: one unhealed junction leaks
two rooms into one, and the error propagates silently into every later
phase. The visible wall gaps in the §4 demo are exactly this step
missing.

**Junction taxonomy — each needs distinct handling:**

| Type | Geometry | Resolution |
|---|---|---|
| **L (corner)** | two wall *ends* meet, non-collinear | extend/trim both centerlines to their exact intersection |
| **T** | one wall's *end* meets another's *interior* | extend the terminating wall to the through-wall's centerline; split the through-wall at the node |
| **X (cross)** | two centerlines cross mid-run | split both at the intersection; 4 edges from 1 node |
| **Collinear join** | two collinear runs, end to end | merge into a single run if thickness and type match |
| **Near-miss** | endpoints within tolerance, not touching | cluster to a single node |
| **Overshoot** | wall extends past the junction | trim to intersection |
| **Undershoot** | wall stops short (the common CAD case) | extend to intersection |

**Algorithm, in order:**

1. **Endpoint clustering.** Union-find over endpoints within snap
   tolerance (default **1.0 in**, expressed in inches — never pixels).
   Cluster centroid becomes the junction node.
2. **Intersection resolution.** For every wall pair whose centerlines
   intersect within an extension budget of either end, move that
   endpoint to the exact intersection. Extension budget defaults to
   **6 in** — beyond that, the gap is a doorway or a genuine
   discontinuity, not sloppy drafting, and must not be silently closed.
3. **Through-wall splitting.** Split any wall whose interior contains a
   junction node, so T and X junctions become real graph nodes rather
   than geometric coincidences.
4. **Collinear merge.** Merge degree-2 nodes joining collinear runs of
   equal thickness and type.
5. **Dangle pruning.** Remove degree-1 edges below minimum length, but
   **report each one** — a dangle is often a real wall whose partner
   was missed, not noise.
6. **Closure validation.** Every space polygon from Stage 4 must be
   closed. Unclosed regions are reported with the specific node and
   distance, never auto-fudged by widening tolerance.

**Geometric correctness at the joint.** Extending centerlines to the
intersection makes the swept solids *overlap* at corners. Overlap is
correct for visual and spatial purposes but double-counts volume in
quantity takeoff. Accepted for Phase 1 and recorded as a known
limitation; proper mitering is deferred.

**Junctions must be recorded in IFC, not merely implied by geometry.**
Stage 8 emits `IfcRelConnectsPathElements` for every wall-to-wall
junction, carrying `RelatingConnectionType` / `RelatedConnectionType`
(`ATSTART` / `ATEND` / `ATPATH`) and priorities. This is what lets
Bonsai and other BIM tools perform their own wall cleanup and mitering,
and it is what makes the model genuinely parametric rather than a pile
of correctly-positioned boxes. **A model whose walls merely touch is not
a model whose walls are connected.**

**Tolerances are configurable and always reported** in the model's
provenance, so a run's junction behaviour is auditable rather than
buried in constants.

**Thickness defaults when the drawing does not give one:**

| Wall class | Default |
|---|---|
| Interior / partition | **4 in** |
| Exterior | **8 in** |

Defaults are applied only when measurement fails, are configurable, and
are always recorded in provenance with a lowered confidence so they are
distinguishable from measured values.

### Stage 4 — Space detection

**Purpose:** rooms as exact closed polygons.

**Method:** `shapely.polygonize` over the healed centerline graph. Each
bounded face is a candidate space. Filter by minimum area and
containment within the envelope.

**This is the step that replaces vision entirely.** Rooms are not
guessed; they fall out of wall topology as a mathematical consequence.

**Dominant failure mode:** an unclosed wall run merges two rooms —
caught by the area-sum validator in Stage 7.

### Stage 5 — Opening detection

**Purpose:** locate doors/windows/gates and bind each to a host wall.

1. Take symbol geometry from `door` / `window`-role layers (present in
   all three sample files).
2. Bind each to a host wall by proximity and containment.
3. Derive **swing** from arc geometry: arc center = hinge, radius = leaf
   width, sweep = swing side.
4. Cross-check against gaps in wall runs.
5. Compute `offset` along the host wall, plus width and height (from
   sill/head text where available, else type defaults).

**LLM involvement:** only to adjudicate cases the deterministic pass
flags as ambiguous.

### Stage 6 — Semantic labelling & symbol naming `[LLM]`

**Room naming:**
1. *Deterministic:* point-in-polygon each `TextItem` on
   `text_label`-role layers into its containing space.
2. *LLM:* normalize the raw label to a `room_type` —
   `"PANTRY CUM SEATING"` → `pantry`, `"CABIN 2"` → `office`.

**Symbol naming:**
1. *Deterministic:* cluster geometrically-identical repeated path groups
   (CAD blocks and XObjects reuse identical geometry). **Locating is
   never the LLM's job.**
2. *LLM:* render one clean crop **per cluster** and classify it once —
   WC / sink / shower / floor drain / ceiling rose / switchboard.
3. Propagate the label to every instance in the cluster.

~12 judgments instead of ~300, each on an isolated glyph.
Classification of an isolated symbol is what vision models are good at,
as distinct from metric localization, which they are not.

### Stage 7 — Model assembly + validation

| Check | Failure meaning |
|---|---|
| Space areas sum ≈ envelope area | missing or merged rooms |
| Every space polygon closed and simple | unclosed wall run |
| Every opening has a resolvable host wall | orphaned symbol |
| **Printed dimensions vs. computed, clear-basis, <2 in** | wrong scale or wrong wall |
| No overlapping/duplicate walls | detector double-count |
| No zero-length or zero-thickness walls | extraction artifact |
| Every space has ≥1 opening | isolated room (usually an error) |

Output is the model **plus an issues list** with severity and the
implicated entity ID. Nothing is silently corrected.

### Stage 8 — IFC authoring (headless)

**Proven in §3.4.** A fixed, versioned, tested builder reads the
assembled model and authors IFC4 via `ifcopenshell.api` — **no Blender
required**.

**Two hard rules:**

1. **The builder is fixed, tested code**, never LLM-improvised. The LLM
   invokes it and inspects results; it does not write it.
2. **Author via the IFC API, never by mesh-modelling.** Mesh-first
   yields `IfcFacetedBrep` blobs with no parametric meaning, which would
   gut Phase 3 (MEP needs real entities to attach to). API-first yields
   true `IfcWallStandardCase` swept from a centerline.

**Construction order:** `IfcProject` → units → `IfcSite` → `IfcBuilding`
→ `IfcBuildingStorey` → walls (axis + profile extrusion) →
`IfcOpeningElement` voids → `IfcDoor`/`IfcWindow` fills → `IfcSlab` →
`IfcColumn` → `IfcStair` → `IfcSpace` → provenance property sets.

Mind the five traps in §4.

### Stage 9 — Load into Blender

Bonsai (`bpy.ops.bim.load_project`) loads the IFC for viewing, manual
editing, and rendering. **Proven in §4** on Blender 5.1.2 with Bonsai
and ifcopenshell 0.8.5.

This is the user's working surface — no custom review UI is built.

### Stage 10 — Dump bpy script

**Purpose:** emit a reproducible `bpy` script representing the loaded
scene.

**Blender has no built-in "dump scene as bpy script."** We write a
deterministic serializer that walks the loaded scene graph and emits
`bpy` calls. It is fixed, versioned, tested code.

**The LLM never writes `bpy`.** It invokes the serializer.

---

## 8. Source of truth and the correction loop

**Decision: `model.ifc` is the source of truth once generated.** The
Building Model JSON is a **derived, LLM-readable view**, regenerated
from the IFC on demand.

**Rationale.** The user interacts with the IFC pre-loaded in Blender.
Two independently-mutable representations would require bidirectional
merge — a classic bug source. One truth plus a projection avoids it
entirely.

### Flow

```
Stages 0–7  ──▶  assembled model  ──▶  Stage 8  ──▶  model.ifc
                                                        │
                          ┌─────── project ─────────────┤
                          ▼                             │
                  Model View (JSON)                     │
                          │                             │
              LLM reads, proposes edit ops              │
                          │                             │
                          └──── apply via ──────────────┘
                               ifcopenshell.api
                                     │
                                     ▼
                          Blender/Bonsai reload
```

- **Projection (re-import):** `ifc_to_model_view()` reads the IFC and
  produces the JSON view with stable IDs mapped to IFC GlobalIds.
- **Edits:** the LLM emits *operations*, not files. Operations are
  applied to the IFC by deterministic code via `ifcopenshell.api`.
- **Manual Blender edits** are picked up on the next projection, since
  Bonsai's native IFC keeps the file authoritative.

### Model View schema

```json
{
  "schema_version": "1.0",
  "ifc_file": "model.ifc",
  "source": { "file": "GROUND FLOOR PLAN_WORKING REVISED.pdf",
              "sha256": "...", "page": 0 },

  "scale": { "units_per_point": 11.861, "units": "ft",
             "method": "dimension-consensus", "convention": "clear",
             "residuals_in": [0.2, 0.0, 0.7, 0.5], "max_residual_in": 0.7,
             "confidence": 0.97 },

  "layer_decisions": [
    { "layer": "WALLS", "role": "wall_structural", "confidence": 0.98,
      "reason": "solid double-line pair on a layer named WALLS",
      "source": "llm" },
    { "layer": "0", "role": "ignore", "confidence": 0.0, "reason": "",
      "source": "manual" } ],

  "levels": [ { "id": "L0", "ifc_guid": "1FdJRn48X10RjxNO4d$wwl",
                "name": "Ground Floor", "elevation": 0.0, "height": 12.0 } ],

  "walls": [ { "id": "W001", "ifc_guid": "...", "level": "L0",
               "start": [0.0, 0.0], "end": [50.0, 0.0],
               "thickness_in": 8.0, "height_ft": 12.0, "type": "exterior",
               "provenance": { "layer": "WALLS", "detector": "paired-line",
                               "thickness_source": "measured" },
               "confidence": 0.98 } ],

  "spaces": [ { "id": "S001", "ifc_guid": "...", "name": "Restaurant",
                "type": "dining", "boundary": [[18.6,5.0],[40.0,5.0]],
                "area_sqft": 570.2, "confidence": 0.90 } ],

  "openings": [ { "id": "O001", "ifc_guid": "...", "host_wall": "W012",
                  "type": "door", "offset_ft": 4.5, "width_ft": 3.0,
                  "height_ft": 7.0, "swing": "in-left", "confidence": 0.72 } ],

  "issues": [ { "severity": "warn", "entity": "W031",
                "code": "unclosed_junction",
                "msg": "endpoint 1.8in from nearest wall, above snap tolerance" } ]
}
```

`layer_decisions` carries one `LayerDecision` per layer — role,
confidence, reason and source. The earlier `layer_roles: dict[str, str]`
discarded the classifier's confidence and had no field for a reason, which
left the W2 review surface with nothing to display. See
`docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md` §3.

### Why provenance is non-negotiable

When a wall is wrong, provenance reports it came from layer `BEAM` via
the hatch detector. The fix is then to correct the *layer role* — and
forty walls correct at once. Without provenance, that is forty manual
edits. `thickness_source` likewise distinguishes measured values from
the 4/8 in defaults.

---

## 9. Module layout

```
archiagent/
  ingest/
    primitives.py      # Normalized Primitive Set dataclasses
    pdf_vector.py      # PyMuPDF front-end (v1)
    dxf.py             # ezdxf front-end
    dwg.py             # ODA/LibreDWG → DXF shim
  classify/layers.py   # Stage 1 — LLM layer-role classification
  scale/
    dimensions.py      # dimension-string parsing
    resolve.py         # Stage 2 — clear-basis consensus + residual table
  geometry/
    walls.py           # Stage 3 — detectors, snapping, junction healing
    spaces.py          # Stage 4 — polygonize
    openings.py        # Stage 5 — symbol → host wall, swing
  semantics/
    rooms.py           # Stage 6 — label → room_type
    symbols.py         # symbol clustering + LLM naming
  model.py             # assembled model + Model View schema
  validate.py          # Stage 7 — validators, issues list
  ifc/
    author.py          # Stage 8 — headless builder (ifcopenshell.api)
    project.py         # IFC → Model View projection (re-import)
    ops.py             # edit operations applied to IFC
  blender/
    load.py            # Stage 9 — Bonsai load
    dump_bpy.py        # Stage 10 — deterministic scene → bpy serializer
  preview.py           # SVG overlay: model vs. source drawing
  mcp_server.py        # MCP tool surface
```

Geometry modules have **no LLM dependency** and are deterministically
unit-testable.

---

## 10. MCP tool surface

| Tool | Purpose |
|---|---|
| `extract_floorplan(path, page)` | Stages 0–8 → `model.ifc` + issues report |
| `project_model(ifc_path)` | IFC → Model View JSON (re-import) |
| `inspect_model(filter)` | Query entities by id, type, confidence, layer |
| `edit_model(ops)` | Apply edit operations to the IFC |
| `reclassify_layer(layer, role)` | Re-run downstream stages after a layer fix |
| `load_in_blender(ifc_path)` | Stage 9 |
| `dump_bpy(out_path)` | Stage 10 |
| `render_preview()` | SVG/PNG overlay against the source drawing |

`edit_model` is the workhorse of the correction loop — how the remaining
10–20% gets fixed without re-running extraction.

---

## 11. Testing strategy

TDD throughout (`superpowers:test-driven-development`).

**Golden tests** against the three PDFs in `input-floorplans/`:
- layer classification matches a hand-checked expected mapping
- **resolved scale within 2 in on every dimension (hard gate)**
- room count and named-room set match
- **the dimension-residual table is the primary regression metric**

**Property tests (deterministic, no LLM):**
- every polygonized space is closed and simple
- every opening resolves to exactly one host wall
- snapping is idempotent
- wall thickness always > 0
- **wall placement: profile corner-anchor offset is correct** (§4 trap 1)
- round-trip: model → IFC → project → model preserves entity counts

**Junction tests (R2) — synthetic fixtures with known-correct answers,
one per taxonomy row in §7 Stage 3b:**
- L / T / X / collinear / near-miss / overshoot / undershoot each resolve
  to the expected node count and edge count
- a gap **within** the extension budget closes; a gap **beyond** it stays
  open and is reported (a doorway must never be silently sealed)
- after resolution, no wall endpoint lies within snap tolerance of a
  wall it is not connected to
- every junction node emits a corresponding `IfcRelConnectsPathElements`
  with correct `ATSTART` / `ATEND` / `ATPATH` types
- **a closed rectangular ring of 4 walls yields exactly 1 space**; the
  same ring with one wall removed yields 0 and reports the opening
- junction resolution is idempotent and order-independent (shuffling
  input wall order gives an identical graph)

**Integration:** full pipeline per sample → valid IFC opening in
Blender/Bonsai and FreeCAD without error.

**Evaluation:** junction/room IoU metrics so accuracy is measured, not
eyeballed.

---

## 12. Milestones

| # | Milestone | Proves |
|---|---|---|
| **M1** | Ingest + layer classification on all 3 PDFs | the layer hypothesis generalizes |
| **M2** | Scale resolution, clear-basis, <2 in on all dimensions | metric correctness |
| **M3** | Wall graph + **full junction taxonomy resolved (R2)** + polygonized spaces | the core replacement for vision |
| **M4** | Model assembly + validators | internal consistency |
| **M5** | Headless IFC authoring incl. `IfcRelConnectsPathElements` + Bonsai load | end-to-end deliverable |
| **M6** | IFC → Model View projection + `edit_model` | the correction loop |
| **M7** | bpy script dump | reproducible scene artifact |
| **M8** | Openings, columns, stairs | completeness |

M1–M5 is a working vertical slice — and §4 already demonstrates a
throwaway version of it. Ship the real one before broadening.

---

## 13. Non-goals (Phase 1)

- **Merged / flattened PDFs** — fail fast; future work
- **Raster and scanned input** — deferred; front-end interface reserved
- Multi-page / multi-storey stitching (one page per run)
- Automatic conflict resolution between concurrent edits
- Interior design, furniture, materials → **Phase 2**
- MEP routing and systems → **Phase 3**
- Structural analysis, code compliance checking
- A custom review UI — the review surface is Blender plus conversation

---

## Limitations discovered during M1–M5 implementation

These were found while implementing and reviewing Tasks 1–14 (scale
resolution, junction resolution, space detection, IFC authoring). They are
recorded here — not only in review notes, which are not committed — so a
future maintainer does not have to rediscover them from scratch.

1. **A wrong scale can pass the R1 gate.** Measured: with hatch layers
   misclassified as walls, `resolve_scale` settled on 8.36 pt/ft against a
   true 11.67 — a 40% error — with *both* a higher match count (22 vs 21)
   and a lower residual (1.904 vs 1.568) than the correct answer. Neither
   of the gate's criteria distinguishes them. The structural fix is §7
   Stage 2's spatial association of each dimension string with its own
   dimension-line geometry, which M1–M5 simplified to length-magnitude
   matching. **This is now a required M6 item, not optional.**
2. **Centerline polygonization cannot detect rooms with doors.** Every
   room has a door; a door is a genuine gap; correctly refusing to close
   gaps beyond 6in means a room with a door never forms a closed ring.
   Spaces therefore use a separate `space_boundary_graph` that bridges
   door-width gaps for polygonization only, never mutating wall geometry.
3. **Room coverage is bounded by wall coverage, not by bridging.**
   Collinear-gap bridging was implemented and measured: it creates
   bridges but yields zero additional rooms. The binding constraint is
   the deferred hatch-body wall detector.
4. **`extend_in` (6in) and `min_dangle_ft` (0.5ft) are the same length**,
   so the fixpoint dangle-pruning branch is unreachable in production
   runs. Exercised only by tests that separate the thresholds.
5. **Ingest drops curve and quad path items** — 619 of 14,006 paths in
   the sample drawing. M1–M5 needs only lines and rects; **M6 opening
   detection will need arcs** to derive door swing from arc centre and
   radius.
6. **Genuine mid-run X-crossings are not resolved.** `cluster_endpoints`
   creates nodes only at endpoints and `split_through_walls` splits only
   at existing nodes, so two walls crossing mid-run with no endpoint at
   the crossing produce no junction. Zero occurrences on the sample
   drawing. **§7 Stage 3b's claim that the taxonomy handles this case is
   incorrect and should be read as aspirational.**
7. **`extend_to_intersections` picks the last-iterated intersection, not
   the nearest one, when a wall's original endpoint is within budget of
   two different walls' lines.** See the `KNOWN LIMITATION` comment in
   `archiagent/geometry/junctions.py::extend_to_intersections`, immediately
   above the budget check, for the reproduction and why nearest-wins was
   prototyped but parked: it drops the sample drawing's only detected
   space from 1 to 0, and today's practical impact is nil because wall
   detection order is stable per input PDF. Sequence a nearest-wins fix
   with the M6 hatch-body detector.

---

## 14. Open risks

| Risk | Impact | Mitigation |
|---|---|---|
| Layer names too idiosyncratic for reliable classification | high | thumbnail-per-layer as LLM input; `reclassify_layer` correction; visual preview |
| **Junction resolution leaves rooms unclosed** | **highest — breaks R2 and all of Stage 4** | full taxonomy handling (§7 Stage 3b); synthetic per-type fixtures (§11); area-sum validator; explicit per-node reporting, never tolerance-widening |
| Extension budget too generous — seals real doorways | high — silently wrong rooms | budget capped at 6 in and reported per junction; opening detection cross-checks gaps |
| Clear-vs-centerline misclassification on some dimensions | medium | residual table exposes it; per-dimension flagging |
| Outlined text (confirmed in 1 of 3 files) | medium | targeted OCR on text-layer crops |
| Hatch-body medial axis unstable on irregular hatches | medium | prefer paired-line where both detectors fire |
| DWG requires external converter binary | low | document dependency; PDF/DXF path unaffected |
| CubiCasa5K licence (if adopted later) | low | verify before commercial use; do not assume |

---

## 15. Decision log

0. **Accuracy (R1, <2 in) and junction correctness (R2) outrank every
   other goal** — where anything conflicts with them, they win.
1. **Vector-first, not raster-first** — inputs are CAD exports.
2. **Layer-separated PDFs only in v1** — fail fast otherwise; merged PDFs
   and images are future work.
3. **IFC4 as the output format** — semantic BIM, not meshes; Phases 2
   and 3 require the semantic layer.
4. **Human-in-the-loop, not fully autonomous** — 80–90% automatic, the
   remainder corrected via conversation or by hand in Blender.
5. **No custom review UI** — review happens in Blender.
6. **`model.ifc` is the source of truth**; the Model View JSON is a
   derived projection, regenerated on demand (re-import).
7. **IFC authored headless via `ifcopenshell.api`** — proven in §3.4;
   Blender loads the result rather than constructing it.
8. **The builder and the bpy serializer are fixed, tested code** — the
   LLM writes neither IFC nor `bpy`.
9. **The LLM classifies; it never emits coordinates.**
10. **Printed dimensions default to CLEAR (face-to-face).**
11. **Wall thickness defaults: interior 4 in, exterior 8 in**, applied
    only when measurement fails, always recorded in provenance.
12. **Scale accuracy <2 in is a hard gate**, not a goal.
13. **Pretrained floorplan models deferred** — wrong input modality and
    a severe domain gap, not a hardware limitation.
14. **RAG confined to the semantic layer** — it cannot fix perception.
