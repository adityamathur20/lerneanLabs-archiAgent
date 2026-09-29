# Phase 5 — Tier 3: ifc-lite headless analysis (IDS + plan-vs-DXF diff) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Node analysis tier that validates an authored `plan.ifc` against a client-supplied buildingSMART IDS file and renders a plan-vs-source visual diff, producing two new job artifacts without Tier 1 or Tier 2 changing behaviour.

**Architecture:** A new `archiagent-viewer/analysis/` Node package wraps `@ifc-lite/parser` + `@ifc-lite/ids` + `@ifc-lite/drawing-2d`. It is invoked by the Python worker as a subprocess, exactly as the worker already invokes the archiAgent CLI, so the process boundary contains WASM crashes and memory growth. It reads `plan.ifc` and the source DXF; it writes `plan.ids.json` and `plan.diff.svg`. It never writes IFC and never imports archiagent.

**Tech Stack:** Node 20 (ESM), `@ifc-lite/parser@9.1.0`, `@ifc-lite/ids@3.1.0`, `@ifc-lite/drawing-2d@4.0.4`, `node:test`, Python 3.14 / FastAPI / RQ on the calling side.

**Spec:** `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md` (§2.2 why ifc-lite is Tier 3, §2.1 invariants I1–I6, §3 artifact contract, §11 Phase 5)

---

## Findings from the pre-plan spike (2026-09-29)

These were measured, not assumed, and the tasks below depend on them. Re-verify
if the pinned versions move.

1. **The IDS path works headlessly and is fast.** `scanIfcEntities(buf)` →
   `new ColumnarParser().parseLite(buf, entityRefs)` →
   `createDataAccessor(store)` → `validateIDS(doc, accessor, modelInfo)` produced
   a real report against `wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc`
   (15337 entity refs) in **36.6 ms of parse time**. The accessor read
   archiAgent's own `ArchiAgent_Provenance` property set with full fidelity, and
   `getSchemaVersion()` returned `IFC4`.
2. **`createDataAccessor` is NOT exported from the package root.** It lives at
   the `@ifc-lite/ids/bridge` subpath. Importing `@ifc-lite/ids/dist/bridge/index.js`
   fails with `ERR_PACKAGE_PATH_NOT_EXPORTED`.
3. **IDS entity matching has no subtype inheritance, deliberately.**
   `@ifc-lite/ids`'s `entity-facet.d.ts` documents this as buildingSMART
   conformance: "There is no automatic inheritance in IDS entity facet
   interpretation." Measured against a real archiAgent IFC with one spec
   requiring `ArchiAgent_Provenance.SourceLayer`:

   | IDS `<entity><name>` | entities checked | result |
   |---|---|---|
   | `IFCWALL` | **0** | specification FAILED |
   | `IFCWALLSTANDARDCASE` | 169 | 169 passed, 100% |

   archiAgent writes **both** classes on purpose: `IfcWallStandardCase` for
   straight wall runs (`ifc/author.py:271`, *enforced* by
   `ifc/inspect.py:202`) and `IfcWall` for profile-based walls
   (`ifc/author.py:240`). A client IDS naming only `IfcWall` therefore silently
   skips every straight wall. **This plan does not change Tier 1's wall class** —
   that is a Tier 1 decision with GlobalId consequences (`ifc/identity.py:24-26`
   deliberately keys a wall as `IfcWall` whatever its subtype). Task 3 makes the
   miss loud instead.
4. **`drawing-2d` splits cleanly into an easy half and a hard half.**
   `importDxf(text, name?) -> DxfUnderlay` is pure, synchronous and needs no
   WASM. `generateFloorPlan(meshes, elevation, options?)` needs
   `MeshData[]` from `@ifc-lite/geometry`, whose entry points
   (`GeometryProcessor`, `createPlatformBridge`) are worker/browser-shaped and
   carry a `.wasm` asset. Task 5 spikes that behind a feature flag and the
   deliverable degrades to underlay-only rather than failing.

---

## Global Constraints

- **Invariant I1 — one IFC writer.** This tier never writes IFC. `@ifc-lite/create` and `@ifc-lite/mutations` must not appear in `analysis/package.json`.
- **Invariant I3 — Tier 3 is read-only.** Every entry point opens `plan.ifc` for reading. No artifact under a job prefix is modified, only added.
- **Invariant I4 — no cross-tier kernels.** `analysis/` must not depend on `web-ifc` or `@thatopen/fragments`; the viewer must not depend on `@ifc-lite/*`. A test asserts both directions.
- **Invariant I6 — agent tooling read-only until the operation log exists.** Phase 5 ships no MCP server; that is Phase 7.
- **Node 20 ESM only.** `"type": "module"`. No TypeScript build step: the packages ship `.d.ts` but the analysis code is plain `.mjs`/`.js`, matching the existing viewer scripts.
- **Exact pins.** `@ifc-lite/parser@9.1.0`, `@ifc-lite/ids@3.1.0`, `@ifc-lite/drawing-2d@4.0.4`. Report shapes are version-bound, exactly as `@thatopen/fragments` is pinned in the viewer.
- **`createDataAccessor` is imported from `@ifc-lite/ids/bridge`**, never a `dist/` path.
- **Artifact naming follows §3**: working stem `plan`, so `plan.ids.json` and `plan.diff.svg` beside `plan.ifc`.
- **Exit codes are the error taxonomy.** `0` success, `2` usage error, `3` analysis produced a finding that fails the gate, `4` input unreadable. The worker maps these, inventing no new taxonomy.
- **No network at analysis time.** The IDS file is a job input, never fetched.

## Review Focus

The five input classes the spec implies but no task's own tests exercise, most
likely to bite first. Each has its test added to the task that owns the code.

1. **An IDS whose specifications match zero entities** — the `IfcWall` case in finding 3. A reasonable person expects to be told their IDS matched nothing, not to receive a 100% pass. Test in Task 3 (`coverage` block and exit code).
2. **A malformed or non-IDS XML file uploaded as an IDS** — expect a usage error naming the file, not a stack trace. Test in Task 2.
3. **An IFC with no geometry at all** (the site-plan outliers in this corpus) — expect an empty-but-valid diff and a stated reason, not a crash or a blank SVG indistinguishable from failure. Test in Task 5.
4. **A DXF whose extents are wildly larger than the IFC's** (units mismatch, the known-bad `$INSUNITS` in this corpus) — expect the diff to say the two do not share a coordinate system rather than drawing a hairline against a football field. Test in Task 4.
5. **An IDS with hundreds of specifications over a large model** — `validateIDS` is CPU-bound and the spike ran one spec; expect a bounded runtime and a timeout that reports partial progress rather than a hung worker. Test in Task 3 (`maxEntities`/timeout path).

