# Generic reconstruction enhancements — validation record

These changes extend the existing semantic pipeline. No drawing-specific
coordinates, handles, layer ranges, floor assignments or unit overrides were
added to the reconstruction code. Reviewed decisions remain input data.
No external code, model weights or packages were downloaded for this work.

## Changes delivered

| Concern | Change | Practical boundary |
|---|---|---|
| Repeated interpretation changes results | A typed, checksum-bound canonical snapshot; `--freeze-only` and `--replay-manifest`; code/package/prompt versions and stable IFC root IDs | Replay preserves saved decisions. Reinterpreting a new drawing still requires evidence and review. Version changes can affect the geometry kernel. |
| Incorrect symbol interpretation contaminates walls | Reviewed symbol classes bypass automatic note reclassification; source ownership removes claimed block/hatch descendants while retaining unrelated coincident geometry; layer prompts explicitly acknowledge ambiguous arcs and mixed layers | Existing detectors/templates are bounded. No universal door/window/MEP recognizer was added. |
| Irregular walls lose shape | Native complex filled contours and reviewed profiles preserve concavity, holes and orientation. Optional exploded-hatch polygonization requires separate explicit layers and bounded work | Thin-fill tests are rule hypotheses. Uncertain outlines require review; no automatic wall skeleton from arbitrary profiles. |
| Profiles duplicate wall runs or lose opening hosts | Covered runs map to profile solids; real IFC opening relationships are retained; partial overlaps are rejected | Separate sweep junctions still need future trimming to eliminate all quantity overlaps. |
| Export succeeds despite missing geometry | Authoring and reopened IFC checks now include missing physical representations, supported product census, profile/void volumes, bounds and relationships | IFC validity does not independently establish architectural accuracy. |
| Blender diverges from IFC | IFC-first package with tessellated editable meshes, stable identities, optional Bonsai links, per-face appearance and camera framing | Producing `.blend` requires running the generated Blender script. Ordinary mesh edits do not update IFC. |
| Material appearance is lost | Reused IFC surface styles for six architectural classes; transparency and face styles survive the mesh bridge | Palette is illustrative and labelled as such; material-layer assemblies and finish schedules are not inferred. |
| Review acceptance is inferred from supplied geometry | `footprint_verified` now requires explicit `true`; frozen replay recomputes current acceptance gates | Review booleans record a claim, not authenticated human approval. |

## Verification

The existing baseline passed **208 tests and 6 subtests**. The final full suite
passed **257 tests and 6 subtests** after the enhancements:

```bash
XDG_CACHE_HOME="$PWD/.cache" .venv/bin/python -m pytest checks -q \
  --basetemp=.test-tmp/enhancement-final
```

The final run reported 442 warnings, comprising SWIG deprecations and Shapely
`oriented_envelope` runtime warnings. They were not suppressed; the geometry
assertions and export checks passed. This is regression evidence on the local
dependency versions, not a clean-warning or cross-platform certification.

Meaningful checks include:

- Real generated DXFs in feet, inches and millimetres, including rotations of
  23 and -17 degrees: extraction → freeze → replay → IFC. A 17 ft² wall ring
  retained its hole and physical area, producing one profile wall in each case.
- Concave wall area, canonical contour ordering, explicit exploded-hatch
  evidence, resource caps and ownership-based exclusion of nested entities.
- A reviewed door classification stays unchanged beside potentially
  contradictory OCR text. Unknown or unhosted symbols remain unresolved.
- Replay succeeds when CAD loading and classifiers are replaced by failing
  stubs; source/digest mismatch, malformed fields, duplicate JSON keys,
  nonfinite values and conflicting replay options are rejected.
- Frozen models cannot bypass current acceptance merely by deleting saved
  issues. Geometry-affecting review changes require another extraction.
- IFC profile openings, holes, expected volumes/bounds, missing-representation
  mutations, stable root identities across replay/output relocation, and
  actual surface colors/transparency after tessellation.

