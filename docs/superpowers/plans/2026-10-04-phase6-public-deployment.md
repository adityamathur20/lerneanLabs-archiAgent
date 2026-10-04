# Phase 6 — Public Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stranger with an API key can POST a DXF or PDF to `https://api.planto3d.in`, poll the job, and open the resulting IFC at `https://planto3d.in` over TLS.

**Architecture:** One Hostinger KVM 2 running six containers from one compose file: Caddy (TLS + routing), Postgres, Redis, Garage (S3 on the VPS disk), an `api` image, and a `worker` image carrying archiAgent in a second virtualenv. `storage.py` is unmodified except for a boot-readiness wait — Garage speaks S3, so the backing store is a config change. The viewer gains a data-source seam so its production build talks to the real API instead of the dev server's disk-scanning middleware.

**Tech Stack:** Docker Compose, Caddy 2, `dxflrs/garage:v2.3.0`, `postgres:16-alpine`, `redis:7-alpine`, Python 3.12 (pinned), FastAPI, RQ, Vite 6, `node:test`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-04-phase6-public-deployment-design.md`

## Global Constraints

- **Python is pinned to `3.12`** in both images, though `requires-python` admits newer (spec §5).
- **The `api` image must not contain archiAgent.** Spec §5.2's rule stays structurally true, not merely observed.
- **The worker runs archiAgent as a subprocess from a second virtualenv**, never by import (spec §5).
- **`ARCHIAGENT_SERVICE_S3_ENDPOINT` is `https://s3.planto3d.in`** for api, worker and browser alike — never `http://garage:3900` (spec §4.3).
- **Only Caddy publishes host ports.** Postgres, Redis, Garage and the api are reachable only on the compose network (spec §3).
- **Migrations are an explicit step**, never run on API startup (spec §7.3).
- **DWG stays refused**; DXF and PDF ship (spec §2.1, §2.2).
- **Garage's bucket and key are provisioned once by script**, not by `ensure_bucket()` at startup (spec §4.2).
- **ACME account email is `lerneantechlabs@gmail.com`**; DNS is at the registrar, no proxy (spec §9).
- No file in `lerneanLabs-archiAgent/archiagent/` is modified by this plan.

## Review Focus

1. **A presigned URL signed for a host the browser cannot resolve** — artifact downloads 302 to `s3.planto3d.in`; if anything signs for `garage:3900` every download dies in the browser while `curl` on the box succeeds. *(Task 9 asserts the endpoint is a public https host; Task 8 asserts the Caddy route exists.)*
2. **A cross-origin viewer request with no CORS** — `api.py` ships no middleware today; without it every browser call fails while `curl` passes. *(Task 4 tests the preflight and the allow-origin header.)*
3. **Garage not yet accepting connections when api/worker boot** — Garage has no healthcheck, so compose cannot order it. *(Task 7 tests the readiness wait, including the give-up path.)*
4. **A `.dwg` upload on a converter-less worker** — must stay a clean 400 at upload time, never a 500 or a job that fails minutes later. *(Task 6 tests `converter_available()` is False in the built worker image.)*
5. **Caddy losing its certificate state across a redeploy** — re-requesting certificates repeatedly hits Let's Encrypt's rate limit and locks you out of TLS for a week. *(Task 9 asserts `caddy_data` is a named volume.)*

---

### Task 1: Publish the viewer repository and scan the public archiAgent history

The viewer's local git has no remote, so there is nothing for the VPS to clone. `lerneanLabs-archiAgent` is already public, which makes its history a disclosure surface.

**Files:**
- Modify: `archiagent-viewer/.git/config` (via `git remote add`)
- Create: `archiagent-viewer/.gitignore` entries if `service/.venv` or `node_modules` are not already ignored

- [ ] **Step 1: Confirm what is about to be published**

```bash
cd archiagent-viewer
git status --short
cat .gitignore
```

Expected: `node_modules/`, `dist/`, `service/.venv/`, `.env` all ignored. If `service/.venv/` is absent from `.gitignore`, add it before pushing — it is 100s of MB and may contain cached credentials.

- [ ] **Step 2: Verify no secrets are staged or committed**

```bash
cd archiagent-viewer
git log --all -p | grep -nEi '(ANTHROPIC|OPENAI)_API_KEY|sk-ant-|sk-proj-|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH) PRIVATE KEY' | head
```

Expected: no output. If there are hits, stop — rewriting history before the first push is cheap; after it is not.

- [ ] **Step 3: Add the remote and push**

```bash
cd archiagent-viewer
git remote add origin https://github.com/adityamathur20/lerneanLabs-archiViewer.git
git push -u origin HEAD
```

- [ ] **Step 4: Verify the VPS will be able to clone it**

```bash
GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/adityamathur20/lerneanLabs-archiViewer.git HEAD
```

Expected: a SHA if the repo is public. **No output means it is private** — record that, because Task 11's runbook then needs a read-only deploy key for it.

- [ ] **Step 5: Scan the already-public archiAgent history**

```bash
cd ../lerneanLabs-archiAgent
git log --all -p | grep -nEi 'sk-ant-|sk-proj-|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH) PRIVATE KEY' | head
```

Expected: no output. This repository is already world-readable, so a hit here is an incident requiring key rotation, not a cleanup task.

- [ ] **Step 6: Commit any .gitignore change**

```bash
cd ../archiagent-viewer
git add .gitignore && git commit -m "chore: ignore the service venv before publishing" || echo "nothing to commit"
git push
```

---

### Task 2: A data-source seam for the viewer

`src/main.js` fetches `/api/models`, `/api/model` and `/api/frag`, all of which exist only inside `vite.config.js`'s `configureServer()` — a dev-server plugin. A production build has no server, so it has no data. This task introduces one interface with two implementations; Task 3 consumes it.

**Files:**
- Create: `archiagent-viewer/src/source.js`
- Create: `archiagent-viewer/tests/source.test.mjs`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `createApiSource({ base, token, fetchImpl })` and `createDiskSource({ fetchImpl })`, both returning `{ kind, describe(), list(), fetchIfc(id) }`. `list()` resolves to `[{ id, name, label }]`. `fetchIfc(id)` resolves to a `Uint8Array`. Both throw `SourceError` with a numeric `.status` on HTTP failure. Task 3 calls exactly these.

- [ ] **Step 1: Write the failing tests**