## File Structure

| Path | Responsibility |
|---|---|
| `archiagent-viewer/analysis/package.json` | Node package, exact pins, `node:test` wiring |
| `archiagent-viewer/analysis/src/model.js` | `openIfc(path)` → `{ store, accessor, entityCount, schemaVersion }`. The only place the parser is touched. |
| `archiagent-viewer/analysis/src/ids.js` | `runIds(model, idsText)` → report + coverage. No I/O. |
| `archiagent-viewer/analysis/src/diff.js` | `buildDiff(model, dxfText, options)` → SVG string. No I/O. |
| `archiagent-viewer/analysis/bin/analyse.mjs` | CLI: argument parsing, file I/O, exit codes. The only place with side effects. |
| `archiagent-viewer/analysis/tests/*.test.mjs` | `node:test` suites, one per src module plus a CLI suite |
| `archiagent-viewer/analysis/README.md` | What this tier is, what it must never do, how to run it |
| `archiagent-viewer/service/archiagent_service/analysis.py` | Python side: build the command, run it, collect the two artifacts |
| `archiagent-viewer/service/tests/test_analysis.py` | Python-side tests |

Tasks 1–5 build the Node package bottom-up; Task 6 wires the worker.

---

## Task 1: The analysis package and its model loader

**Files:**
- Create: `archiagent-viewer/analysis/package.json`
- Create: `archiagent-viewer/analysis/src/model.js`
- Create: `archiagent-viewer/analysis/README.md`
- Test: `archiagent-viewer/analysis/tests/model.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: `openIfc(path: string) => Promise<Model>` where
  `Model = { store, accessor, entityCount: number, schemaVersion: string, modelId: string }`.
  Every later task consumes exactly this shape.

- [ ] **Step 1: Create the package with exact pins**

```bash
mkdir -p analysis/src analysis/bin analysis/tests
cd analysis
```

`analysis/package.json`:

```json
{
  "name": "archiagent-analysis",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "description": "Tier 3: read-only IDS validation and plan-vs-source diff over an authored IFC.",
  "bin": { "archiagent-analyse": "bin/analyse.mjs" },
  "scripts": { "test": "node --test tests/" },
  "dependencies": {
    "@ifc-lite/parser": "9.1.0",
    "@ifc-lite/ids": "3.1.0",
    "@ifc-lite/drawing-2d": "4.0.4"
  }
}
```

- [ ] **Step 2: Install and record the resolved tree**

Run: `cd analysis && npm install`
Expected: installs, and `node -e "import('@ifc-lite/ids/bridge').then(m=>console.log(typeof m.createDataAccessor))"` prints `function`.

- [ ] **Step 3: Write the failing test**

`analysis/tests/model.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openIfc } from "../src/model.js";

const IFC = process.env.ARCHIAGENT_IFC;

test("openIfc reports the schema, entity count and a usable accessor", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  assert.equal(model.schemaVersion, "IFC4");
  assert.ok(model.entityCount > 0, "entity count must be positive");
  assert.equal(typeof model.accessor.getEntitiesByType, "function");
  // archiAgent authors both wall classes; at least one must be present.
  const walls = model.accessor.getEntitiesByType("IFCWALLSTANDARDCASE").length
              + model.accessor.getEntitiesByType("IFCWALL").length;
  assert.ok(walls > 0, "an authored plan has walls");
});

test("openIfc rejects a file that is not IFC", async () => {
  const dir = mkdtempSync(join(tmpdir(), "analysis-"));
  const path = join(dir, "not.ifc");
  writeFileSync(path, "this is not a STEP file");
  await assert.rejects(() => openIfc(path), /not a readable IFC/);
});

