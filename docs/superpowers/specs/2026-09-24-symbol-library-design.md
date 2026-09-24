# Reusable symbol library with scale- and mirror-tolerant matching

Date: 2026-09-24
Status: design, implementation in progress
Supersedes: `2026-09-19-fixture-library-design.md`
Related: `2026-09-18-geometry-first-wall-candidacy-design.md`

## Problem

Fixtures, furniture and site symbols are sometimes built into the IFC as walls
or columns. On `MR RAJEEV JI TWANI JI.dxf` a shower-head glyph produced five
phantom `IfcWallStandardCase` walls, one per instance.

`classify/templates.match_templates` already matches this kind of geometry, and
already works over `ps.primitives` rather than block definitions — so exploded
linework and block contents look identical to it. Two limits stop it being
usable here:

- its seed must name entity IDs **in the current drawing**, so a drawing with no
  already-identified example of a shape cannot be matched at all
- scaling is refused outright (`"template scaling is unsupported"`), so only
  instances at the seed's exact size are found

This supersedes the earlier fixture-library spec, which assumed the work was
block-definition matching with loose linework deferred. That framing was wrong:
DXF ingestion expands every INSERT into primitives, so the block/loose
distinction is largely artificial.

## Corpus evidence

A survey of all 10 drawings in `input-floorplans/dxf` grounds the scope.

| Kind | Instances | Files |
|---|---|---|
| unnamed/opaque block names | 1292 | 9/10 |
| anonymous `*U` blocks (dimension/hatch artifacts) | 208 | 3/10 |
| plumbing/sanitary | 78 | 5/10 |
| vegetation | 74 | 3/10 |
| furniture | 68 | 4/10 |
| vehicle | 24 | 4/10 |
| door | 15 | 2/10 |

Findings that shape the design:

- **~73% of block instances carry names that mean nothing** (`dasd`, `FGNFF`,
  `A$C6c8b0541`). Name/keyword recognition structurally cannot cover this
  corpus. Some real names are also non-English (`Chaukhat` = door frame).
- **The same shape recurs across unrelated drawings under meaningless names.**
  `dasd` (521 segments, 7 closed loops, aspect 0.99) appears 39 times in one
  drawing and 72 in another, geometrically identical. `plant9`, `car1` and
  `TREE-3` likewise.
- **Two drawings have no block symbols at all.** `PLAN.dxf` has zero INSERTs;
  `Floor Plan.dxf` has 17 across 51,786 entities and zero text entities. Any
  approach restricted to block definitions ignores these entirely.

## Scope

In the library: `plumbing` (wc, washbasin, sink, shower_head, bathtub, tap,
urinal), `furniture` (sofa, bed, chair, dining table, tv unit, dressing table),
`vehicle` (car), `vegetation` (tree, plant). `vegetation` is a new entry in
`templates.KINDS`.

Not in the library: doors, windows, stairs, columns, beams. Not because they are
rare, but because their size legitimately varies per building — a 2'6" door is
not a scaled copy of a 4' door. Rigid-shape matching is the wrong tool for them,
and `classify/rules.py` keyword roles plus `wall_adjudicator.py` vision review
already cover them. Columns and beams remain served by `match_templates`
unscaled.

## Non-goals

- Non-uniform stretching. A stretched rectangle or circle matches too much.
- Harvesting new templates from loose-line clusters with no INSERT to delimit
  them. Matching an approved template *against* loose linework already works,
  because matching runs over `ps.primitives`; only discovery is deferred.
- Authoring these symbols as 3D IFC objects.

## 1. Components

- **`archiagent/data/symbol_library.json`** — committed, versioned, reviewed
  data. Declared as package data so installed builds include it.
- **`archiagent/classify/shapes.py`** — shared geometry internals, moved
  verbatim out of `templates.py`: the `Part` record, `parts_from(ps, upf)` which
  builds the candidate pool from a `PrimitiveSet`, and `measure(parts)` which
  reduces matched parts to centroid/width/depth/orientation/boundary. One
  implementation of "what is a comparable shape", used by both matchers.
- **`archiagent/classify/library_templates.py`** —
  `match_library_templates(ps, units_per_foot, templates)`, returning
  `SymbolInstance`s, the same type `match_templates` produces, so no downstream
  code needs to know which matcher found a symbol.

`match_templates` keeps its signature, its fixed-inch tolerance and its
exact-length anchor search, unchanged. The two matchers are siblings sharing a
toolbox; neither calls the other.

## 2. Library file

```json
{
  "schema_version": 1,
  "templates": [{
    "id": "wc-8f2a1c4e",
    "kind": "plumbing",
    "subtype": "wc",
    "label": "western WC, tank at rear",
    "geometry": [{"category": "closed", "points": [[-0.5,-0.5], "..."], "holes": []}],
    "size_ft": {"min": 1.2, "max": 2.5},
    "mirror_allowed": true,
    "rotations_deg": [0, 90, 180, 270],
    "provenance": [{"source_sha256": "…", "block_name_sha256": "…", "instances": 24}],
    "status": "reviewed",
    "reviewed_by": "user"
  }]
}
```

