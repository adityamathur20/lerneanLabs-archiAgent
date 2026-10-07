# mlightcad CAD Viewer + Server-side DWG Conversion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user open their uploaded DWG/DXF in archiViewer as a 2D CAD drawing (pan, zoom, layers, measure), select a wall to set the scale, and have every DWG converted to DXF on the VPS so the browser and archiAgent read the same file.

**Architecture:** DWG → DXF happens once, on the worker, through a small Node program wrapping `@mlightcad/libredwg-web` (GPL-3, server-only). The worker stores the result as `plan.dxf`. The browser only ever receives DXF and renders it with mlightcad's MIT packages, in a **separate sub-app** of archiViewer (its own `package.json`), because mlightcad needs `three@0.172.x` and the IFC viewer uses `three@0.182`. Stage A ships `@mlightcad/cad-simple-viewer`; Stage B swaps in `@mlightcad/cad-viewer` (Vue 3 + Element Plus UI, like https://mlightcad.com/cad-viewer/cad-viewer/) on the same engine.

**Tech Stack:** Node 22, `@mlightcad/libredwg-web@0.7.15` (server only), `@mlightcad/cad-simple-viewer@1.7.4` → `@mlightcad/cad-viewer@1.7.4`, `three@0.172.0`, Vite, Vue 3 + Element Plus (Stage B), Python 3.12 / pytest (archiAgent, service).

**Supersedes:** the `three-dxf-viewer` choice in `docs/superpowers/specs/2026-10-06-scale-resolution-design.md` §5, and the "browser picker" deferred section of `docs/superpowers/plans/2026-10-07-scale-resolution.md`.

---

## Is "simple viewer first, full viewer later" feasible?

**Yes, and little is thrown away.** `cad-viewer` is built *on top of* `cad-simple-viewer`. It lists it as a required peer at the same version (1.7.4) and drives the same `AcApDocManager` engine. So everything Stage A builds carries over unchanged:

| Built in Stage A | Kept in Stage B? |
|---|---|
| Server-side DWG → DXF, `plan.dxf` artifact, API route | Yes, untouched |
| Sub-app split, `three@0.172`, self-hosted fonts and workers, CSP | Yes, untouched |
| Loading a job's DXF with auth | Yes. Passed to `<MlCadViewer :local-file>` instead |
| "Set scale from wall" command (Phase 4) | Yes. It is an engine command, so it runs in either UI |
| Our own minimal toolbar and layer list | **Replaced** by cad-viewer's ribbon and layer manager. This is the only throwaway, and it is kept deliberately small |

Facts checked against the published 1.7.4 packages on 2026-10-07:

- **Measure tools are already in `cad-simple-viewer`**: distance, angle, area, arc, point, continuous, plus import/export of measurements. `cad-viewer` adds the ribbon, dialogs, layer manager and status bar around them.
- **Measuring is point-to-point** (`editor.getPoint` twice, with object snap). It does **not** measure a selected entity, so "click a wall → scale" is our own command (Task 13), built on the engine's selection events (`selectionSet.events.selectionAdded` → entity ids).
- **Licences:** the full `cad-viewer` dependency tree is MIT, Apache-2.0, ISC, BSD and 0BSD only (106 packages, no GPL). The GPL packages (`libredwg-*`, `dxf-json*`, `libdxfrw-*`) are opt-in and must stay out of the browser.
- **Fonts load from `https://cdn.jsdelivr.net/gh/mlightcad/cad-data` by default.** The production CSP (`font-src 'self'`, `connect-src` limited to our hosts) blocks that, so fonts must be self-hosted and passed as `baseUrl`.
- **`cad-viewer` is a Vue 3 component.** archiViewer is vanilla JS, which is fine: `createApp(MlCadViewer).mount('#cad')` works in a plain page. Vue SFCs are precompiled, so no `unsafe-eval` is needed.
- **The `url` prop fetches without our `Authorization` header.** We fetch the bytes ourselves and pass a `File` through `local-file`.

---

## Global Constraints

- **GPL boundary.** `@mlightcad/libredwg-*`, `@mlightcad/dxf-json*` and `@mlightcad/libdxfrw-*` may appear **only** in archiAgent's `tools/dwg2dxf/`, never in archiViewer's lockfiles or `dist/`. Task 5 enforces this in CI.
- **The worker image stays on private GHCR.** Handing that image to anyone else (customers, self-hosting) would convey LibreDWG and bring GPL-3 source obligations for the converter.
- **The browser never receives a DWG.** It receives `plan.dxf` only.
- **No CDN at runtime.** Fonts, workers and WASM are served from our own origin. The production CSP in `deploy/Caddyfile` does not gain new external hosts.
- **Pin mlightcad exactly** (`1.7.4`, no caret). The packages release often, and the full and simple viewers must stay on the same version.
- **The service never imports archiagent** (existing rule). DWG conversion stays inside archiAgent's Tier 1, behind `archiagent.ingest.dwg`.
- archiAgent tests live in `checks/` (`python -m pytest`). archiViewer tests are `node --test tests/` and `service/tests` (pytest).

