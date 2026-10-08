# Phase 0 gates: mlightcad viewer + server-side DWG conversion

Date: 2026-10-07
Plan: `docs/superpowers/plans/2026-10-07-mlightcad-cad-viewer.md`, Phase 0
Status: **Tasks 0, 2, 3a pass. Task 1 passes on public samples, with one known
writer failure. Its real verdict needs the private corpus and ODA (see "What
is still open").**

Run environment: Linux, Node 22.22, Python 3.13, ezdxf 1.4.4, ifcopenshell
0.9.0, `@mlightcad/libredwg-web` 0.7.15, `@mlightcad/cad-simple-viewer` 1.7.4,
headless Chromium 1194. No client drawings and no ODA File Converter were
available, so every run below used public or synthetic files.

## Task 0 — the service's stale scale flag (fixed)

archiAgent removed `--units-per-foot` (commit `8a36972`) and now **refuses
every DXF** unless the run asserts a wall (`--scale-from-wall`) or trusts the
drawing's dimensions (`--trust-extracted-scale`). The service still sent the
removed flag and could send neither replacement, so **every deployed DXF job
was failing on scale**.

Fixed in archiViewer `074dd36`: `StartRequest` takes `trust_extracted_scale`
and `scale_from_wall`, and refuses `units_per_foot` by name (422) with the
replacement in the message. The upload form's units field became a "use the
drawing's own dimensions" checkbox. Arguments the service builds were parsed
by archiAgent's real `_parser()`, negative coordinates included.

## Task 1 — LibreDWG conversion fidelity

Harness: `scripts/gates/dwg_fidelity.py` (converts with LibreDWG, and with ODA
when installed; compares ezdxf, ingest and full-pipeline results).
Converter: `tools/dwg2dxf/convert.mjs` (built now rather than as a throwaway).

| | Result on 18 public LibreDWG samples (R14 → 2018) |
|---|---|
| Converted | **17 / 18** |
| ezdxf reads the output | strict mode, all 17 |
| Handles preserved | yes, DWG handle = DXF handle (`1BD`, `8B`, …) |
| Time per file | **0.23–0.40 s** end to end |
| Failure | `example_2004.dwg`: LibreDWG's **DXF writer** throws "table index is out of bounds". Its **parser** reads the same file (67 entities). |

The pipeline ran on every converted file and refused them all on scale, which
is correct: these are CAD test files without usable dimensions, not floor
plans. A synthetic floor plan (`scripts/gates/sample_plan.py`) runs through to
an IFC.

**Found and fixed while measuring: 7.5 s per conversion → 0.3 s.** V8's
optimising compiler spends ~7 s on the 9.5 MB WASM module in background
threads, and process exit waits for it. `convert.mjs` now sets
`--liftoff-only` on itself. Output is byte-identical.

**Fallback for writer failures (for Phase 1, not built):** since the parser
succeeds where the writer fails, the converter can fall back to parse →
`@mlightcad/libredwg-converter` → `@mlightcad/data-model`'s `dxfOut`, still
inside the separate GPL program.

## Task 2 — handle join

Python side, already measured on three real corpus drawings
(`2026-10-07-handle-join-spike.md`): every top-level `SourceEntity.id` is the
raw DXF handle (100%).

Viewer side, new: `archiViewer/gates/handle-join.mjs` loads the same DXF with
mlightcad's own reader and checks every LINE and LWPOLYLINE (the entities a
user selects to set scale) for the same id, kind, layer and vertices.

| File | Selectable joined | All top-level in viewer |
|---|---|---|
| synthetic plan | 9 / 9 | 19 / 19 |
| 6 LibreDWG `example_*` (R14 → 2018) | 19 / 19 each | 60–64 of 62–68 |
| `sample_2018` | 4 / 4 | 6 / 6 |

**Pass.** The chain holds end to end: DWG handle = converted DXF handle =
mlightcad `objectId` = archiAgent `SourceEntity.id`. A tampered expectation
(moved vertex, unknown id) fails the gate, so it is not passing vacuously.

Not loaded by mlightcad (none selectable): `REGION`, `LIGHT`, `ACAD_TABLE`,
`ARC_DIMENSION`. Arc segments in a polyline are flattened by archiAgent and
kept as bulged vertices by mlightcad; the gate checks that the flattened run
passes through every vertex. Scale picking should accept straight segments
only.

## Task 3a — production CSP

`archiViewer/gates/csp-gate.mjs` builds a minimal `cad-simple-viewer` page,
serves it with the CSP read from `deploy/Caddyfile`, and drives headless
Chromium.

| Check | Result |
|---|---|
| Drawing opened | ✓ |
| CSP violations | **0** |
| Third-party requests | **0** (the default jsDelivr font CDN was replaced) |
| Fonts self-hosted and loaded | ✓ |
| Canvas drew the plan | ✓ (walls, door blocks, arc, hatch, text, dimensions) |
| Bundle | 1 JS chunk, 3.86 MB raw / **1.04 MB gzip** |

No CSP change is needed: `worker-src 'self' blob:` and `connect-src 'self'`
already cover the MTEXT worker and font fetches.

### Finding that changes Task 7: the default fonts are not licensed

mlightcad's default font repository (`mlightcad/cad-data`) has **no licence**,
and its README says "It is your own responsibility to buy license of those
data." It holds Autodesk SHX fonts (`simplex`, `txt`, `romans`, …) and
Microsoft's SimSun. Self-hosting them on planto3d.in would redistribute them.

Instead, we serve our own `fonts.json` that aliases the CAD font names onto
openly licensed fonts. Measured with Liberation Sans (SIL OFL): text in
aliased fonts, unknown SHX fonts (`isocp.shx`) and unknown TrueType fonts
(`tahoma.ttf`, `OpenSansCondensed-Light.ttf`) all render through the fallback,
in TEXT and MTEXT alike. Text will look like a clean sans-serif rather than
AutoCAD's stroke fonts, which is fine for reading a plan.

Minor: the dimension text of ezdxf's own default dimension style did not
render, while dimensions in the `Standard` style did. To recheck on real
drawings in Stage A.

## Other finding: archiAgent cannot run a DXF with no units header

`load_dxf` raises `DxfUnitsError` ("supply a finite positive
--units-per-foot") when `$INSUNITS` is absent or unitless, and the CLI exits 3.
That flag no longer exists, and `--scale-from-wall` does not help because the
error comes first. Every R14 file and many "unitless" real drawings hit this.
**Not fixed here**: it belongs to the scale-resolution work in archiAgent.

## What is still open for Task 1

The fidelity verdict needs real floor plans and ODA as the reference, and both
are on your machine only:

```bash
cd tools/dwg2dxf && npm ci && cd ../..
python scripts/gates/dwg_fidelity.py /path/to/corpus out/fidelity
#   add --pipeline-args "--trust-extracted-scale" (or --scale-from-wall …) as suits the corpus
```

With ODA installed, it converts each DWG both ways and lists every difference
in parse, ingest and pipeline output in `out/fidelity/fidelity.md`. It exits 1
on any conversion failure or pipeline difference.
