# Reusable sanitary-fixture library

Date: 2026-09-19
Status: design, pending implementation plan
Related: `2026-09-18-geometry-first-wall-candidacy-design.md`

## Problem

Sanitary fixtures — shower heads, WCs, washbasins, sinks, bathtubs, taps — are
sometimes built into the IFC as walls or columns. On
`MR RAJEEV JI TWANI JI.dxf` a shower-head glyph produced five phantom
`IfcWallStandardCase` walls, one per instance.

The pipeline already has the right *behaviour* for recognised fixtures; it
fails at *recognition*.

### What already exists

- **Name keywords.** `classify/rules.py:named_role` maps names to
  `Role.PLUMBING` on `plumbing, sanitary, sink, wc, basin, toilet, drain`.
- **Recognition.** `recognition.recognize_symbols` checks each INSERT's block
  name first, then its layer's role (at confidence ≥ 0.7), and emits a
  `SymbolInstance`.
- **Exclusion and retention.** `recognition.exclude_symbol_geometry` removes a
  recognised symbol's source entities and their descendants — never ancestors —
  before wall detection. The `SymbolInstance` stays on the model with position,
  size, rotation, outline and source IDs, and is written into the IFC as
  `SymbolsJSON`. Only `column` and `beam` symbols are authored as 3D objects
  (`ifc/author.py:391`).
- **Templates.** `classify/templates.match_templates` matches reviewed seed
  shapes, but seeds must come from the same drawing, and scaling is refused
  (`"template scaling is unsupported"`).

So a recognised plumbing fixture is already excluded from wall and column
creation and retained for later placement. This spec does not change that
behaviour; it makes recognition work.

### Why recognition failed

- The shower block is named `GGATGREASYS` and nested inside `FGFDGGDF`. Neither
  name means anything.
- Its layers are `WALL` and `S-WALL`, so layer role cannot identify it either.
- `S-WALL` was classified `column` at 0.5. At ≥ 0.7 the shower heads would have
  been recognised as **columns** and authored as `IfcColumn`.

### What the corpus contains

| Drawing | Fixture-named blocks |
|---|---|
| Aiims Road 3BHK Flats | `Sink` ×24, `shower head` ×24, `dsink` ×8 |
| MB Panwar JI Revision 2 | `BASIN 1` ×3, `basin 3` ×3, `BATHTUB` ×1 |
| SANJANA SURESH JI | `Toilet - top` ×6, `wash` ×6 |
| MR RAJEEV JI TWANI JI | none of 10 blocks usefully named |
| PLAN.dxf | no blocks at all — fixtures are exploded linework |

Names are reliable in some drawings and useless in others. Drawings with good
names are a free source of labelled fixture shapes for drawings without them.

## Goals

1. Recognise named fixture blocks that the current keyword list misses.
2. Build a reviewed, versioned library of fixture shapes harvested from
   well-named drawings.
3. Match that library against block definitions in any drawing, tolerant of
   rotation, mirroring and uniform scale, bounded by real-world size.
4. Guarantee a matched fixture is never authored as a wall or a column, and is
   retained with enough information for a later interior-placement step.

## Non-goals

- Matching library shapes against exploded linework (PLAN.dxf). Deferred to its
  own spec. Until then, exploded fixtures are handled only by the wall-candidacy
  rejection signals — rejected as walls, but not labelled.
- Non-uniform stretching. A stretched simple rectangle or circle matches too
  much unrelated geometry.
- Electrical and furniture fixtures. The mechanism is kind-agnostic, so they can
  be added later by harvesting with their keywords; this spec ships sanitary
  fixtures only.
- Authoring fixtures as 3D IFC objects.

## 1. Keyword expansion

Add to the `Role.PLUMBING` token set in `named_role`:

```
shower, tap, taps, faucet, bath, bathtub, tub, wash, washbasin,
commode, urinal, ewc, iwc
```

`named_role` tokenises on `[a-z]+`, so `shower head` yields `{shower, head}` and
now resolves to plumbing; today it does not.

`named_role` returns the first matching group, and `WALL_STRUCTURAL` is checked
before `PLUMBING`. A name containing both, such as `shower wall`, still resolves
to wall. This ordering is unchanged and is covered by a test so it stays
deliberate.

## 2. Library file

