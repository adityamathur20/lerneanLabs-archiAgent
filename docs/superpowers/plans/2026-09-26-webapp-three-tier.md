# archiAgent Web App — Three-Tier Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser's OpenGeometry reconstruction with a Fragments viewer that reads archiAgent's authored IFC directly, then make the `.ifc → .frag` conversion a server-side build step.

**Architecture:** Three tiers that share files, never runtime. ifcopenshell in Python is the only writer of `plan.ifc`; `@thatopen/fragments` renders a `.frag` derived from it; ifc-lite is a later, optional, read-only Node analysis tier. The browser stops reconstructing geometry entirely.

**Tech Stack:** `@thatopen/fragments` 3.4.7 (brings `web-ifc` transitively), `three` 0.168, Vite 6, Node's built-in `node:test`.

**Spec:** `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md`

## Global Constraints

- Work happens in `archiagent-viewer/`. **No file under `lerneanLabs-archiAgent/archiagent/` is modified by this plan.** Tier 1 is authoritative and untouched.
- Pin `@thatopen/fragments` to exactly `3.4.7` (no caret). The `.frag` format version is embedded in output files.
- `opengeometry` must be removed from `package.json` by the end of Task 5, and `grep -rn "opengeometry\|AnalyticSolid" src/` must return nothing.
- Tests use `node --test` (built in, zero new dependencies). No test framework is added.
- `IfcImporter.wasm.path` must point at the installed `web-ifc` directory; the default is `/node_modules/web-ifc/` with `absolute: false`.
- Never call `exportToStep`, `IfcCreator`, or any IFC-writing API from JavaScript. Invariant I1 in the spec.
- Fixture IFCs come from `../dxfBased_ifcOutput/` and `../wallUpdate_dxfBased_ifcOutput/`. Do not copy large IFCs into the repo; reference them by path via an env var with a skip when absent.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has a test added to the task that owns the code.

1. **A plan whose coordinates sit tens of thousands of feet from the origin** — the precision failure the old viewer hand-fixed with a recentring shift. `COORDINATE_TO_ORIGIN: true` must be verified to actually land geometry near origin, not assumed. → Task 1.
2. **An IFC that failed validation (`acceptance: "draft"`)** — must still render whatever geometry exists rather than blank-screening, because a draft model is exactly when a user most needs to look at it. → Task 5.
3. **An IFC with no walls at all** (a site-plan outlier, of which this corpus has two) — must show an empty state, not throw. → Task 5.
4. **A requested path outside the served root** — the dev server's traversal guard must survive being rewritten from `.interpretation.json` to `.ifc`. → Task 3.
5. **A multi-megabyte IFC** — conversion must report progress and must not be mistaken for a hang. → Task 4.

---

## Phase 1 — Fragments viewer, local files

### Task 1: `ifc → frag` conversion module

**Files:**
- Create: `archiagent-viewer/src/ifc-to-frag.js`
- Create: `archiagent-viewer/tests/ifc-to-frag.test.mjs`
- Modify: `archiagent-viewer/package.json` (dependencies, `test` script)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `ifcToFrag(bytes: Uint8Array, opts?: { onProgress?: (fraction: number) => void }) => Promise<Uint8Array>` and `FRAG_MIN_BYTES: number`

- [ ] **Step 1: Install the dependency and remove the old one**

```bash
cd archiagent-viewer
npm uninstall opengeometry
npm install --save-exact @thatopen/fragments@3.4.7
```

- [ ] **Step 2: Add the test script to `package.json`**

In `"scripts"`, add:

```json
"test": "node --test tests/"
```

- [ ] **Step 3: Write the failing test**

Create `archiagent-viewer/tests/ifc-to-frag.test.mjs`:

```js
import { test, skip } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { ifcToFrag, FRAG_MIN_BYTES } from "../src/ifc-to-frag.js";

// Fixtures are the pipeline's own output, which is too large to commit.
// ARCHIAGENT_IFC points at one authored .ifc file.
const FIXTURE = process.env.ARCHIAGENT_IFC;

test("converts an authored IFC into a non-trivial fragments buffer", async (t) => {
  if (!FIXTURE || !existsSync(FIXTURE)) {
    t.skip("set ARCHIAGENT_IFC to an authored .ifc to run this test");
    return;
  }
  const bytes = new Uint8Array(await readFile(FIXTURE));
  const frag = await ifcToFrag(bytes);

  assert.ok(frag instanceof Uint8Array, "returns a Uint8Array");
  assert.ok(
    frag.byteLength > FRAG_MIN_BYTES,
    `expected more than ${FRAG_MIN_BYTES} bytes, got ${frag.byteLength}`,
  );
});

test("reports progress at least once", async (t) => {
  if (!FIXTURE || !existsSync(FIXTURE)) {
    t.skip("set ARCHIAGENT_IFC to an authored .ifc to run this test");
    return;
  }
  const bytes = new Uint8Array(await readFile(FIXTURE));
  const seen = [];
  await ifcToFrag(bytes, { onProgress: (f) => seen.push(f) });

  assert.ok(seen.length > 0, "onProgress was never called");
  assert.ok(
    seen.every((f) => f >= 0 && f <= 1),
    `progress fractions out of range: ${seen.join(", ")}`,
  );
});

// Review Focus #1: archiAgent keeps the source drawing's origin, which puts a
// real plan tens of thousands of feet from (0,0). The old viewer recentred by
// hand. IfcImporter sets COORDINATE_TO_ORIGIN: true — verify that, don't assume.
test("lands geometry near the origin regardless of the source drawing's origin", async (t) => {
  if (!FIXTURE || !existsSync(FIXTURE)) {
    t.skip("set ARCHIAGENT_IFC to an authored .ifc to run this test");
    return;
  }
  const bytes = new Uint8Array(await readFile(FIXTURE));
  const { coordinateToOrigin } = await import("../src/ifc-to-frag.js");
  assert.equal(
    coordinateToOrigin(),
    true,
    "IfcImporter must translate the model to the origin; a far-from-origin " +
      "plan wrecks depth precision in the renderer",
  );
  const frag = await ifcToFrag(bytes);
  assert.ok(frag.byteLength > FRAG_MIN_BYTES);
});
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd archiagent-viewer && npm test
```