```javascript
// archiagent-viewer/tests/source.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiSource, createDiskSource, SourceError } from "../src/source.js";

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  arrayBuffer: async () => new ArrayBuffer(0),
  headers: new Map(),
});

test("api source sends the bearer token", async () => {
  let seen;
  const source = createApiSource({
    base: "https://api.planto3d.in",
    token: "ak_secret",
    fetchImpl: async (url, init) => {
      seen = { url, init };
      return jsonResponse({ jobs: [] });
    },
  });
  await source.list();
  assert.equal(seen.url, "https://api.planto3d.in/v1/jobs?limit=200");
  assert.equal(seen.init.headers.authorization, "Bearer ak_secret");
});

test("api source lists only succeeded jobs that have a plan.ifc", async () => {
  const source = createApiSource({
    base: "https://api.planto3d.in",
    token: "k",
    fetchImpl: async () =>
      jsonResponse({
        jobs: [
          { id: "A", status: "succeeded", artifacts: ["plan.ifc"], source_filename: "a.dxf" },
          { id: "B", status: "running", artifacts: [], source_filename: "b.dxf" },
          { id: "C", status: "succeeded", artifacts: ["plan.report.json"], source_filename: "c.pdf" },
        ],
      }),
  });
  const listed = await source.list();
  assert.deepEqual(listed.map((m) => m.id), ["A"]);
  assert.equal(listed[0].name, "a.dxf");
});

test("api source raises a typed error when the key is rejected", async () => {
  const source = createApiSource({
    base: "https://api.planto3d.in",
    token: "",
    fetchImpl: async () => jsonResponse({ detail: "missing bearer token" }, 401),
  });
  await assert.rejects(() => source.list(), (error) => {
    assert.ok(error instanceof SourceError);
    assert.equal(error.status, 401);
    return true;
  });
});

test("api source downloads plan.ifc for a job id", async () => {
  let seen;
  const source = createApiSource({
    base: "https://api.planto3d.in",
    token: "k",
    fetchImpl: async (url) => {
      seen = url;
      return { ok: true, status: 200, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer, headers: new Map() };
    },
  });
  const bytes = await source.fetchIfc("A");
  assert.equal(seen, "https://api.planto3d.in/v1/jobs/A/artifacts/plan.ifc");
  assert.deepEqual(Array.from(bytes), [1, 2, 3]);
});

test("disk source preserves the dev server contract", async () => {
  const urls = [];
  const source = createDiskSource({
    fetchImpl: async (url) => {
      urls.push(url);
      if (url === "/api/models") return jsonResponse({ root: "/out", models: [{ path: "a/plan.ifc", name: "plan" }] });
      return { ok: true, status: 200, arrayBuffer: async () => new Uint8Array([9]).buffer, headers: new Map() };
    },
  });
  const listed = await source.list();
  assert.equal(listed[0].id, "a/plan.ifc");
  await source.fetchIfc("a/plan.ifc");
  assert.equal(urls[1], "/api/model?path=a%2Fplan.ifc");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd archiagent-viewer && node --test tests/source.test.mjs`
Expected: FAIL — `Cannot find module '../src/source.js'`

- [ ] **Step 3: Write the implementation**

```javascript
// archiagent-viewer/src/source.js
/**
 * Where the viewer's models come from.
 *
 * Two implementations, one interface. `createDiskSource` is the dev server's
 * disk scan (vite.config.js middleware); `createApiSource` is the deployed
 * service. main.js picks one and never learns which.
 *
 * There is deliberately no `.frag` path here: the worker produces none
 * (`ARTIFACT_PATTERNS` has no `*.frag`, and the converter is Node), so the
 * browser converts the IFC itself. With no server-side cache, invariant I2
 * holds for want of a cache to go stale.
 */

export class SourceError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "SourceError";
    this.status = status;
  }
}

async function detail(response) {
  try {
    const body = await response.json();
    return body?.detail ?? body?.error ?? response.statusText;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

export function createApiSource({ base, token, fetchImpl = globalThis.fetch }) {
  if (!base) throw new Error("createApiSource needs a base URL");
  const trimmed = base.replace(/\/$/, "");
  // The token goes to the API only. Browsers strip Authorization across an
  // origin-changing redirect, which is what keeps it away from Garage — a
  // forwarded bearer would collide with the query-string signature.
  const init = () => ({ headers: token ? { authorization: `Bearer ${token}` } : {} });

  return {
    kind: "api",
    describe: () => trimmed,

    async list() {
      const response = await fetchImpl(`${trimmed}/v1/jobs?limit=200`, init());
      if (!response.ok) throw new SourceError(await detail(response), response.status);
      const { jobs = [] } = await response.json();
      return jobs
        .filter((job) => job.status === "succeeded" && (job.artifacts ?? []).includes("plan.ifc"))
        .map((job) => ({
          id: job.id,
          name: job.source_filename ?? job.id,
          label: `${job.source_filename ?? job.id} — ${job.id}`,
        }));
    },

    async fetchIfc(id) {
      const response = await fetchImpl(`${trimmed}/v1/jobs/${id}/artifacts/plan.ifc`, init());
      if (!response.ok) throw new SourceError(await detail(response), response.status);
      return new Uint8Array(await response.arrayBuffer());
    },
  };
}

export function createDiskSource({ fetchImpl = globalThis.fetch } = {}) {
  return {
    kind: "disk",
    describe: () => "local disk (dev server)",

    async list() {
      const response = await fetchImpl("/api/models");
      if (!response.ok) throw new SourceError(await detail(response), response.status);
      const { models = [] } = await response.json();
      return models.map((model) => ({
        id: model.path,
        name: model.name,
        label: `${model.name} — ${model.path}`,
      }));
    },

    async fetchIfc(id) {
      const response = await fetchImpl(`/api/model?path=${encodeURIComponent(id)}`);
      if (!response.ok) throw new SourceError(await detail(response), response.status);
      return new Uint8Array(await response.arrayBuffer());
    },
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd archiagent-viewer && node --test tests/source.test.mjs`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
cd archiagent-viewer
git add src/source.js tests/source.test.mjs
git commit -m "feat(viewer): one data-source interface, disk or API

main.js currently fetches three endpoints that exist only in the dev
server's configureServer() hook, so a production build has no data at
all. This introduces the seam; wiring follows.

No .frag path: the worker produces none, so the browser converts the
IFC and I2 holds for want of a cache."
```

---

### Task 3: Wire the viewer to the source and add the API-key field

**Files:**
- Modify: `archiagent-viewer/src/main.js` (the `show()` frag/model fetches ~lines 88-121, and `loadList()` ~lines 189-208)
- Modify: `archiagent-viewer/index.html` (the controls block, lines 73-75)
- Test: `archiagent-viewer/tests/source.test.mjs` (already covers the seam)

**Interfaces:**
- Consumes: `createApiSource`, `createDiskSource`, `SourceError` from Task 2.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Add the API-key control to index.html**

Replace lines 73-75 (`<select id="manifests">` through `<div class="sub" id="root">`) with:

```html
      <select id="manifests"><option value="">loading…</option></select>
      <button id="reload">Reload</button>
      <div class="sub" id="root" style="margin-top:8px"></div>
      <div id="auth" style="margin-top:8px; display:none">
        <input id="apiKey" type="password" placeholder="API key (ak_…)" style="width:100%" />
        <button id="saveKey" style="margin-top:4px">Save key</button>
      </div>
```

- [ ] **Step 2: Replace the imports and source selection at the top of main.js**

After the existing `import { createFragViewer } from "./frag-viewer.js";` line, add:

```javascript
import { createApiSource, createDiskSource, SourceError } from "./source.js";

// Unset means the dev server: local development keeps working untouched.
const API_BASE = import.meta.env?.VITE_API_BASE ?? "";
const KEY_STORAGE = "planto3d.apiKey";

function readKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return ""; // private mode, blocked storage: degrade, never throw
  }
}

function buildSource() {
  return API_BASE
    ? createApiSource({ base: API_BASE, token: readKey() })
    : createDiskSource({});
}

let source = buildSource();
```

- [ ] **Step 3: Replace the frag and model fetches inside `show()`**

Delete the whole precomputed-`.frag` block (from the `// Prefer a precomputed .frag` comment through the closing brace of `if (fragResponse.ok) { … }`) and replace the subsequent `/api/model` fetch with:

```javascript
  let bytes;
  try {
    bytes = await source.fetchIfc(relativePath);
  } catch (error) {
    if (error instanceof SourceError && error.status === 401) {
      el("auth").style.display = "";
      fail("API key missing or rejected — enter a key and reload.");
    } else {
      fail(`failed: ${error.message}`);
    }
    return;
  }
```

