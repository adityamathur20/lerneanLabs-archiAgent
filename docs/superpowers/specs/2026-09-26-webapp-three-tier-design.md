# archiAgent Web App — Three-Tier Architecture — Design

**Date:** 2026-09-26
**Status:** proposed
**Supersedes:** the OpenGeometry browser-reconstruction approach in `archiagent-viewer/`

---

## 1. What this builds

A web application where a user uploads a 2D floor plan (DWG or DXF), archiAgent
produces a BIM-compliant IFC, and the user views that IFC in the browser. Editing
is out of scope for this design; only the seam it will attach to is defined.

The governing rule:

> **Fragments in the browser, ifc-lite as a server-side library, ifcopenshell
> stays canonical, and they never meet at runtime.**

"Never meet at runtime" means: no request is served by two BIM engines, no
artifact is produced by one engine and mutated by another, and no tier imports
another tier's geometry kernel. The tiers communicate only through files with
declared schemas.

### 1.1 Why this shape

archiAgent's IFC authoring is already correct and already solves the hard part.
`archiagent/ifc/wall_runs.py` writes `IfcWallStandardCase` with an Axis and Body,
`IfcRectangleProfileDef` on rectangular runs, a centred material layer set, and
`IfcRelConnectsPathElements` carrying `IfcConnectionPointGeometry` at axis
crossings. `archiagent/geometry/junctions.py` resolves L/T/X junctions so one wall
runs through and the other stops at its face. `archiagent/ifc/inspect.py`
re-opens the authored file and verifies all of it.

The browser viewer does **not** benefit from re-deriving any of that. It currently
does, and every defect listed in `archiagent-viewer/README.md` under "Known gaps"
is a consequence of that choice, not of a missing library:

| Documented gap | Actual cause |
|---|---|
| "Wall junctions butt, they do not join" | The browser rebuilds walls from centrelines, discarding the junction resolution the Python tier already performed |
| "No IFC export" | The browser is being asked to author IFC it was never given the spatial hierarchy for |
| "cuboid arrangement contains sub-tolerance features" | A boolean cutter coextensive with a wall that Python had already split |

Making IFC the viewer's **input** instead of its failed output removes all three
at once, and deletes the code that produced them.

---

## 2. The three tiers

```
                          ┌─────────────────────────────────────┐
  upload (DWG/DXF)        │  TIER 1 — INGEST + AUTHORING        │
        │                 │  Python. Authoritative.             │
        ▼                 │                                      │
  ┌───────────┐           │  ODA File Converter (DWG→DXF)        │
  │  API      │──────────▶│  archiagent: classify → recognise →  │
  │  FastAPI  │           │    junctions → spaces → ifcopenshell │
  └───────────┘           │  ifc/inspect.py validate_export      │
        │                 └──────────────┬───────────────────────┘
        │                                │ writes
        │                                ▼
        │                 ╔══════════════════════════════════════╗
        │                 ║  ARTIFACT STORE (S3)                 ║
        │                 ║  plan.ifc          ← canonical       ║
        │                 ║  plan.interpretation.json ← replay    ║
        │                 ║  plan.report.json  ← validation      ║
        │                 ║  plan.overlay.svg  ← source evidence ║
        │                 ║  plan.frag         ← render cache    ║
        │                 ╚═══════┬══════════════════════┬═══════╝
        │                         │                      │
        │        ┌────────────────┘                      └────────────────┐
        ▼        ▼                                                       ▼
┌────────────────────────────────┐              ┌───────────────────────────────┐
│  TIER 2 — VIEW                 │              │  TIER 3 — ANALYSIS (optional) │
│  Browser. @thatopen/fragments  │              │  Node. ifc-lite. Headless.    │
│                                │              │                               │
│  reads: plan.frag              │              │  reads: plan.ifc + source DXF │
│  writes: nothing               │              │  writes: plan.ids.json        │
│                                │              │          plan.diff.svg        │
│  NEVER reads plan.ifc          │              │  NEVER renders. NEVER writes  │
│  NEVER writes IFC              │              │  plan.ifc.                    │
└────────────────────────────────┘              └───────────────────────────────┘
```

