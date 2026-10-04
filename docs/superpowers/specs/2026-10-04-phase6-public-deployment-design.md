# Phase 6 — Public Deployment — Design

**Date:** 2026-10-04
**Status:** proposed
**Builds on:** `2026-09-26-webapp-three-tier-design.md` (§3 artifact contract, §7 SaaS design)
**Domains:** `planto3d.in` (primary), `planto3d.si` (held)

---

## 1. What this builds

Phase 6 puts **what already exists** on a public host. It adds no product
features. The deliverable is: a stranger with an API key can POST a DXF **or PDF** to
`api.planto3d.in`, poll the job, and open the resulting IFC in a browser at
`planto3d.in` over TLS.

Phases 3–5 built the service, the DWG path and the IDS gate on a laptop against
`docker compose up`. Nothing about them is deployed. This phase closes only that
gap, and deliberately refuses to grow: accounts, projects and billing are
Phase 7 and Phase 8, specified separately.

### 1.1 Why this phase is separate from accounts

A deployment that works is a prerequisite for an account system, not a
consequence of one. Building signup against infrastructure that has never run
in production means debugging OAuth redirects, TLS, container networking and
session cookies simultaneously, with no known-good baseline to bisect against.

Phase 6 ends with a URL that works. Phase 7 then changes one thing at a time.

---

## 2. Scope

### 2.1 Ships

- **DXF and PDF ingest**, which needs no code change: `ALLOWED_SUFFIXES` in
  `uploads.py` is already `{".dxf", ".dwg", ".pdf"}` and `build_command` already
  maps `.pdf` to `--pdfFilePath`
- Two container images (`api`, `worker`) built from the existing service
- `docker-compose.prod.yml`: Caddy, Postgres, Redis, Garage, api, worker
- Garage as the S3 backend, on the VPS disk, with `storage.py` unmodified
- Automatic TLS for `planto3d.in`, `api.planto3d.in`, `planto3d.si`
- The viewer, built to static files, with its three dev endpoints replaced by
  the real API (§8)
- A provisioning runbook: VPS purchase spec, OS hardening, DNS records, deploy
  keys, first deploy, verification
- Nightly `pg_dump`, and an offsite copy of it
- GitHub Actions building both images to GHCR, so deploy #2 onward is a pull

### 2.2 Does not ship, by decision

| Deferred | Why |
|---|---|
| **DWG upload** | The converter in use is a macOS `.app` bundle; the Linux build is a different binary needing an X11/xvfb shim. Independently, ODA's redistribution terms restrict bundling into a hosted service — the service README already records this as a business prerequisite. Phase 6 launches **DXF and PDF**, and `.dwg` stays refused — a configured state the service was built to handle: it returns 400 for `.dwg` on a worker with no converter. |
| **Browser upload UI** | Uploading from the browser requires a credential in the browser. The only credential that exists today is a tenant API key, which grants that tenant's *entire* account. Putting one in a web page would be a security defect, not a shortcut. Upload arrives in Phase 7 with session auth. |
| **Accounts, projects, plan limits** | Phase 7. |
| **Billing, LLM quotas** | Phase 8. Until then, LLM spend is bounded by key distribution: there is no public signup, so only keys issued by hand can spend money. |

### 2.3 Known limitations this leaves

**Multi-page PDFs always convert page 0.** The CLI takes `--page` (default 0),
but `build_command` in `pipeline.py` plumbs only `units-per-foot`, `height` and
`walls` from the job's `options`. A two-page plan set therefore yields the first
page. Exposing `page` is a one-line addition to `build_command` plus a field in
the upload request; it is listed here rather than silently inherited.

### 2.4 The honest limitation this leaves

After Phase 6, `planto3d.in` is a **viewer for jobs that already exist**, and
jobs are created with `curl` and an API key. That is a private beta, not a
product. It is stated here so that nobody discovers it at launch.

---

## 3. Architecture