---

## Phase 0 — Gates (do these first; each is a go/no-go)

### Task 0: Fix the service's stale `--units-per-foot` flag (prerequisite)

archiAgent commit `8a36972` removed `--units-per-foot`, but `service/archiagent_service/pipeline.py::build_command` still passes it when `options.units_per_foot` is set. Any such job exits 3 ("bad usage").

**Files:** `archiViewer/service/archiagent_service/pipeline.py`, `service/archiagent_service/api.py` (StartRequest), `service/tests/test_pipeline.py`

- [ ] Remove `units_per_foot` from `build_command` and `StartRequest`. Return 422 if a client still sends it, so the mismatch fails loudly.
- [ ] Test: `build_command` never emits `--units-per-foot`.

### Task 1: Gate — LibreDWG conversion fidelity on the real corpus

**Output:** `docs/superpowers/notes/2026-10-xx-libredwg-vs-oda.md`

- [ ] Convert every corpus DWG with both ODA and LibreDWG (`tools/dwg2dxf` from Task 3, or the prototype script).
- [ ] Run archiAgent on both DXFs. Compare per drawing: parse success, entity counts by type, wall count, region count, `acceptance`, and IFC element counts.
- [ ] Note the worst file (largest, oldest DWG version, heavy blocks or hatches).
- **Pass:** every drawing converts, and archiAgent's outputs match ODA's within the variance already seen run to run. **Fail:** keep ODA as the default and LibreDWG as an option, and record which drawings break.

Already proven on two public samples (`Arc.dwg` R2000, `example_2018.dwg`): conversion took about 100 ms, ezdxf reads the result in strict mode, and handles survive (`1BD`, `8B`).

### Task 2: Gate — handles join the viewer to the pipeline

This replaces Task 1 of the scale-resolution plan, which tested the `dxf` npm parser.