Location: `archiagent/data/fixtures.json`, committed and versioned. It must be
declared as package data so installed builds include it.

`--fixture-library PATH` replaces the bundled library for one run.

### Record

```json
{
  "schema_version": 1,
  "templates": [{
    "id": "shower_head-3f9a1c2e",
    "kind": "plumbing",
    "subtype": "shower_head",
    "label": "square shower head with centre drain",
    "geometry": [[[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]], "..."],
    "signature": {"closed_loops": 2, "circles": 1, "segments": 8, "aspect": 1.0},
    "size_ft": {"min": 0.25, "max": 1.5},
    "provenance": [{"source_sha256": "…", "block_name_sha256": "…", "instances": 24}],
    "status": "reviewed",
    "reviewed_by": "user"
  }]
}
```

- `geometry` is normalised: centred on the bounding-box centre, scaled so the
  larger bounding-box side is 1, principal axis aligned to x.
- `signature` is a cheap prefilter; templates whose signature disagrees are not
  compared geometrically.
- `size_ft` bounds the **larger** side of a matched instance in real feet.
- **Provenance holds hashes only** — no client file names and no raw block names,
  since both can carry client or project identifiers and this file is
  committed. `label` is written by the reviewer.

Subtypes: `wc`, `washbasin`, `sink`, `shower_head`, `bathtub`, `tap`, `urinal`.

### Loading

Malformed records fail loudly with `ValueError`, following `regions.load_regions`
— this is reviewed data, and silently dropping a record hides a mistake. Only
`status: "reviewed"` records are used for matching.

## 3. Harvesting

```
archiagent fixtures harvest <dxf> [<dxf> …]
archiagent fixtures approve <candidate-id> [--subtype S] [--label TEXT] [--size MIN MAX]
archiagent fixtures reject  <candidate-id>
```

`harvest`:

1. Finds INSERTs whose block name resolves to `Role.PLUMBING` under the expanded
   `named_role`.
2. Flattens each block definition, including nested blocks, into block-local
   geometry.
3. Normalises it as above and computes the signature.
4. Merges near-identical shapes across drawings into one candidate, unioning
   provenance.
5. Proposes a subtype from the block name, and a size range from the real sizes
   observed at each insert's scale, widened by 25% either side and clamped to
   the subtype defaults below.
6. Writes candidates and one preview PNG each under the input drawing's
   `.archiagent-cache/fixtures/` — never under the repo, since previews are
   renders of client drawings.

`approve` moves a candidate into `archiagent/data/fixtures.json` with
`status: "reviewed"`. Nothing enters the library without approval.

Default size ranges (larger side, feet), editable per template:

| Subtype | min | max |
|---|---|---|
| wc | 1.2 | 2.5 |
| washbasin | 1.0 | 2.5 |
| sink | 1.0 | 3.5 |
| shower_head | 0.2 | 1.5 |
| bathtub | 4.0 | 6.5 |
| tap | 0.1 | 0.8 |
| urinal | 0.8 | 2.0 |

## 4. Matching

New function `match_fixture_library(ps, units_per_foot, library)` in
`archiagent/classify/fixture_library.py`, returning `SymbolInstance`s.

1. **Per block definition, once.** For each distinct block definition used by
   any INSERT, including nested ones, flatten, normalise and signature it.
   Compare against every library template whose signature agrees, testing the
   principal-axis alignment in four orientations × mirrored. The score is the
   fraction of template geometry within tolerance of the candidate and vice
   versa, where "within tolerance" means within `FIXTURE_TOLERANCE` = 0.03 of
   the normalised larger side. At or above `FIXTURE_MATCH_FLOOR` = 0.90, the
   definition matches. Both are starting values, tuned against the harvested
   corpus.
2. **Per instance.** For each INSERT of a matched definition, compute the real
   size of the larger side through the full insert transform chain. Inside the
   template's `size_ft` range, emit a `SymbolInstance`:
   - `kind="plumbing"`, `subtype` from the template
   - position, width, depth, rotation and boundary in model feet
   - `source_ids` = that INSERT and its descendants
   - `evidence="fixture-library"`, `confidence` = the match score
   - `properties` includes `("fixture_template_id", <id>)`, so a later
     interior-placement step can choose the right object

   Outside the range, nothing is emitted and an info Issue
   `fixture_size_out_of_range` records the template, instance and measured size.