- `geometry` is **normalised**: centred on the bounding-box centre and scaled so
  the larger bounding-box side is exactly 1. Each part carries the same
  `category` (`fill`/`curve`/`closed`/`path`) and hole rings that `shapes.Part`
  derives a signature from, so library parts and drawing parts compare directly.
  Orientation is deliberately *not* normalised: the matcher searches rotations
  anyway, and the principal axis of a near-square glyph is unstable enough that
  aligning it would make a harvested template depend on drafting noise.
- `size_ft` bounds the real-world **larger side**. Because normalisation makes
  that side exactly 1, the derived scale factor *is* that size in feet.
- `mirror_allowed` is set by the reviewer, not inferred. Most fixtures are
  symmetric and a mirror pass is wasted search; asymmetric ones need it. An
  automatic symmetry test on hand-drafted geometry is unreliable, and a wrong
  guess silently halves recall or doubles cost.
- Provenance holds hashes only — no client file names, no raw block names. This
  file is committed and reused across projects.

Malformed records raise `ValueError` at load, following `regions.load_regions`.
Only `status: "reviewed"` records are used.

## 3. Matching

1. **Anchor without a length.** `match_templates` selects its anchor by matching
   signature *and* length. A library template is scale-free, so length is not
   yet comparable: the anchor is instead the template part with the fewest
   same-key candidates in the drawing, preferring a part with no holes because a
   holed part's length sums its rings and ring closure differs between drawings.
2. **Scale is derived, not searched.** For each same-signature candidate,
   `scale = candidate.length / anchor_normalised.length`. Since normalisation
   fixes the larger side at 1, that scale is the instance's real larger side in
   feet, and is checked against `size_ft` immediately. This is the cost control:
   it runs before any geometric comparison.
3. **Full-template trial.** For each surviving (candidate, scale), each rotation
   and each mirror branch: scale the whole template, mirror, rotate, translate
   the anchor onto the candidate. Every remaining template part must find a
   drawing part with an identical signature within Hausdorff tolerance. One
   unmatched part fails the trial — partial symbols are never created, matching
   `match_templates`' existing rule.
4. **Tolerance scales with size**: `TOLERANCE_FRACTION * scale`, starting at
   0.03. A 6-inch tap and a 6-foot bathtub need proportionally different slack,
   which is precisely why this cannot be a branch inside `match_templates` and
   its fixed inch tolerance.

### Vertex counts describe tessellation, not shape

The design originally assumed the existing signature — category, segment count,
hole vertex counts — was scale-invariant and could key the prefilter. Corpus
testing disproved it. Matching the same block across two drawings found 22 of 26
signatures agreeing and the rest differing as `('fill', 182, ())` against
`('fill', 181, ())`, **with identical length to four decimals**: the shapes are
the same and only the discretisation differs. Hatch boundaries and tessellated
curves are flattened at an absolute chord tolerance, so their vertex counts vary
between files at the same size and grow with size. Only authored polyline
vertices are stable.

The library matcher therefore groups candidates by a derived key: category plus
hole *count* (topological, stable) for `fill` and `curve`, plus the exact
segment count only for authored `closed` and `path` parts. `match_templates`
keeps the original signature untouched — it compares within one drawing, where
tessellation is identical, so the stricter key costs it nothing.

A length prefilter was tried alongside this and removed: a holed part's length
sums its rings, and inconsistent ring closure between drawings made it reject
parts whose Hausdorff distance was well inside tolerance. Hausdorff distance,
bounded by centroid proximity, is the discriminator.
5. **Partial entities are never claimed.** As in `match_templates`, a match that
   would claim some but not all primitives of a source entity is discarded.
6. **Conflicts go to review.** If two templates claim overlapping source
   entities, neither survives.
7. **Budget.** `max_candidates` per template; exceeding it raises `ValueError`
   naming the template, so a pathological drawing fails loudly.

## 4. Harvesting

`archiagent symbols harvest <dxf> …`, `approve`, `reject`. Two detection paths,
both producing candidates for human review; neither approves anything.

1. **Keyword path.** INSERTs whose block name resolves to a known role. Precise
   where names are informative.
2. **Frequency path.** Every distinct block definition across the supplied
   drawings is flattened, normalised and signatured; any signature group with at
   least 8 total instances, or present in at least 2 drawings, becomes a
   candidate **regardless of name**. This is what surfaces `dasd` and `plant9`,
   which the keyword path cannot see.