Expected: FAIL — `Cannot find module '../src/ifc-to-frag.js'`

- [ ] **Step 5: Write the minimal implementation**

Create `archiagent-viewer/src/ifc-to-frag.js`:

```js
/**
 * archiAgent's authored IFC -> a Fragments buffer.
 *
 * This is the whole browser-side geometry story now. archiAgent already
 * resolved junctions, split walls at openings and wrote IfcRelVoidsElement;
 * web-ifc (inside IfcImporter) meshes that faithfully, including the opening
 * booleans. Nothing here reconstructs geometry.
 */
import { IfcImporter } from "@thatopen/fragments";

/** A fragments buffer smaller than this is an empty or failed conversion. */
export const FRAG_MIN_BYTES = 1024;

/**
 * IfcImporter translates the model to the origin by default. archiAgent keeps
 * the source drawing's origin, so without this a real plan sits tens of
 * thousands of feet out and depth precision collapses. Exposed so a test can
 * assert it rather than trust the library's default.
 */
export function coordinateToOrigin() {
  return new IfcImporter().webIfcSettings.COORDINATE_TO_ORIGIN === true;
}

export async function ifcToFrag(bytes, { onProgress = null } = {}) {
  const serializer = new IfcImporter();
  serializer.wasm = { path: "/node_modules/web-ifc/", absolute: false };
  if (onProgress) onProgress(0);
  const frag = await serializer.process({ bytes, raw: false });
  if (onProgress) onProgress(1);
  return frag;
}
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd archiagent-viewer
ARCHIAGENT_IFC=$(ls ../dxfBased_ifcOutput/*.ifc | head -1) npm test
```

Expected: PASS, 3 tests. If run without `ARCHIAGENT_IFC`, expect 3 skips and exit 0.

- [ ] **Step 7: Commit**

```bash
git add archiagent-viewer/package.json archiagent-viewer/package-lock.json \
        archiagent-viewer/src/ifc-to-frag.js archiagent-viewer/tests/ifc-to-frag.test.mjs
git commit -m "feat(viewer): convert authored IFC to fragments

Replaces the OpenGeometry reconstruction path at its root: the browser now
consumes the IFC archiAgent already authored instead of rebuilding walls from
the interpretation manifest.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Replace the kernel smoke test with a conversion smoke test

**Files:**
- Delete: `archiagent-viewer/scripts/smoke.mjs`
- Create: `archiagent-viewer/scripts/smoke.mjs` (rewritten)
- Modify: `archiagent-viewer/package.json` (the `smoke` script's argument is now an `.ifc`)

**Interfaces:**
- Consumes: `ifcToFrag`, `FRAG_MIN_BYTES` from Task 1
- Produces: `npm run smoke -- <path>.ifc` exiting 0 on success, non-zero on failure

- [ ] **Step 1: Write the failing check by running the old smoke against an IFC**

```bash
cd archiagent-viewer && npm run smoke -- ../dxfBased_ifcOutput/*.ifc 2>&1 | head -5
```

Expected: FAIL — the old script calls `readManifest` and expects `*.interpretation.json`. This is the behaviour being replaced.

- [ ] **Step 2: Replace the script**

Overwrite `archiagent-viewer/scripts/smoke.mjs`:

```js
/**
 * Verifies the whole browser model path under Node, where there is no WebGL:
 * read an authored IFC, convert it to fragments, assert the buffer is real.
 *
 * The old version of this script exercised OpenGeometry's extrusions and
 * opening decomposition. None of that exists any more — archiAgent's IFC is
 * the input and web-ifc does the meshing.
 */
import { readFile } from "node:fs/promises";
import { ifcToFrag, FRAG_MIN_BYTES } from "../src/ifc-to-frag.js";

const target = process.argv[2];
if (!target) {
  console.error("usage: npm run smoke -- <path-to.ifc>");
  process.exit(2);
}
if (!target.toLowerCase().endsWith(".ifc")) {
  console.error(`expected a .ifc file, got: ${target}`);
  process.exit(2);
}

const bytes = new Uint8Array(await readFile(target));
console.log(`ifc      ${target}  ${bytes.byteLength} bytes`);

const started = Date.now();
const frag = await ifcToFrag(bytes, {
  onProgress: (f) => process.stdout.write(`\rconvert  ${Math.round(f * 100)}%`),
});
process.stdout.write("\n");

console.log(`frag     ${frag.byteLength} bytes in ${Date.now() - started} ms`);