### 2.1 The invariants

These are the design. Violating any one of them collapses the architecture into
the two-BIM-engines problem this document exists to avoid.

**I1. `plan.ifc` has exactly one writer, forever: ifcopenshell in Tier 1.**
Not web-ifc, not `@ifc-lite/create`, not the browser. Every other artifact is
derived and disposable.

**I2. `plan.frag` is a render cache, never a source of truth.**
It is regenerated from `plan.ifc` by a pure function. If it is lost, it is
rebuilt. Nothing reads it except Tier 2. Nothing but Tier 2's own optimistic
preview ever writes it.

**A cache that cannot be shown to match its input must not be served.** This is
the half of I2 that is easy to skip and expensive to miss: matching filenames is
not matching content. `plan.frag` therefore carries a sidecar recording
`sha256(plan.ifc)` and the fragments / web-ifc versions that produced it, and any
reader — the dev server today, the Phase 3 worker and CDN tomorrow — serves it
only when that sidecar still matches. Otherwise the cache is rebuilt from
`plan.ifc`.

Without this, the ordinary case breaks the invariant silently: archiAgent
re-runs, overwrites `plan.ifc`, the previous run's `plan.frag` stays on disk, and
the user reviews yesterday's building believing it is today's. The file is still
"just a cache" in intent while outranking the source of truth in practice. A
corrupt or half-written `.frag` must likewise cost a re-conversion, never the
model.

**I3. Tier 3 is read-only with respect to `plan.ifc`.**
It emits reports beside it. `exportToStep` is never called. `IfcCreator` is never
imported. If Tier 3 disappears, the product still works.

**I4. No tier imports another tier's kernel.**
Tier 2 gets web-ifc transitively through Fragments' `IfcImporter` and that is the
only copy in the browser. Tier 3's Rust/WASM kernel never ships to the browser.
Tier 1 has ifcopenshell and nothing else.

**I5. The browser never reconstructs geometry from the interpretation manifest.**
The manifest travels to the browser only as *metadata* (provenance, source ids,
confidence), never as geometry input.

**I6. Any agent tooling over the model is read-only until the operation log exists.**
ifc-lite's MCP server advertises `create_entity`, `delete_entity`,
`entity_set_attribute`, `entity_set_property`, `draft_apply_ops` and
`export_ifc`. Every one of those would make it a second writer of `plan.ifc`,
which is I1. It therefore runs with `--read-only` until Phase 6 lands the
semantic-operation log, after which mutations are routed into that log and
replayed through ifcopenshell — never written directly. See §8.1.

### 2.2 Why ifc-lite is Tier 3, and what it is for

The redundancy analysis in §4 found that most of ifc-lite duplicates working
archiAgent code. Three capabilities survive as genuinely additive. None is on
the critical path for a first release, but all three are planned rather than
speculative:

- **`@ifc-lite/ids`** — validates the IFC against a buildingSMART IDS file. This
  is *data* completeness (does every wall carry the properties the client asked
  for), which is a different question from `validate_export`'s *geometric*
  correctness. It is additive because the IDS file is a client-supplied,
  standard, version-controllable artifact rather than more Python.
- **`@ifc-lite/drawing-2d`** — `generateFloorPlan` re-projects the built 3D to a
  plan, and `importDxf` underlays the original source drawing. Together they give
  a plan-vs-source visual diff, which archiAgent cannot currently produce because
  `reporting.py`'s `overlay.svg` draws *source evidence*, not the reconstructed
  model.

- **`@ifc-lite/mcp`** — a Model Context Protocol server over the model, so an
  agent can answer questions about a plan, colour and isolate elements, and walk
  the spatial tree on the user's behalf. This is the product-experience bet: it
  is what turns "here is your IFC" into "ask about your building". It is the
  reason ifc-lite is a planned adoption and not a maybe.