One Hostinger KVM 2 (2 vCPU / 8 GB / 100 GB NVMe), Ubuntu 24.04 LTS, Bangalore.
One compose file. Six containers.

```
   planto3d.in ─────────┐
   api.planto3d.in ─────┤   caddy          :80 :443   TLS, Let's Encrypt
   planto3d.si ─────────┘     │
                              ├── / ──────▶ viewer dist/  (static files)
                              └── api.* ──▶ api        :8000  FastAPI
                                                │
                    ┌───────────────┬───────────┴────────┐
                    │               │                    │
                 garage         postgres              redis
                 :3900 S3       :5432                 :6379
                    │               │                    │
                    └────────── worker ──────────────────┘
                                   │
                        subprocess: python -m archiagent
```

Only Caddy publishes ports to the host. Postgres, Redis, Garage and the API are
reachable only on the compose network. This matters more than it looks: Garage's
S3 endpoint is unauthenticated-by-default at the network level and secured by
access keys, so it must never be bound to a public interface.

### 3.1 Hostname routing

| Hostname | Serves |
|---|---|
| `planto3d.in` | viewer static build |
| `www.planto3d.in` | 301 → `planto3d.in` |
| `api.planto3d.in` | FastAPI |
| `s3.planto3d.in` | Garage's S3 API (§4.3 — required, not optional) |
| `planto3d.si` | 301 → `planto3d.in` |

`.si` is held defensively and redirected. It is not a second deployment, and it
is not a locale: serving the same app on two TLDs would split sessions and
duplicate content for no gain. If it ever becomes a marketing site, that is its
own decision.

---

## 4. Storage: Garage

`storage.py` already declares the seam this phase relies on:

> *"everything else calls put/get/presign/delete_prefix and never learns whether
> that is S3Mock, MinIO, S3 or a directory. Swapping the backing store is a
> config change, not a rewrite."*

Phase 6 takes that at its word. Garage (`dxflrs/garage:v2.3.0`) speaks S3
including presigned URLs, so **no application code changes**. What changes is
`ARCHIAGENT_SERVICE_S3_ENDPOINT`, and the access keys.

### 4.1 Why Garage and not the alternatives

| Option | Verdict |
|---|---|
| **Garage** | Chosen. Apache-2.0, single binary, ~200 MB RAM, built for small self-hosted nodes, images freely pullable. `--single-node` (added in v2.3.0) configures a one-node cluster with no replication — which is correct here, because replication across one disk is theatre. |
| MinIO | Rejected: the service README already records that its images stopped being publicly pullable (Docker Hub and quay.io both 401 as of 2026-09-27). |
| S3Mock | Rejected for production. It is a test double; it is in `docker-compose.yml` for exactly that reason. |
| Filesystem backend | Rejected. `presign_get`/`presign_put` are S3 features; replacing them means writing signed-URL endpoints in FastAPI — new code on the auth-critical path, to avoid running a container that costs 200 MB. |
| Backblaze B2 / AWS S3 | Viable and better for durability, rejected for Phase 6 on cost, and because it moves artifacts out of India. Reconsider when there is revenue. Because `storage.py` is untouched, this remains an endpoint change later. |

### 4.3 Garage must be publicly reachable, on one canonical hostname

This is the non-obvious requirement of the whole phase, and skipping it
produces a failure that cannot appear in development.

`get_artifact` returns **a 302 to a presigned URL**, by deliberate design
("302, never a proxy"). A SigV4 presigned URL always signs the `host` header,
so the signature is only valid for the hostname it was generated for. Three
consequences follow:

1. If `S3_ENDPOINT` is the compose-internal `http://garage:3900`, the browser is
   redirected to a hostname that does not resolve outside Docker. The download
   fails for every user.
2. Rewriting the host of an already-signed URL invalidates the signature. There
   is no post-hoc fix.
3. Therefore Garage needs a public hostname, **and api, worker and browser must
   all use the same one**, or each will sign for a host the others reject.

