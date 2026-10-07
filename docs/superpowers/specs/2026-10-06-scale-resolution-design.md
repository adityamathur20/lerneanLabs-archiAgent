# Scale resolution: extracted dimensions, one asserted wall length, and a picker

Date: 2026-10-06
Status: design, pending implementation plan
Related: `2026-10-06-declared-wall-metrics-design.md` (supersedes its §3 and §6)

## Problem

`--units-per-foot` asks the user to describe the file's internals: how many of a
DXF's bare coordinate numbers make one real foot. Nobody opens a DXF to count
coordinate units, and the number is counter-intuitive — metres is `0.3048`, not
`3.28`, so inverting it gives a silent 10.7x error.

Meanwhile the drawing usually already states its own dimensions, and the tool can
already read them — but not on the DXF path.

## What already exists

| Mechanism | Reads | Derives scale unaided? |
|---|---|---|
| `extract_dimensions(ps)` ([dimensions.py:82](../../../archiagent/scale/dimensions.py#L82)) + `resolve_scale` | every text in `ps.texts` that parses as feet-inches, then matches them to candidate wall run lengths by consensus | **yes** |
| `measurements_from_source(ps)` ([verify.py:80](../../../archiagent/scale/verify.py#L80)) | native `DIMENSION` entities; `load_dxf` stores each one's measured span **and** its text | **yes** — span ÷ text |
| `associate_dimension_text(ps, upf)` | loose text near geometry | no, needs scale already |

`resolve_scale` is reached only from `extract_from_primitives`
([pipeline.py:215](../../../archiagent/pipeline.py#L215)) — the PDF path.
`extract_from_dxf` takes `units_per_foot` and never derives it. So on a DXF, all
three mechanisms are either unused or verification-only.

### Native DIMENSION counts in the corpus

| Drawing | DIMENSION | Note |
|---|---|---|
| Aiims Road 3BHK Flats | 417 | |
| Manoj JI Ladnu plumbing | 141 | |
| M.r Premg Agarwal | 37 | |
| Floor Plan.dxf | 37 | header units known wrong |
| MR RAJEEV JI TWANI JI | 14 | |
| MB Panwar JI Revision 2 | 5 | |
| VINAYAK APARTMENTS | 4 | |
| SANJANA Giriraj Ji | 4 | |
| SANJANA SURESH JI | 0 | 135 MTEXT available to `extract_dimensions` |
| PLAN.dxf | 0 | 159 MTEXT; site plan, header units known wrong |

### Generated text is better evidence than an override

`load_dxf` already records `dimension_text_origin` as `"explicit-override"` when
a DIMENSION carries literal text, else `"generated"`
([dxf_vector.py:128](../../../archiagent/ingest/dxf_vector.py#L128)), and keeps
`dimstyle_dimlfac` as a hint with the comment *"Formatting factors are hints,
never independent physical measurements: default rendered text derives from
geometry."*

For **calibration** this inverts the usual intuition:

- **Generated** text is the CAD rendering the measured span into the drafter's
  own unit system. The ratio of span to text therefore reveals the units
  reliably, because a machine produced it. Prefer these.
- **Explicit override** text is a number a human typed, which need not match the
  geometry at all — overriding dimension text to make a drawing "read right" is
  common practice. Treat as suspect for calibration.
- `dimlfac != 1` means the drafter deliberately decoupled displayed from drawn
  length. Exclude those dimensions from calibration and say so.

## Goals

1. Extract the drawing's own dimensions and use them as the default scale source
   on the DXF path.
2. Require exactly **one** asserted wall length from the user. Invite a second;
   never compel it.
3. When the user's assertion and the extracted dimensions disagree, **the user
   wins** — loudly, never silently.
4. Reach a *verified* scale from one user assertion by pairing it with an
   extracted dimension as the second independent span.
5. Stop expecting anyone to know `--units-per-foot`, without removing it.
6. Let the assertion be picked in a viewer rather than typed as coordinates.

## Non-goals

- Removing `units_per_foot` as a concept. It is also a field of the reviewed
  regions JSON (`PlanRegion.units_per_foot`,
  [semantic.py:82](../../../archiagent/semantic.py#L82)) for drawings holding
  several plans at different scales. The flag is demoted, not deleted.
- Changing the PDF scale path or `infer_associated_scale`.
- Trusting `$INSUNITS` as a scale source. It becomes a cross-check only.

## 1. The scale ladder

Resolved once per region, in this order:

| Rung | Source | User effort | Can verify |
|---|---|---|---|
| 1 | a reviewed region's `units_per_foot` | reviewed data | no |
| 2 | **Asserted wall length(s)** — mandatory, at least one | pick 1 wall, 2 invited | with 2 spans, or 1 + a corroborating extracted dimension |
| 3 | **Extracted dimensions** — native `DIMENSION` first, then text consensus | none | with ≥2 agreeing |
| 4 | `$INSUNITS` header | none | never — cross-check only |

Rung 1 stays first so existing invocations and reviewed regions keep working
unchanged. Rung 2 is where the user actually interacts. Rung 3 runs **always**,
whether or not it is the chosen source, because it is free and it is the
cross-check for rungs 1, 2 and 4.

### The assertion requirement

For a DXF, scale resolution **fails with a configuration error** unless one of
these is true:

- `--units-per-foot` was given (expert override), or
- the selected region carries `units_per_foot`, or
- at least one asserted wall length was supplied, or
- `--trust-extracted-scale` was given, accepting rung 3 alone without an assertion.

A bare run with none of these no longer silently falls back to the header. That
silent fallback is what produced wrong models from `PLAN.dxf` and
`Floor Plan.dxf`, whose headers disagree with their geometry.

The error names what to do, and reports what rung 3 found so the user can see
the tool's own estimate before asserting anything:

```
error: SANJANA SURESH JI.dxf: scale is not established.
  extracted dimensions suggest 12 units/foot (from 6 agreeing texts, unverified)
  the file header declares 1 units/foot
  Supply one wall you can identify:
    --scale-from-wall X1 Y1 X2 Y2 LENGTH
  or pick it in a viewer:
    --measure-workbench
  or accept the extracted estimate:
    --trust-extracted-scale
```

### Inviting the second span without compelling it

With exactly one asserted length and no corroboration, the run proceeds and
prints:

```
note: scale 12 units/foot set from one asserted length. A second independent
      span would let it be verified; supply --scale-from-wall again, or pick a
      second wall in the workbench.
```

With one asserted length **and** an extracted dimension agreeing within
tolerance, the two are independent spans and scale becomes verifiable by the
existing `scale_verified` rule, with no second user input at all. This is the
point of running rung 3 unconditionally.

## 2. Disagreement: the user overrides, loudly

When an asserted length and the extracted dimensions imply different scales
beyond `tolerance_in` (default 2in of residual), the asserted length wins —
the user is looking at the drawing and the extractor is pattern-matching text.

Never silently. Emit a `warn` Issue `asserted_scale_overrides_extracted`:

```
asserted wall length implies 12 units/foot; extracted dimensions imply 25.4
(from 14 agreeing texts). Using 12 as asserted. If the drawing is in
millimetres, re-check the length you entered.
```

The ratio is the diagnostic: 25.4/12 ≈ 2.12 is not a unit factor and suggests a
genuine disagreement, whereas a clean 25.4, 12, 304.8 or 1/12 ratio almost
always means one side read the wrong unit. The Issue names the ratio and, when
it is within 1% of a known unit factor, says which.

Two or more asserted lengths disagreeing **with each other** remains an error,
not an override — there is no basis to choose between two human assertions.

## 3. What the tool extracts, precisely

New function in `scale/resolve.py`:

```python
def extracted_scale(ps, tolerance_in=2.0) -> ExtractedScale | None
```

returning

```python
@dataclass(frozen=True)
class ExtractedScale:
    units_per_foot: float
    spans: tuple[tuple[tuple[float, float], tuple[float, float]], ...]  # source coords
    support: int          # how many dimensions agreed
    basis: str            # "native-dimension" | "text-consensus"
    rejected: tuple[tuple[str, str], ...]   # (dimension id, why) for review
```

Order of attempts:

1. **Native dimensions.** From `measurements_from_source(ps)`, keep those whose
   `expected_ft` parsed, whose `dimension_text_origin` is `generated`, and whose
   `dimstyle_dimlfac` is 1 (or absent). Ratio per dimension is
   `span / expected_ft`. Take the median; require at least two agreeing within
   `tolerance_in` after scaling. `basis="native-dimension"`.
2. **Explicit-override dimensions**, same arithmetic, only when step 1 yielded
   nothing. Each one is recorded in `rejected` if it disagrees with the median,
   since an override that contradicts its own geometry is worth seeing.
3. **Room-label pairs.** A label like `12'-1½"×12'-10"` states a room's two
   side lengths. `extract_dimensions` already parses each half, because
   `parse_dimension` strips a trailing `X`
   ([dimensions.py:56](../../../archiagent/scale/dimensions.py#L56)). Rather
   than matching one text to one run, match the **pair** to the bounding box of
   the enclosure the label sits inside: a room whose label says 12'-1½" × 12'-10"
   has an interior whose two sides are in that ratio and whose absolute size
   fixes the scale. Two independent numbers from one label, mutually
   constraining, make this far stronger than a lone text matched to a lone run.
   `basis="room-label"`.

   This is the only extraction source that benefits from the declared wall
   thickness: the label describes the **clear interior** of the room, while the
   enclosure found from centrelines includes half a wall on each side. Knowing
   the thickness converts between the two instead of leaving a one-wall-width
   error, which on a 12ft room at 9in walls is 6% — enough to matter.
   Without a declared thickness this source is still usable but is recorded with
   a wider tolerance.
4. **Text consensus.** `resolve_scale(extract_dimensions(ps), candidate_runs(ps, layers))`,
   the mechanism the PDF path already uses. The weakest source, because it
   matches any dimension-shaped text to any candidate run. `basis="text-consensus"`.

Returns `None` when nothing agrees. `rejected` always travels so the report can
explain why a drawing full of dimensions still produced no scale.

`spans` is what makes a single user assertion verifiable: an extracted span is
an independent witness and is handed to `verify_dimensions` alongside the
asserted one.

## 4. CLI surface

```
--scale-from-wall X1 Y1 X2 Y2 LENGTH   at least one required for DXF; repeatable
--scale-from-entity HANDLE LENGTH      the same assertion, tied to a DXF entity handle;
                                       what the picker emits
--measure-workbench                    write the picker and exit
--trust-extracted-scale                accept the drawing's own dimensions without an assertion
--scale-tolerance-in FLOAT             agreement tolerance, default 2.0
```

### `--units-per-foot` is removed from the CLI

The flag is **deleted**, not demoted. It was rung 1 — beating every other
source, carrying no provenance and never cross-checked — which is exactly how
`PLAN.dxf` and `Floor Plan.dxf` produced confidently wrong models. A bare
number that silently wins is the failure mode, not a convenience.

What remains:

- **The Python parameter** `extract_from_dxf(units_per_foot=...)` stays. It is
  how tests and library callers inject a known scale, and it is not a user
  surface.
- **`PlanRegion.units_per_foot` stays**, because a reviewed regions file is the
  established way to give different plans in one DXF different scales. It is
  reviewed data with an author, not an unattributed flag.

Anyone who genuinely knows the scale states it as an assertion about a span they
can point at, which is self-documenting, attributable and cross-checked against
the drawing's own dimensions. That is strictly better provenance than `12`.

Migration: the two corpus drawings that today need `--units-per-foot 12` move to
`--trust-extracted-scale` or one picked wall.

### What the GUI exposes, and what it does not

The flags above are the machine surface. The user does not type them.

- **`units-per-foot` appears nowhere a user can see**, and the CLI flag is gone
  entirely. Scale is always computed internally, from the extracted dimensions
  or from the wall the user selects. Asking a person how many drawing units make
  a foot is asking them to describe the file's internals.
- **`--trust-extracted-scale` is a tick-box**, labelled in the user's terms —
  *"Use the dimensions already in this drawing"* — showing the scale that was
  extracted and how many dimensions agreed, for example *"12 units/foot, from 14
  agreeing dimensions"*. Ticking it **satisfies the scale requirement outright**:
  no wall measurement is then asked for.
- The tick-box is **pre-ticked and the measure step hidden** when extraction
  found a confident scale, so a well-dimensioned drawing needs no interaction.
  It is **unticked and disabled** when extraction found nothing, with the measure
  step shown and required.
- Unticking it reveals the measure step. The two are alternatives, and at least
  one must be satisfied before the run can proceed.

## 5. The picker

Promoted from "deferred" to the primary way rung 2 gets its input. It is **not**
a new DXF viewer: `review_workbench.py` is already a self-contained offline plan
viewer needing no CDN or service, and already has the parts.

| Needed | Already present |
|---|---|
| render the drawing | SVG `#source` group of clickable shapes |
| pan and zoom | `changeView`, fit-to-bounds |
| click points | the `trace` tool collects clicked corners |
| live coordinates | `#position` readout |
| export JSON | `download()` and the export buttons |
| attribution | reviewer field, draft/reviewed status |

### Revised 2026-10-07: the picker is `three-dxf-viewer`, not the SVG workbench

The SVG analysis below stands on its measurements and is kept for the record,
but its conclusion is **reversed**. It compared the wrong thing: it asked
whether SVG could render a drawing fast enough, when the requirement is that the
user **select a wall** and have the span taken from that entity's own geometry.

Selecting an entity, rather than clicking two points, changes the quality of the
input:

- **Click precision stops mattering.** The span comes from the entity's
  vertices, not from where a cursor landed.
- **The assertion becomes durable.** Recording the DXF **handle** of the chosen
  entity makes the measurement re-checkable on a later run and survives
  re-ingestion, where a bare coordinate pair does not.
- **Snapping is free.** The library stores vertices for exactly this.

`ieskudero/three-dxf-viewer` provides it: **MIT**, dependencies `dxf@5.3.0`
(MIT), `three` and `rtf.js`, with `Select`, `Hover`, `Snap` and `Merger`
utilities and layer show/hide. The licence chain is cleaner than both
alternatives — no MPL file-level copyleft as in `vagran/dxf-viewer`, and no
DWG parser pulling GPL-3 LibreDWG in as `mlightcad/cad-viewer` risks. It has no
measure tool; that is what gets built on top.

It also removes the payload problem rather than mitigating it: the browser
parses the `.dxf` directly, so no SVG is generated, transferred or held in a
DOM, and WebGL buffers replace 17k–60k nodes.

**Gate before committing to this — a half-day spike.** The JS parser and
`ezdxf` must agree on entity identity, or the selected wall cannot be tied back
to anything. `load_dxf` uses the DXF handle as the source id for top-level
entities ([dxf_vector.py:231](../../../archiagent/ingest/dxf_vector.py#L231))
and the `dxf` parser exposes handles, so **handles are the join key**. Prove on
three corpus drawings that the handle of a selected `LINE`/`LWPOLYLINE` matches
a `SourceEntity.id`. If it does, build this. If it does not, fall back to
matching by coordinates and accept the weaker provenance.

**Known risks.** `three-dxf-viewer` last published 1.0.44 on 2025-09-04 after 38
releases since 2023 — roughly a year quiet. MIT means it can be vendored and
patched, which is the mitigation, and the vendored bundle is committed so a run
never depends on a registry. Adding a JS build step to a Python project is a
real cost; bundling to a single inlined asset preserves the
no-CDN-no-service property that matters for confidential drawings.

### What the measurement is, and is not

This input is a **ratio and nothing else**:

```
units_per_foot = endpoint-to-endpoint span in source units / stated length in feet
```

The selected entity carries no architectural meaning here. A wall line, a plot
boundary, a car outline and a dimension leader all calibrate equally well. The
user selects any entity, states how long that entity is, and the tool divides.

An earlier draft of this section asked the UI to distinguish a wall's `face`
from its `centerline`, and worried that a polyline may span several rooms.
**Both were wrong.** `Measurement.basis` matters when *verifying a wall* against
an expected dimension, where thickness changes the comparison; it has no bearing
on converting units. And an entity spanning several rooms is not a problem,
because the user is stating the length of the highlighted entity, not of a room.

**Three real constraints remain**, none of which need the entity to be a wall:

1. **A multi-vertex polyline has two lengths** — endpoint-to-endpoint, and the
   path along its segments. They are equal only for a straight run. The
   definition is endpoint-to-endpoint, and the UI makes that self-evident by
   **drawing the span it is measuring**: a straight overlay line between the two
   endpoints, as AutoCAD and Blender measure tools do. Showing it beats
   documenting it.
2. **Closed and circular entities are degenerate.** A closed polyline or a
   circle has an endpoint-to-endpoint span of about zero, since the first and
   last vertices coincide. Selecting one for measurement is refused with that
   reason, rather than producing a division by zero or an absurd scale.
3. **The span is measured in XY.** `load_dxf` projects to the drawing plane and
   warns on non-zero elevation, so a browser measuring a 3D distance on an
   entity with Z extent would disagree with the pipeline. The picker measures
   the XY projection.

The implied scale is shown live as the length is typed, next to whatever the
drawing's own dimensions imply, so a mistyped length is visible at the moment it
is cheapest to fix.

### Superseded: measured case for keeping SVG

The concern that generating SVG is slow was tested rather than assumed. On this
corpus, building the full SVG path data costs **0.11–0.24 s**. It is not the
bottleneck. The cost is `load_dxf` itself — **4.8 s** (MR RAJEEV, 17k
primitives), **10.9 s** (Aiims Road, 53k), **5.6 s** (Floor Plan, 60k) — and the
pipeline pays that anyway, so the picker adds almost nothing.

The real risk is **DOM size**, not generation: a full payload is 4.8–9.2 MB and
17k–60k `<path>` nodes, which makes pan and zoom sluggish.

That is solved by sending only what picking a wall needs — no fills, nothing
shorter than 2 ft:

| Drawing | all primitives | picker payload | share |
|---|---|---|---|
| MR RAJEEV JI TWANI JI | 17,017 | 1,073 | 6.3% |
| Aiims Road 3BHK Flats | 53,310 | 6,544 | 12.3% |
| Floor Plan.dxf | 59,594 | 25,938 | 43.5% |

1k–26k nodes for a task with no continuous animation — the user pans, zooms and
clicks twice. SVG handles that.

**Alternatives considered.** `vagran/dxf-viewer` (WebGL via three.js, built
precisely for huge files) is **MPL-2.0**: usable commercially, but it is
file-level copyleft, so modifications to its own files must be published.
`mlightcad/cad-viewer` is reported MIT and ships pan/zoom/measure/annotate out
of the box, which is the closest off-the-shelf fit — but its DWG support implies
a parser whose transitive licence must be checked before adoption, since
LibreDWG is GPL-3 and this project already evaluated and rejected LibreDWG. The
`dxf` npm parser is MIT but is a parser only, with no viewer or measure tool.

~~**Decision: keep the self-contained HTML/SVG workbench, with a filtered
payload.**~~ **Reversed above.** The measurements were right and the conclusion
was wrong: rendering speed was never the binding requirement. Selecting a wall
entity — which SVG-of-filtered-primitives cannot do well, because the filtering
discards the entity identity the assertion needs — is. The licence review that
this paragraph treated as a cost came back clean for `three-dxf-viewer` (MIT
throughout), and bundling preserves the no-CDN property after all.

The work:

1. **A source-coordinate payload.** The existing workbench renders region-local
   **model feet** built from a finished `BuildingModel`. Calibration happens
   before any of that exists, and `load_measurements` wants **source
   coordinates**. So a new `write_measure_workbench(ps, output_path)` builds the
   payload straight from the `PrimitiveSet` in raw units. A viewer needs no
   scale to render — it fits the drawing to the viewport in source units; scale
   is only needed to *label* lengths. The SVG, interaction and export code is
   shared; only the payload source and coordinate space differ. **This is the
   real work item.**
2. **A two-click Measure tool**, instead of the polygon `trace`.
3. **A length field plus a unit select** (`mm`, `cm`, `m`, `ft`, `in`, or a
   feet-inch string), validated against the same grammar
   `parse_explicit_length` accepts, so the GUI cannot produce a value the CLI
   would reject.
4. **The second-span invitation in the UI**: after the first wall is measured,
   show the implied scale and what the tool's own extracted estimate was, and
   offer "measure another wall to verify". Agreement or disagreement is visible
   at the moment of entry, which is when it is cheapest to fix.
5. **Export** `{"measurements": [...]}` in source coordinates, consumed by the
   existing `--measurements` path with no new file format.

Because the export is a durable file, batch and CI runs consume the stored
measurements rather than needing a human each time. The artefact is the
measurement, with its reviewer and its asserted length — better provenance than
a bare `12`.

## 6. Error handling

| Condition | Result |
|---|---|
| DXF, no override, no region scale, no assertion, no `--trust-extracted-scale` | configuration error, exit, reporting the extracted estimate and the header |
| `--trust-extracted-scale` but nothing extractable | configuration error, exit, listing `rejected` reasons |
| one asserted length | proceeds; note that a second span would allow verification |
| one assertion + agreeing extracted dimension | proceeds, scale verifiable |
| assertion disagrees with extracted beyond tolerance | assertion wins, `warn` Issue naming both and their ratio |
| two assertions disagreeing with each other | error, exit |
| `--units-per-foot` with an assertion | override wins, `info` Issue that the assertion set no scale but is still verified against |
| header disagrees with the chosen scale | existing `declared_units_overridden` warning, unchanged |
| `--measure-workbench` on a PDF | configuration error — the picker reads DXF source geometry |

## 7. Testing

- `extracted_scale` on synthetic native dimensions: two agreeing generated texts
  at 120 units / `10'-0"` give 12.0, `support == 2`, `basis == "native-dimension"`
- an explicit-override dimension is not used while a generated one exists
- `dimlfac = 25.4` excludes that dimension and records it in `rejected`
- one dimension alone yields `None` (no agreement possible)
- text-consensus fallback fires only when no native dimension qualifies
- a DXF run with no scale input at all exits with the configuration error, and
  the message contains both the extracted estimate and the header value
- `--trust-extracted-scale` accepts rung 3 and proceeds
- one assertion: proceeds, `scale_verified` false, note printed
- one assertion plus an agreeing extracted span: `scale_verified` true
- assertion 12 versus extracted 25.4: scale is 12, `warn` issued naming ratio
  2.12 and not claiming a unit factor
- assertion 12 versus extracted 304.8: `warn` names the 25.4 ratio as a likely
  mm/inch unit error
- two assertions disagreeing: exits
- `PLAN.dxf`-shaped synthetic geometry with a wrong header: one assertion
  produces the correct scale and the header warning fires
- regression pins: `infer_associated_scale` and `extract_from_primitives`
  behaviour unchanged; a reviewed region carrying `units_per_foot` still works
  with no assertion

## 8. Risks

- **A mandatory assertion is friction on good drawings.** Aiims Road has 417
  dimensions and needs no help. `--trust-extracted-scale` is the release valve,
  and the error message shows the extracted estimate so accepting it is one
  flag away rather than a guess.
- **One assertion cannot be checked by itself.** A typo — "100 ft" for a wall
  that is 10 ft — produces a silently 10x wrong building, and every
  foot-denominated threshold in candidacy goes with it. Running rung 3
  unconditionally is the mitigation: in most drawings the extracted dimensions
  will contradict the typo and the warning will fire. Where a drawing has no
  extractable dimensions at all, nothing can catch it, and the note inviting a
  second span is the only defence. This is the sharpest remaining risk and the
  reason the UI should show the implied scale as the length is typed.
- **The user override can enshrine a mistake.** By decision, an assertion beats
  the extractor. The warning, the ratio diagnostic and the named unit factor
  exist so an override is visible in the report rather than buried.
- **Text consensus is weaker than it looks.** `extract_dimensions` keeps any
  text parsing as feet-inches, which in this corpus includes room labels like
  `12'-1½"×12'-10"` that describe a room, not the span they sit on. That is why
  it is the last extraction attempt and why `basis` is recorded.