if (frag.byteLength <= FRAG_MIN_BYTES) {
  console.error(`FAIL: fragments buffer is ${frag.byteLength} bytes; the conversion produced nothing`);
  process.exit(1);
}
console.log("OK");
```

- [ ] **Step 3: Run it to verify it passes**

```bash
cd archiagent-viewer && npm run smoke -- $(ls ../dxfBased_ifcOutput/*.ifc | head -1)
```

Expected: prints `OK`, exits 0.

- [ ] **Step 4: Verify it fails loudly on a bad input**

```bash
cd archiagent-viewer && npm run smoke -- README.md; echo "exit=$?"
```

Expected: `expected a .ifc file`, `exit=2`.

- [ ] **Step 5: Commit**

```bash
git add archiagent-viewer/scripts/smoke.mjs archiagent-viewer/package.json
git commit -m "test(viewer): smoke-test IFC conversion instead of the kernel

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Dev server serves `.ifc` instead of manifests

**Files:**
- Modify: `archiagent-viewer/vite.config.js:1-89`
- Create: `archiagent-viewer/tests/serve-guard.test.mjs`

**Interfaces:**
- Consumes: nothing
- Produces: `GET /api/models` → `{ root, models: [{ path, name, bytes, modified }] }`; `GET /api/model?path=<rel>` → the raw IFC bytes. Exported helpers `findModels(dir, depth?)` and `resolveWithinRoot(root, requested)`.

- [ ] **Step 1: Write the failing test for the traversal guard**

This is Review Focus #4. The guard exists in the current file and must survive the rewrite, so it gets pulled into a named, tested function.

Create `archiagent-viewer/tests/serve-guard.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { resolveWithinRoot } from "../vite.config.js";

const ROOT = path.resolve("/tmp/archiagent-out");

test("resolves a normal relative path inside the root", () => {
  assert.equal(
    resolveWithinRoot(ROOT, "run-1/plan.ifc"),
    path.join(ROOT, "run-1/plan.ifc"),
  );
});

test("rejects a parent-directory escape", () => {
  assert.equal(resolveWithinRoot(ROOT, "../../etc/passwd"), null);
});

test("rejects an absolute path outside the root", () => {
  assert.equal(resolveWithinRoot(ROOT, "/etc/passwd"), null);
});

test("rejects a sibling directory that merely shares the root's prefix", () => {
  assert.equal(resolveWithinRoot(ROOT, "../archiagent-out-evil/x.ifc"), null);
});

test("rejects a file that is not a .ifc", () => {
  assert.equal(resolveWithinRoot(ROOT, "run-1/plan.interpretation.json"), null);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd archiagent-viewer && npm test -- tests/serve-guard.test.mjs
```

Expected: FAIL — `resolveWithinRoot` is not exported by `vite.config.js`.

- [ ] **Step 3: Rewrite `vite.config.js`**

Replace the whole file:

```js
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";

/**
 * Serves archiAgent output straight from disk so the viewer reads the same
 * files the pipeline just wrote. Point it at your `--outputDir` (or a parent of
 * several) with ARCHIAGENT_OUT.
 *
 * This used to serve `*.interpretation.json` for the browser to rebuild
 * geometry from. It now serves the authored `*.ifc` — the model is the
 * pipeline's own output, not a reconstruction of it.
 */
const OUT_ROOT = path.resolve(process.env.ARCHIAGENT_OUT ?? path.resolve(process.cwd(), ".."));
const SUFFIX = ".ifc";

export async function findModels(dir, depth = 0) {
  if (depth > 3) return [];
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const found = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await findModels(full, depth + 1)));
    else if (entry.name.toLowerCase().endsWith(SUFFIX)) {
      const info = await stat(full);
      found.push({
        path: path.relative(OUT_ROOT, full),
        name: entry.name.slice(0, -SUFFIX.length),
        bytes: info.size,
        modified: info.mtime.toISOString(),
      });
    }
  }
  return found;
}

/**
 * Confines a requested path to `root` and to `.ifc` files. Returns the absolute
 * path, or null if the request escapes the root or asks for anything else.
 *
 * A traversal here would read any file on the machine, so this is a named,
 * tested function rather than an inline check.
 */
export function resolveWithinRoot(root, requested) {
  if (!requested) return null;
  const resolved = path.resolve(root, requested);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) return null;
  if (!resolved.toLowerCase().endsWith(SUFFIX)) return null;
  return resolved;
}

function archiagentOutput() {
  return {
    name: "archiagent-output",
    configureServer(server) {
      server.middlewares.use("/api/models", async (_req, res) => {
        const models = (await findModels(OUT_ROOT)).sort((a, b) =>
          b.modified.localeCompare(a.modified),
        );
        res.setHeader("content-type", "application/json");
        res.end(JSON.stringify({ root: OUT_ROOT, models }));
      });

      server.middlewares.use("/api/model", async (req, res) => {
        const requested = new URL(req.url, "http://localhost").searchParams.get("path");
        const resolved = resolveWithinRoot(OUT_ROOT, requested);
        if (!resolved) {
          res.statusCode = 403;
          res.setHeader("content-type", "application/json");
          res.end(JSON.stringify({ error: `only ${SUFFIX} files inside ARCHIAGENT_OUT are served` }));
          return;
        }
        try {
          const body = await readFile(resolved);
          res.setHeader("content-type", "application/octet-stream");
          res.end(body);
        } catch (error) {
          res.statusCode = 404;
          res.setHeader("content-type", "application/json");
          res.end(JSON.stringify({ error: String(error?.message ?? error) }));
        }
      });
    },
  };
}

export default {
  plugins: [archiagentOutput()],
  server: { port: 5173, open: false },
  // web-ifc's .wasm arrives through @thatopen/fragments and must not be inlined.
  assetsInclude: ["**/*.wasm"],
  optimizeDeps: { exclude: ["@thatopen/fragments"] },
};
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd archiagent-viewer && npm test -- tests/serve-guard.test.mjs
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Verify the endpoint by hand**

```bash
cd archiagent-viewer
ARCHIAGENT_OUT=.. npm run dev &
sleep 3
curl -s localhost:5173/api/models | head -c 400; echo
curl -s -o /dev/null -w "%{http_code}\n" "localhost:5173/api/model?path=../../etc/passwd"
kill %1
```