The resolution: `ARCHIAGENT_SERVICE_S3_ENDPOINT = https://s3.planto3d.in` for
every component, with Caddy reverse-proxying that hostname to `garage:3900`.
The worker's own uploads then hairpin through Caddy over loopback, which is
negligible and buys one canonical signing host.

**Garage still publishes no host port.** Only Caddy does; `s3.planto3d.in` is a
Caddy route into the compose network, so §3's rule holds — the store is never
bound to a public interface directly, and its access keys remain the
authorization boundary.

### 4.4 Cross-origin access is required at two hops

The viewer is served from `planto3d.in` and talks to `api.planto3d.in`, which
is a different origin. `api.py` installs **no CORS middleware today** — there
was never a cross-origin caller, because the dev server served both from
`localhost:5173`. Without it every viewer request fails in the browser while
working perfectly under `curl`.

Two hops need it, for different reasons:

| Hop | Needs | Why |
|---|---|---|
| `planto3d.in` → `api.planto3d.in` | FastAPI `CORSMiddleware`, allowing the viewer origin and the `authorization` header | These requests carry a bearer token, which makes them non-simple and triggers an `OPTIONS` preflight that FastAPI must answer |
| `api.planto3d.in` → 302 → `s3.planto3d.in` | `Access-Control-Allow-Origin` on the **Garage** response | `fetch` follows the redirect, and the *final* response is the one whose CORS headers are checked against the original origin |

The second hop is served by **Caddy adding the header on the `s3` route**, not
by per-bucket S3 CORS configuration: it keeps the policy in one file next to the
routing it belongs to. A presigned GET sends no custom headers — the credential
is in the query string — so it is a simple request and needs no preflight, only
the response header.

**The bearer token must not reach Garage.** Browsers strip `Authorization` on
cross-origin redirects, so this holds by default; it is recorded because a
forwarded bearer token would collide with Garage's own query-string signature
and fail in a way that looks like a storage bug.

### 4.2 Operational facts that will bite

- **Garage has no container healthcheck mechanism.** Compose cannot
  `depends_on: condition: service_healthy` it, so the api and worker must
  tolerate a not-yet-ready store at boot.
- **Nothing creates the bucket in production.** `storage.py` defines
  `ensure_bucket()`, but its only caller is `tests/conftest.py` — the running
  service never creates its own bucket. Phase 6 therefore creates the bucket,
  the access key, and that key's permissions **once**, with the `garage` CLI, as
  a documented provisioning step. This is preferable to calling
  `ensure_bucket()` at startup: a service that can create its own bucket can
  also silently create the *wrong* one after a mistyped endpoint, then appear
  healthy while writing into a store nobody reads.
- **Garage data on the VPS disk is one disk.** See §10 — this is why a backup
  that lives on the same disk does not count.

---

## 5. Two images, not one

| Image | Contains | Why |
|---|---|---|
| `api` | `archiagent_service` only | It never needs ifcopenshell. Keeping archiAgent out keeps it small and keeps the §5.2 rule ("the service never imports archiagent") structurally true, not merely observed. |
| `worker` | `archiagent_service` **and** archiAgent, in **two separate virtualenvs** | The worker shells out to `{ARCHIAGENT_PYTHON} -m archiagent`. Two venvs in one image preserves that subprocess boundary — a different interpreter, a different dependency set — while keeping deployment to one image. |

`ARCHIAGENT_SERVICE_ARCHIAGENT_PYTHON` points at the archiAgent venv's
interpreter inside the worker image; `ARCHIAGENT_SERVICE_ARCHIAGENT_CWD` at its
checkout. Both default to host paths today (`config.py` derives them from
`REPO_ROOT`) and must be set explicitly in the container.

**Python is pinned to 3.12** in both images. `requires-python = ">=3.12"` admits
newer, and the dev venv is on 3.14, but 3.12 has the widest wheel availability
for ifcopenshell, pymupdf, shapely and opencv — a production image is the wrong
place to be compiling a geometry kernel from source.