3. **Innermost claim.** When nested definitions match, only the innermost
   matching INSERT is claimed. For MR RAJEEV, `GGATGREASYS` is claimed and
   `FGFDGGDF` is not, because an outer block can also carry real walls.
   `exclude_symbol_geometry` already never traverses to ancestors.

Block definitions per drawing number roughly 0–63 and the library tens of
templates, so matching cost is negligible.

### Precedence

In `pipeline._assemble`, library matches override recognised symbols using the
pattern `_assemble` already applies to reviewed templates (`pipeline.py:57-58`):
run `recognize_symbols`, run `match_fixture_library`, then drop every
recognised symbol whose `source_ids` intersect a library match's, and append the
library matches.

A library match therefore overrides both block-name and layer-role recognition.
A shower head on a `column`-classified layer becomes a plumbing fixture, never a
column. Explicit `review["symbols"]` records continue to override everything,
unchanged.

## 5. Effect on walls, columns and later placement

No new code is needed for the guarantees in goal 4:

- matched geometry is removed by `exclude_symbol_geometry` before any wall
  detector runs, including the candidacy detectors
- `ifc/author.py` authors 3D objects only for `column` and `beam` symbols
- plumbing `SymbolInstance`s remain on the model and in `SymbolsJSON`, with
  subtype and template ID

Tests pin these guarantees so a later change cannot silently break them.

## Relationship to wall candidacy

The two are complementary:

- The library **labels** fixtures drawn as recognisable blocks and removes them
  before candidate generation.
- Candidacy's rejection signals — small closed loops, enclosed circles and
  hatches, nested concentric faces, repeated instancing — remain the backstop
  for fixtures the library cannot see, chiefly exploded linework.

Neither depends on the other. Both can land in either order.

## Error handling

| Condition | Result |
|---|---|
| bundled library missing or empty | matching skipped, info Issue |
| `--fixture-library` path missing | configuration error, exit |
| malformed library record | `ValueError` at load |
| instance outside `size_ft` | not matched, info Issue `fixture_size_out_of_range` |
| block definition fails to flatten | that definition skipped, warn Issue |

Library matching never fails a build; only an explicitly supplied, unreadable
library file does, since that is a configuration mistake.

## Testing

- normalisation invariance: a template rotated at arbitrary angles, mirrored,
  and uniformly scaled from 0.1× to 50× still matches; a different shape of the
  same signature does not
- size-range boundaries, exactly at `min` and `max`
- nested blocks: only the innermost matching INSERT is claimed; sibling and
  ancestor geometry survives
- precedence over a `column` layer role and over `recognize_symbols`
- `review["symbols"]` still overrides library matches
- MR RAJEEV JI TWANI JI: five `plumbing/shower_head` symbols, zero walls and
  zero columns at those locations
- harvesting Aiims Road yields `sink` and `shower_head` candidates; MB Panwar
  yields `washbasin` and `bathtub`
- keyword expansion, including `shower head` → plumbing and `shower wall` → wall
- provenance contains no client file names or raw block names
- guarantee pins: plumbing symbols are excluded from wall input and never
  authored as `IfcColumn`/`IfcBeam`

## Risks

- **False matches between simple glyphs.** A square-in-square-with-circle
  shower head resembles a floor drain. Mitigated by the size range and the 0.90
  floor; a drain misread as a shower head is still correctly excluded from walls
  and columns, so the cost is a wrong subtype, not a wrong building.
- **Library quality depends on review.** Approving a bad template affects every
  future drawing. Mitigated by preview images and by provenance counts, which
  show how many real instances support a template.
- **Keyword expansion reaches layer names too.** `named_role` also classifies
  layers in `RuleClassifier` and in `recognize_symbols`' layer fallback, so a
  layer named `BATH` for room labels would now read as plumbing and its geometry
  could be grouped into plumbing symbols. The effect is exclusion from walls, not
  wrong construction, and `WALL` still wins over `PLUMBING` for names containing
  both. A test on each corpus drawing's layer names records any role changes
  so they are reviewed rather than discovered.
- **Package data.** A library missing from an installed build silently disables
  matching; the info Issue plus a packaging test covers it.