A real installed Blender background build and reopen also passed:
**8 mesh objects, 8 unique saved IFC links**, metric coordinates matching IFC
within 1e-5 m, and the existing scene preserved. Evidence is in
`.test-tmp/blender runtime/verification.json` and `result.blend`.
The Blender bridge was exercised with styles; the later generic author palette
was separately verified through actual IFC tessellation tests. No controlled
Bonsai edit/export round trip was performed, so automatic synchronization is
not claimed. The background invocation required the environment's approved
sandbox escalation after a sandboxed Blender startup failed.

## Scoped performance experiment

The repeatable local benchmark creates a generic ring wall and 2,000 unrelated
native text annotations. After warming imports/caches, three runs compared
DXF parsing plus interpretation against checksum verification plus frozen-model
loading. All canonical models were equal.

| Phase | Median time |
|---|---:|
| DXF extraction and interpretation | 47.542 ms |
| Frozen model loading and validation | 1.149 ms |

The ratio was **41.37× for this phase on this fixture**. It is not a whole-pipeline
speedup, a new-drawing accuracy improvement, or a prediction for all drawings.
IFC writing/validation, Blender, OCR and provider latency were excluded. The
benchmark used a known wall-layer classifier; no model service was called.

Raw timings and implementation versions are recorded in
`.test-tmp/enhancement-benchmark/timings.json`. This measurement preceded the
appearance-only IFC author addition; it did not exercise authoring.
Reproduce with a fresh output directory:

```bash
XDG_CACHE_HOME="$PWD/.cache" .venv/bin/python scripts/benchmark_frozen_replay.py \
  --output-dir .test-tmp/replay-benchmark-new --annotations 2000 --repeats 3
```

## Recommended use and remaining work

Use source review → freeze → acceptance-gated IFC replay → Blender package for
repeat builds. For a new drawing, verify scale and plan regions, review symbol
hypotheses and compare registered geometry overlays before freezing. Keep
review data separate from generic implementation. Start with `--rules` or
`--walls` when an offline run is required; default provider classification
can send drawing evidence externally.

A repository copy of the requested reusable full prompt is saved at
[`REUSABLE_SYSTEM_PROMPT.md`](REUSABLE_SYSTEM_PROMPT.md).
Its old drawing-specific appendix was replaced with generic CLI usage,
calculation conventions, decision rules and implemented-versus-target limits.
The narrower versioned interpretation contract is available through
`--print-interpretation-prompt`; printing it does not automatically execute an
agent-based interpretation stage.

Priorities for further accuracy work remain independently annotated evaluation
across varied drawings, richer symbol/host inference, profile-to-room topology,
trimming overlapping wall sweeps, and reviewed storey registration. DWG
conversion, general image/raster-PDF reconstruction, arbitrary curved-wall
modeling and comprehensive furniture/MEP generation remain outside the current
implementation. Source accuracy on the original client drawing has not been
measured by these synthetic checks. No claim of a universal accuracy boost is
supported yet.

## Active classifier prompt follow-up

Both active classifier prompts in `archiagent/classify/prompt.py` now share
explicit role definitions, within-drawing evidence comparison, counterexamples,
mixed-layer handling and uncertainty bands. Native wall/column hatching is no
longer automatically routed to annotation. The DXF prompt separately explains
inventory-only and labelled image-review modes, including evidence ownership
between isolated layers and the reference image. The response schema remains
unchanged; the content-derived version changed from `b560648bb28b` to
`886594f1567e`, so old cached classifications are not reused.

Validation: **21 targeted checks passed**, covering the PDF/DXF text callers,
DXF visual batch delivery, old-cache invalidation/new-cache reuse and the frozen
replay regressions. These use fake provider transports, not actual LLM responses,
and therefore do not measure classification accuracy. Seven dependency warnings
were reported. Commands:

```bash
XDG_CACHE_HOME="$PWD/.cache" .venv/bin/python -m pytest \
  checks/test_classifier_prompt_wiring.py checks/test_interpretation_replay.py \
  -q --basetemp=.test-tmp/prompt-enhancement
```