---

## 6. Configuration and secrets

One `.env` on the VPS, never committed, generated from a committed
`.env.example`. The dev defaults in `config.py` are replaced wholesale:

| Variable | Dev default | Production |
|---|---|---|
| `ARCHIAGENT_SERVICE_DATABASE_URL` | `archiagent:archiagent@localhost:5433` | generated password, host `postgres:5432` |
| `ARCHIAGENT_SERVICE_REDIS_URL` | `localhost:6380` | `redis:6379` |
| `ARCHIAGENT_SERVICE_S3_ENDPOINT` | `localhost:9090` | `https://s3.planto3d.in` (§4.3 — **not** `garage:3900`) |
| `ARCHIAGENT_SERVICE_S3_ACCESS_KEY` / `_SECRET_KEY` | `test` / `test` | issued by `garage key create` |
| `ANTHROPIC_API_KEY` | from shell | **worker only**; the api never needs it. Anthropic is the chosen provider |
| `ACME_EMAIL` | n/a | `lerneantechlabs@gmail.com` — Caddy's Let's Encrypt account |

Secrets are generated on the VPS (`openssl rand`), not chosen by hand and not
transmitted. The `.env` is `chmod 600`, owned by the deploy user.

**`config.py` needs no change for any of this** — `BaseSettings` with
`env_prefix="ARCHIAGENT_SERVICE_"` already reads every one of these from the
environment. That is the design working as intended.

---

## 7. Code transfer and deployment

### 7.1 The two repositories

| Local | Remote | Observed visibility |
|---|---|---|
| `archiagent-viewer/` | `github.com/adityamathur20/lerneanLabs-archiViewer` | **not publicly readable** — private, or still empty |
| `lerneanLabs-archiAgent/` | `github.com/adityamathur20/lerneanLabs-archiAgent` | **public** (`git ls-remote` returns HEAD unauthenticated) |

Two consequences:

1. **The viewer has never been pushed.** Its local git has no remote configured,
   so the first task of this phase is `git remote add` and a push. Until that
   lands there is nothing for the VPS to clone, because the service tier lives
   in that repository.
2. **archiAgent being public means a deploy key is unnecessary for it** — a
   plain HTTPS clone works. It also means anything ever committed there is
   public, so a secret scan of its history (`.env`, API keys, tokens) is a
   prerequisite to launch, not a nicety. If the viewer repo is private, it needs
   one read-only deploy key; if it is made public, the same scan applies to it.

### 7.2 Mechanism

**`git clone` over read-only deploy keys.** One ed25519 keypair generated *on
the VPS* per repository, public half added as a deploy key on GitHub. Not
`scp`, not `rsync`: those leave no record of which commit is running, make
rollback a guess, and turn every update into manual file comparison.

```
/srv/planto3d/
├── .env                      (600, never in git)
├── docker-compose.prod.yml   (from the viewer repo)
├── archiagent-viewer/        (clone)
└── lerneanLabs-archiAgent/   (clone)
```

### 7.3 Deploy sequence

First deploy builds on the box — one-time, ~10 minutes, acceptable with zero
traffic and no CI dependency:

```
git -C archiagent-viewer pull && git -C lerneanLabs-archiAgent pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
```

The final task of this phase adds GitHub Actions publishing both images to
GHCR, after which deploys are `docker compose pull && up -d` and the 2-vCPU box
never compiles anything. Rollback becomes `pull` of the previous tag.

**Migrations run as an explicit step, never on API startup.** Two API replicas
racing `alembic upgrade head` is a corrupted schema; and an app that migrates on
boot cannot be rolled back without rolling back the database.

### 7.4 Hostinger's Docker Manager is not the deploy mechanism

Hostinger's panel offers a Docker Manager: compose-from-URL or pasted YAML,
lifecycle buttons, aggregated logs. It is declined for deployment, for reasons
that are structural rather than stylistic:

- **The first deploy builds from two source trees.** The worker image needs both
  this repo and `archiagent-viewer`; a compose file URL supplies no build
  context. Docker Manager assumes the images already exist.
- **Secrets would move into the panel.** `.env` is generated on the box and
  `chmod 600`. Pasting the Postgres password, Garage keys and LLM key into a
  web form puts them in Hostinger's database instead of a file under our
  control.
- **It creates a second source of truth.** Production editable in a web form
  means the repository no longer describes what is running.
- **It covers none of the steps that matter here:** `alembic upgrade head` as a
  discrete pre-start step, one-time `garage bucket create` / `key create`, and
  deploy-key setup are SSH work either way.

Two things from Hostinger *are* taken: the **Docker-preinstalled OS template**
(saves a provisioning step, no lock-in), and Docker Manager as a **read-only
dashboard** for container status and logs from a browser.

Note for later: once §7.3's GHCR pipeline lands, the compose file references
prebuilt images and needs no build context, at which point compose-from-URL
becomes technically possible. It still would not address secrets or migrations,
so this decision stands.

---

## 8. The viewer's production data path

This is the only application code this phase changes, and it is required: the
viewer's data source today is a **dev-server plugin**. `vite.config.js`
registers `/api/models`, `/api/model` and `/api/frag` inside
`configureServer()`, which Vite runs only for `vite dev`. A production build has
no server and therefore no data at all — deploying `dist/` unchanged yields a
viewer with an empty model list.

**Change:** replace those three fetches in `src/main.js` with the real API.

| Dev endpoint | Production |
|---|---|
| `GET /api/models` | `GET {API}/v1/jobs` → jobs with status `succeeded` |
| `GET /api/model?path=` | `GET {API}/v1/jobs/{id}/artifacts/plan.ifc` (302 → presigned) |
| `GET /api/frag?path=` | **removed** — no server-side `.frag` exists (below) |

`VITE_API_BASE` selects the base URL; unset keeps today's dev behaviour, so
local development is unaffected.

**There is no server-side `.frag` in Phase 6, so that dev endpoint is removed
rather than repointed.** The worker cannot produce one: `ARTIFACT_PATTERNS` in
`pipeline.py` is `("*.ifc", "*.interpretation.json", "*.report.json",
"*.overlay.svg")`, and archiAgent is Python — while the converter,
`scripts/build-frag.mjs`, is Node and lives in the viewer repo. Serving a
precomputed `.frag` would mean adding Node to the worker image: a performance
optimisation, not a launch requirement.

Phase 6 therefore serves `plan.ifc` and the browser converts it in-page, which
is already its fallback path today (`src/ifc-to-frag.js`). **Invariant I2 holds
trivially: with no server-side cache, no stale cache can outrank the authored
IFC.** When server-side frag is added later, the sidecar check the dev
middleware performed must be reimplemented *server-side* — it must never move
into the browser, which cannot be trusted to enforce it.

**Credentials:** the viewer sends the API key as a bearer token, read from
`localStorage` and entered by hand on a settings screen. This is acceptable
*only* because Phase 6 is a private beta with hand-issued keys, and it is
replaced by session auth in Phase 7. It is the reason §2.2 refuses a public
upload form.

---

## 9. DNS and TLS

Five records, all `A` to the VPS IPv4 (plus `AAAA` if Hostinger assigns IPv6):

| Host | Type | Value |
|---|---|---|
| `planto3d.in` | A | VPS IP |
| `www.planto3d.in` | A | VPS IP |
| `api.planto3d.in` | A | VPS IP |
| `s3.planto3d.in` | A | VPS IP |
| `planto3d.si` | A | VPS IP |

**DNS is managed at the registrar directly** — no Cloudflare in front, so
Caddy's HTTP-01 challenge reaches the origin with no proxy caveats. Pre-existing
parking records must be deleted, and AAAA records must be absent unless
Hostinger actually assigns IPv6: an AAAA pointing nowhere while A works produces
intermittent IPv6-only failures that read as random downtime.