Everything else in ifc-lite is either redundant (§4) or a liability in the
browser (§9). Note what the MCP server does **not** do: it operates on a model
that already exists. It contributes nothing to turning a DXF into one, which
remains archiAgent's own hard problem.

---

## 3. Artifact contract

Every artifact for one plan lives under one prefix. The prefix is the unit of
tenancy, caching, and deletion.

```
s3://archiagent/{tenant_id}/{job_id}/
  source.dxf                  # as uploaded, or ODA output if input was DWG
  source.original.dwg         # only when the upload was DWG
  plan.ifc                    # CANONICAL. Tier 1 writes. Nothing else.
  plan.interpretation.json    # frozen interpretation; enables --replay-manifest
  plan.report.json            # validate_export result + acceptance status
  plan.overlay.svg            # source-evidence overlay (reporting.py)
  plan.frag                   # render cache, derived from plan.ifc
  plan.ids.json               # Tier 3, optional
  plan.diff.svg               # Tier 3, optional
  job.json                    # status, timings, archiagent version, exit code
```

### 3.1 Derivation rules

| Artifact | Produced by | Input | Pure? |
|---|---|---|---|
| `source.dxf` | ODA File Converter, or passthrough | upload | yes |
| `plan.ifc` | `archiagent` CLI | `source.dxf` + options | no (LLM calls) |
| `plan.interpretation.json` | `archiagent --freeze-only` | same | no |
| `plan.report.json` | `ifc/inspect.py` | `plan.ifc` | yes |
| `plan.frag` | `IfcImporter` (Node) | `plan.ifc` | semantically, not byte-wise |
| `plan.ids.json` | `@ifc-lite/ids` | `plan.ifc` + `.ids` | yes |
| `plan.diff.svg` | `@ifc-lite/drawing-2d` | `plan.ifc` + `source.dxf` | yes |

Every pure derivation is cacheable by the SHA-256 of its inputs and safe to
recompute.

**Measured 2026-09-26: `plan.frag` is not byte-deterministic.** Two conversions
of the same IFC with fragments 3.4.7 produced 222702 and 222701 bytes, diverging
from offset 26. The embedded provenance is only `{generator, version}`, so this
is ordering noise inside geometry processing, not a strippable timestamp. This
does not weaken invariant I2 — the file is still a cache that can be regenerated
at will — but it fixes the cache key: **key on
`sha256(plan.ifc) + fragments version + web-ifc version`, never on the hash of
the `.frag` itself.** A content-addressed store keyed by output bytes would miss
on every rebuild. `plan.ifc` is not pure, which is exactly why
`plan.interpretation.json` exists: `--replay-manifest` reconstructs the same IFC
without recognition, OCR or provider calls.

### 3.2 `job.json` schema

```json
{
  "schema_version": 1,
  "job_id": "01JQ8F3K9V2XQ0000000000000",
  "tenant_id": "t_9f2a",
  "status": "succeeded",
  "source": { "filename": "PLAN.dwg", "bytes": 4821004, "converted_from_dwg": true },
  "options": { "units_per_foot": 12, "height_ft": 10.0, "walls": ["WALLS"] },
  "archiagent_version": "0.1.0",
  "exit_code": 0,
  "acceptance": "checks-passed",
  "artifacts": ["plan.ifc", "plan.frag", "plan.report.json"],
  "timings_ms": { "convert": 1840, "author": 41220, "fragments": 2110 },
  "error": null
}
```

`acceptance` mirrors `reporting.py`'s existing vocabulary (`draft` |
`checks-passed`). `exit_code` mirrors the CLI's documented codes (0 success,
1 pipeline/validation failure, 2 LLM unavailable, 3 bad usage).

---

## 4. What becomes redundant

Evidence-based. Each row states the verdict and when to act. Per the surgical-
changes principle, nothing outside the viewer is deleted in Phase 1.