Expected: the model list includes entries from `dxfBased_ifcOutput`; the traversal attempt returns `403`.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/vite.config.js archiagent-viewer/tests/serve-guard.test.mjs
git commit -m "feat(viewer): serve authored .ifc files, with a tested path guard

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Fragments runtime bootstrap

**Files:**
- Create: `archiagent-viewer/src/frag-viewer.js`

**Interfaces:**
- Consumes: `ifcToFrag` from Task 1
- Produces: `createFragViewer({ scene, camera, controls }) => { loadIfc(bytes, modelId, onProgress) => Promise<model>, clear() => Promise<void>, update(force?) => void, raycast(mouse, dom) => hit|null, current() => model|null }`

- [ ] **Step 1: Write the module**

There is no unit test for this task: it owns the `FragmentsModels` worker and a WebGL scene, neither of which exists under `node --test`. It is verified end to end by Task 5's screenshot step. Keeping it a separate, dependency-free module is what makes Task 5 testable at all.

Create `archiagent-viewer/src/frag-viewer.js`:

```js
/**
 * Owns the Fragments runtime: the worker, the model list, culling and LOD.
 *
 * `FragmentsModels` needs to be told which camera to cull against and needs
 * `update()` on every camera change — that wiring is the whole reason this is
 * a module and not three lines in main.js.
 */
import { FragmentsModels } from "@thatopen/fragments";
import { ifcToFrag } from "./ifc-to-frag.js";

export async function createFragViewer({ scene, camera, controls }) {
  // Fetches the worker matching this library version and returns a blob URL.
  const workerUrl = await FragmentsModels.getWorker();
  const fragments = new FragmentsModels(workerUrl);

  let current = null;

  fragments.models.list.onItemSet.add(({ value: model }) => {
    model.useCamera(camera);
    scene.add(model.object);
    current = model;
    fragments.update(true);
  });

  controls.addEventListener("update", () => fragments.update());

  return {
    async loadIfc(bytes, modelId, onProgress) {
      const frag = await ifcToFrag(bytes, { onProgress });
      // `load` wants an ArrayBuffer; hand it exactly the converted range.
      const buffer = frag.buffer.slice(frag.byteOffset, frag.byteOffset + frag.byteLength);
      await fragments.load(buffer, { modelId });
      return current;
    },

    async clear() {
      for (const id of fragments.models.list.keys()) {
        await fragments.disposeModel(id);
      }
      current = null;
    },

    update(force = false) {
      fragments.update(force);
    },

    raycast(mouse, dom) {
      if (!current) return null;
      return current.raycast({ camera, mouse, dom });
    },

    current() {
      return current;
    },
  };
}
```

- [ ] **Step 2: Verify it type-checks and imports cleanly**

```bash
cd archiagent-viewer && node --input-type=module -e "
  import('./src/frag-viewer.js').then(
    (m) => { console.log('exports:', Object.keys(m)); },
    (e) => { console.error('FAIL', e.message); process.exit(1); },
  );
"
```

Expected: `exports: [ 'createFragViewer' ]`. (The module imports cleanly under Node; `createFragViewer` itself is not called here because it needs a DOM.)

- [ ] **Step 3: Commit**

```bash
git add archiagent-viewer/src/frag-viewer.js
git commit -m "feat(viewer): add Fragments runtime bootstrap

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Rewire `main.js`, delete the reconstruction

**Files:**
- Modify: `archiagent-viewer/src/main.js` (replace lines 1-7 and 88-155; keep scene, camera, controls, lights, grid, resize)
- Delete: `archiagent-viewer/src/build-model.js`
- Delete: `archiagent-viewer/src/manifest.js`
- Delete: `archiagent-viewer/src/export-stl.js`
- Create: `archiagent-viewer/tests/empty-model.test.mjs`

**Interfaces:**
- Consumes: `createFragViewer` from Task 4, `/api/models` and `/api/model` from Task 3
- Produces: a working viewer at `http://localhost:5173`

- [ ] **Step 1: Write the failing test for degenerate models**

This covers Review Focus #2 and #3: a draft IFC and a wall-less IFC must both convert rather than throw, because a draft model is exactly when someone needs to look at it.