TLS is Caddy's automatic Let's Encrypt, registered to `lerneantechlabs@gmail.com`
— no certbot, no renewal cron, no expiry incident. Caddy must hold its `/data` volume across restarts or it will
re-request certificates and hit rate limits.

**DNS must resolve before the first `up -d`.** Caddy orders certificates at
startup; if the records are absent the ACME challenge fails and the failures
count toward Let's Encrypt's rate limit. Records first, then deploy.

---

## 10. Backups

Two things are stateful: Postgres (the job index) and Garage (every artifact).

- **Nightly `pg_dump`**, gzipped, written into Garage, and **copied off the
  VPS**. A dump that only exists on the disk it is protecting is not a backup.
- **Hostinger's weekly snapshot** covers the whole volume. It is a floor, not a
  plan: weekly means up to seven days of lost jobs.
- **Artifacts are regenerable, and that is the real backup.** Every artifact is
  derived from the uploaded source file by a pipeline whose inputs are recorded
  (`plan.interpretation.json` exists to replay). Losing Garage costs
  re-conversion; losing Postgres costs knowing what to re-convert. Postgres is
  therefore the asset that actually needs the offsite copy.

---

## 11. Resource budget

| Container | Idle RSS |
|---|---|
| postgres | ~400 MB |
| redis | ~150 MB |
| garage | ~200 MB |
| caddy | ~50 MB |
| api | ~300 MB |
| worker (idle) | ~500 MB |
| **total idle** | **~1.6 GB of 8 GB** |

RAM is comfortable. **CPU is the constraint**: 2 vCPUs shared between Postgres,
the API and an ifcopenshell conversion. One RQ worker with concurrency 1, so
conversions serialize rather than contend. Jobs queue; the queue is the honest
behaviour and `tenant_max_concurrent=2` already assumes a cap exists.

A second worker is a KVM 4 decision, not a Phase 6 one.

---

## 12. Verification

Phase 6 is done when, from a machine that is not the VPS:

1. `curl https://api.planto3d.in/healthz` → 200 over valid TLS
2. `curl https://planto3d.si -I` → 301 to `https://planto3d.in`
3. A DXF **and a PDF** uploaded with a real API key reach `succeeded`, and
   `plan.ifc`, `plan.report.json` and `plan.interpretation.json` download — the
   download followed from a browser, not curl on the box, since that is what
   proves §4.3's signing host is right
4. `planto3d.in` lists that job and renders its walls
5. `curl` with another tenant's job id → **404**, not 403 (the existing
   `auth.owned_job` guarantee, re-verified across the network)
6. `docker compose down && up -d` loses no jobs and re-issues no certificates
7. The nightly dump exists offsite and restores into a scratch database

Items 5 and 7 are the ones that are tempting to skip and expensive to have
skipped.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Garage's missing healthcheck leaves api/worker racing it at boot | Retry around the existing `ensure_bucket()`; no new abstraction |
| First build on 2 vCPUs is slow and competes with nothing yet | Accepted once; GHCR removes it for every later deploy |
| An API key in `localStorage` is a weak credential | Bounded: private beta, hand-issued keys, replaced in Phase 7. No public upload form until then |
| Hostinger shared vCPU contention stretches conversion times | Accepted at this scale; measurable via existing `timings_ms` |
| LLM spend from a leaked key | Bounded by key distribution; quotas are Phase 8. Keys are revocable — `api_keys` rows can be deleted |
| VPS loss | Postgres offsite nightly; artifacts regenerable from source (§10) |

---

## 14. What Phase 7 attaches to

Phase 7 (accounts) changes `Tenant` into the thing a `User` belongs to, adds
`Project`, and adds `project_id` to `Job`. Nothing in Phase 6 forecloses that:
`Job.prefix` is `{tenant_id}/{job_id}/`, and projects group jobs without
re-keying storage. The API-key path stays as the programmatic interface
alongside session auth, rather than being replaced.