- [ ] **Step 4: Replace `loadList()`**

```javascript
async function loadList() {
  const select = el("manifests");
  try {
    const models = await source.list();
    el("root").textContent = `reading ${source.describe()}`;
    if (models.length === 0) {
      select.innerHTML = "<option value=''>no models found</option>";
      fail(
        source.kind === "api"
          ? "No succeeded jobs yet. Upload a DXF or PDF to the API first."
          : "No IFC files found. Set ARCHIAGENT_OUT to your archiAgent --outputDir.",
      );
      return;
    }
    select.innerHTML = models
      .map((m) => `<option value="${encodeURIComponent(m.id)}" data-name="${m.name}">${m.label}</option>`)
      .join("");
    await show(models[0].id, models[0].name);
  } catch (error) {
    if (error instanceof SourceError && error.status === 401) {
      el("auth").style.display = "";
      fail("API key missing or rejected — enter a key below, then Save.");
      return;
    }
    fail(`Could not list models: ${error.message}`, error.stack ?? String(error));
  }
}

el("saveKey")?.addEventListener("click", () => {
  try {
    localStorage.setItem(KEY_STORAGE, el("apiKey").value.trim());
  } catch {
    /* storage blocked: the key lives for this page only */
  }
  source = buildSource();
  loadList();
});
```

- [ ] **Step 5: Verify dev behaviour is unchanged and the build succeeds**

```bash
cd archiagent-viewer
node --test tests/
npm run build
grep -c "api/frag" dist/assets/*.js || echo "no frag path in the bundle — correct"
```

Expected: all tests pass; `npm run build` succeeds; no `api/frag` in the bundle.

- [ ] **Step 6: Commit**

```bash
cd archiagent-viewer
git add src/main.js index.html
git commit -m "feat(viewer): read models from the API when VITE_API_BASE is set

Unset keeps the dev server path, so local development is untouched. The
.frag fetch is removed rather than repointed: no server-side .frag
exists. A 401 reveals the key field instead of failing blankly."
```

---

### Task 4: CORS on the API

The viewer is a different origin from the API in production. `api.py` installs no CORS middleware, because the dev server served both from one port.

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/config.py`
- Modify: `archiagent-viewer/service/archiagent_service/api.py:61` (just after `app = FastAPI(...)`)
- Test: `archiagent-viewer/service/tests/test_cors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.cors_origins: list[str]`, read from `ARCHIAGENT_SERVICE_CORS_ORIGINS` as a JSON list. Task 9's `.env.example` sets it.

- [ ] **Step 1: Write the failing test**

```python
# archiagent-viewer/service/tests/test_cors.py
"""The viewer is a different origin in production; curl never notices.

Mirrors tests/test_api.py: `create_app()` is a factory, so the app is built
AFTER the CORS env var is set and the settings cache is cleared.
"""
import pytest
from fastapi.testclient import TestClient

from archiagent_service import auth
from archiagent_service.api import create_app
from archiagent_service.auth import issue_key
from archiagent_service.config import get_settings
from archiagent_service.models import Tenant, ulid

VIEWER = "https://planto3d.in"


@pytest.fixture
def cors_client(pg_session, monkeypatch):
    monkeypatch.setenv(
        "ARCHIAGENT_SERVICE_CORS_ORIGINS", f'["{VIEWER}","http://localhost:5173"]'
    )
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[auth.db_session] = lambda: pg_session
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def alice(pg_session):
    tenant = Tenant(id=ulid(), name="alice")
    pg_session.add(tenant)
    pg_session.flush()
    return tenant, issue_key(pg_session, tenant.id)