### 4.1 Delete in Phase 1 — `archiagent-viewer/`

| Item | Lines | Why it goes |
|---|---|---|
| `src/build-model.js` | 331 | The entire OpenGeometry reconstruction: `buildWalls`, `buildSlabs`, `pieceRing`, `mergeIntervals`, `cleanRing`, `orient`, `project`, `station`. Replaced by `IfcImporter`. Its opening-decomposition workaround and its `MIN_PIECE_M`/`MIN_EDGE_M` tolerances exist only to survive an exact kernel it will no longer call. |
| `src/manifest.js` | 133 | Manifest → viewer-model conversion, `FT_TO_M`, and the bounding-box recentring. **Redundant with evidence:** `IfcImporter` sets `webIfcSettings.COORDINATE_TO_ORIGIN = true` (index.ts:36), so the origin shift the README describes as necessary is handled by the importer. IFC carries SI units and placements, so the foot→metre conversion is gone too. |
| `src/export-stl.js` | 61 | STL was a stopgap because `AnalyticSolid.exportIfc()` could not produce usable IFC. IFC is now the input and the deliverable. **User-visible removal** — see §9. |
| `scripts/smoke.mjs` | 117 | Exercises the kernel under Node (extrusions, opening decomposition, STL byte layout). Nothing it tests will exist. Replaced by a conversion smoke test (Phase 1 Task 6). |
| `opengeometry` dependency | — | The kernel itself. |
| `vite.config.js` manifest scanning | ~50 | `findManifests`, `SUFFIX = ".interpretation.json"`, `/api/manifests`, `/api/manifest`. Becomes `.ifc` discovery. The path-confinement guard is **kept** — it is the one piece of that file worth preserving. |
| `src/main.js` lines 3–7, 88–140 | ~60 | OpenGeometry init, wasm URL import, `render(manifest)`'s call into `buildWalls`/`buildSlabs`, and the stats tables that report piece counts and extrusion failures. |

Net: roughly 700 of the viewer's 1070 lines are deleted, not rewritten.

### 4.2 Do NOT remove — commonly mistaken for redundant

| Item | Why it stays |
|---|---|
| `archiagent/ifc/inspect.py` `validate_export` | IDS cannot replace it. It checks lost void relationships, filled slab holes, world-coordinate bounds, downgraded wall classes, lost axis representations, polygon profiles on rectangular runs, missing connection points, and wall overlaps. IDS checks properties and relationships, never whether a wall is in the right place. `validate_export` remains the primary gate; IDS is additive. |
| `archiagent/geometry/junctions.py`, `spaces.py` | ifc-lite's `space_dcel` is the redundant one. It self-describes as a prototype, does not support courtyards ("every CW cycle is treated as exterior"), and resolves T-junctions in ~O(n²). `junctions.py` already does complete-link endpoint clustering in inches with reported tolerances and an explicit refusal to join separated collinear ends. **My earlier recommendation to port `snap_corners` was wrong.** |
| `archiagent/ifc/author.py`, `wall_runs.py` | `@ifc-lite/create`'s `IfcCreator` has no junction handling and would move authoring into a browser-oriented library with no `IfcRelConnectsPathElements` support. Strictly worse. |
| `--freeze-only`, `--replay-manifest` | These become load-bearing SaaS infrastructure: replay is how a job is re-run deterministically without paying for LLM calls again, and the frozen manifest is the edit seam's anchor (§8). Elevate, don't remove. |
| `archiagent/reporting.py` `overlay.svg` | Draws *source evidence* in region-local model feet. `drawing-2d` draws the *reconstructed model* from 3D meshes. Different artifacts answering different questions; keep both. |
| `scripts/shot.mjs` | Headless-Chrome screenshot verification stays useful, and becomes more useful once there is a real renderer to screenshot. |

### 4.3 Decide at the Phase 3 gate — do not touch before then