Both write a candidate record plus a preview PNG under `.archiagent-cache/symbols/`
— never the repo, since previews render client drawings. `approve` moves a
candidate into the library with `status: "reviewed"`; the reviewer supplies
kind, subtype, label, size range and `mirror_allowed`.

Harvesting reads block definitions only. Discovering candidates from loose-line
clusters, where nothing delimits one symbol from the next, remains deferred.

## 5. Precedence and pipeline integration

One insertion point in `pipeline._assemble`, between `recognize_symbols` and the
existing template block. That placement alone produces the precedence chain,
because the existing code already drops symbols whose `source_ids` intersect the
reviewed templates' claims:

> `recognize_symbols` < library matches < `review["templates"]` < `review["symbols"]`

A human decision about this drawing beats a generic library shape; an explicit
instance record beats everything.

Unlike `match_templates`, which fires only when `review["templates"]` is set,
library matching runs by default — it exists for drawings where nobody has set
up review records. `--symbol-library PATH` overrides the bundled file;
`--no-symbol-library` disables it.

### The wall-exclusion invariant

`_assemble` excludes symbol geometry from wall detection with:

```python
wall_ps = exclude_symbol_geometry(ps, tuple(s for s in symbols if not yields_to_walls(s)))
```

and `candidacy.yields_to_walls` returns True only when
`symbol.evidence == "layer-and-geometry"`. Library matches therefore carry
`evidence="symbol-library"`, and the guarantee that a matched fixture never
becomes a wall rests entirely on that string. `reconcile_symbols` skips them for
the same reason. This is a load-bearing invariant, not a label: a future edit
setting the evidence to `"layer-and-geometry"` would silently restore the
phantom-wall bug. A test pins it.

`ifc/author.py` authors 3D objects only for `column` and `beam`, so these
symbols are retained in `SymbolsJSON` with subtype and template id for a later
interior-placement step, and are never built as geometry.

## 6. Error handling

| Condition | Result |
|---|---|
| bundled library missing or empty | matching skipped, info Issue; never fails a build |
| `--symbol-library` path missing | configuration error, exit |
| malformed library record | `ValueError` at load |
| instance outside `size_ft` | not matched, info Issue `symbol_size_out_of_range` |
| candidate budget exceeded | `ValueError` naming the template |

## 7. Testing

- `evidence="symbol-library"` ⇒ `yields_to_walls()` is False ⇒ geometry excluded
  from wall input
- scale and mirror invariance: a template matches its own shape from 0.1× to
  50× and mirrored; a different shape with the same signature does not
- `size_ft` boundaries exactly at min and max
- partial-entity and conflicting-claim rules hold, as for `match_templates`
- precedence: library beats `recognize_symbols` including a `column`-role layer;
  `review["templates"]` beats library; `review["symbols"]` beats all
- `MR RAJEEV JI TWANI JI.dxf`: five `plumbing/shower_head` symbols, zero walls
  and zero columns at those locations
- a template harvested from a block in one drawing matches exploded linework of
  the same shape in another
- provenance contains no client file names or raw block names

## Measured on the corpus

Verified against real drawings, not only unit fixtures.

- **In-drawing.** Seeding from one `Toilet - top` instance in
  `SANJANA SURESH JI.dxf` (23 primitives) matched all 6 instances at 2.007 ft,
  detecting 0° and 180° rotations, with no false positives.
- **Cross-drawing, the capability this spec exists for.** Seeding from one
  `dasd` instance in `M.r Premg Agarwal Baglow 90x50.dxf` and matching against
  `MR RAJEEV JI TWANI JI.dxf` produced **60 matches, 60 true positives, 0 false
  positives**, covering 60 of that drawing's 72 instances. Matches occurred at
  two distinct real sizes, 1.373 ft and 1.805 ft, and included mirrored
  instances — scale and mirror tolerance confirmed on hand-drafted geometry.

Recall is 83% on that pair and precision is 100%. That balance is the intended
one: an unmatched fixture falls through to the candidacy rejection signals,
while a false match would remove real geometry from wall detection. The 12
misses are not yet attributed; rotations outside the default four are the first
suspect, and `rotations_deg` is per-template configurable.

## Risks

- **False matches between simple glyphs.** A square-in-square-with-circle shower
  head resembles a floor drain. Mitigated by `size_ft` and the all-or-nothing
  part rule. A drain misread as a shower head is still correctly kept out of
  walls and columns, so the cost is a wrong subtype, not a wrong building.
- **Scale tolerance widens the candidate pool.** Without the exact-length
  prefilter `match_templates` relies on, more candidates reach the geometric
  stage. The `size_ft` check and the per-template budget bound it; the budget
  error tells the caller to narrow layers or region.
- **Library quality depends on review.** Approving a bad template affects every
  future drawing. Mitigated by preview images and provenance instance counts.
- **Package data.** A library missing from an installed build silently disables
  matching; the info Issue plus a packaging test covers it.
