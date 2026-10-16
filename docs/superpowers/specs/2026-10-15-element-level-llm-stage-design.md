# Element-level LLM stage

Date: 2026-10-15. Status: approved by the owner 2026-10-16 (decisions in section 10). P0 built; P1 onward in progress.

## 1. Why

The pipeline finds lines well and understands drawings badly. The owner's review of a
South Face run (ground and first floor) listed what is wrong, and almost none of it is a
line-detection failure:

| Observation | What is missing |
|---|---|
| stepped garden boundary drawn as ordinary 10 ft walls | a notion of site wall versus building wall |
| pool walls at 10 ft | per-element height; a pool is not a room |
| an extra wall in the living room | no element-level review; a door swing or void marker became a wall |
| most windows absent | windows are thin lines across a wall gap, not symbols |
| foyer and double height not understood | the drawing states them in text we ignored |
| "LIVING ROOM BELOW" got ceiling-high walls | no void, no railing |
| lift not understood | no shaft concept |

The evidence about the current design (measured in this repository's runs, October 2026):

* Of 227 wall candidates on MB Panwar (two floors), 192 (85%) were decided by the *layer
  default*, not by anyone looking: wall adjudication is capped at 24 per floor.
* Across RAJEEV and Baglow, 136 to 261 candidates per run were ambiguous; 5 to 13 got a
  usable verdict; 24% of verdicts overturned the layer default.
* Layer classification costs 4 to 5 vision calls (16 to 20 layers), which is about two
  thirds of a run's tokens, and answers a question ("is this layer walls?") that the
  elements on it do not all share.
* Wall coverage by the proxy metric is about 55% of drawn wall linework.
* A full two-floor run is about 75k to 85k tokens, 17 to 18 calls, about $0.30 at assumed
  Sonnet-class rates, 3.5 to 4 minutes.

The owner's direction: qualify *elements*, not layers; give the model the floor, scale,
widths and a render; let it name spaces, openings and how floors connect.

## 2. Principle

**The model decides; geometry executes.** The model never emits a coordinate. It returns
verdicts and attributes keyed by candidate id, and the local code builds exact geometry from
the candidates it already holds. An id the model invents cannot reach geometry (the rule
`WallAdjudicator` already follows).

This keeps what local code is good at (exactness, closure, joins, IFC validity) and moves
what it is bad at (what a thing *is*, what it is *for*) to the model, one element at a time.

## 3. Pipeline, per floor

```
 evidence ──► candidates ──► overview + tiles ──► model ──► reconcile ──► build
 (local)      (local)         (local render)      (LLM)      (local)       (local)
```

### 3.1 Evidence (local, exists)
Primitives, native text, scale, declared thicknesses, parsed label attributes
(`geometry/room_attributes.py`: level, clear height, double height, "below"/void).

### 3.2 Candidates (local, partly exists)
Every thing that could become an element gets a stable id and a record of the facts the model
and a reviewer need:

| Kind | Source today | Facts carried |
|---|---|---|
| wall | paired lines, filled bodies, profiles (`candidacy.py`) | layer, thickness, length, neighbours, signals and score, declared-thickness match |
| opening | symbol hosts, gap windows (`gap_windows.py`), unexplained wall gaps | width, host wall, evidence lines, flanking walls |
| region | closed cells of the wall graph (`spaces.py`) | area, label text and parsed attributes, bounded-by walls, door graph degree |
| symbol | library templates, block names, clusters | kind guess, size, library id, evidence |
| annotation | text items | text, parsed attributes, nearest region |

Candidate ids are content hashes (geometry plus source ids), so the same drawing yields the
same ids on every run and feedback can be attached to them.

### 3.3 Explained versus unexplained
Each candidate is *settled* when deterministic evidence is strong and corroborated (accept
floor with at least two agreeing signals, or a library template at high confidence). Settled
candidates are drawn thin and grey; unsettled ones are numbered and coloured. The model is
**asked** about unsettled ones and **shown** all of them. Grey-not-delete keeps adjacent
walls readable (the owner's "remove matched symbols first", with context preserved).

### 3.4 Render
One overview image of the floor, then overlapped tiles when the floor is dense. Each image
carries: candidate numbers, a colour per kind, the scale bar, north arrow if known, and the
floor name. A compact table accompanies each image: id, kind, layer, thickness, length,
declared-thickness match, neighbours, score.

Budget: at most N images per floor (default 6 including overview), set by an option; the
overview is always sent.

### 3.5 Model call
One call per tile (parallel, concurrency capped at 6), plus one overview call for
floor-level meaning. Constrained JSON schema:

```
per candidate id:  verdict (accept | reject | unsure), class, confidence 0..1, reason (<= 12 words)
wall classes:      exterior | interior | partition | parapet | railing | site_wall | pool_edge | retaining | not_a_wall
opening classes:   door | window | passage | not_an_opening
region classes:    room(type) | balcony | terrace | void | double_height | shaft | stair | pool | garden | parking | not_a_room
floor level:       vertical_links [ {from, to, kind: stair|lift|void|double_height} ], notes
```

Prompt content: the drawing's declared widths and scale, the label attributes already parsed,
the owner's glossary of regional conventions (BDC naming, "LVL +", "UP"/"DN", "OPEN TO SKY").
Layer names are hints, never decisions.

### 3.6 Reconcile (local)
Precedence, highest first:

1. Recorded human feedback or a review file for this drawing hash.
2. Model verdict at or above its confidence floor.
3. Deterministic decision.

Local validation can veto a model verdict that geometry contradicts (a "wall" with no
thickness, an "opening" with no host, a "void" with a wall running through it). Every veto
and every override of a deterministic decision is recorded in the element's provenance with
`decided_by` and the reason, and counted in the run report.

### 3.7 Build (local)
The model's classes drive the existing builder:

* per-element height: wall by class (storey height; parapet; railing 3.5 ft; site wall; pool
  edge), already supported by `WallSeg.height_ft`;
* voids and double heights remove slabs and set space height from the stated clear height;
* site walls and pool edges are authored distinct from the building envelope;
* openings from windows without symbols are authored as `IfcWindow` as now.

## 4. Vertical connectivity

Per-floor model calls report `vertical_links` they can see. A separate, local, building-level
step then checks them against geometry across floors: a "void" on floor N must sit above a
room on floor N-1 whose label or height is double; a stair run on N must have a landing on
N+1 within tolerance; lift shafts must align. Disagreements are reported, never silently
fixed. Stacking floors into one continuous building and roofs remain out of scope (owner's
earlier decision) until floors ship.

## 5. Cost, latency and failure

* Target: within $1 to $1.5 per sheet (owner decision), under five minutes per floor. Calls are capped by an option; the run reports spend by purpose.
* The stage is **an improvement, never a dependency**. Any failure (provider, schema, render)
  leaves the deterministic decision in place and is reported, exactly as wall adjudication
  behaves now.
* Replayability: the model's raw replies are stored in the interpretation manifest next to the
  candidates, so a run can be rebuilt without calling the model.

## 6. Feedback loop

`--feedback-template` (built) lists every created element with a blank verdict and reasons;
`--feedback-summary` (built) turns filled files into precision by kind, a recall estimate
from the "missing" list, and wrong-reason histograms. This stage consumes feedback three ways:

1. **Per drawing:** corrections for a drawing hash become reconcile precedence 1 on re-run.
2. **Prompt:** recurring corrections become glossary entries and few-shot examples, selected by
   candidate kind and layer-name pattern.
3. **Calibration:** the fixed thresholds in `candidacy.py` and the escalation rules are fitted
   against the labelled set, replacing hand-set numbers.

Only the owner's feedback trains anything (standing decision: harvesting is owner-only).

## 7. Evaluation

Measured on a reference set of about five drawings with reference walls (walls, openings,
rooms) built from owner feedback, scored by `--reference-file` and `--feedback-summary`.

Proposed acceptance thresholds (to be confirmed by the owner):

| Metric | Today (proxy) | Target |
|---|---|---|
| wall precision | not measured | >= 0.90 |
| wall recall (coverage of drawn wall linework) | ~0.55 | >= 0.85 |
| window recall | low (symbols only) | >= 0.70 |
| ceiling-high walls where a void, pool edge or site wall belongs | frequent | <= 5% of such elements |
| tokens per floor | ~35-45k | <= 90k |
| time per floor | ~2 min | <= 5 min |

A change ships only if precision does not fall on any kind while recall rises on at least one.

## 8. Phasing

| Phase | Content | Status |
|---|---|---|
| P0 | feedback JSON and summary; label attributes, voids, railings; windows from wall gaps | built (branch `feat/semantic-floor-attributes`) |
| P1 | candidate ids and facts table; overview and tile renders with numbered overlays; wall classes from the model for *all* unsettled walls, no cap; site wall, pool edge, parapet classes drive heights | next |
| P2 | opening and region classes; unexplained gaps; room typing | after P1 measurement |
| P3 | vertical connectivity check; double-height and stair-landing consistency | after P2 |
| P4 | feedback-driven prompt and threshold calibration | continuous |

## 9. Risks

* **Spatial reading from images is imperfect.** Mitigation: ids not coordinates; tiles with
  overlap; local validation; unsure verdicts fall back to deterministic.
* **Cost on dense sheets** (South Face has 16 regions, three of them floors). Mitigation: only
  confirmed floors are processed; image and call caps; settled candidates not asked about.
* **Confidential drawings are sent to the provider** as images. Already true for layer vision
  and wall adjudication; the owner should confirm the provider terms cover the larger volume.
* **Provider drift** changes verdicts between runs. Mitigation: stored replies, replay,
  feedback-based regression set.
* **Overfitting to four drawings.** Mitigation: the reference set grows from feedback; no
  threshold is accepted on fewer than five drawings.

## 10. Owner decisions (2026-10-16)

1. **Cost ceiling:** $1 to $1.5 per floor-plan *sheet* (all its floors together), replacing the $0.5 per floor assumed in section 5.
2. **Census:** `IfcRailing` and `IfcWall` with `PredefinedType=PARAPET` are accepted, provided the IFC stays schema-valid BIM.
3. **Reference set (seven drawings, all in `input-floorplans/PLANS`):** MB Panwar, SANJANA Suresh, Aiims Road, M.r Premg Agarwal Baglow, MR Rajeev ji, Abhishek ji, SANJANA Giriraj. The owner reviews each 3D output and gives element feedback.
4. **Model:** compare Claude Haiku 5.5 at effort `high` with Claude Sonnet 5.5 at effort `medium` on one drawing (RAJEEV) before choosing; report decision agreement, consistency, tokens, cost and time.
5. **Feedback is internal.** It tunes archiAgent only; customers never see the feedback file, its summary, or any per-element verdict UI.

## 11. What changes in the code

| Area | Change |
|---|---|
| `archiagent/geometry/candidacy.py` | expose candidates with ids and fact rows; settled flag |
| new `archiagent/classify/element_stage.py` | render, tile, call, parse, reconcile |
| `archiagent/classify/thumbnails.py` | numbered-overlay renders (extends `render_candidate`) |
| `archiagent/pipeline.py` | run the stage between candidacy and junctions; apply classes |
| `archiagent/ifc/author.py`, `inspect.py` | classes to heights and types; census for parapet and railing |
| `archiagent/interpretation.py` | store model replies and decisions for replay |
| `archiagent/feedback.py` | read feedback as reconcile precedence 1 |