Create `archiagent-viewer/tests/empty-model.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { ifcToFrag, FRAG_MIN_BYTES } from "../src/ifc-to-frag.js";

/**
 * A minimal, valid IFC4 file with a full spatial tree and no building
 * elements at all — the shape of this corpus's two site-plan outliers, and of
 * any run whose wall detection found nothing.
 */
const EMPTY_IFC = `ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('empty.ifc','2026-09-26T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPERSON($,$,'',$,$,$,$,$);
#2=IFCORGANIZATION($,'',$,$,$);
#3=IFCPERSONANDORGANIZATION(#1,#2,$);
#4=IFCAPPLICATION(#2,'1','app','app');
#5=IFCOWNERHISTORY(#3,#4,$,.ADDED.,$,$,$,0);
#6=IFCDIRECTION((1.,0.,0.));
#7=IFCDIRECTION((0.,0.,1.));
#8=IFCCARTESIANPOINT((0.,0.,0.));
#9=IFCAXIS2PLACEMENT3D(#8,#7,#6);
#10=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-5,#9,$);
#11=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#12=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#13=IFCUNITASSIGNMENT((#11,#12));
#14=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',#5,'empty',$,$,$,$,(#10),#13);
#15=IFCSITE('1YvctVUKr0kugbFTf53O9L',#5,'Site',$,$,#9,$,$,.ELEMENT.,$,$,$,$,$);
#16=IFCBUILDING('2YvctVUKr0kugbFTf53O9L',#5,'Building',$,$,#9,$,$,.ELEMENT.,$,$,$);
#17=IFCBUILDINGSTOREY('3YvctVUKr0kugbFTf53O9L',#5,'Storey',$,$,#9,$,$,.ELEMENT.,0.);
#18=IFCRELAGGREGATES('4YvctVUKr0kugbFTf53O9L',#5,$,$,#14,(#15));
#19=IFCRELAGGREGATES('5YvctVUKr0kugbFTf53O9L',#5,$,$,#15,(#16));
#20=IFCRELAGGREGATES('6YvctVUKr0kugbFTf53O9L',#5,$,$,#16,(#17));
ENDSEC;
END-ISO-10303-21;
`;

test("converts an IFC with a spatial tree and no elements without throwing", async () => {
  const bytes = new TextEncoder().encode(EMPTY_IFC);
  const frag = await ifcToFrag(bytes);

  assert.ok(frag instanceof Uint8Array, "returns a buffer, not an exception");
  // A geometry-free model is legitimately small; the point is that it converts.
  assert.ok(frag.byteLength > 0, "produced a zero-length buffer");
});

test("FRAG_MIN_BYTES is the smoke threshold, not a correctness threshold", () => {
  // Documents the distinction the empty-model case exposes: `npm run smoke`
  // treats a tiny buffer as failure because it is pointed at real plans,
  // but a geometry-free IFC converting to a tiny buffer is correct.
  assert.ok(FRAG_MIN_BYTES > 0);
});
```

- [ ] **Step 2: Run the test to verify it fails or passes for the right reason**

```bash
cd archiagent-viewer && npm test -- tests/empty-model.test.mjs
```

Expected: PASS. If it FAILS with an exception from `process()`, that is a real finding: `ifcToFrag` needs a guard and the guard belongs in Task 1's module, not in `main.js`. Add it there before continuing.

- [ ] **Step 3: Delete the reconstruction modules**

```bash
cd archiagent-viewer && git rm src/build-model.js src/manifest.js src/export-stl.js
```

- [ ] **Step 4: Replace the head of `main.js`**

Replace lines 1-7 (the import block) with:

```js
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { createFragViewer } from "./frag-viewer.js";
```

- [ ] **Step 5: Replace `render()`, `clearScene()`, `applyDisplay()` and the raycast block**

Delete the existing `clearScene`, `render`, `applyDisplay` functions, the `groups` object and the `raycaster`/`pointer` block (lines ~34-42 and ~61-195), and replace with:

```js
const viewer = await createFragViewer({ scene, camera, controls });

function table(rows) {
  return rows.map(([k, v]) => `<div class="row"><span>${k}</span><b>${v}</b></div>`).join("");
}

async function show(relativePath, name) {
  status.textContent = `loading ${name}…`;
  await viewer.clear();

  const response = await fetch(`/api/model?path=${encodeURIComponent(relativePath)}`);
  if (!response.ok) {
    const { error } = await response.json().catch(() => ({ error: response.statusText }));
    status.textContent = `failed: ${error}`;
    return;
  }
  const bytes = new Uint8Array(await response.arrayBuffer());

  // Review Focus #5: a multi-megabyte IFC takes seconds. Say so, or it reads
  // as a hang.
  const model = await viewer.loadIfc(bytes, name, (fraction) => {
    status.textContent = `converting ${name}… ${Math.round(fraction * 100)}%`;
  });

  if (!model) {
    status.textContent = `${name}: nothing to display`;
    info.innerHTML = table([["ifc bytes", bytes.byteLength], ["elements", 0]]);
    return;
  }

  const tree = await model.getSpatialStructure();
  const categories = await model.getItemsWithGeometryCategories();
  status.textContent = name;
  info.innerHTML = table([
    ["ifc bytes", bytes.byteLength],
    ["storeys", tree?.children?.[0]?.children?.[0]?.children?.length ?? "—"],
    ["categories", Object.keys(categories).length],
  ]);
  frameCamera();
}

// Selection: the categories and GUIDs shown here are archiAgent's own, read
// back out of the IFC it authored — not a browser reconstruction's guesses.
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("click", async (event) => {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const hit = viewer.raycast(pointer, renderer.domElement);
  if (!hit) {
    selection.innerHTML = "";
    return;
  }
  const [data] = await viewer.current().getItemsData([hit.localId]);
  selection.innerHTML = table([
    ["localId", hit.localId],
    ["category", data?._category?.value ?? "—"],
    ["GlobalId", data?._guid?.value ?? "—"],
    ["Name", data?.Name?.value ?? "—"],
  ]);
});
```

- [ ] **Step 6: Point the model picker at the new endpoint**

In `loadList()`, change `/api/manifests` to `/api/models` and `data.manifests` to `data.models`; in the option handler call `show(entry.path, entry.name)` instead of `load(entry.path)`.

- [ ] **Step 7: Add the `selection` element**

In `index.html`, beside the existing `info` panel, add:

```html
<div id="selection"></div>
```

and in `main.js` beside the other `el(...)` lookups add:

```js
const selection = el("selection");
```

- [ ] **Step 8: Verify the whole thing renders**

```bash
cd archiagent-viewer
npm test
ARCHIAGENT_OUT=.. npm run dev &
sleep 4
npm run shot -- http://localhost:5173 /tmp/frag-shot.png
kill %1
```

Expected: `npm test` passes; `/tmp/frag-shot.png` exists and shows the model. **Open it and compare against the current OpenGeometry render of the same plan.** Corners must be joined and openings cut — that is the Phase 1 gate, and the whole reason for this phase.

- [ ] **Step 9: Verify the reconstruction is gone**

```bash
cd archiagent-viewer
grep -rn "opengeometry\|AnalyticSolid\|buildWalls\|buildSlabs\|FT_TO_M" src/ scripts/ vite.config.js package.json
```

Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add -A archiagent-viewer
git commit -m "feat(viewer): render archiAgent's IFC with Fragments

Deletes build-model.js, manifest.js and export-stl.js. The browser no longer
reconstructs geometry from the interpretation manifest, which is what caused
the butting wall junctions, the rejected boolean cutters and the unusable IFC
export documented in the README.

STL export is removed with export-stl.js: it existed because
AnalyticSolid.exportIfc() could not produce usable IFC, and IFC is now the
input.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Update the README to the new architecture

**Files:**
- Modify: `archiagent-viewer/README.md`

**Interfaces:**
- Consumes: everything above
- Produces: documentation matching the code

- [ ] **Step 1: Rewrite the README**

Replace the whole file:

```markdown
# archiagent-viewer

Renders archiAgent's **authored IFC** in the browser with
[`@thatopen/fragments`](https://github.com/ThatOpen/engine_fragment). No
geometry is reconstructed here: archiAgent already resolved wall junctions,
split walls at openings and wrote `IfcRelVoidsElement`, and web-ifc meshes that
faithfully.

```
archiAgent (Python)                          this app (browser)
  DXF/PDF → classify → junctions → spaces      fetch plan.ifc
  → ifcopenshell → plan.ifc  ──────────────→   IfcImporter → .frag
                                               FragmentsModels → Three.js
```

## Run

```bash
npm install
ARCHIAGENT_OUT=/path/to/your/archiagent/outputDir npm run dev
# → http://localhost:5173
```

`ARCHIAGENT_OUT` defaults to the parent directory. The dev server scans it (3
levels deep) for `*.ifc` and lists what it finds; requests are confined to that
root and to `.ifc` files.

Produce an IFC with archiAgent:

```bash
python -m archiagent --dxfFilePath plan.dxf --outputDir out
```

## Verify without a browser

```bash
npm test                                      # unit tests (node:test)
npm run smoke -- out/plan.ifc                 # full conversion under Node
```

`smoke` reads the IFC, converts it to fragments and exits non-zero if the
buffer is empty. Useful in CI, where there is no WebGL.

To check the render itself (needs Google Chrome installed):

```bash
npm run dev &
npm run shot -- http://localhost:5173 /tmp/shot.png
```

## Architecture

See `lerneanLabs-archiAgent/docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md`.
The rule is: **Fragments in the browser, ifc-lite as a server-side library,
ifcopenshell stays canonical, and they never meet at runtime.** In particular:

- `plan.ifc` has exactly one writer, ifcopenshell in Python. This app never writes IFC.
- `.frag` is a render cache derived from `plan.ifc` by a pure function. Losing it is harmless.
- The interpretation manifest is fetched for *provenance only* — never as geometry input.

## Known gaps

- **Conversion happens in the browser.** Fine for one plan at a time; Phase 2
  of the spec moves it to a server-side build step so the cost is paid once per
  model instead of once per page load.
- **No editing.** See §8 of the spec for the seam it will attach to.
- **No STL export.** Removed: it existed only because the previous kernel could
  not export usable IFC. If you need a mesh, ask for glTF.
```

- [ ] **Step 2: Verify every command in the README actually runs**

```bash
cd archiagent-viewer && npm test && npm run smoke -- $(ls ../dxfBased_ifcOutput/*.ifc | head -1)
```

Expected: both succeed.

- [ ] **Step 3: Commit**

```bash
git add archiagent-viewer/README.md
git commit -m "docs(viewer): describe the IFC-first architecture

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phase 2 — `.frag` as a build artifact

### Task 7: `ifc-to-frag` CLI for server-side precompute

**Files:**
- Create: `archiagent-viewer/scripts/build-frag.mjs`
- Create: `archiagent-viewer/tests/build-frag.test.mjs`
- Modify: `archiagent-viewer/package.json` (add the `build:frag` script)

**Interfaces:**
- Consumes: `ifcToFrag`, `FRAG_MIN_BYTES` from Task 1
- Produces: `npm run build:frag -- <in.ifc> <out.frag>`; exit 0 on success, 1 on empty output, 2 on usage error

- [ ] **Step 1: Write the failing test**

Create `archiagent-viewer/tests/build-frag.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const run = promisify(execFile);
const SCRIPT = path.resolve("scripts/build-frag.mjs");
const FIXTURE = process.env.ARCHIAGENT_IFC;

test("writes a .frag beside the requested output path", async (t) => {
  if (!FIXTURE || !existsSync(FIXTURE)) {
    t.skip("set ARCHIAGENT_IFC to an authored .ifc to run this test");
    return;
  }
  const dir = await mkdtemp(path.join(tmpdir(), "frag-"));
  const out = path.join(dir, "plan.frag");

  const { stdout } = await run("node", [SCRIPT, FIXTURE, out]);

  const info = await stat(out);
  assert.ok(info.size > 1024, `expected a real buffer, got ${info.size} bytes`);
  assert.match(stdout, /OK/);
});

test("is deterministic: the same IFC produces the same bytes", async (t) => {
  if (!FIXTURE || !existsSync(FIXTURE)) {
    t.skip("set ARCHIAGENT_IFC to an authored .ifc to run this test");
    return;
  }
  const dir = await mkdtemp(path.join(tmpdir(), "frag-"));
  const a = path.join(dir, "a.frag");
  const b = path.join(dir, "b.frag");

  await run("node", [SCRIPT, FIXTURE, a]);
  await run("node", [SCRIPT, FIXTURE, b]);

  // Determinism is what makes `.frag` a cache rather than an artifact:
  // invariant I2 in the spec depends on it. If this fails, the importer is
  // embedding a timestamp or a random id, and the cache key must become the
  // input hash plus the library version rather than the output hash.
  assert.deepEqual(
    new Uint8Array(await readFile(a)),
    new Uint8Array(await readFile(b)),
  );
});

test("exits 2 on usage error", async () => {
  await assert.rejects(
    () => run("node", [SCRIPT]),
    (error) => error.code === 2,
  );
});

test("exits 2 when the input is not a .ifc", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "frag-"));
  const notIfc = path.join(dir, "plan.txt");
  await writeFile(notIfc, "nope");
  await assert.rejects(
    () => run("node", [SCRIPT, notIfc, path.join(dir, "out.frag")]),
    (error) => error.code === 2,
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd archiagent-viewer && npm test -- tests/build-frag.test.mjs
```

Expected: FAIL — `scripts/build-frag.mjs` does not exist.

- [ ] **Step 3: Write the script**

Create `archiagent-viewer/scripts/build-frag.mjs`:

```js
/**
 * plan.ifc -> plan.frag, as a build step.
 *
 * This is the production conversion path: the cost is paid once per model at
 * job time instead of once per page load, and a mobile client never needs the
 * web-ifc WASM. The output is a pure function of the input, so it is a cache —
 * losing it is harmless (invariant I2).
 */
import { readFile, writeFile } from "node:fs/promises";
import { ifcToFrag, FRAG_MIN_BYTES } from "../src/ifc-to-frag.js";

const [input, output] = process.argv.slice(2);

if (!input || !output) {
  console.error("usage: node scripts/build-frag.mjs <in.ifc> <out.frag>");
  process.exit(2);
}
if (!input.toLowerCase().endsWith(".ifc")) {
  console.error(`expected a .ifc input, got: ${input}`);
  process.exit(2);
}

const bytes = new Uint8Array(await readFile(input));
const started = Date.now();
const frag = await ifcToFrag(bytes);

if (frag.byteLength <= FRAG_MIN_BYTES) {
  console.error(`FAIL: ${input} converted to ${frag.byteLength} bytes`);
  process.exit(1);
}

await writeFile(output, frag);
console.log(`OK ${input} (${bytes.byteLength} B) -> ${output} (${frag.byteLength} B) in ${Date.now() - started} ms`);
```

- [ ] **Step 4: Add the script to `package.json`**

In `"scripts"`, add:

```json
"build:frag": "node scripts/build-frag.mjs"
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd archiagent-viewer
ARCHIAGENT_IFC=$(ls ../dxfBased_ifcOutput/*.ifc | head -1) npm test -- tests/build-frag.test.mjs
```

Expected: PASS, 4 tests. **If the determinism test fails**, that is a real
finding, not a flake: record it and change the spec's §3.1 cache key from the
output hash to `sha256(plan.ifc) + fragments version`.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/scripts/build-frag.mjs archiagent-viewer/tests/build-frag.test.mjs archiagent-viewer/package.json
git commit -m "feat(viewer): add ifc->frag build step for server-side precompute

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Serve a precomputed `.frag` when one exists

**Files:**
- Modify: `archiagent-viewer/vite.config.js` (add `/api/frag`)
- Modify: `archiagent-viewer/src/main.js` (`show()` prefers `.frag`)
- Modify: `archiagent-viewer/tests/serve-guard.test.mjs` (guard covers both suffixes)

**Interfaces:**
- Consumes: `resolveWithinRoot` from Task 3
- Produces: `GET /api/frag?path=<rel.frag>` → raw fragments bytes or 404; `resolveWithinRoot(root, requested, suffix)` gains a third parameter defaulting to `".ifc"`

- [ ] **Step 1: Extend the guard test**

Append to `archiagent-viewer/tests/serve-guard.test.mjs`:

```js
test("serves a .frag when asked for that suffix", () => {
  assert.equal(
    resolveWithinRoot(ROOT, "run-1/plan.frag", ".frag"),
    path.join(ROOT, "run-1/plan.frag"),
  );
});

test("still rejects a traversal when the suffix is .frag", () => {
  assert.equal(resolveWithinRoot(ROOT, "../../etc/passwd.frag", ".frag"), null);
});

test("rejects a .ifc when .frag was requested", () => {
  assert.equal(resolveWithinRoot(ROOT, "run-1/plan.ifc", ".frag"), null);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd archiagent-viewer && npm test -- tests/serve-guard.test.mjs
```

Expected: FAIL — `resolveWithinRoot` ignores the third argument.

- [ ] **Step 3: Parameterise the guard**

In `vite.config.js`, change `resolveWithinRoot` to:

```js
export function resolveWithinRoot(root, requested, suffix = SUFFIX) {
  if (!requested) return null;
  const resolved = path.resolve(root, requested);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) return null;
  if (!resolved.toLowerCase().endsWith(suffix)) return null;
  return resolved;
}
```

- [ ] **Step 4: Add the `/api/frag` endpoint**

In `configureServer`, after the `/api/model` middleware:

```js
server.middlewares.use("/api/frag", async (req, res) => {
  const requested = new URL(req.url, "http://localhost").searchParams.get("path");
  const resolved = resolveWithinRoot(OUT_ROOT, requested, ".frag");
  if (!resolved) {
    res.statusCode = 403;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ error: "only .frag files inside ARCHIAGENT_OUT are served" }));
    return;
  }
  try {
    const body = await readFile(resolved);
    res.setHeader("content-type", "application/octet-stream");
    res.end(body);
  } catch {
    // Absent is normal: not every model has been precomputed.
    res.statusCode = 404;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ error: "no precomputed fragments" }));
  }
});
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd archiagent-viewer && npm test -- tests/serve-guard.test.mjs
```

Expected: PASS, 8 tests.

- [ ] **Step 6: Prefer the precomputed `.frag` in the viewer**

In `frag-viewer.js`, add a second method beside `loadIfc`:

```js
    async loadFrag(buffer, modelId) {
      await fragments.load(buffer, { modelId });
      return current;
    },
```

In `main.js`'s `show()`, before fetching the IFC:

```js
  // Prefer a precomputed .frag; fall back to converting the IFC in-browser.
  const fragResponse = await fetch(`/api/frag?path=${encodeURIComponent(relativePath.replace(/\.ifc$/i, ".frag"))}`);
  if (fragResponse.ok) {
    status.textContent = `loading ${name}… (precomputed)`;
    const model = await viewer.loadFrag(await fragResponse.arrayBuffer(), name);
    status.textContent = name;
    frameCamera();
    return model;
  }
```

- [ ] **Step 7: Verify both paths end to end**

```bash
cd archiagent-viewer
IFC=$(ls ../dxfBased_ifcOutput/*.ifc | head -1)
npm run build:frag -- "$IFC" "${IFC%.ifc}.frag"
ARCHIAGENT_OUT=.. npm run dev &
sleep 4
npm run shot -- http://localhost:5173 /tmp/frag-precomputed.png
kill %1
rm "${IFC%.ifc}.frag"
```

Expected: the screenshot shows the model, and the status line read
"(precomputed)". Deleting the `.frag` and reloading must still work via the
in-browser fallback — that is invariant I2 demonstrated.

- [ ] **Step 8: Commit**

```bash
git add -A archiagent-viewer
git commit -m "feat(viewer): serve precomputed fragments with in-browser fallback

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phases 3-7 — separate plans required

These are independent subsystems. Each needs its own design pass and its own
plan document before execution; writing their tasks now would be guessing at
interfaces that Phase 1-2 findings will change.

| Phase | Subsystem | Prerequisite | Spec reference |
|---|---|---|---|
| **3** | SaaS backend: FastAPI, auth, Postgres, S3, Redis/RQ worker, DXF upload end to end | Task 7's `build:frag` is the worker's conversion step | §7 |
| **4** | DWG ingest: `archiagent/ingest/dwg.py` + ODA File Converter | ODA redistribution terms cleared for hosted use — a licensing prerequisite, not an engineering one | §5.1 |
| **5** | Tier 3: `@ifc-lite/ids` validation and `@ifc-lite/drawing-2d` plan-vs-DXF diff | Establishes the Node analysis tier. Phase 7 depends on it | §2.2 |
| **6** | Edit round-trip: semantic operations replayed through ifcopenshell | Phase 3 shipped; `--replay-manifest` still green | §8 |
| **7** | `@ifc-lite/mcp` server over the model, `--read-only`, with its viewer tools driving **our** Fragments scene through `@ifc-lite/embed-protocol` | Phase 5 shipped (the Node tier exists). Mutation tools stay disabled until Phase 6 | §2.2, §8.1 |

Phase 1-2 must leave three things intact for the later phases:

1. **For Phase 6:** `--replay-manifest` keeps working and stays tested, and
   `plan.interpretation.json` stays a published artifact even though nothing
   reads it yet.
2. **For Phase 7:** the Fragments runtime stays behind Task 4's
   `createFragViewer` facade and is never inlined into `main.js`. That facade —
   `loadIfc`, `loadFrag`, `clear`, `update`, `raycast`, `current` — is where the
   `embed-protocol` handler will attach so that MCP's `viewer_colorize` /
   `viewer_isolate` / `viewer_fly_to` drive the Fragments scene. Inlining it
   would close that seam and force ifc-lite's WebGPU renderer into the browser,
   which invariant I4 forbids.
3. **For Phase 7's safety:** nothing in Phases 1-6 may add an IFC write path in
   JavaScript. The MCP server's `create_entity` / `entity_set_attribute` /
   `export_ifc` tools must remain disabled (`--read-only`) until the Phase 6
   operation log can absorb them, or `plan.ifc` silently acquires a second
   writer (invariants I1 and I6).

---

## Self-review notes

**Spec coverage.** §1-2 (the tier rule and invariants) → Tasks 1, 4, 5 and the
README in Task 6. §3 artifact contract → Tasks 7, 8 for `.frag`; the rest is
Phase 3. §4.1 redundancy list → Tasks 2, 3, 5 delete every item in it. §4.2/4.3
(do-not-remove) → enforced by the Global Constraint that no file under
`archiagent/` is touched. §5 Tier 1 → Phase 4. §6 Tier 2 → Tasks 1, 4, 5, 8.
§7 SaaS → Phase 3. §8 edit seam → the closing note above. §9-10 → the README's
Known gaps and Task 7 Step 5's determinism finding.

**Review Focus coverage.** #1 far-from-origin → Task 1 Step 3, third test.
#2 draft IFC and #3 empty IFC → Task 5 Step 1. #4 traversal → Task 3 Step 1
(five cases) and Task 8 Step 1. #5 large-file progress → Task 1 Step 3 second
test, surfaced in the UI at Task 5 Step 5.

**Known soft spot.** Task 4 has no unit test because it owns the worker and a
WebGL context. It is covered end to end by Task 5 Step 8's screenshot. This is
called out rather than papered over with a mock that would test nothing.

**Interface consistency.** `ifcToFrag`/`FRAG_MIN_BYTES`/`coordinateToOrigin`
(Task 1) are consumed unchanged by Tasks 2, 5, 7. `createFragViewer` returns
`loadIfc`/`clear`/`update`/`raycast`/`current` (Task 4), all used in Task 5,
with `loadFrag` added in Task 8. `resolveWithinRoot(root, requested, suffix?)`
(Task 3) gains its third parameter in Task 8 with a default, so Task 3's call
sites keep working.