| Item | Consideration |
|---|---|
| `archiagent/blender/` (`bridge.py`, `build_scene.py`, `load.py`) + `--blender-package` | Its purpose was an editable 3D view, which Tier 2 supersedes. But Blender/Bonsai is a legitimate professional workflow and this is a working escape hatch while Tier 2 has no editing. Keep until Phase 3 ships; then remove only if telemetry shows it unused. |
| `archiagent/review_workbench.py` + `--workbench` | Will eventually be duplicated by in-app annotation, but the web app will not have annotation for several phases and this is currently the only annotation tool. Keep. Fold in later, as its own design. |

### 4.4 Pre-existing dead code — mention only, outside this scope

`blender-experiment/floorplan_3d.py`, `floorplan_3d_commercial.py` (standalone
Blender generators superseded by the archiagent pipeline) and `fix_ifc.py` (a
one-off repair script). Flagged, not touched.

---

## 5. Tier 1 — Ingest and authoring (Python, authoritative)

Unchanged in substance. Three additions.

### 5.1 DWG conversion

ODA File Converter runs as a subprocess before the existing DXF path:

```
source.original.dwg ──ODAFileConverter──▶ source.dxf ──▶ existing pipeline
```

New module `archiagent/ingest/dwg.py`, one function:

```python
def convert_dwg(dwg_path: Path, out_dir: Path, *, timeout_s: int = 120) -> Path:
    """Convert a DWG to ASCII DXF R2018 via ODA File Converter.

    Raises DwgConversionError with the converter's stderr on any failure.
    Returns the path to the produced .dxf.
    """
```