- [ ] Load three corpus DXFs in `cad-simple-viewer` (Node or a headless page) and list `objectId` for each `LINE` and `LWPOLYLINE`.
- [ ] Compare with ezdxf handles and with `SourceEntity.id` from `load_dxf`.
- **Pass:** ids match for top-level entities. **Fail:** fall back to matching by coordinates (the spec's weaker provenance), and Task 13 records points only.

### Task 3a: Gate — the mlightcad sub-app runs under the production CSP

- [ ] Build a throwaway page with `cad-simple-viewer@1.7.4` + `three@0.172.0` and self-hosted fonts and workers.
- [ ] Serve it with the exact CSP header from `deploy/Caddyfile` and open a corpus DXF. No CSP violations in the console. Text renders, which proves fonts load.
- [ ] Record the gzipped bundle size (budget: note it, then decide).

---

## Phase 1 — Server-side DWG → DXF (archiAgent + worker)

### Task 3: `tools/dwg2dxf` — the Node converter, as a separate program

**Files (archiAgent):**
- Create: `tools/dwg2dxf/package.json` (private, `"license": "GPL-3.0-or-later"`, exact pin `@mlightcad/libredwg-web@0.7.15`), `package-lock.json`, `convert.mjs`, `README.md`, `LICENSE` (GPL-3 text)

`convert.mjs <in.dwg> <out.dxf>`:
- [ ] Loads the WASM from its own `node_modules` path, never from the network.
- [ ] Calls `dwg_write_dxf(buffer)`. A `null` result, an empty output or a throw → exit 1 with a message on stderr. Never leave a partial `out.dxf`: write to a temp name and rename.
- [ ] Exit codes: 0 ok, 1 conversion failed, 2 bad usage.
- [ ] Converts one file per process, so a WASM crash or leak cannot outlive the job.
- [ ] The README states it is a **separate program**, invoked as a subprocess and never linked into archiAgent, and that it is GPL-3 because LibreDWG is.

### Task 4: `archiagent.ingest.dwg` gains a LibreDWG backend

**Files (archiAgent):** `archiagent/ingest/dwg.py`, `checks/test_dwg_ingest.py`, `checks/test_dwg_hardening.py`

- [ ] `ARCHIAGENT_DWG_CONVERTER=libredwg|oda`, default `libredwg`. `ARCHIAGENT_DWG2DXF` overrides the script path (default `<repo>/tools/dwg2dxf/convert.mjs`). `ARCHIAGENT_ODA_CONVERTER` stays for ODA.
- [ ] `converter_available()` checks the selected backend: `node` on PATH and the script plus its `node_modules` present, or the ODA binary.
- [ ] `convert_dwg()` keeps its signature and error type (`DwgConversionError`), so `cli.py` does not change. Same timeout behaviour.
- [ ] Update the module docstring: what was measured about LibreDWG (Task 1) next to what was measured about ODA.
- [ ] Tests: a missing node or script → `converter_available() is False`; a converter that writes nothing → `DwgConversionError`; a real conversion is `skipUnless` node is present.

### Task 5: Worker image + the GPL guard

**Files (archiViewer):** `service/Dockerfile.worker`, `service/archiagent_service/uploads.py`, `scripts/security-scan.mjs`, `tests/` (new `licence-boundary.test.mjs`), `.github/workflows/images.yml` if needed

- [ ] Dockerfile: copy the `node` binary from `node:22-slim` (multi-stage), then `npm ci --omit=dev` in `/opt/archiagent/tools/dwg2dxf`. Set `ARCHIAGENT_DWG_CONVERTER=libredwg`.
- [ ] `uploads.py`: replace the ODA-specific error text. `dwg_supported()` keeps asking Tier 1.
- [ ] **Licence guard (CI):** fail if any of `@mlightcad/libredwg`, `@mlightcad/dxf-json`, `@mlightcad/libdxfrw` or `libredwg-web.wasm` appears in archiViewer's `package-lock.json`, `cad/package-lock.json` or `dist/**`.
- [ ] Prove it end to end: upload a DWG to a local compose stack, and the job succeeds with `converted_from_dwg = true` and a `plan.dxf` artifact.

### Task 6: `plan.dxf` for every drawing job

Today `plan.dxf` is stored only for DWG jobs. A DXF upload is kept as `source.dxf`, which is not an artifact. The viewer needs one name for both.

**Files:** `service/archiagent_service/worker.py`, `service/tests/test_end_to_end.py`

- [ ] DXF jobs: also store the source as `plan.dxf` and add it to `artifacts`. DWG jobs: unchanged (the converted file). PDF jobs: no `plan.dxf`.
- [ ] Test: both DXF and DWG jobs list `plan.dxf`, and `GET /v1/jobs/{id}/artifacts/plan.dxf` returns it.

---

## Phase 2 — Stage A: `cad-simple-viewer` in archiViewer

### Task 7: The `cad/` sub-app

**Files (archiViewer):**
- Create: `cad/package.json` (exact pins: `@mlightcad/cad-simple-viewer@1.7.4`, `@mlightcad/data-model`, `@mlightcad/three-renderer@1.7.4`, `@mlightcad/mtext-parser`, `@mlightcad/mtext-renderer`, `three@0.172.0`, `lodash-es@4.17.21`), `cad/vite.config.js`, `cad/index.html`, `cad/src/main.js`, `cad/public/cad-data/fonts/…`, `cad/public/workers/mtext-renderer-worker.js`
- Modify: root `package.json` scripts (`build` builds both and outputs `dist/` and `dist/cad/`), `index.html` (a "Drawing" / "3D model" switch per job)

- [ ] Its own `package.json` and lockfile keep `three@0.172` and `three@0.182` from colliding. They never share a bundle.
- [ ] Copy the worker files from `node_modules` at build time (script, not hand-copied). Register them through `webworkerFileUrls`.
- [ ] Vendor a **font subset** from `mlightcad/cad-data` into `cad/public/cad-data/fonts` and set `baseUrl` to `/cad/cad-data/`. Check each font's licence before vendoring, and list them in `cad/FONTS.md`.
- [ ] The sub-app shares `src/source.js` (API base, token, `SourceError`) by import, so auth stays in one place.

### Task 8: Open a job's drawing

**Files:** `src/source.js` (add `fetchDxf(id)`), `cad/src/main.js`, `tests/source.test.mjs`

- [ ] `fetchDxf(id)` → `GET /v1/jobs/{id}/artifacts/plan.dxf` with the bearer token. The disk source serves `*.dxf` from `ARCHIAGENT_OUT` with the same `resolveWithinRoot` guard.
- [ ] Open the bytes in the engine. Show a status line for progress and errors (same style as the IFC viewer's overlay).
- [ ] Minimal UI: fit, pan and zoom (built in), a layer on/off list, and a measure-distance button. **Keep this minimal**, because Stage B replaces it.

### Task 9: Checks for the sub-app

**Files:** `scripts/shot.mjs` (CAD mode), `scripts/csp-check.mjs`, `tests/`

- [ ] Headless render check: the canvas draws geometry for a corpus DXF.
- [ ] The CSP check covers `/cad/`.
- [ ] Bundle-size report in CI (warn, not fail).

**Stage A done when:** a user picks a job, clicks "Drawing", sees their DWG (converted) or DXF, can toggle layers and measure a distance, all under the production CSP with no external requests.

---

## Phase 3 — Stage B: upgrade to the full `cad-viewer`

### Task 10: Mount `MlCadViewer`

**Files:** `cad/package.json` (add `@mlightcad/cad-viewer@1.7.4`, `vue`, `element-plus`, `vue-i18n`, `@vueuse/core` and the required peer plugins at 1.7.4), `cad/src/main.js`

- [ ] `createApp(MlCadViewer, { localFile, baseUrl: '/cad/cad-data/', theme: 'dark', locale: 'en' }).use(ElementPlus).mount('#cad')`. Build `localFile` as `new File([bytes], 'plan.dxf')` from `fetchDxf`.
- [ ] `AcApSettingManager.configure({ storageKey: 'planto3d.cad-viewer' })` so its saved preferences don't collide with other apps on the origin.
- [ ] Delete Stage A's toolbar and layer list.

### Task 11: Decide and configure the feature surface

Default: **a viewer with measuring, not an editor**. Edits in the browser would not flow back to archiAgent, so offering them would mislead.

- [ ] Keep: open, pan/zoom, layers (on/off/freeze/isolate), all measure commands, measurement import/export, switch background, reading mode.
- [ ] Hide or unregister: draw and modify commands (`line`, `circle`, `move`, `erase`, …), `open` from local disk (the job's drawing is the only input), and the **`cad-agent-plugin`** (a natural-language CAD agent that we neither need nor allow network access for).
- [ ] Export plugins (PDF/SVG/HTML/PNG): keep or hide is a product call. Default: keep PNG/PDF export.
- [ ] Hide the language selector (English only for now).

### Task 12: Re-run the Stage A checks against Stage B

- [ ] Render, CSP and licence checks green. The bundle-size delta is recorded.
- [ ] Match the dark theme to archiViewer's palette (`--bg #14161a`) if the defaults clash.

**Stage B done when:** the "Drawing" view looks and behaves like https://mlightcad.com/cad-viewer/cad-viewer/, minus editing, under our CSP and auth.

---

## Phase 4 — "Set scale from a wall" (the reason this viewer exists)

### Task 13: A `SETSCALE` engine command

**Files:** `cad/src/set-scale-cmd.js`, registered with the engine's command stack, plus a ribbon button in Stage B

- [ ] Prompt "Select a wall". On `selectionAdded`, accept only `LINE` or a single `LWPOLYLINE` segment, and take the span from the **entity's own vertices**, never the cursor.
- [ ] Prompt for the real length (`10'-6"`, `10ft`, `3.2m` — reuse archiAgent's accepted formats) and `face` or `centerline`.
- [ ] Show the implied scale live as the length is typed.
- [ ] Produce `{"measurements":[{"id","start","end","expected_ft","basis","handle"}]}` in **source coordinates**. This is exactly what `archiagent.scale.verify.load_measurements` reads (`handle` is extra and ignored by today's loader, kept for provenance).

### Task 14: Send the assertion back and re-run

**Files:** `service/archiagent_service/api.py`, `pipeline.py`, `worker.py`, tests

- [ ] `StartRequest` (or a new `POST /v1/jobs/{id}/rerun`) accepts `measurements`. The worker writes them to `measurements.json` and passes `--measurements` to the CLI.
- [ ] Validate server-side: at most N entries, finite numbers, and `basis` in {face, centerline}.
- [ ] Test: a job re-run with a measurement produces a report whose scale source is the reviewed measurement.

---

## Phase 5 — Cleanup

- [ ] Once Task 1 passes and Phase 1 is live: make ODA optional in docs, and remove the "ODA needs SaaS clearance" blocker from the deployment README.
- [ ] Update the scale-resolution spec §5 and plan to point here.
- [ ] Run `graphify update .` in archiAgent.

---

## Risks

| Risk | Mitigation |
|---|---|
| LibreDWG mis-reads some real drawings | Task 1 gate. ODA stays selectable through `ARCHIAGENT_DWG_CONVERTER` |
| A GPL package leaks into the browser bundle | Task 5 CI guard on lockfiles and `dist/` |
| Worker image ever leaves private GHCR | Constraint above. If self-hosting is sold, ship LibreDWG's source with it |
| mlightcad releases often, with breaking changes | Exact pins. Upgrade both viewer packages together, behind the Task 9/12 checks |
| `three` version clash | The separate `cad/` package, never one bundle |
| Fonts: CDN default, licence of each font | Self-host a vetted subset (Task 7) |
| Bundle weight (Vue + Element Plus + engine) | Loaded only on the "Drawing" view. Size tracked in CI |
| Handles don't match | Task 2 gate. Fall back to coordinates |