def test_preflight_is_answered_for_the_viewer_origin(cors_client):
    response = cors_client.options(
        "/v1/jobs",
        headers={
            "Origin": VIEWER,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == VIEWER
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_actual_request_carries_allow_origin(cors_client, alice):
    _, key = alice
    response = cors_client.get(
        "/v1/jobs", headers={"Origin": VIEWER, "authorization": f"Bearer {key}"}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == VIEWER


def test_an_unlisted_origin_gets_no_allow_header(cors_client, alice):
    _, key = alice
    response = cors_client.get(
        "/v1/jobs",
        headers={"Origin": "https://evil.example", "authorization": f"Bearer {key}"},
    )
    # The request still succeeds — the bearer token authorizes it — but the
    # browser refuses to hand the body to that origin's script.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


- [ ] **Step 2: Run it to verify it fails**

Run: `cd archiagent-viewer/service && .venv/bin/pytest tests/test_cors.py -v`
Expected: FAIL — `KeyError: 'access-control-allow-origin'`, because no middleware is installed. The `pg_session` fixture comes from `tests/conftest.py`; the suite skips rather than fails if Postgres is not running.

- [ ] **Step 3: Add the setting**

In `config.py`, inside `Settings`, after `tenant_max_concurrent`:

```python
    # The viewer is a different origin in production (spec §4.4). Empty means
    # same-origin only, which is correct for the dev server.
    cors_origins: list[str] = ["http://localhost:5173"]
```

- [ ] **Step 4: Install the middleware**

In `api.py`, immediately after `app = FastAPI(title="archiAgent", version="0.1.0")`:

```python
    # Spec §4.4: planto3d.in -> api.planto3d.in is cross-origin, and the bearer
    # token makes every request non-simple, so the preflight must be answered.
    # allow_credentials stays False: the credential is a bearer header, not a
    # cookie, and True would forbid the wildcard we never use anyway.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type"],
    )
```

Add the import at the top of `api.py`:

```python
from fastapi.middleware.cors import CORSMiddleware
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd archiagent-viewer/service && .venv/bin/pytest tests/test_cors.py -v`
Expected: PASS, 3 tests. The `cors_client` fixture already sets the env var and clears the settings cache before `create_app()`, which is why the middleware sees the test origin.

- [ ] **Step 6: Run the whole suite, then commit**

```bash
cd archiagent-viewer/service
.venv/bin/pytest -q
git add archiagent_service/api.py archiagent_service/config.py tests/test_cors.py
git commit -m "feat(service): answer CORS preflights for the viewer origin

Nothing was ever cross-origin before: the dev server served the viewer
and the API from one localhost port. In production they are separate
hosts and every bearer-carrying request triggers a preflight.

allow_credentials stays False — the credential is a header, not a cookie."
```

---

### Task 5: The `api` image

**Files:**
- Create: `archiagent-viewer/service/Dockerfile`
- Create: `archiagent-viewer/service/.dockerignore`

**Interfaces:**
- Produces: an image whose default command serves uvicorn on `:8000`, and which **cannot** import `archiagent`. Task 9's compose file builds it with context `./archiagent-viewer/service`.

- [ ] **Step 1: Write the .dockerignore**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
tests/
alembic/versions/__pycache__/
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
# The API tier. archiAgent is deliberately absent: spec §5.2 forbids importing
# it, and the surest way to honour that is for the import to be impossible.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY archiagent_service ./archiagent_service
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir . \
 && adduser --system --no-create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "archiagent_service.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build it**

Run: `cd archiagent-viewer/service && docker build -t planto3d-api:test .`
Expected: build succeeds.

- [ ] **Step 4: Verify the §5.2 boundary holds structurally**

```bash
docker run --rm planto3d-api:test python -c "
import importlib.util, sys
assert importlib.util.find_spec('archiagent_service'), 'service missing'
assert importlib.util.find_spec('archiagent') is None, 'archiAgent must NOT be in the api image'
print('api image boundary: ok')
"
```

Expected: `api image boundary: ok`

- [ ] **Step 5: Verify it boots and answers /healthz without a database**

```bash
docker run --rm -d --name api-smoke -p 8001:8000 planto3d-api:test
sleep 3 && curl -fsS http://localhost:8001/healthz && echo
docker rm -f api-smoke
```

Expected: a 200 JSON body. If `/healthz` touches the database it will fail here — read `api.py:63` and, if so, assert on the container *starting* instead, leaving the endpoint test to Task 9 where Postgres exists.

- [ ] **Step 6: Commit**

```bash
cd archiagent-viewer/service
git add Dockerfile .dockerignore
git commit -m "build(service): api image, without archiAgent

Pinned to 3.12 for wheel availability. archiAgent is absent by
construction, so spec §5.2's no-import rule is enforced by the image
rather than observed by convention."
```

---

### Task 6: The `worker` image, with two virtualenvs

**Files:**
- Create: `archiagent-viewer/service/Dockerfile.worker`

**Interfaces:**
- Consumes: Task 5's `.dockerignore` conventions.
- Produces: an image where `/opt/agent/bin/python` runs archiAgent and the default `python` runs the service. Task 9 sets `ARCHIAGENT_SERVICE_ARCHIAGENT_PYTHON=/opt/agent/bin/python` and `ARCHIAGENT_SERVICE_ARCHIAGENT_CWD=/opt/archiagent`. Build context is the **repository parent**, because it needs both trees.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
# The worker. Carries BOTH tiers, in two virtualenvs that never import each
# other: the service venv is the image default, archiAgent lives in /opt/agent
# and is reached only as a subprocess (spec §5.2, §5.3).
#
# Build context is the directory CONTAINING both repositories.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# pymupdf and shapely ship wheels; opencv-python-headless needs libGL's
# runtime even headless, and ifcopenshell wants libstdc++.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# --- Tier 1, in its own venv -------------------------------------------------
COPY lerneanLabs-archiAgent /opt/archiagent
RUN python -m venv /opt/agent \
 && /opt/agent/bin/pip install --no-cache-dir "/opt/archiagent[llm,vision,cv]"

# --- the service, as the image default ---------------------------------------
WORKDIR /app
COPY archiagent-viewer/service/pyproject.toml ./
COPY archiagent-viewer/service/archiagent_service ./archiagent_service
RUN pip install --no-cache-dir . \
 && adduser --system --no-create-home --uid 10001 appuser
USER appuser

ENV ARCHIAGENT_SERVICE_ARCHIAGENT_PYTHON=/opt/agent/bin/python \
    ARCHIAGENT_SERVICE_ARCHIAGENT_CWD=/opt/archiagent

CMD ["rq", "worker", "archiagent"]
```

- [ ] **Step 2: Build it from the parent directory**

Run: `cd /path/to/blender-experiment && docker build -f archiagent-viewer/service/Dockerfile.worker -t planto3d-worker:test .`
Expected: build succeeds. This is the slow one (~10 min on 2 vCPUs); Task 12 moves it off the box.

- [ ] **Step 3: Verify the two venvs are genuinely separate**

```bash
docker run --rm planto3d-worker:test python -c "
import importlib.util
assert importlib.util.find_spec('archiagent_service'), 'service missing from the default venv'
assert importlib.util.find_spec('archiagent') is None, 'archiAgent leaked into the service venv'
print('service venv: isolated')
"
docker run --rm planto3d-worker:test /opt/agent/bin/python -m archiagent --help > /dev/null && echo "agent venv: runs"
```

Expected: `service venv: isolated` then `agent venv: runs`

- [ ] **Step 4: Verify DWG is refused rather than half-supported (Review Focus 4)**

```bash
docker run --rm planto3d-worker:test /opt/agent/bin/python -c "
from archiagent.ingest.dwg import converter_available
assert converter_available() is False, 'a converter appeared; uploads.py would now accept .dwg'
print('dwg: correctly unsupported, uploads will 400 at request time')
"
```

Expected: the success line. This is what makes `uploads.py:50`'s 400 the real behaviour in production rather than an untested branch.

- [ ] **Step 5: Verify the RQ entry point resolves**

```bash
docker run --rm planto3d-worker:test rq --version
```

Expected: a version string.

- [ ] **Step 6: Commit**

```bash
cd archiagent-viewer
git add service/Dockerfile.worker
git commit -m "build(service): worker image carrying both tiers in two venvs

The worker shells out to archiAgent, so the image needs it — but not on
the service's import path. Two venvs keep the subprocess boundary real:
a different interpreter and a different dependency set, verified by the
build's own checks.

No ODA converter, so converter_available() is False and .dwg is refused
at upload time rather than failing minutes later."
```

---

### Task 7: Garage readiness, and the provisioning script

Garage has no container healthcheck, so compose cannot order startup against it. Separately, nothing creates the bucket in production: `ensure_bucket()`'s only caller is `tests/conftest.py`.

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/storage.py`
- Create: `archiagent-viewer/service/tests/test_storage_ready.py`
- Create: `archiagent-viewer/deploy/provision-garage.sh`

**Interfaces:**
- Consumes: the existing `ObjectStore`.
- Produces: `ObjectStore.wait_ready(attempts: int = 30, delay: float = 2.0) -> None`, raising `RuntimeError` when the store never answers. Task 9's compose runs it as the api's pre-start command.

- [ ] **Step 1: Write the failing test**

```python
# archiagent-viewer/service/tests/test_storage_ready.py
"""Garage publishes no healthcheck, so the client waits instead (spec §4.2)."""
import pytest
from botocore.exceptions import EndpointConnectionError

from archiagent_service.storage import ObjectStore


def _store() -> ObjectStore:
    return ObjectStore(endpoint="http://127.0.0.1:1", bucket="b", access_key="k", secret_key="s")


def test_wait_ready_returns_once_the_store_answers(monkeypatch):
    store = _store()
    calls = {"n": 0}

    def head_bucket(**_):
        calls["n"] += 1
        if calls["n"] < 3:
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:1")
        return {}

    monkeypatch.setattr(store._client, "head_bucket", head_bucket)
    store.wait_ready(attempts=5, delay=0)
    assert calls["n"] == 3


def test_wait_ready_gives_up_loudly(monkeypatch):
    store = _store()

    def head_bucket(**_):
        raise EndpointConnectionError(endpoint_url="http://127.0.0.1:1")

    monkeypatch.setattr(store._client, "head_bucket", head_bucket)
    with pytest.raises(RuntimeError, match="not reachable"):
        store.wait_ready(attempts=2, delay=0)


def test_wait_ready_accepts_a_missing_bucket_as_reachable(monkeypatch):
    """A 404 proves the store is answering. Creating the bucket is provisioning's
    job, not the application's — a service that creates its own bucket can also
    create the wrong one after a typo'd endpoint and look healthy."""
    store = _store()
    from botocore.exceptions import ClientError

    def head_bucket(**_):
        raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    monkeypatch.setattr(store._client, "head_bucket", head_bucket)
    store.wait_ready(attempts=1, delay=0)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd archiagent-viewer/service && .venv/bin/pytest tests/test_storage_ready.py -v`
Expected: FAIL — `AttributeError: 'ObjectStore' object has no attribute 'wait_ready'`

- [ ] **Step 3: Implement `wait_ready`**

Add to `ObjectStore` in `storage.py`, directly after `ensure_bucket`:

```python
    def wait_ready(self, attempts: int = 30, delay: float = 2.0) -> None:
        """Blocks until the store answers at all, or raises.

        Garage ships no container healthcheck (spec §4.2), so compose cannot
        order the api and worker behind it. A ClientError means it answered —
        including 404 for a missing bucket — and only a transport failure is
        worth retrying. Creating the bucket is provisioning's job.
        """
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                self._client.head_bucket(Bucket=self.bucket)
                return
            except ClientError:
                return  # it answered; the bucket's existence is not our business
            except Exception as error:  # transport: not up yet
                last = error
                if attempt < attempts - 1:
                    time.sleep(delay)
        raise RuntimeError(f"object store not reachable after {attempts} attempts: {last}")
```

Add `import time` to the imports at the top of `storage.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd archiagent-viewer/service && .venv/bin/pytest tests/test_storage_ready.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Write the provisioning script**

```bash
# archiagent-viewer/deploy/provision-garage.sh
#!/usr/bin/env bash
# One-time Garage setup: layout, bucket, key, grant. Idempotent — safe to
# re-run. Nothing in the application creates its own bucket (spec §4.2).
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.prod.yml}"
BUCKET="${BUCKET:-archiagent}"
KEY_NAME="${KEY_NAME:-archiagent-service}"
g() { $COMPOSE exec -T garage /garage "$@"; }

echo "==> waiting for garage to answer"
for _ in $(seq 1 30); do g status >/dev/null 2>&1 && break; sleep 2; done
g status >/dev/null || { echo "garage never came up"; exit 1; }

echo "==> assigning a single-node layout (no-op if already assigned)"
NODE_ID="$(g node id -q 2>/dev/null | cut -d@ -f1)"
if ! g layout show 2>/dev/null | grep -q "$NODE_ID"; then
  g layout assign "$NODE_ID" -z dc1 -c 90G
  g layout apply --version 1
fi

echo "==> bucket"
g bucket info "$BUCKET" >/dev/null 2>&1 || g bucket create "$BUCKET"

echo "==> key"
if ! g key info "$KEY_NAME" >/dev/null 2>&1; then
  g key create "$KEY_NAME"
fi
g bucket allow --read --write --owner "$BUCKET" --key "$KEY_NAME"

echo
echo "==> put these in .env (shown once):"
g key info "$KEY_NAME" --show-secret | grep -Ei 'key id|secret'
```

- [ ] **Step 6: Verify the script is valid shell and executable**

```bash
cd archiagent-viewer
chmod +x deploy/provision-garage.sh
bash -n deploy/provision-garage.sh && echo "syntax ok"
```

Expected: `syntax ok`. It cannot run until Task 9's compose file exists; the runbook (Task 11) sequences it.

- [ ] **Step 7: Run the whole service suite, then commit**

```bash
cd archiagent-viewer/service && .venv/bin/pytest -q
cd .. && git add service/archiagent_service/storage.py service/tests/test_storage_ready.py deploy/provision-garage.sh
git commit -m "feat(service): wait for the object store, and provision it by script

Garage has no healthcheck, so compose cannot order startup behind it;
wait_ready retries transport failures and treats any HTTP answer —
including a 404 for a missing bucket — as ready.

The bucket, key and grant are a one-time script. ensure_bucket() stays
test-only on purpose: a service that can create its own bucket can also
create the wrong one after a mistyped endpoint and still look healthy."
```

---

### Task 8: The Caddyfile

**Files:**
- Create: `archiagent-viewer/deploy/Caddyfile`

**Interfaces:**
- Produces: routes for five hostnames. Task 9 mounts this at `/etc/caddy/Caddyfile` and passes `ACME_EMAIL`, `VIEWER_ORIGIN`.

- [ ] **Step 1: Write the Caddyfile**

```caddyfile
{
	email {$ACME_EMAIL}
}

# --- the viewer --------------------------------------------------------------
planto3d.in {
	root * /srv/viewer
	try_files {path} /index.html
	file_server
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
	}
}

www.planto3d.in {
	redir https://planto3d.in{uri} permanent
}

# Held defensively, not a second deployment (spec §3.1).
planto3d.si, www.planto3d.si {
	redir https://planto3d.in{uri} permanent
}

# --- the API ----------------------------------------------------------------
api.planto3d.in {
	reverse_proxy api:8000
	header Strict-Transport-Security "max-age=31536000"
}

# --- object storage ---------------------------------------------------------
# Public because presigned URLs sign the host, and the browser must reach the
# same host the signature was made for (spec §4.3). Garage itself publishes no
# port; this is the only way in, and its access keys remain the authorization
# boundary.
s3.planto3d.in {
	reverse_proxy garage:3900

	# fetch() follows the API's 302 here, and the FINAL response is the one
	# whose CORS headers are checked (spec §4.4). A presigned GET sends no
	# custom headers, so there is no preflight to answer — only this.
	header Access-Control-Allow-Origin "{$VIEWER_ORIGIN}"
	header Access-Control-Expose-Headers "ETag, Content-Length"
	header Vary "Origin"
}
```

- [ ] **Step 2: Validate it**

```bash
cd archiagent-viewer/deploy
ACME_EMAIL=lerneantechlabs@gmail.com VIEWER_ORIGIN=https://planto3d.in \
  docker run --rm -e ACME_EMAIL -e VIEWER_ORIGIN \
    -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Expected: `Valid configuration`

- [ ] **Step 3: Assert the three routes that the spec requires exist**

```bash
cd archiagent-viewer/deploy
for host in "api.planto3d.in" "s3.planto3d.in" "planto3d.si"; do
  grep -q "$host" Caddyfile && echo "route present: $host" || { echo "MISSING: $host"; exit 1; }
done
grep -q "Access-Control-Allow-Origin" Caddyfile && echo "s3 CORS header present"
```

Expected: four confirmation lines. The `s3` route and its CORS header are Review Focus items 1 and 2.

- [ ] **Step 4: Commit**

```bash
cd archiagent-viewer
git add deploy/Caddyfile
git commit -m "feat(deploy): Caddy config for all five hostnames

s3.planto3d.in is required, not decorative: presigned URLs sign the
host, so the browser must reach the same name the signature was made
for. Caddy also adds the allow-origin header there, because fetch()
checks the final response of the redirect chain.

.si redirects rather than serving a second copy."
```

---

### Task 9: The production compose file

**Files:**
- Create: `archiagent-viewer/docker-compose.prod.yml`
- Create: `archiagent-viewer/.env.example`
- Create: `archiagent-viewer/tests/compose-guard.test.mjs`

**Interfaces:**
- Consumes: Task 5's `api` image, Task 6's `worker` image, Task 7's `wait_ready`, Task 8's `Caddyfile`.
- Produces: the deployable topology. Task 11's runbook drives it; Task 12 replaces `build:` with `image:`.

- [ ] **Step 1: Write .env.example**

```bash
# archiagent-viewer/.env.example
# Copy to .env on the VPS, fill in, chmod 600. Never commit the result.

# --- generated on the box: openssl rand -hex 24 ---
POSTGRES_PASSWORD=
GARAGE_RPC_SECRET=
GARAGE_ADMIN_TOKEN=

# --- printed by deploy/provision-garage.sh ---
ARCHIAGENT_SERVICE_S3_ACCESS_KEY=
ARCHIAGENT_SERVICE_S3_SECRET_KEY=

# --- fixed for this deployment ---
# MUST be the public host: presigned URLs sign it and the browser must reach
# the same name (spec §4.3). Never http://garage:3900.
ARCHIAGENT_SERVICE_S3_ENDPOINT=https://s3.planto3d.in
ARCHIAGENT_SERVICE_S3_BUCKET=archiagent
ARCHIAGENT_SERVICE_CORS_ORIGINS=["https://planto3d.in"]
ACME_EMAIL=lerneantechlabs@gmail.com
VIEWER_ORIGIN=https://planto3d.in

# --- yours ---
ANTHROPIC_API_KEY=
```

- [ ] **Step 2: Write the compose file**

```yaml
# archiagent-viewer/docker-compose.prod.yml
# Only caddy publishes ports (spec §3). Everything else is reachable on this
# network alone — Garage especially, whose S3 endpoint must never bind a
# public interface.
name: planto3d

services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    environment:
      ACME_EMAIL: ${ACME_EMAIL}
      VIEWER_ORIGIN: ${VIEWER_ORIGIN}
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./dist:/srv/viewer:ro
      # Named, because losing it re-requests certificates and Let's Encrypt
      # rate-limits you out of TLS for a week.
      - caddy_data:/data
      - caddy_config:/config
    depends_on: [api]

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: archiagent
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: archiagent
    volumes: [pg_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U archiagent"]
      interval: 5s
      retries: 20

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes: [redis_data:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 20

  garage:
    image: dxflrs/garage:v2.3.0
    restart: unless-stopped
    command: ["/garage", "server"]
    environment:
      GARAGE_RPC_SECRET: ${GARAGE_RPC_SECRET}
      GARAGE_ADMIN_TOKEN: ${GARAGE_ADMIN_TOKEN}
    volumes:
      - ./deploy/garage.toml:/etc/garage.toml:ro
      - garage_meta:/var/lib/garage/meta
      - garage_data:/var/lib/garage/data
    # No healthcheck: Garage ships no mechanism for one (spec §4.2). The api
    # and worker wait via storage.wait_ready() instead.

  api:
    build: ./service
    restart: unless-stopped
    env_file: [.env]
    environment:
      ARCHIAGENT_SERVICE_DATABASE_URL: postgresql+psycopg://archiagent:${POSTGRES_PASSWORD}@postgres:5432/archiagent
      ARCHIAGENT_SERVICE_REDIS_URL: redis://redis:6379/0
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      garage: {condition: service_started}
    command:
      - sh
      - -c
      - >-
        python -c "from archiagent_service.storage import get_store; get_store().wait_ready()" &&
        exec uvicorn archiagent_service.api:app --host 0.0.0.0 --port 8000

  worker:
    build:
      context: ..
      dockerfile: archiagent-viewer/service/Dockerfile.worker
    restart: unless-stopped
    env_file: [.env]
    environment:
      ARCHIAGENT_SERVICE_DATABASE_URL: postgresql+psycopg://archiagent:${POSTGRES_PASSWORD}@postgres:5432/archiagent
      ARCHIAGENT_SERVICE_REDIS_URL: redis://redis:6379/0
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      garage: {condition: service_started}
    command:
      - sh
      - -c
      - >-
        python -c "from archiagent_service.storage import get_store; get_store().wait_ready()" &&
        exec rq worker archiagent --url redis://redis:6379/0

volumes:
  caddy_data: {}
  caddy_config: {}
  pg_data: {}
  redis_data: {}
  garage_meta: {}
  garage_data: {}
```

- [ ] **Step 3: Write garage.toml**

```toml
# archiagent-viewer/deploy/garage.toml
# Single node: replication_factor 1, because replicating across one disk is
# theatre (spec §4.1). Durability comes from §10's backups.
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"

[s3_api]
s3_region = "us-east-1"
api_bind_addr = "[::]:3900"
root_domain = ".s3.planto3d.in"

[admin]
api_bind_addr = "[::]:3903"
```

- [ ] **Step 4: Write the guard test**

```javascript
// archiagent-viewer/tests/compose-guard.test.mjs
// The production topology has invariants worth failing a build over.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const compose = readFileSync(new URL("../docker-compose.prod.yml", import.meta.url), "utf8");
const env = readFileSync(new URL("../.env.example", import.meta.url), "utf8");

test("only caddy publishes host ports", () => {
  // A "ports:" under any other service exposes Postgres, Redis or the object
  // store to the internet (spec §3).
  const services = compose.split(/\n  (?=\w)/);
  for (const block of services) {
    const name = block.trim().split(":")[0];
    if (name === "caddy" || !block.includes("ports:")) continue;
    assert.fail(`service ${name} publishes host ports; only caddy may`);
  }
});

test("the S3 endpoint is the public host, never the compose-internal one", () => {
  // Presigned URLs sign the host; signing for garage:3900 breaks every
  // browser download while curl on the box still works (spec §4.3).
  assert.match(env, /ARCHIAGENT_SERVICE_S3_ENDPOINT=https:\/\/s3\.planto3d\.in/);
  assert.doesNotMatch(compose, /S3_ENDPOINT.*garage:3900/);
});

test("caddy's certificate state is a named volume", () => {
  // Losing /data re-requests certificates and hits Let's Encrypt rate limits.
  assert.match(compose, /caddy_data:\/data/);
  assert.match(compose, /^volumes:[\s\S]*caddy_data:/m);
});

test("garage declares no healthcheck it cannot satisfy", () => {
  const garageBlock = compose.split(/\n  (?=\w)/).find((b) => b.trim().startsWith("garage:"));
  assert.ok(garageBlock, "no garage service found");
  assert.ok(!garageBlock.includes("healthcheck:"), "garage ships no healthcheck mechanism");
});

test("both app services wait for the store before serving", () => {
  const waits = compose.match(/wait_ready\(\)/g) ?? [];
  assert.equal(waits.length, 2, "api and worker must both wait_ready");
});
```

- [ ] **Step 5: Run the guard test and validate the compose file**

```bash
cd archiagent-viewer
node --test tests/compose-guard.test.mjs
cp .env.example .env && sed -i.bak 's/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=x/' .env
docker compose -f docker-compose.prod.yml config > /dev/null && echo "compose config valid"
rm -f .env .env.bak
```

Expected: 5 tests pass, then `compose config valid`

- [ ] **Step 6: Commit**

```bash
cd archiagent-viewer
git add docker-compose.prod.yml .env.example deploy/garage.toml tests/compose-guard.test.mjs
git commit -m "feat(deploy): production compose topology

Caddy owns the only published ports; Postgres, Redis, Garage and the api
live on the compose network. The api and worker both wait_ready() before
serving, because Garage has no healthcheck to order them behind.

The guard test fails the build on the three mistakes that would only
surface in production: a second service publishing a port, an S3
endpoint signed for the compose-internal host, and caddy's cert state
on an anonymous volume."
```

---

### Task 10: Backups, with a restore that is actually tried

Artifacts are regenerable from their source upload; the job index is not. Postgres is the asset that needs the offsite copy (spec §10).

**Files:**
- Create: `archiagent-viewer/deploy/backup.sh`
- Create: `archiagent-viewer/deploy/restore-check.sh`

- [ ] **Step 1: Write the backup script**

```bash
# archiagent-viewer/deploy/backup.sh
#!/usr/bin/env bash
# Nightly: dump Postgres, keep it in Garage AND off the box. A dump that only
# exists on the disk it protects is not a backup (spec §10).
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="pg-${STAMP}.sql.gz"
LOCAL="/var/backups/planto3d"
mkdir -p "$LOCAL"

$COMPOSE exec -T postgres pg_dump -U archiagent archiagent | gzip -9 > "${LOCAL}/${NAME}"

# Refuse to keep a dump that is suspiciously small — an empty dump is worse
# than no dump, because it looks like success.
SIZE=$(wc -c < "${LOCAL}/${NAME}")
[ "$SIZE" -gt 1024 ] || { echo "dump is only ${SIZE} bytes; refusing"; exit 1; }

$COMPOSE exec -T api python - "$NAME" <<'PY'
import sys, pathlib
from archiagent_service.storage import get_store
name = sys.argv[1]
get_store().put(f"_backups/{name}", pathlib.Path(f"/var/backups/planto3d/{name}").read_bytes())
print(f"uploaded _backups/{name}")
PY

find "$LOCAL" -name 'pg-*.sql.gz' -mtime +14 -delete
echo "backup ${NAME} complete (${SIZE} bytes)"
echo "REMINDER: offsite copy is a separate step — see deploy/README.md §Backups"
```

- [ ] **Step 2: Write the restore check**

```bash
# archiagent-viewer/deploy/restore-check.sh
#!/usr/bin/env bash
# Proves the newest dump restores. An untested backup is a belief.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
NEWEST="$(ls -t /var/backups/planto3d/pg-*.sql.gz | head -1)"
echo "==> restoring ${NEWEST} into a scratch database"

$COMPOSE exec -T postgres psql -U archiagent -c 'DROP DATABASE IF EXISTS restore_check;'
$COMPOSE exec -T postgres psql -U archiagent -c 'CREATE DATABASE restore_check;'
gunzip -c "$NEWEST" | $COMPOSE exec -T postgres psql -U archiagent -d restore_check -q

COUNT="$($COMPOSE exec -T postgres psql -U archiagent -d restore_check -tAc \
  "select count(*) from information_schema.tables where table_schema='public'")"
$COMPOSE exec -T postgres psql -U archiagent -c 'DROP DATABASE restore_check;'

[ "$COUNT" -ge 3 ] || { echo "restored only ${COUNT} tables; expected tenants, api_keys, jobs"; exit 1; }
echo "restore verified: ${COUNT} tables"
```

- [ ] **Step 3: Verify both are valid shell**

```bash
cd archiagent-viewer
chmod +x deploy/backup.sh deploy/restore-check.sh
bash -n deploy/backup.sh && bash -n deploy/restore-check.sh && echo "syntax ok"
```

Expected: `syntax ok`

- [ ] **Step 4: Commit**

```bash
cd archiagent-viewer
git add deploy/backup.sh deploy/restore-check.sh
git commit -m "feat(deploy): nightly pg_dump, and a restore drill that runs

Artifacts are regenerable from their source upload; the job index is
not, so Postgres is what needs the offsite copy.

backup.sh refuses a suspiciously small dump, because an empty dump is
worse than none: it looks like success. restore-check.sh restores the
newest dump into a scratch database and counts tables, so the backup is
tested rather than believed."
```

---

### Task 11: The provisioning runbook

**Files:**
- Create: `archiagent-viewer/deploy/README.md`

- [ ] **Step 1: Write the runbook**

````markdown
# Deploying planto3d

One Hostinger KVM 2, Ubuntu 24.04 LTS, Bangalore. Spec:
`lerneanLabs-archiAgent/docs/superpowers/specs/2026-10-04-phase6-public-deployment-design.md`

## 1. Buy the VPS

| Setting | Value |
|---|---|
| Plan | KVM 2 — 2 vCPU / 8 GB / 100 GB NVMe |
| Location | Bangalore |
| OS | Ubuntu 24.04 LTS (the "with Docker" image if offered) |
| Control panel | **None.** A panel template binds :80/:443 and fights Caddy |
| Auth | Upload an SSH public key at creation |

## 2. DNS — before the first deploy

Five A records at the registrar, all to the VPS IPv4:

| Host | Type | Value |
|---|---|---|
| `planto3d.in` | A | VPS IP |
| `www.planto3d.in` | A | VPS IP |
| `api.planto3d.in` | A | VPS IP |
| `s3.planto3d.in` | A | VPS IP |
| `planto3d.si` | A | VPS IP |

- Delete the registrar's parking records.
- Add **no** AAAA unless Hostinger assigned IPv6 — an AAAA pointing nowhere
  gives intermittent IPv6-only failures that read as random downtime.
- `s3.planto3d.in` is **not optional**: presigned URLs sign the host, so the
  browser must reach the same name the signature was made for.

Verify before continuing:

```bash
for h in planto3d.in www.planto3d.in api.planto3d.in s3.planto3d.in planto3d.si; do
  echo -n "$h -> "; dig +short "$h" | tail -1
done
```

Caddy orders certificates at boot; deploying before DNS resolves burns
Let's Encrypt rate limit on failures.

## 3. Harden the box

```bash
ssh root@VPS_IP
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/ && chown deploy:deploy /home/deploy/.ssh/authorized_keys

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl reload ssh

ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable

apt-get update && apt-get install -y unattended-upgrades fail2ban
systemctl enable --now fail2ban
```

Open a **second** SSH session as `deploy` and confirm it works before closing
the root one.

If Docker was not preinstalled:

```bash
curl -fsSL https://get.docker.com | sh
```

## 4. Clone

```bash
sudo install -d -o deploy -g deploy /srv/planto3d && cd /srv/planto3d
git clone https://github.com/adityamathur20/lerneanLabs-archiAgent.git
git clone https://github.com/adityamathur20/lerneanLabs-archiViewer.git archiagent-viewer
```

If the viewer repo is private, give the box a read-only deploy key first:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/viewer_deploy -C "planto3d-vps"
cat ~/.ssh/viewer_deploy.pub   # add at repo -> Settings -> Deploy keys (read-only)
cat >> ~/.ssh/config <<'EOF'
Host github-viewer
  HostName github.com
  User git
  IdentityFile ~/.ssh/viewer_deploy
EOF
git clone git@github-viewer:adityamathur20/lerneanLabs-archiViewer.git archiagent-viewer
```

## 5. Secrets

```bash
cd /srv/planto3d/archiagent-viewer
cp .env.example .env && chmod 600 .env
for k in POSTGRES_PASSWORD GARAGE_RPC_SECRET GARAGE_ADMIN_TOKEN; do
  sed -i "s|^${k}=.*|${k}=$(openssl rand -hex 24)|" .env
done
# Then edit .env and paste ANTHROPIC_API_KEY. S3 keys come from step 6.
```

## 6. Start storage, then provision it

```bash
cd /srv/planto3d/archiagent-viewer
docker compose -f docker-compose.prod.yml up -d garage
./deploy/provision-garage.sh        # prints the key id and secret
# paste both into .env as ARCHIAGENT_SERVICE_S3_ACCESS_KEY / _SECRET_KEY
```

## 7. Build the viewer, migrate, start

```bash
cd /srv/planto3d/archiagent-viewer
docker run --rm -v "$PWD":/app -w /app -e VITE_API_BASE=https://api.planto3d.in \
  node:22-alpine sh -c "npm ci && npm run build"

docker compose -f docker-compose.prod.yml build          # ~10 min, once
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f caddy   # watch certificates issue
```

## 8. Issue yourself a tenant and key

```bash
cd /srv/planto3d/archiagent-viewer
docker compose -f docker-compose.prod.yml exec -T api python - <<'PY'
from archiagent_service.db import session_scope
from archiagent_service.auth import issue_key
from archiagent_service.models import Tenant, ulid
with session_scope() as s:
    t = Tenant(id=ulid(), name="founder"); s.add(t); s.flush()
    print("tenant", t.id); print("key", issue_key(s, t.id))
PY
```

Save the key — only its hash is stored. Paste it into the viewer's key field at
`https://planto3d.in`.

## 9. Verify (spec §12)

```bash
curl -fsS https://api.planto3d.in/healthz && echo " <- healthz ok"
curl -sI https://planto3d.si | grep -i '^location'        # -> https://planto3d.in
KEY=ak_...
# upload a DXF
U=$(curl -fsS -X POST https://api.planto3d.in/v1/uploads -H "Authorization: Bearer $KEY" \
     -H 'content-type: application/json' -d '{"filename":"plan.dxf","bytes":1234}')
echo "$U"   # contains a job id and a presigned PUT url
# PUT the file to that url, then:
curl -fsS -X POST "https://api.planto3d.in/v1/jobs/<id>/start" -H "Authorization: Bearer $KEY"
curl -fsS "https://api.planto3d.in/v1/jobs/<id>" -H "Authorization: Bearer $KEY"
# another tenant's id must 404, never 403:
curl -s -o /dev/null -w '%{http_code}\n' "https://api.planto3d.in/v1/jobs/ZZZZ" -H "Authorization: Bearer $KEY"
```

Then, **in a browser** (this is what proves the signing host, §4.3): open
`https://planto3d.in`, enter the key, and confirm the model lists and renders.
A download that works under `curl` on the box but not in the browser is the
presign-host or CORS failure, not a storage fault.

Finally:

```bash
./deploy/backup.sh && ./deploy/restore-check.sh
docker compose -f docker-compose.prod.yml down && docker compose -f docker-compose.prod.yml up -d
# jobs survive; caddy must NOT re-request certificates
```

## 10. Backups

Add to `deploy`'s crontab (`crontab -e`):

```
17 2 * * * cd /srv/planto3d/archiagent-viewer && ./deploy/backup.sh >> /var/log/planto3d-backup.log 2>&1
23 3 * * 0 cd /srv/planto3d/archiagent-viewer && ./deploy/restore-check.sh >> /var/log/planto3d-backup.log 2>&1
```

**The offsite copy is still manual and is the part that matters.** Pick one:
`rclone` to Backblaze B2 (free to 10 GB), or `scp` to another machine. A dump
that lives only on the disk it protects is not a backup.

## 11. Updating

```bash
cd /srv/planto3d/archiagent-viewer && git pull
cd ../lerneanLabs-archiAgent && git pull && cd -
docker run --rm -v "$PWD":/app -w /app -e VITE_API_BASE=https://api.planto3d.in \
  node:22-alpine sh -c "npm ci && npm run build"
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
```

After Task 12's GHCR pipeline, `build` becomes `pull`.

## Known limitations

- **DXF and PDF only.** `.dwg` returns 400: the ODA converter is a macOS bundle
  and its terms restrict hosted use.
- **Multi-page PDFs convert page 0.** `build_command` plumbs no `page` option.
- **No signup and no upload UI.** Jobs are created with `curl` and a
  hand-issued key; the browser holds that key in `localStorage`. Phase 7.
````

- [ ] **Step 2: Check every command block is syntactically valid shell**

```bash
cd archiagent-viewer/deploy
awk '/^```bash$/,/^```$/' README.md | grep -v '^```' > /tmp/runbook-check.sh
bash -n /tmp/runbook-check.sh 2>&1 | head
```

Expected: no syntax errors reported. Placeholder lines (`KEY=ak_...`, `<id>`) are fine — they parse.

- [ ] **Step 3: Commit**

```bash
cd archiagent-viewer
git add deploy/README.md
git commit -m "docs(deploy): the provisioning runbook, start to verified

Ordered so the mistakes that are expensive cannot happen: DNS before the
first up -d (Caddy orders certs at boot and failures burn rate limit),
Garage provisioned before the api needs its keys, and migrations as an
explicit step.

Verification ends in a browser on purpose: a download that works under
curl on the box but fails in a browser is the presign-host or CORS
failure, and nothing else catches it."
```

---

### Task 12: Build images in CI, so the box never compiles

**Files:**
- Create: `archiagent-viewer/.github/workflows/images.yml`
- Modify: `archiagent-viewer/docker-compose.prod.yml` (`build:` → `image:` with a build fallback)
- Modify: `archiagent-viewer/deploy/README.md` (§11 becomes a pull)

- [ ] **Step 1: Write the workflow**

```yaml
# archiagent-viewer/.github/workflows/images.yml
name: images
on:
  push:
    branches: [main]
    tags: ["v*"]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions: {contents: read, packages: write}
    steps:
      - uses: actions/checkout@v4
        with: {path: archiagent-viewer}

      # The worker image needs BOTH trees, so the build context is their parent.
      - uses: actions/checkout@v4
        with:
          repository: adityamathur20/lerneanLabs-archiAgent
          path: lerneanLabs-archiAgent

      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: api
        uses: docker/build-push-action@v6
        with:
          context: archiagent-viewer/service
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/planto3d-api:latest
            ghcr.io/${{ github.repository_owner }}/planto3d-api:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: worker
        uses: docker/build-push-action@v6
        with:
          context: .
          file: archiagent-viewer/service/Dockerfile.worker
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/planto3d-worker:latest
            ghcr.io/${{ github.repository_owner }}/planto3d-worker:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Point compose at the registry, keeping build as a fallback**

In `docker-compose.prod.yml`, change the `api` service's `build: ./service` to:

```yaml
    image: ghcr.io/adityamathur20/planto3d-api:${IMAGE_TAG:-latest}
    build: ./service
```

and the `worker` service's build block to:

```yaml
    image: ghcr.io/adityamathur20/planto3d-worker:${IMAGE_TAG:-latest}
    build:
      context: ..
      dockerfile: archiagent-viewer/service/Dockerfile.worker
```

With both keys present, `docker compose pull` fetches and `docker compose build` still works — and `IMAGE_TAG=<sha>` is the rollback.

- [ ] **Step 3: Verify compose still validates and the guard test still passes**

```bash
cd archiagent-viewer
cp .env.example .env && sed -i.bak 's/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=x/' .env
docker compose -f docker-compose.prod.yml config | grep -E 'image: ghcr' && echo "registry images wired"
node --test tests/compose-guard.test.mjs
rm -f .env .env.bak
```

Expected: both ghcr image lines, then 5 passing guard tests.

- [ ] **Step 4: Replace runbook §11 with the pull-based update**

```markdown
## 11. Updating

```bash
cd /srv/planto3d/archiagent-viewer && git pull
docker run --rm -v "$PWD":/app -w /app -e VITE_API_BASE=https://api.planto3d.in \
  node:22-alpine sh -c "npm ci && npm run build"
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
```

Rollback: `IMAGE_TAG=<previous-sha> docker compose -f docker-compose.prod.yml up -d`

The box compiles nothing. `docker compose build` remains available if CI is down.
```

- [ ] **Step 5: Commit**

```bash
cd archiagent-viewer
git add .github/workflows/images.yml docker-compose.prod.yml deploy/README.md
git commit -m "ci: build both images in Actions, publish to GHCR

A 2-vCPU box should not compile ifcopenshell while serving traffic. The
worker build checks out both repositories, because its context is their
parent.

compose keeps both image: and build:, so pull is the normal path, build
still works if CI is down, and IMAGE_TAG=<sha> is the rollback."
```