test("openIfc reports a missing file by path", async () => {
  await assert.rejects(() => openIfc("/nonexistent/plan.ifc"), /\/nonexistent\/plan\.ifc/);
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd analysis && node --test tests/model.test.mjs`
Expected: FAIL — `Cannot find module '../src/model.js'`.

- [ ] **Step 5: Implement the loader**

`analysis/src/model.js`:

```js
/**
 * The only place @ifc-lite/parser is touched.
 *
 * Tier 3 is read-only (invariant I3): this opens plan.ifc and never writes IFC.
 * `createDataAccessor` comes from the `@ifc-lite/ids/bridge` subpath -- the
 * package root does not export it, and a `dist/` path throws
 * ERR_PACKAGE_PATH_NOT_EXPORTED.
 */
import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import { ColumnarParser, scanIfcEntities } from "@ifc-lite/parser";
import { createDataAccessor } from "@ifc-lite/ids/bridge";

export class IfcUnreadableError extends Error {}

export async function openIfc(path) {
  const bytes = await readFile(path);           // ENOENT carries the path
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);

  let entityRefs;
  try {
    ({ entityRefs } = await scanIfcEntities(buffer));
  } catch (cause) {
    throw new IfcUnreadableError(`${path} is not a readable IFC: ${cause.message}`, { cause });
  }
  if (!entityRefs || entityRefs.length === 0) {
    throw new IfcUnreadableError(`${path} is not a readable IFC: no STEP entities found`);
  }

  const store = await new ColumnarParser().parseLite(buffer, entityRefs);
  const accessor = createDataAccessor(store);
  return {
    store,
    accessor,
    entityCount: entityRefs.length,
    schemaVersion: accessor.getSchemaVersion?.() ?? "IFC4",
    modelId: basename(path),
  };
}
```

- [ ] **Step 6: Run the tests**

Run: `cd analysis && ARCHIAGENT_IFC="$PWD/../../wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc" node --test tests/model.test.mjs`
Expected: PASS 3/3.

- [ ] **Step 7: Write the README**

`analysis/README.md` states: this is Tier 3; it is read-only; it must never
depend on `web-ifc`, `@thatopen/fragments`, `@ifc-lite/create` or
`@ifc-lite/mutations`; `createDataAccessor` comes from `@ifc-lite/ids/bridge`;
and the IDS-inheritance finding (finding 3) with its measured table.

- [ ] **Step 8: Commit**

```bash
git add analysis/
git commit -m "feat(analysis): Tier 3 Node package with a read-only IFC loader"
```

---

## Task 2: Parse and audit an IDS document

**Files:**
- Create: `archiagent-viewer/analysis/src/ids.js`
- Test: `archiagent-viewer/analysis/tests/ids-parse.test.mjs`

**Interfaces:**
- Consumes: nothing from Task 1 yet (parsing is independent of the model).
- Produces: `parseIdsDocument(xmlText: string) => { document, audit }` and
  `IdsInputError`. Task 3 consumes `document`.

- [ ] **Step 1: Write the failing test**

`analysis/tests/ids-parse.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { parseIdsDocument, IdsInputError } from "../src/ids.js";

const MINIMAL = `<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS">
  <info><title>t</title></info>
  <specifications>
    <specification name="Walls record their source layer" ifcVersion="IFC4" minOccurs="1" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCWALLSTANDARDCASE</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceLayer</simpleValue></baseName>
        </property>
      </requirements>
    </specification>
  </specifications>
</ids>`;

test("a valid IDS parses to one specification", () => {
  const { document } = parseIdsDocument(MINIMAL);
  assert.equal(document.specifications.length, 1);
  assert.equal(document.info.title, "t");
});

test("XML that is not IDS is a usage error naming what was wrong", () => {
  assert.throws(() => parseIdsDocument("<html><body>nope</body></html>"), IdsInputError);
});

test("XML that does not parse at all is a usage error, not a stack trace", () => {
  assert.throws(() => parseIdsDocument("<ids><unclosed>"), IdsInputError);
});

test("an empty file is a usage error", () => {
  assert.throws(() => parseIdsDocument(""), IdsInputError);
});

test("an IDS with no specifications is reported, not silently accepted", () => {
  const empty = MINIMAL.replace(/<specifications>[\s\S]*<\/specifications>/, "<specifications/>");
  assert.throws(() => parseIdsDocument(empty), /no specifications/);
});

test("the structural audit travels with the document", () => {
  const { audit } = parseIdsDocument(MINIMAL);
  assert.ok(Array.isArray(audit.issues), "audit must expose an issues array");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd analysis && node --test tests/ids-parse.test.mjs`
Expected: FAIL — `Cannot find module '../src/ids.js'`.

- [ ] **Step 3: Implement**

`analysis/src/ids.js` (the validation half is added in Task 3):

```js
/** IDS parsing and validation. Pure: no file or network I/O. */
import { parseIDS, IDSParseError, auditIDSDocument } from "@ifc-lite/ids";

export class IdsInputError extends Error {}

export function parseIdsDocument(xmlText) {
  if (!xmlText || !xmlText.trim()) throw new IdsInputError("the IDS file is empty");
  let document;
  try {
    document = parseIDS(xmlText);
  } catch (cause) {
    const detail = cause instanceof IDSParseError && cause.details ? `: ${cause.details}` : "";
    throw new IdsInputError(`not a valid buildingSMART IDS document${detail}`, { cause });
  }
  if (!document?.specifications?.length) {
    throw new IdsInputError("the IDS document declares no specifications");
  }
  return { document, audit: auditIDSDocument(document) };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd analysis && node --test tests/ids-parse.test.mjs`
Expected: PASS 6/6. If `auditIDSDocument` returns a shape without `issues`,
read `node_modules/@ifc-lite/ids/dist/audit/types.d.ts` and assert the real
field name — the audit shape is version-bound and the pin is 3.1.0.

- [ ] **Step 5: Commit**

```bash
git add analysis/src/ids.js analysis/tests/ids-parse.test.mjs
git commit -m "feat(analysis): parse and audit an IDS document with usable errors"
```

---

## Task 3: Validate the model against the IDS, and report coverage

This is the task that closes Review Focus #1 and #5.

**Files:**
- Modify: `archiagent-viewer/analysis/src/ids.js`
- Test: `archiagent-viewer/analysis/tests/ids-validate.test.mjs`

**Interfaces:**
- Consumes: `openIfc` from Task 1 (`Model`), `parseIdsDocument` from Task 2.
- Produces: `runIds(model, document, options?) => Promise<IdsResult>` where

```
IdsResult = {
  report,                     // the @ifc-lite/ids IDSValidationReport, verbatim
  coverage: {
    specifications: number,   // total in the document
    matchedNothing: string[], // names of specs whose applicability matched 0 entities
    entitiesChecked: number,
  },
  status: "passed" | "failed" | "matched-nothing",
}
```

`status` is `"matched-nothing"` when at least one specification matched zero
entities, even if nothing failed. Task 6 maps it to exit code 3.

- [ ] **Step 1: Write the failing test**

`analysis/tests/ids-validate.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { openIfc } from "../src/model.js";
import { parseIdsDocument, runIds } from "../src/ids.js";

const IFC = process.env.ARCHIAGENT_IFC;

function idsFor(entityName) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS">
  <info><title>archiAgent</title></info>
  <specifications>
    <specification name="Walls record their source layer" ifcVersion="IFC4" minOccurs="1" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>${entityName}</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceLayer</simpleValue></baseName>
        </property>
      </requirements>
    </specification>
  </specifications>
</ids>`;
}

test("an IDS matching the authored wall class passes", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  const { document } = parseIdsDocument(idsFor("IFCWALLSTANDARDCASE"));
  const result = await runIds(model, document);
  assert.equal(result.status, "passed");
  assert.ok(result.coverage.entitiesChecked > 0);
  assert.deepEqual(result.coverage.matchedNothing, []);
});

test("an IDS that matches nothing says so instead of reporting a pass", { skip: !IFC }, async () => {
  // IDS entity matching has no subtype inheritance (buildingSMART conformance),
  // and archiAgent writes IfcWallStandardCase for straight runs. An IDS naming
  // IfcWall therefore matches no straight wall -- the single most likely way a
  // real client's IDS silently checks nothing.
  const model = await openIfc(IFC);
  const { document } = parseIdsDocument(idsFor("IFCWALL"));
  const result = await runIds(model, document);
  assert.equal(result.status, "matched-nothing");
  assert.deepEqual(result.coverage.matchedNothing, ["Walls record their source layer"]);
  assert.equal(result.coverage.entitiesChecked, 0);
});

test("a requirement the model does not meet fails", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  const { document } = parseIdsDocument(
    idsFor("IFCWALLSTANDARDCASE").replace("SourceLayer", "NoSuchPropertyAtAll"));
  const result = await runIds(model, document);
  assert.equal(result.status, "failed");
});

test("onProgress is reported so a long run is not a silent hang", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  const { document } = parseIdsDocument(idsFor("IFCWALLSTANDARDCASE"));
  const seen = [];
  await runIds(model, document, { onProgress: (p) => seen.push(p) });
  assert.ok(seen.length >= 1, "at least one progress callback");
});

test("maxEntities bounds the work on a large model", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  const { document } = parseIdsDocument(idsFor("IFCWALLSTANDARDCASE"));
  const result = await runIds(model, document, { maxEntities: 5 });
  assert.ok(result.coverage.entitiesChecked <= 5,
    `bounded run checked ${result.coverage.entitiesChecked}`);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd analysis && ARCHIAGENT_IFC="$PWD/../../wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc" node --test tests/ids-validate.test.mjs`
Expected: FAIL — `runIds is not a function` (it is not exported yet).

- [ ] **Step 3: Implement**

Append to `analysis/src/ids.js`:

```js
import { validateIDS, createCachedAccessor } from "@ifc-lite/ids";

/**
 * Validate a model against an IDS document.
 *
 * `coverage.matchedNothing` exists because a specification that matches zero
 * entities is the quiet failure mode of IDS: the report is not "wrong", it is
 * vacuous. archiAgent writes IfcWallStandardCase for straight wall runs, and
 * IDS entity facets are exact-match by buildingSMART's own rule, so a client
 * IDS naming IfcWall checks nothing at all. Saying so is the whole value.
 */
export async function runIds(model, document, { onProgress, maxEntities } = {}) {
  // The validator re-reads the same entities once per specification; without
  // this a multi-specification document is O(specs x entities x source-parses).
  const accessor = createCachedAccessor(model.accessor);
  const report = await validateIDS(document, accessor, {
    modelId: model.modelId,
    schemaVersion: model.schemaVersion,
    entityCount: model.entityCount,
  }, { onProgress, maxEntities });

  const results = report.specifications ?? [];
  const matchedNothing = results
    .filter((s) => (s.applicableCount ?? s.entities?.length ?? 0) === 0)
    .map((s) => s.name);
  const entitiesChecked = report.summary?.totalEntitiesChecked ?? 0;
  const failed = (report.summary?.failedSpecifications ?? 0) > 0;

  // Order matters: a document that matched nothing must not be reported as a
  // pass, and a real failure outranks a vacuous specification.
  let status = "passed";
  if (failed && matchedNothing.length === 0) status = "failed";
  else if (matchedNothing.length > 0) status = "matched-nothing";
  else if (failed) status = "failed";

  return {
    report,
    coverage: { specifications: document.specifications.length, matchedNothing, entitiesChecked },
    status,
  };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd analysis && ARCHIAGENT_IFC="$PWD/../../wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc" node --test tests/ids-validate.test.mjs`
Expected: PASS 5/5.

The third test ("a requirement the model does not meet fails") asserts
`"failed"`, and that specification also matches 169 entities, so
`matchedNothing` is empty and the first branch applies. If it reports
`matched-nothing`, the branch order above is wrong — fix the code, not the test.

- [ ] **Step 5: Commit**

```bash
git add analysis/src/ids.js analysis/tests/ids-validate.test.mjs
git commit -m "feat(analysis): validate against IDS and report specifications that matched nothing"
```

---

## Task 4: The DXF underlay and the coordinate-system guard

This task closes Review Focus #4. It delivers the half of the diff that needs
no WASM, so a useful artifact exists before Task 5's geometry spike.

**Files:**
- Create: `archiagent-viewer/analysis/src/diff.js`
- Test: `archiagent-viewer/analysis/tests/diff-underlay.test.mjs`

**Interfaces:**
- Consumes: `Model` from Task 1.
- Produces:
  - `underlayFromDxf(dxfText, name) => DxfUnderlay`
  - `modelBounds(model) => { min: [x,y], max: [x,y] } | null`
  - `compareExtents(ifcBounds, underlay) => { ratio: number, comparable: boolean, reason: string }`
  - `renderDiffSvg({ underlay, plan, extents }) => string`

  Task 5 adds `plan` (the reprojected floor plan) and calls `renderDiffSvg` with it.

- [ ] **Step 1: Write the failing test**

`analysis/tests/diff-underlay.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { underlayFromDxf, compareExtents, renderDiffSvg } from "../src/diff.js";

const DXF = process.env.ARCHIAGENT_DXF;

function tinyDxf(scale = 1) {
  const p = (n) => String(n * scale);
  return ["  0","SECTION","  2","ENTITIES",
          "  0","LINE","  8","WALLS"," 10",p(0)," 20",p(0)," 11",p(10)," 21",p(0),
          "  0","ENDSEC","  0","EOF",""].join("\n");
}

test("a DXF becomes an underlay with named layers", () => {
  const underlay = underlayFromDxf(tinyDxf(), "tiny.dxf");
  assert.ok(underlay, "underlay must be produced");
  assert.ok(Array.isArray(underlay.layers), "underlay exposes layers");
});

test("a real corpus DXF imports", { skip: !DXF }, () => {
  const underlay = underlayFromDxf(readFileSync(DXF, "utf8"), "plan.dxf");
  assert.ok(underlay.layers.length > 0);
});

test("extents within an order of magnitude are comparable", () => {
  const underlay = underlayFromDxf(tinyDxf(), "tiny.dxf");
  const verdict = compareExtents({ min: [0, 0], max: [11, 4] }, underlay);
  assert.equal(verdict.comparable, true);
});

test("a units mismatch is reported, not drawn", () => {
  // The known-bad declared units in this corpus: a DXF in inches against a
  // model in feet is a 12x extent ratio. Drawing that overlays a hairline on a
  // football field and looks like catastrophic reconstruction failure.
  const underlay = underlayFromDxf(tinyDxf(1000), "huge.dxf");
  const verdict = compareExtents({ min: [0, 0], max: [11, 4] }, underlay);
  assert.equal(verdict.comparable, false);
  assert.match(verdict.reason, /coordinate system|extent|units/i);
});

test("a model with no bounds is reported rather than guessed", () => {
  const underlay = underlayFromDxf(tinyDxf(), "tiny.dxf");
  const verdict = compareExtents(null, underlay);
  assert.equal(verdict.comparable, false);
  assert.match(verdict.reason, /no geometry/i);
});

test("the SVG is well-formed and states the verdict when not comparable", () => {
  const underlay = underlayFromDxf(tinyDxf(1000), "huge.dxf");
  const extents = compareExtents({ min: [0, 0], max: [11, 4] }, underlay);
  const svg = renderDiffSvg({ underlay, plan: null, extents });
  assert.match(svg, /^<\?xml|^<svg/);
  assert.match(svg, /<\/svg>\s*$/);
  assert.match(svg, /coordinate system|not comparable/i);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd analysis && node --test tests/diff-underlay.test.mjs`
Expected: FAIL — `Cannot find module '../src/diff.js'`.

- [ ] **Step 3: Implement**

`analysis/src/diff.js`:

```js
/**
 * Plan-vs-source diff. Pure: callers read the files.
 *
 * Two independent halves, deliberately:
 *   - the DXF underlay, which importDxf gives us with no WASM at all;
 *   - the reprojected floor plan, which needs tessellated meshes (Task 5).
 * The underlay alone is already a useful artifact, so a missing plan degrades
 * the SVG rather than failing the job.
 */
import { importDxf } from "@ifc-lite/drawing-2d";

/** Above this ratio the two drawings are not in the same coordinate system. */
export const EXTENT_RATIO_LIMIT = 10;

export function underlayFromDxf(dxfText, name = "source.dxf") {
  return importDxf(dxfText, name);
}

function underlayExtent(underlay) {
  const b = underlay?.bounds;
  if (b && Number.isFinite(b.minX)) {
    return Math.max(b.maxX - b.minX, b.maxY - b.minY);
  }
  let lo = Infinity, hi = -Infinity, loY = Infinity, hiY = -Infinity;
  for (const layer of underlay?.layers ?? []) {
    for (const path of layer.paths ?? []) {
      for (const [x, y] of path.points ?? []) {
        if (x < lo) lo = x; if (x > hi) hi = x;
        if (y < loY) loY = y; if (y > hiY) hiY = y;
      }
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(loY)) return null;
  return Math.max(hi - lo, hiY - loY);
}

export function compareExtents(ifcBounds, underlay) {
  if (!ifcBounds) {
    return { ratio: NaN, comparable: false,
             reason: "the IFC contains no geometry, so there is nothing to compare" };
  }
  const dxfSpan = underlayExtent(underlay);
  if (!dxfSpan) {
    return { ratio: NaN, comparable: false,
             reason: "the DXF contains no drawable geometry" };
  }
  const ifcSpan = Math.max(ifcBounds.max[0] - ifcBounds.min[0],
                           ifcBounds.max[1] - ifcBounds.min[1]);
  if (!(ifcSpan > 0)) {
    return { ratio: NaN, comparable: false,
             reason: "the IFC geometry has zero extent" };
  }
  const ratio = Math.max(dxfSpan / ifcSpan, ifcSpan / dxfSpan);
  if (ratio > EXTENT_RATIO_LIMIT) {
    return { ratio, comparable: false,
             reason: `the DXF and the model do not share a coordinate system `
                   + `(extents differ ${ratio.toFixed(1)}x; a units mismatch looks like this)` };
  }
  return { ratio, comparable: true, reason: "extents are comparable" };
}

const escape = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

export function renderDiffSvg({ underlay, plan, extents, width = 1600, height = 1200 }) {
  const parts = [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">`,
    `<rect width="100%" height="100%" fill="#14161a"/>`,
  ];
  if (!extents.comparable) {
    // Say why, in the artifact. A blank or absurd overlay is indistinguishable
    // from a reconstruction failure, which is the wrong thing to imply.
    parts.push(`<text x="24" y="40" fill="#e8833a" font-family="sans-serif" font-size="20">`
             + `Not comparable: ${escape(extents.reason)}</text>`);
  } else {
    parts.push(`<g id="source" stroke="#6c8cff" fill="none" stroke-width="0.5" opacity="0.65">`);
    for (const layer of underlay.layers ?? []) {
      for (const path of layer.paths ?? []) {
        const pts = (path.points ?? []).map(([x, y]) => `${x},${-y}`).join(" ");
        if (pts) parts.push(`<polyline points="${pts}"/>`);
      }
    }
    parts.push(`</g>`);
    if (plan) {
      parts.push(`<g id="model" stroke="#e6e8ec" fill="none" stroke-width="1">`);
      for (const line of plan.lines ?? []) {
        parts.push(`<line x1="${line.start[0]}" y1="${-line.start[1]}" `
                 + `x2="${line.end[0]}" y2="${-line.end[1]}"/>`);
      }
      parts.push(`</g>`);
    } else {
      parts.push(`<text x="24" y="40" fill="#949aa6" font-family="sans-serif" font-size="16">`
               + `Source underlay only: the model was not reprojected.</text>`);
    }
  }
  parts.push(`</svg>`);
  return parts.join("\n");
}
```

- [ ] **Step 4: Run the tests**

Run: `cd analysis && node --test tests/diff-underlay.test.mjs`
Expected: PASS 6/6 (one skipped without `ARCHIAGENT_DXF`).

`DxfUnderlay`'s real field names are version-bound. If `underlay.layers[].paths[].points`
does not exist, read `node_modules/@ifc-lite/drawing-2d/dist/dxf/types.d.ts`
and use the actual names; do not weaken the tests to match a guess.

- [ ] **Step 5: Run with a real corpus DXF**

Run: `cd analysis && ARCHIAGENT_DXF="$PWD/../../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf" node --test tests/diff-underlay.test.mjs`
Expected: PASS 6/6, none skipped.

- [ ] **Step 6: Commit**

```bash
git add analysis/src/diff.js analysis/tests/diff-underlay.test.mjs
git commit -m "feat(analysis): DXF underlay with a coordinate-system guard"
```

---

## Task 5: Reproject the model to a plan (spike, feature-flagged)

This is the one task whose feasibility is genuinely open (finding 4). It closes
Review Focus #3. **The deliverable degrades rather than fails:** if the geometry
tier cannot run headlessly, `plan.diff.svg` ships with the underlay and a stated
reason, and the plan records that outcome.

**Files:**
- Modify: `archiagent-viewer/analysis/src/diff.js`
- Create: `archiagent-viewer/analysis/src/reproject.js`
- Test: `archiagent-viewer/analysis/tests/reproject.test.mjs`

**Interfaces:**
- Consumes: `Model` from Task 1.
- Produces: `reprojectPlan(model, { elevation }) => Promise<{ lines } | null>`.
  Returns `null` — never throws — when geometry is unavailable, so Task 4's
  `renderDiffSvg` takes its `plan: null` branch.

- [ ] **Step 1: Spike whether the geometry tier runs in Node at all**

Run, and read the output before writing any code:

```bash
cd analysis && npm install @ifc-lite/geometry@7.6.0 && node -e "
const g = await import('@ifc-lite/geometry');
console.log(Object.keys(g).filter(k => /Geometry|Processor|Bridge|wasm/i.test(k)));
console.log('GeometryProcessor:', typeof g.GeometryProcessor);
"
```

Expected: prints the exported names. Record in the ledger whether
`GeometryProcessor` is constructible without a `Worker` or DOM global. Node 20
has no global `Worker`, so if the constructor or its first call reaches for one,
the answer is no.

- [ ] **Step 2: Write the failing test**

`analysis/tests/reproject.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { openIfc } from "../src/model.js";
import { reprojectPlan, geometryAvailable } from "../src/reproject.js";

const IFC = process.env.ARCHIAGENT_IFC;

test("geometryAvailable answers without throwing", async () => {
  const available = await geometryAvailable();
  assert.equal(typeof available, "boolean");
});

test("reprojectPlan returns lines or null, never throws", { skip: !IFC }, async () => {
  const model = await openIfc(IFC);
  const plan = await reprojectPlan(model, { elevation: 4 });
  if (plan === null) return;            // degraded path is a valid outcome
  assert.ok(Array.isArray(plan.lines), "a plan exposes lines");
  assert.ok(plan.lines.length > 0, "a plan with walls produces lines");
});

test("an IFC with no building elements yields null rather than an empty crash",
  { skip: !process.env.ARCHIAGENT_IFC_EMPTY }, async () => {
  const model = await openIfc(process.env.ARCHIAGENT_IFC_EMPTY);
  assert.equal(await reprojectPlan(model, { elevation: 4 }), null);
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd analysis && node --test tests/reproject.test.mjs`
Expected: FAIL — `Cannot find module '../src/reproject.js'`.

- [ ] **Step 4: Implement against what Step 1 actually found**

`analysis/src/reproject.js`. The shape is fixed even though the body depends on
the spike: one `geometryAvailable()` probe that caches its answer, and one
`reprojectPlan()` that returns `null` on any unavailability.

```js
/**
 * Reproject the built model to a 2D plan, when the geometry tier can run here.
 *
 * @ifc-lite/geometry is worker- and browser-shaped and carries a .wasm asset.
 * Node 20 has no global Worker, so this may be unavailable in the worker
 * container. That is a supported outcome, not an error: the diff artifact is
 * still worth producing from the DXF underlay alone, and saying "not
 * reprojected" beats failing a job over a nicety.
 */
import { generateFloorPlan } from "@ifc-lite/drawing-2d";

let probed;

export async function geometryAvailable() {
  if (probed !== undefined) return probed;
  try {
    const geometry = await import("@ifc-lite/geometry");
    probed = typeof geometry.GeometryProcessor === "function";
  } catch {
    probed = false;
  }
  return probed;
}

export async function reprojectPlan(model, { elevation }) {
  if (!(await geometryAvailable())) return null;
  let meshes;
  try {
    meshes = await meshesFor(model);
  } catch {
    return null;
  }
  if (!meshes?.length) return null;
  const drawing = await generateFloorPlan(meshes, elevation, {
    includeHiddenLines: false, includeProjection: false, mergeLines: true, useGPU: false,
  });
  return { lines: drawing.lines ?? [] };
}

/** Fill in from the Step 1 findings; return [] when tessellation is impossible. */
async function meshesFor(model) {
  const { GeometryProcessor } = await import("@ifc-lite/geometry");
  const processor = new GeometryProcessor({ useWorker: false });
  return await processor.process(model.store);
}
```

`meshesFor` is the only speculative body in this plan, because its API cannot be
read off a `.d.ts` without running it. Step 1 replaces it with the real calls.
**If Step 1 shows the geometry tier needs a Worker or DOM**, delete `meshesFor`,
make `geometryAvailable()` return `false` unconditionally with a comment
recording what Step 1 found, keep both tests, and ledger the ruling. The
underlay-only artifact is then Phase 5's delivered scope and reprojection moves
to its own phase.

- [ ] **Step 5: Run the tests**

Run: `cd analysis && ARCHIAGENT_IFC="$PWD/../../wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc" node --test tests/reproject.test.mjs`
Expected: PASS 3/3 (one may skip). Whether `reprojectPlan` returned a plan or
`null` goes in the ledger either way — it is Phase 5's headline result.

- [ ] **Step 6: Commit**

```bash
git add analysis/src/reproject.js analysis/tests/reproject.test.mjs
git commit -m "feat(analysis): reproject the model to a plan when geometry can run headlessly"
```

---

## Task 6: The CLI and the worker wiring

**Files:**
- Create: `archiagent-viewer/analysis/bin/analyse.mjs`
- Create: `archiagent-viewer/analysis/tests/cli.test.mjs`
- Create: `archiagent-viewer/service/archiagent_service/analysis.py`
- Create: `archiagent-viewer/service/tests/test_analysis.py`
- Modify: `archiagent-viewer/service/archiagent_service/worker.py`
- Modify: `archiagent-viewer/README.md`

**Interfaces:**
- Consumes: `openIfc`, `parseIdsDocument`, `runIds`, `underlayFromDxf`, `compareExtents`, `renderDiffSvg`, `reprojectPlan`.
- Produces: the CLI contract below, and Python `build_analysis_command(...)`, `run_analysis(...)`, `ANALYSIS_ARTIFACTS`.

CLI contract:

```
archiagent-analyse --ifc PATH [--ids PATH] [--dxf PATH] --out DIR [--elevation FT]
```

Exit codes — the taxonomy the Global Constraints fix:
`0` analysis complete and every gate passed; `2` usage error (bad arguments, unreadable IDS);
`3` a gate failed (an IDS specification failed, or matched nothing); `4` the IFC was unreadable.

Writes `plan.ids.json` when `--ids` is given, `plan.diff.svg` when `--dxf` is given.

- [ ] **Step 1: Write the failing CLI test**

`analysis/tests/cli.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const run = promisify(execFile);
const BIN = new URL("../bin/analyse.mjs", import.meta.url).pathname;
const IFC = process.env.ARCHIAGENT_IFC;

async function cli(args) {
  try {
    const { stdout } = await run(process.execPath, [BIN, ...args]);
    return { code: 0, stdout };
  } catch (e) {
    return { code: e.code, stdout: e.stdout ?? "", stderr: e.stderr ?? "" };
  }
}

const IDS = (entity) => `<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS"><info><title>t</title></info>
<specifications><specification name="s" ifcVersion="IFC4" minOccurs="1" maxOccurs="unbounded">
<applicability minOccurs="1" maxOccurs="unbounded"><entity><name><simpleValue>${entity}</simpleValue></name></entity></applicability>
<requirements><property dataType="IFCLABEL" cardinality="required">
<propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
<baseName><simpleValue>SourceLayer</simpleValue></baseName></property></requirements>
</specification></specifications></ids>`;

test("no arguments is a usage error", async () => {
  assert.equal((await cli([])).code, 2);
});

test("an unreadable IFC exits 4", async () => {
  const dir = mkdtempSync(join(tmpdir(), "cli-"));
  const bad = join(dir, "bad.ifc");
  writeFileSync(bad, "not step");
  assert.equal((await cli(["--ifc", bad, "--out", dir])).code, 4);
});

test("a passing IDS exits 0 and writes plan.ids.json", { skip: !IFC }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "cli-"));
  const ids = join(dir, "spec.ids");
  writeFileSync(ids, IDS("IFCWALLSTANDARDCASE"));
  const r = await cli(["--ifc", IFC, "--ids", ids, "--out", dir]);
  assert.equal(r.code, 0);
  const report = JSON.parse(readFileSync(join(dir, "plan.ids.json"), "utf8"));
  assert.equal(report.status, "passed");
  assert.ok(report.report.summary.totalEntitiesChecked > 0);
});

test("an IDS that matches nothing exits 3 and still writes the report",
     { skip: !IFC }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "cli-"));
  const ids = join(dir, "spec.ids");
  writeFileSync(ids, IDS("IFCWALL"));
  const r = await cli(["--ifc", IFC, "--ids", ids, "--out", dir]);
  assert.equal(r.code, 3);
  const report = JSON.parse(readFileSync(join(dir, "plan.ids.json"), "utf8"));
  assert.equal(report.status, "matched-nothing");
  assert.deepEqual(report.coverage.matchedNothing, ["s"]);
});