Constraints fixed by this design:
- Output format **ASCII DXF, version R2018**. ezdxf reads it, and it is the format `--units-per-foot` overrides already assume.
- The converter is invoked on a **directory**, not a file (ODA's CLI contract). The implementation creates a private temp input directory containing only the one DWG, so a batch converter cannot pick up neighbours.
- `$INSUNITS` in ODA output is **not trusted**. The known-bad declared units in this corpus mean the operator-supplied `--units-per-foot` remains authoritative, exactly as today.

**Measured 2026-09-29: ODA is deterministic apart from two header timestamps.**
Converting one DWG twice produces files differing in exactly `$TDUPDATE` and
`$TDUUPDATE`, the two "last updated" Julian dates -- not even `$TDINDWG`, the
cumulative editing time, moves. A DXF converted weeks earlier still matches.

A DXF's source fingerprint is therefore `archiagent.ingest.digest.dxf_source_digest`,
a SHA-256 taken with those two values neutralised and nothing else changed. This
makes freeze/replay survive reconversion of the same DWG, so the converted DXF is
a debugging convenience rather than a correctness requirement. The replay gate also
accepts the plain byte hash, so manifests frozen before this digest existed keep
replaying; both fingerprint the same file, so nothing new passes the gate.

**Licence risk, flagged once:** ODA File Converter is free to download but its
redistribution terms restrict bundling into a hosted service. Clearing that for
SaaS distribution is a prerequisite for Phase 4 shipping, not an engineering task.
If it cannot be cleared, the fallback is client-side conversion instructions and a
DXF-only upload path — which is why Phase 3 ships DXF-only and Phase 4 adds DWG.

### 5.2 Invocation from the API

The API never imports archiagent. It runs the CLI as a subprocess, so the
process boundary contains crashes, the LLM timeout, and memory growth:

```
archiagent --dxfFilePath {work}/source.dxf --outputDir {work} \
           --units-per-foot {n} --height {ft} [--walls ...] \
           --require-accepted
```

Exit code goes straight into `job.json`. The existing exit-code contract is the
API's error taxonomy; no new one is invented.

### 5.3 What Tier 1 does not gain

No HTTP server, no S3 client, no job awareness. It reads a directory and writes a
directory. The worker (§7) does the I/O. This keeps the CLI testable exactly as
it is today and keeps `--replay-manifest` meaningful.

---

## 6. Tier 2 — The browser viewer (Fragments)

### 6.1 Dependencies

```
@thatopen/fragments  ^3.4.7    # brings web-ifc transitively
three                ^0.168.0  # already present
```

Removed: `opengeometry`.

### 6.2 Runtime shape

```js
const workerUrl = await FragmentsModels.getWorker();
const fragments = new FragmentsModels(workerUrl);
controls.addEventListener("update", () => fragments.update());

fragments.models.list.onItemSet.add(({ value: model }) => {
  model.useCamera(camera);
  scene.add(model.object);
  fragments.update(true);
});

await fragments.load(arrayBuffer, { modelId });
```

`FragmentsModels` owns the worker, the model list, culling and LOD. The existing
`OrbitControls` camera and scene survive; the `AnalyticSolid` group management,
the per-solid raycast arrays and the piece-count stats do not.

### 6.3 Where the `.frag` comes from

Two paths, deliberately:

- **Phase 1 (browser-side conversion).** The viewer fetches `plan.ifc` and runs
  `IfcImporter` in the browser. No backend change, so the whole thesis is
  validated against real files in days.
- **Phase 2 onward (server-side precompute).** A Node script converts
  `plan.ifc` → `plan.frag` at job time; the browser fetches `.frag` only. This
  is the production path: conversion cost is paid once per model rather than
  once per page load, and mobile clients stop needing the web-ifc WASM at all.

The browser-side importer is retained after Phase 2 **only** as a local-file
drag-and-drop affordance, never as the path for a stored job.

### 6.4 Selection and properties

`model.raycast({ camera, mouse, dom })` returns the hit;
`model.getItemsData([localId])` returns its attributes; `model.getSpatialStructure()`
gives the project/site/building/storey tree for a navigator panel. All three
come from the IFC that Tier 1 authored, so the categories and GUIDs the user sees
are the deliverable's own — not a browser reconstruction's guesses.

### 6.5 Provenance overlay

`plan.interpretation.json` is still fetched, but only to map an IFC GUID back to
the source DXF entity ids that produced it, for a "why is this wall here" panel.
Metadata only — invariant I5.

---

## 7. Multi-tenant SaaS design

### 7.1 Components

| Component | Technology | Responsibility |
|---|---|---|
| API | FastAPI | auth, upload, job submission, artifact URLs |
| Queue | Redis + RQ | one job = one plan conversion |
| Worker | Python container | S3 → temp dir → CLI subprocess → S3 |
| Frag builder | Node container | `plan.ifc` → `plan.frag` |
| Store | S3 | the `{tenant_id}/{job_id}/` prefix of §3 |
| Metadata | Postgres | tenants, users, jobs, artifact index |

### 7.2 Endpoints

```
POST   /v1/uploads                 → { upload_url, job_id }   presigned PUT
POST   /v1/jobs/{job_id}/start     → { status: "queued" }     body: options
GET    /v1/jobs/{job_id}           → job.json
GET    /v1/jobs/{job_id}/artifacts/{name} → 302 to presigned GET
GET    /v1/jobs                    → paginated, tenant-scoped
DELETE /v1/jobs/{job_id}           → deletes the whole prefix
```

### 7.3 Tenancy rules

- **Every artifact path is prefixed with `tenant_id`.** Authorization is a prefix
  comparison, enforced in one function, not per-endpoint.
- **Presigned URLs only.** The API never proxies artifact bytes; a 400 MB IFC must
  not traverse the app server.
- **Per-tenant concurrency cap.** LLM classification is the cost centre and the
  slow step; without a cap one tenant's batch starves everyone. Default 2
  concurrent jobs per tenant, configurable.
- **Upload limits.** 200 MB, and an extension allowlist of `.dxf`, `.dwg`, `.pdf`.
  Content-type from the client is not trusted; the worker validates by parsing.
- **Cost attribution.** LLM token counts belong in `job.json` from day one. This
  is a per-job-cost product and retrofitting attribution is painful.

### 7.4 The one thing to get right early

The storage boundary is a single module (`storage.py`) with `put`, `get`,
`presign`, `delete_prefix`. Phase 3 implements it against S3. Local development
implements it against a directory. Nothing else in the codebase knows which.

---

## 8. The edit seam (deferred — seam only)

Editing is not designed here. What is fixed here is *where it attaches*, so that
Phases 1–4 do not foreclose it.

**Edits are semantic operations against the frozen interpretation, never mesh
edits against the IFC.**

```
user gesture in Tier 2
  → operation  { "op": "set_wall_thickness", "wall_index": 17, "value_ft": 0.75 }
  → appended to plan.operations.json
  → Tier 1: archiagent --replay-manifest plan.interpretation.json
                       --operations plan.operations.json
  → new plan.ifc → new plan.frag → reload
```

Why this and not Fragments' editor: `editor.createShell`/`createItem` is
geometry-level and has no notion that a wall hosts a door. Moving a wall there
would not re-cut its openings, update the adjacent space, or refresh quantities —
and `grep -rniE "exportIfc|toIfc|IfcExporter|writeIfc" packages/fragments/src`
returns zero hits, so there is no path back to IFC at all. Replaying through
ifcopenshell keeps invariant I1 and reuses the junction, space and validation
code that already works.

The cost is latency: an edit is a round-trip, not a drag. Fragments' editor is
then useful for exactly one thing — optimistic local preview while the round-trip
runs. That is a Phase 6 concern.

**What Phases 1–4 must therefore preserve:** `--replay-manifest` must stay
working and stay covered by tests, and `plan.interpretation.json` must remain a
published artifact even though nothing reads it yet.

---

### 8.1 The MCP seam

ifc-lite's MCP server (Phase 7) is how an agent reaches the model. Two facts
about it determine the integration, and both were verified in the source:

**It does not need its own renderer.** `HeadlessLikeBackend` (exported from
`@ifc-lite/mcp`) drives the whole `bim.*` surface over a parsed store with no
viewer at all. So the server runs beside the Python worker in Tier 3 and never
ships WASM to the browser — invariant I4 survives.

**Its viewer tools can drive *our* viewer.** `@ifc-lite/embed-protocol` is a
typed postMessage protocol across an iframe boundary: commands in
(`LOAD_MODEL`, `SELECT`, `SET_COLORS`, `SET_CAMERA`, `GET_PROPERTIES`) and
events out (`READY`, `MODEL_LOADED`, `ENTITY_SELECTED`, `CAMERA_CHANGED`).
Implementing the *viewer side* of that protocol against Fragments means
`viewer_colorize` / `viewer_isolate` / `viewer_fly_to` control the Fragments
scene, and ifc-lite's WebGPU renderer is never used.

This is why Task 4 of the plan puts the Fragments runtime behind a
`createFragViewer` facade instead of inlining it into `main.js`: that facade's
surface — `loadFrag`, `raycast`, `current`, `update` — is the adapter point the
embed-protocol handler will sit on. Nothing in Phase 1 needs to know that, but
the seam should not be closed by accident.

**The constraint that matters:** the MCP server advertises mutation tools
(`create_entity`, `delete_entity`, `entity_set_attribute`,
`entity_set_property`, `draft_apply_ops`, `export_ifc`). Running it without
`--read-only` before Phase 6 exists would give `plan.ifc` a second writer and
break invariant I1 silently — the file would still be valid IFC, just no longer
reproducible from the interpretation. Phase 7 therefore ships `--read-only`,
and mutation tools are enabled only once they can be routed into the
operation log of §8.

## 9. What this deliberately does not do

- **No IFC writing in JavaScript.** web-ifc can (`CreateIfcEntity`/`WriteLine`/
  `SaveModel`) and `@ifc-lite/create` can more conveniently. Both duplicate
  ifcopenshell, neither handles junctions, and both would create a second writer
  for `plan.ifc`. Invariant I1 exists to close this door.
- **No `.frag` as an interchange format.** It is open and FlatBuffers-schema'd,
  but nothing outside the ThatOpen ecosystem reads it. Treating it as a
  deliverable would be a lock-in mistake.
- **No ifc-lite in the browser.** Its renderer is WebGPU, which is a real support
  matrix problem for Safari and iOS; and shipping it alongside Fragments means two
  WASM kernels, two scene graphs, two origin-shift conventions (`RtcFrame` vs
  Fragments' `COORDINATE_TO_ORIGIN`) and two id spaces (`expressId` vs `localId`)
  for one model.
- **No STL export.** Removed with `export-stl.js`. If a user needs a mesh, glTF
  from Tier 3's `exportGlb` is the better answer and costs nothing to add later.
  This is a user-visible removal and should be announced, not silently dropped.
- **No `space_dcel` port.** §4.2.
- **No editing.** §8.

---

## 10. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Fragments renders archiAgent's IFC incorrectly (unusual profiles, the `WP:` polygon-profile walls) | medium | **This is what Phase 1 exists to find out**, before any SaaS spend. Phase 1's gate is a visual comparison against the current OpenGeometry render on the same plan. |
| ODA licence blocks SaaS redistribution | medium | Phase 3 ships DXF-only and is complete without DWG; Phase 4 is severable. |
| `IfcImporter` in the browser is too slow on large plans | low for floor plans | Phase 2 moves conversion server-side, which is the production path regardless. |
| `@thatopen/fragments` 3.x churn | medium | Pin exactly. The `.frag` format version is embedded in the file; a major bump means re-deriving the cache, which is a pure function by design (I2). |
| Losing the STL affordance annoys an existing user | low | Announce; offer glTF if asked. |
| Two-engine creep — someone adds `exportToStep` to Tier 3 "just for one feature" | medium | I3 is written down. Enforce in review. |

---

## 11. Phasing

Ordered so each phase ships something usable, the riskiest assumption is tested
first and cheapest, and the optional work is last.

| Phase | Deliverable | Size | Gate |
|---|---|---|---|
| **1** | Fragments viewer, local files, OpenGeometry deleted | ~1–2 weeks | Renders a real plan at least as well as today; ~700 lines gone |
| **2** | `plan.frag` build step + artifact contract | ~3–5 days | `ifc→frag` Node CLI; re-conversion yields an equivalent model (byte-identity does not hold — see §3.1) |
| **3** | SaaS: auth, S3, queue, worker, API, DXF upload end to end | ~4–6 weeks | A user uploads a DXF and views the result without touching a terminal |
| **4** | DWG ingest via ODA | ~1 week | A DWG upload produces the same IFC as its DXF export |
| **5** | Tier 3: ifc-lite headless — IDS validation + plan-vs-DXF diff | ~2–3 weeks | Planned. Establishes the Node analysis tier that Phase 7 also needs |
| **6** | Edit round-trip | — | Its own design doc |
| **7** | ifc-lite MCP server, `--read-only`, driving the Fragments viewer over `embed-protocol` | ~2–3 weeks | An agent can answer questions about a plan and highlight elements in the viewer. Depends on Phase 5 |

Phases 3–7 each cover an independent subsystem and each needs its own
implementation plan before execution. The companion plan document details
Phase 1 and Phase 2 task by task.

---

## 12. Success criteria

**Phase 1 is done when:**
1. `archiagent-viewer` has no `opengeometry` dependency and no `build-model.js`.
2. Loading a `.ifc` from `dxfBased_ifcOutput/` shows walls with joined corners and cut openings.
3. Clicking a wall reports its IFC class and GUID from `getItemsData`.
4. `npm run smoke -- <path>.ifc` converts and asserts mesh counts under Node, exiting non-zero on failure.
5. `npm run shot` still produces a screenshot.

**The architecture is holding when:**
- `grep -rn "opengeometry\|AnalyticSolid" archiagent-viewer/src` is empty.
- `plan.ifc` has one writer.
- Deleting every `.frag` in the store breaks nothing permanently.