test("a malformed IDS exits 2 naming the file", { skip: !IFC }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "cli-"));
  const ids = join(dir, "spec.ids");
  writeFileSync(ids, "<html>nope</html>");
  const r = await cli(["--ifc", IFC, "--ids", ids, "--out", dir]);
  assert.equal(r.code, 2);
  assert.match(r.stderr, /spec\.ids/);
});

test("--dxf writes a well-formed plan.diff.svg", { skip: !IFC || !process.env.ARCHIAGENT_DXF },
     async () => {
  const dir = mkdtempSync(join(tmpdir(), "cli-"));
  const r = await cli(["--ifc", IFC, "--dxf", process.env.ARCHIAGENT_DXF, "--out", dir]);
  assert.ok(r.code === 0 || r.code === 3);
  assert.ok(existsSync(join(dir, "plan.diff.svg")));
  assert.match(readFileSync(join(dir, "plan.diff.svg"), "utf8"), /<\/svg>\s*$/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd analysis && node --test tests/cli.test.mjs`
Expected: FAIL — the binary does not exist.

- [ ] **Step 3: Implement the CLI**

`analysis/bin/analyse.mjs`:

```js
#!/usr/bin/env node
/**
 * Tier 3 entry point. The only module here with side effects.
 *
 * Exit codes ARE the error taxonomy the worker maps (spec §5.2's rule, applied
 * to this tier): 0 clean, 2 usage, 3 gate failed, 4 IFC unreadable.
 */
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { openIfc, IfcUnreadableError } from "../src/model.js";
import { parseIdsDocument, runIds, IdsInputError } from "../src/ids.js";
import { underlayFromDxf, compareExtents, renderDiffSvg } from "../src/diff.js";
import { reprojectPlan } from "../src/reproject.js";

const USAGE = "usage: archiagent-analyse --ifc PATH [--ids PATH] [--dxf PATH] --out DIR [--elevation FT]";

function fail(code, message) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

let values;
try {
  ({ values } = parseArgs({ options: {
    ifc: { type: "string" }, ids: { type: "string" }, dxf: { type: "string" },
    out: { type: "string" }, elevation: { type: "string", default: "4" },
  } }));
} catch (error) {
  fail(2, `${error.message}\n${USAGE}`);
}
if (!values.ifc || !values.out) fail(2, USAGE);
if (!values.ids && !values.dxf) fail(2, `nothing to do: pass --ids and/or --dxf\n${USAGE}`);

let model;
try {
  model = await openIfc(values.ifc);
} catch (error) {
  fail(error instanceof IfcUnreadableError ? 4 : 4, `cannot read ${values.ifc}: ${error.message}`);
}

await mkdir(values.out, { recursive: true });
let gate = 0;

if (values.ids) {
  let document;
  try {
    ({ document } = parseIdsDocument(await readFile(values.ids, "utf8")));
  } catch (error) {
    fail(error instanceof IdsInputError ? 2 : 2, `${values.ids}: ${error.message}`);
  }
  const result = await runIds(model, document);
  await writeFile(join(values.out, "plan.ids.json"), JSON.stringify(result, null, 2));
  process.stdout.write(`ids: ${result.status} `
    + `(${result.coverage.entitiesChecked} entities checked, `
    + `${result.coverage.matchedNothing.length} specifications matched nothing)\n`);
  if (result.status !== "passed") gate = 3;
}

if (values.dxf) {
  const underlay = underlayFromDxf(await readFile(values.dxf, "utf8"), "source.dxf");
  const plan = await reprojectPlan(model, { elevation: Number(values.elevation) });
  const bounds = plan?.bounds ?? null;
  const extents = compareExtents(bounds, underlay);
  await writeFile(join(values.out, "plan.diff.svg"), renderDiffSvg({ underlay, plan, extents }));
  process.stdout.write(`diff: ${extents.comparable ? "comparable" : extents.reason}`
    + `${plan ? "" : " (underlay only)"}\n`);
}

process.exit(gate);
```

- [ ] **Step 4: Run the CLI tests**

Run: `cd analysis && ARCHIAGENT_IFC="$PWD/../../wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc" node --test tests/cli.test.mjs`
Expected: PASS 6/6 (the `--dxf` test skips without `ARCHIAGENT_DXF`).

`plan?.bounds` does not exist yet — Task 5 returns `{ lines }` only. Either add
`bounds` there or compute it from `lines` here. Decide, do it in one place, and
ledger which. Do not leave both.

- [ ] **Step 5: Write the failing Python-side test**

`service/tests/test_analysis.py`:

```python
"""The worker runs Tier 3 as a subprocess, exactly as it runs the archiAgent CLI."""
import json
from pathlib import Path

import pytest

from archiagent_service.analysis import (
    ANALYSIS_ARTIFACTS, AnalysisResult, build_analysis_command, run_analysis,
)


def test_command_includes_only_the_inputs_that_exist(tmp_path):
    ifc = tmp_path / "plan.ifc"; ifc.write_text("x")
    cmd = build_analysis_command(ifc, out_dir=tmp_path, ids=None, dxf=None)
    assert "--ids" not in cmd and "--dxf" not in cmd
    assert str(ifc) in cmd


def test_command_passes_both_inputs_when_present(tmp_path):
    ifc = tmp_path / "plan.ifc"; ifc.write_text("x")
    ids = tmp_path / "spec.ids"; ids.write_text("<ids/>")
    dxf = tmp_path / "plan.dxf"; dxf.write_text("0")
    cmd = build_analysis_command(ifc, out_dir=tmp_path, ids=ids, dxf=dxf)
    assert cmd[cmd.index("--ids") + 1] == str(ids)
    assert cmd[cmd.index("--dxf") + 1] == str(dxf)


def test_exit_3_is_a_finding_not_a_crash(tmp_path, monkeypatch):
    """A failed IDS gate must not fail the job: the artifact is the deliverable."""
    monkeypatch.setattr("archiagent_service.analysis._run",
                        lambda cmd, timeout_s: (3, "ids: matched-nothing\n", ""))
    result = run_analysis(tmp_path / "plan.ifc", out_dir=tmp_path)
    assert isinstance(result, AnalysisResult)
    assert result.ok is True
    assert result.gate_failed is True


def test_exit_4_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("archiagent_service.analysis._run",
                        lambda cmd, timeout_s: (4, "", "cannot read plan.ifc"))
    result = run_analysis(tmp_path / "plan.ifc", out_dir=tmp_path)
    assert result.ok is False
    assert "cannot read" in result.detail


def test_artifacts_use_the_plan_stem():
    assert ANALYSIS_ARTIFACTS == ("plan.ids.json", "plan.diff.svg")
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd service && python -m pytest tests/test_analysis.py -q`
Expected: FAIL — `No module named 'archiagent_service.analysis'`.

- [ ] **Step 7: Implement the Python side**

`service/archiagent_service/analysis.py`:

```python
"""Run Tier 3 (Node) as a subprocess and collect its artifacts.

The API never imports ifc-lite, exactly as it never imports archiagent: the
process boundary contains WASM crashes and memory growth. Exit code 3 means a
gate found something, which is a result to store, not a job failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from archiagent_service.config import settings

#: Written beside plan.ifc, following the §3 working stem.
ANALYSIS_ARTIFACTS = ("plan.ids.json", "plan.diff.svg")

ANALYSIS_BIN = Path(__file__).resolve().parents[2] / "analysis" / "bin" / "analyse.mjs"


@dataclass(frozen=True)
class AnalysisResult:
    ok: bool
    gate_failed: bool
    detail: str


def analysis_available() -> bool:
    """Whether Tier 3 can run here -- node plus an installed analysis package."""
    return shutil.which("node") is not None and ANALYSIS_BIN.is_file()


def build_analysis_command(ifc: Path, *, out_dir: Path, ids: Path | None = None,
                           dxf: Path | None = None) -> list[str]:
    cmd = ["node", str(ANALYSIS_BIN), "--ifc", str(ifc), "--out", str(out_dir)]
    if ids is not None:
        cmd += ["--ids", str(ids)]
    if dxf is not None:
        cmd += ["--dxf", str(dxf)]
    return cmd


def _run(cmd: list[str], timeout_s: int) -> tuple[int, str, str]:
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    return done.returncode, done.stdout, done.stderr


def run_analysis(ifc: Path, *, out_dir: Path, ids: Path | None = None,
                 dxf: Path | None = None, timeout_s: int = 300) -> AnalysisResult:
    cmd = build_analysis_command(ifc, out_dir=out_dir, ids=ids, dxf=dxf)
    try:
        code, out, err = _run(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        return AnalysisResult(False, False, f"analysis timed out after {timeout_s}s")
    if code == 0:
        return AnalysisResult(True, False, out.strip())
    if code == 3:
        # A finding, not a fault. The report is the deliverable.
        return AnalysisResult(True, True, out.strip())
    return AnalysisResult(False, False, (err or out).strip()[:2000])
```

- [ ] **Step 8: Run the Python tests**

Run: `cd service && python -m pytest tests/test_analysis.py -q`
Expected: PASS 5/5.

- [ ] **Step 9: Call it from the worker, after the IFC exists**

In `service/archiagent_service/worker.py`, after `collect_artifacts` succeeds and
only when `analysis_available()`: run `run_analysis` with the job's `plan.ifc`,
the published `plan.dxf` when present, and the job's IDS input when present, then
publish whichever of `ANALYSIS_ARTIFACTS` exist. A failed analysis records a note
on the job and **does not** change job status — Tier 3 is additive (invariant I3).

- [ ] **Step 10: Run the whole service suite**

Run: `cd service && ARCHIAGENT_REQUIRE_SERVICES=1 python -m pytest -q`
Expected: the Phase 4 count plus the new tests, no failures. Docker must be up;
a vacuously green run with services down is what `ARCHIAGENT_REQUIRE_SERVICES=1`
exists to prevent.

- [ ] **Step 11: Assert the tier boundary (invariant I4)**

Add to `analysis/tests/cli.test.mjs` a test reading `analysis/package.json` and
`../package.json`, asserting no `@ifc-lite/*` key in the viewer's dependencies
and no `web-ifc`, `@thatopen/fragments`, `@ifc-lite/create` or
`@ifc-lite/mutations` key in the analysis package's. Run both suites.

- [ ] **Step 12: Update the READMEs and commit**

`archiagent-viewer/README.md` gains a Tier 3 paragraph; `analysis/README.md`
records the Task 5 outcome (reprojected, or underlay-only and why).

```bash
git add analysis/ service/ README.md
git commit -m "feat(analysis): run Tier 3 from the worker and publish its artifacts"
```

---

## Self-review notes

- **Spec coverage.** §2.2's `@ifc-lite/ids` bullet → Tasks 2, 3. Its `@ifc-lite/drawing-2d` bullet (`generateFloorPlan` + `importDxf` as a plan-vs-source diff) → Tasks 4, 5. Its `@ifc-lite/mcp` bullet is explicitly Phase 7 and out of scope. §3's artifact contract → Task 6's `ANALYSIS_ARTIFACTS`. §11's gate ("establishes the Node analysis tier that Phase 7 also needs") → Task 1's package plus Task 6's subprocess seam, which is what Phase 7's MCP server attaches to.
- **Invariants.** I1/I3/I4 are asserted by a test in Task 6 Step 11, not merely asserted in prose. I6 is respected by shipping no MCP server.
- **Type consistency.** `Model` (Task 1) is consumed unchanged by Tasks 3, 4, 5. `IdsResult` (Task 3) is what Task 6 serialises. The one known gap is `plan.bounds`, flagged inline at Task 6 Step 4 rather than left to be discovered.
- **The one speculative body** is `meshesFor` in Task 5, and it is labelled as such with an explicit instruction to replace it from Step 1's findings or delete it and degrade. Everything else is copied from a `.d.ts` or from the spike.
- **Not in scope, deliberately:** changing archiAgent's wall class (a Tier 1 decision — see finding 3), serving these artifacts in the viewer UI, and any IDS authoring UI. Phase 5 produces artifacts; presenting them is the next increment.
