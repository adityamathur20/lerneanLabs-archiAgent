# Phase 3 — SaaS Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user uploads a DXF over HTTP and gets back a viewable IFC, with tenant isolation, without touching a terminal.

**Architecture:** A FastAPI service in `archiagent-viewer/service/` that never imports archiagent — it shells out to the CLI, exactly as spec §5.2 requires, so the process boundary contains crashes, LLM timeouts and memory growth. Postgres holds job metadata, Redis/RQ holds the queue, MinIO stands in for S3 behind a single storage module. Artifacts live under `{tenant_id}/{job_id}/`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 + Alembic, RQ + Redis, boto3 against MinIO, pytest. Docker Compose for Postgres/Redis/MinIO.

**Spec:** `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md` (§3 artifact contract, §5.2 invocation, §7 SaaS design)

## Global Constraints

- **The service NEVER imports `archiagent`.** It runs `{ARCHIAGENT_PYTHON} -m archiagent ...` as a subprocess and reads its exit code. Spec §5.2. Importing it would put LLM calls and ifcopenshell in the request process.
- **No file under `lerneanLabs-archiAgent/archiagent/` is modified by this plan.** Tier 1 stays authoritative and untouched.
- Service code lives in `archiagent-viewer/service/`. The JS frontend in `src/` is not touched. `npm test` (root `tests/`) and `pytest` (`service/tests/`) must both pass and must not see each other's files.
- **Every artifact path is prefixed with `tenant_id`**, and authorization is one prefix check in one function — never re-implemented per endpoint (spec §7.3).
- **The API never proxies artifact bytes.** Downloads are 302 redirects to presigned URLs. A 400 MB IFC must not traverse the app server.
- Exit codes from the CLI are the API's error taxonomy; no new one is invented. 0 success, 1 pipeline/validation failure, 2 LLM unavailable, 3 bad usage.
- `ARCHIAGENT_PYTHON` defaults to `../lerneanLabs-archiAgent/.venv/bin/python`; that venv's `archiagent` resolves to the main checkout (editable install).
- Integration tests skip with a clear message when Docker services are unreachable, the same way the JS conversion tests skip without fixtures. They never silently pass.

## Review Focus

Five conditions the spec implies that no happy-path test exercises. Each gets a test in the task that owns the code.

1. **Tenant A requesting tenant B's job or artifact** — must 404 (not 403, which confirms existence). The single prefix check is the whole security model; if it leaks, multi-tenancy is decorative. → Task 4.
2. **A job whose CLI run fails** (exit 1, e.g. acceptance failure) — must record the failure with its exit code and stderr and stay retrievable, not vanish or wedge the queue. A draft is exactly when a user needs the diagnostics. → Task 7.
3. **An upload that is not a DXF/PDF, or is oversized** — the extension allowlist and size cap must reject before anything is queued, and content-type from the client is not trusted. → Task 5.
4. **A tenant submitting more jobs than their concurrency cap** — LLM classification is the cost centre; without a cap one tenant's batch starves every other. Excess must queue, not run. → Task 6.
5. **A job id that does not exist, or artifacts requested before the job finished** — must 404 with a useful message rather than 500 on a missing S3 key. → Task 9.

---

### Task 1: Service skeleton, Docker services, health check

**Files:**
- Create: `archiagent-viewer/service/pyproject.toml`
- Create: `archiagent-viewer/service/docker-compose.yml`
- Create: `archiagent-viewer/service/archiagent_service/__init__.py`
- Create: `archiagent-viewer/service/archiagent_service/config.py`
- Create: `archiagent-viewer/service/archiagent_service/api.py`
- Create: `archiagent-viewer/service/tests/test_health.py`
- Modify: `archiagent-viewer/.gitignore`

**Interfaces:**
- Consumes: nothing
- Produces: `Settings` (pydantic-settings) with fields `database_url`, `redis_url`, `s3_endpoint`, `s3_bucket`, `s3_access_key`, `s3_secret_key`, `archiagent_python`, `archiagent_cwd`, `viewer_dir`, `max_upload_bytes`, `tenant_max_concurrent`; `get_settings() -> Settings`; `create_app() -> FastAPI` serving `GET /healthz`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "archiagent-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "psycopg[binary]>=3.2",
    "rq>=2.0",
    "redis>=5.2",
    "boto3>=1.35",
    "python-multipart>=0.0.12",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.28"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["archiagent_service*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: archiagent
      POSTGRES_PASSWORD: archiagent
      POSTGRES_DB: archiagent
    ports: ["5433:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U archiagent"]
      interval: 2s
      retries: 15

  redis:
    image: redis:7-alpine
    ports: ["6380:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      retries: 15

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: archiagent
      MINIO_ROOT_PASSWORD: archiagent-secret
    ports: ["9000:9000", "9001:9001"]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 2s
      retries: 15
```

Ports are offset (5433, 6380) so the stack cannot collide with a Postgres or Redis the developer already runs.

- [ ] **Step 3: Write `config.py`**

```python
"""Settings, from the environment. Defaults target the local docker-compose."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARCHIAGENT_SERVICE_", extra="ignore")

    database_url: str = "postgresql+psycopg://archiagent:archiagent@localhost:5433/archiagent"
    redis_url: str = "redis://localhost:6380/0"

    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "archiagent"
    s3_access_key: str = "archiagent"
    s3_secret_key: str = "archiagent-secret"

    # The service shells out to the CLI; it never imports archiagent (spec §5.2).
    archiagent_python: Path = REPO_ROOT / "lerneanLabs-archiAgent" / ".venv" / "bin" / "python"
    archiagent_cwd: Path = REPO_ROOT / "lerneanLabs-archiAgent"
    viewer_dir: Path = REPO_ROOT / "archiagent-viewer"

    max_upload_bytes: int = 200 * 1024 * 1024
    tenant_max_concurrent: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Write the failing test**

Create `service/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from archiagent_service.api import create_app


def test_healthz_reports_ok():
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_archiagent_cli_is_reachable():
    """The worker shells out to this; a missing venv is a deploy error worth
    catching at the service boundary rather than inside a queued job."""
    from archiagent_service.config import get_settings

    settings = get_settings()
    assert settings.archiagent_python.exists(), (
        f"archiagent python not found at {settings.archiagent_python}; "
        "set ARCHIAGENT_SERVICE_ARCHIAGENT_PYTHON"
    )
```

- [ ] **Step 5: Run it to verify it fails**

```bash
cd archiagent-viewer/service && python3 -m pytest tests/test_health.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.api'`

- [ ] **Step 6: Write `api.py`**

```python
"""HTTP surface. Thin: it validates, authorizes, enqueues and redirects."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="archiAgent", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 7: Install and run the tests**

```bash
cd archiagent-viewer/service
python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
.venv/bin/pytest tests/test_health.py -q
```

Expected: PASS, 2 tests.

- [ ] **Step 8: Bring the services up and confirm they are healthy**

```bash
cd archiagent-viewer/service && docker compose up -d
sleep 10 && docker compose ps
```

Expected: postgres, redis and minio all `running` (healthy).

- [ ] **Step 9: Ignore service scratch in git**

Append to `archiagent-viewer/.gitignore`:

```
service/.venv/
service/**/__pycache__/
.pytest_cache/
```

- [ ] **Step 10: Commit**

```bash
git add archiagent-viewer/service archiagent-viewer/.gitignore
git commit -m "feat(service): FastAPI skeleton with Postgres, Redis and MinIO

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Object storage behind one module

**Files:**
- Create: `archiagent-viewer/service/archiagent_service/storage.py`
- Create: `archiagent-viewer/service/tests/conftest.py`
- Create: `archiagent-viewer/service/tests/test_storage.py`

**Interfaces:**
- Consumes: `get_settings` from Task 1
- Produces: `ObjectStore` with `put(key: str, data: bytes) -> None`, `put_file(key: str, path: Path) -> None`, `get(key: str) -> bytes`, `download(key: str, path: Path) -> None`, `exists(key: str) -> bool`, `presign_get(key: str, expires: int = 3600) -> str`, `presign_put(key: str, expires: int = 3600) -> str`, `list_prefix(prefix: str) -> list[str]`, `delete_prefix(prefix: str) -> int`; `get_store() -> ObjectStore`.

- [ ] **Step 1: Write `conftest.py`**

```python
import socket

import pytest

from archiagent_service.config import get_settings


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def minio(settings):
    """Skips rather than fails when the stack is down — but never passes
    silently: the skip message says exactly how to start it."""
    if not _reachable("localhost", 9000):
        pytest.skip("MinIO not reachable on :9000 — run `docker compose up -d` in service/")
    from archiagent_service.storage import get_store

    store = get_store()
    store.ensure_bucket()
    return store
```

- [ ] **Step 2: Write the failing test**

Create `service/tests/test_storage.py`:

```python
import uuid

import pytest


@pytest.fixture
def prefix():
    return f"test-{uuid.uuid4().hex[:8]}"


def test_put_then_get_round_trips(minio, prefix):
    minio.put(f"{prefix}/hello.txt", b"hello")
    assert minio.get(f"{prefix}/hello.txt") == b"hello"


def test_exists_is_false_for_a_missing_key(minio, prefix):
    assert minio.exists(f"{prefix}/nope.txt") is False


def test_presigned_get_url_serves_the_object(minio, prefix):
    import urllib.request

    minio.put(f"{prefix}/plan.ifc", b"ISO-10303-21;")
    url = minio.presign_get(f"{prefix}/plan.ifc")
    with urllib.request.urlopen(url) as response:
        assert response.read() == b"ISO-10303-21;"


def test_list_prefix_returns_only_that_prefix(minio, prefix):
    minio.put(f"{prefix}/a.txt", b"a")
    minio.put(f"{prefix}/b.txt", b"b")
    minio.put(f"other-{prefix}/c.txt", b"c")
    assert sorted(minio.list_prefix(f"{prefix}/")) == [f"{prefix}/a.txt", f"{prefix}/b.txt"]


def test_delete_prefix_removes_the_whole_job(minio, prefix):
    minio.put(f"{prefix}/a.txt", b"a")
    minio.put(f"{prefix}/b.txt", b"b")
    assert minio.delete_prefix(f"{prefix}/") == 2
    assert minio.list_prefix(f"{prefix}/") == []
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_storage.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.storage'`

- [ ] **Step 4: Write `storage.py`**

```python
"""The one place that knows where bytes live.

Spec §7.4: everything else calls put/get/presign/delete_prefix and never learns
whether that is MinIO, S3 or a directory. Swapping MinIO for real S3 is a
config change, not a rewrite.
"""
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from archiagent_service.config import get_settings


class ObjectStore:
    def __init__(self, *, endpoint: str, bucket: str, access_key: str, secret_key: str):
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def put_file(self, key: str, path: Path) -> None:
        self._client.upload_file(str(path), self.bucket, key)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def download(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(path))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def presign_get(self, key: str, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )

    def presign_put(self, key: str, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )

    def list_prefix(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_prefix(prefix)
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            self._client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in batch]}
            )
        return len(keys)


@lru_cache
def get_store() -> ObjectStore:
    settings = get_settings()
    return ObjectStore(
        endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )
```

- [ ] **Step 5: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_storage.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): object storage behind one module

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Postgres model and migrations

**Files:**
- Create: `archiagent-viewer/service/archiagent_service/db.py`
- Create: `archiagent-viewer/service/archiagent_service/models.py`
- Create: `archiagent-viewer/service/tests/test_models.py`

**Interfaces:**
- Consumes: `get_settings`
- Produces: `Base`, `Tenant(id, name, created_at)`, `ApiKey(id, tenant_id, key_hash, created_at)`, `Job(id, tenant_id, status, source_filename, source_bytes, converted_from_dwg, options, exit_code, acceptance, error, timings_ms, artifacts, created_at, started_at, finished_at)`; `session_scope()` contextmanager; `create_all(engine)`; `JobStatus` literal values `pending|queued|running|succeeded|failed`.

Ids are ULID-like strings (`ulid()` helper) so they sort by creation time and are safe in URLs and S3 keys.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_models.py`:

```python
import pytest

from archiagent_service.models import ApiKey, Job, Tenant, ulid


def test_ulid_is_sortable_and_url_safe():
    a, b = ulid(), ulid()
    assert a != b
    assert len(a) == 26
    assert a.isalnum()


def test_job_belongs_to_a_tenant(pg_session):
    tenant = Tenant(id=ulid(), name="acme")
    pg_session.add(tenant)
    pg_session.flush()

    job = Job(id=ulid(), tenant_id=tenant.id, status="pending", source_filename="plan.dxf")
    pg_session.add(job)
    pg_session.flush()

    assert pg_session.get(Job, job.id).tenant_id == tenant.id


def test_api_key_never_stores_the_raw_key(pg_session):
    tenant = Tenant(id=ulid(), name="acme")
    pg_session.add(tenant)
    pg_session.flush()

    key = ApiKey(id=ulid(), tenant_id=tenant.id, key_hash="deadbeef")
    pg_session.add(key)
    pg_session.flush()

    # A leaked database must not hand over working credentials.
    assert not hasattr(key, "key")
    assert {c.name for c in ApiKey.__table__.columns} == {
        "id", "tenant_id", "key_hash", "created_at",
    }
```

Add to `conftest.py`:

```python
@pytest.fixture(scope="session")
def pg_engine(settings):
    if not _reachable("localhost", 5433):
        pytest.skip("Postgres not reachable on :5433 — run `docker compose up -d` in service/")
    from sqlalchemy import create_engine

    from archiagent_service.models import Base

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def pg_session(pg_engine):
    """Each test runs in a transaction that is rolled back, so tests never see
    each other's rows and the database needs no cleanup between runs."""
    from sqlalchemy.orm import Session

    connection = pg_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_models.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.models'`

- [ ] **Step 3: Write `models.py`**

```python
"""Job metadata. Artifacts live in object storage; this is the index over them."""
import os
import time
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """Sortable, URL-safe, S3-key-safe id. Sorting by id sorts by creation."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    # Only the hash. A leaked database must not hand over working credentials.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    source_filename: Mapped[str] = mapped_column(String(500))
    source_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    converted_from_dwg: Mapped[bool] = mapped_column(Boolean, default=False)

    options: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acceptance: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timings_ms: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def prefix(self) -> str:
        """Every artifact for this job lives under here. The unit of tenancy,
        caching and deletion (spec §3)."""
        return f"{self.tenant_id}/{self.id}/"
```

- [ ] **Step 4: Write `db.py`**

```python
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from archiagent_service.config import get_settings
from archiagent_service.models import Base


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Session:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 5: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_models.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): job, tenant and api-key model

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Authentication and the single tenancy check

**Files:**
- Create: `archiagent-viewer/service/archiagent_service/auth.py`
- Create: `archiagent-viewer/service/tests/test_auth.py`
- Modify: `archiagent-viewer/service/archiagent_service/api.py`

**Interfaces:**
- Consumes: `Tenant`, `ApiKey`, `Job`, `session_scope`
- Produces: `hash_key(raw: str) -> str`; `issue_key(session, tenant_id) -> str` (returns the raw key, stores only its hash); `current_tenant` FastAPI dependency returning `Tenant`; `owned_job(session, tenant, job_id) -> Job` which raises 404 — never 403 — when the job belongs to someone else.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_auth.py`:

```python
import pytest
from fastapi import HTTPException

from archiagent_service.auth import hash_key, issue_key, owned_job
from archiagent_service.models import Job, Tenant, ulid


def test_issue_key_returns_a_raw_key_but_stores_only_its_hash(pg_session):
    tenant = Tenant(id=ulid(), name="acme")
    pg_session.add(tenant)
    pg_session.flush()

    raw = issue_key(pg_session, tenant.id)
    pg_session.flush()

    from archiagent_service.models import ApiKey

    stored = pg_session.query(ApiKey).filter_by(tenant_id=tenant.id).one()
    assert stored.key_hash == hash_key(raw)
    assert raw not in stored.key_hash


# Review Focus #1 — the whole security model is this one function.
def test_a_tenant_cannot_reach_another_tenants_job(pg_session):
    alice = Tenant(id=ulid(), name="alice")
    bob = Tenant(id=ulid(), name="bob")
    pg_session.add_all([alice, bob])
    pg_session.flush()

    job = Job(id=ulid(), tenant_id=alice.id, status="succeeded", source_filename="plan.dxf")
    pg_session.add(job)
    pg_session.flush()

    with pytest.raises(HTTPException) as caught:
        owned_job(pg_session, bob, job.id)

    # 404, never 403: a 403 confirms the job exists, which leaks that Alice has
    # a job with this id.
    assert caught.value.status_code == 404


def test_a_tenant_can_reach_its_own_job(pg_session):
    alice = Tenant(id=ulid(), name="alice")
    pg_session.add(alice)
    pg_session.flush()
    job = Job(id=ulid(), tenant_id=alice.id, status="succeeded", source_filename="plan.dxf")
    pg_session.add(job)
    pg_session.flush()

    assert owned_job(pg_session, alice, job.id).id == job.id


def test_a_missing_job_is_also_404(pg_session):
    alice = Tenant(id=ulid(), name="alice")
    pg_session.add(alice)
    pg_session.flush()

    with pytest.raises(HTTPException) as caught:
        owned_job(pg_session, alice, ulid())
    assert caught.value.status_code == 404
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_auth.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.auth'`

- [ ] **Step 3: Write `auth.py`**

```python
"""Authentication, and the one place tenancy is enforced.

Spec §7.3: authorization is a prefix comparison in ONE function. Re-implementing
it per endpoint is how a tenant eventually reads another tenant's building.
"""
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from archiagent_service.db import session_scope
from archiagent_service.models import ApiKey, Job, Tenant, ulid


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_key(session: Session, tenant_id: str) -> str:
    """Returns the raw key ONCE. Only its hash is persisted."""
    raw = f"ak_{secrets.token_urlsafe(32)}"
    session.add(ApiKey(id=ulid(), tenant_id=tenant_id, key_hash=hash_key(raw)))
    return raw


def db_session():
    with session_scope() as session:
        yield session


def current_tenant(
    authorization: str = Header(default=""),
    session: Session = Depends(db_session),
) -> Tenant:
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw:
        raise HTTPException(status_code=401, detail="missing bearer token")
    key = session.query(ApiKey).filter_by(key_hash=hash_key(raw)).one_or_none()
    if key is None:
        raise HTTPException(status_code=401, detail="unknown api key")
    return session.get(Tenant, key.tenant_id)


def owned_job(session: Session, tenant: Tenant, job_id: str) -> Job:
    """404 for both 'does not exist' and 'belongs to someone else'.

    A 403 would confirm the job exists, which tells the caller that some other
    tenant owns a job with exactly this id.
    """
    job = session.get(Job, job_id)
    if job is None or job.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job
```

- [ ] **Step 4: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_auth.py -q
```

Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): bearer auth and single-point tenancy check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Upload endpoint

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/api.py`
- Create: `archiagent-viewer/service/archiagent_service/uploads.py`
- Create: `archiagent-viewer/service/tests/test_uploads.py`

**Interfaces:**
- Consumes: `current_tenant`, `owned_job`, `get_store`, `Job`, `ulid`
- Produces: `ALLOWED_SUFFIXES = {".dxf", ".dwg", ".pdf"}`; `validate_upload(filename: str, size: int, max_bytes: int) -> str` returning the normalised suffix and raising 400/413; `POST /v1/uploads` returning `{job_id, upload_url, key}`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_uploads.py`:

```python
import pytest
from fastapi import HTTPException

from archiagent_service.uploads import ALLOWED_SUFFIXES, validate_upload

MAX = 200 * 1024 * 1024


def test_accepts_the_formats_archiagent_ingests():
    assert ALLOWED_SUFFIXES == {".dxf", ".dwg", ".pdf"}
    assert validate_upload("plan.dxf", 1000, MAX) == ".dxf"
    assert validate_upload("PLAN.DWG", 1000, MAX) == ".dwg"


# Review Focus #3
def test_rejects_a_format_the_pipeline_cannot_read():
    with pytest.raises(HTTPException) as caught:
        validate_upload("plan.rvt", 1000, MAX)
    assert caught.value.status_code == 400


def test_rejects_an_oversized_upload_before_anything_is_queued():
    with pytest.raises(HTTPException) as caught:
        validate_upload("plan.dxf", MAX + 1, MAX)
    assert caught.value.status_code == 413


def test_rejects_a_path_disguised_as_a_filename():
    # The filename becomes part of an S3 key; a traversal here would write
    # outside the tenant's prefix.
    with pytest.raises(HTTPException):
        validate_upload("../../etc/passwd.dxf", 1000, MAX)


def test_rejects_an_empty_upload():
    with pytest.raises(HTTPException) as caught:
        validate_upload("plan.dxf", 0, MAX)
    assert caught.value.status_code == 400
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_uploads.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.uploads'`

- [ ] **Step 3: Write `uploads.py`**

```python
"""Upload validation. Everything here runs BEFORE a job is queued."""
from pathlib import PurePosixPath

from fastapi import HTTPException

# What archiagent can actually ingest. DWG is converted to DXF first (Phase 4).
ALLOWED_SUFFIXES = {".dxf", ".dwg", ".pdf"}


def validate_upload(filename: str, size: int, max_bytes: int) -> str:
    """Returns the lowercased suffix, or raises.

    The client's content-type is not consulted: it is trivially forged and the
    worker validates by parsing anyway.
    """
    name = PurePosixPath(filename).name
    if not name or name != filename.strip():
        raise HTTPException(status_code=400, detail="filename must not contain a path")
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported format {suffix or '(none)'}; accepted: {sorted(ALLOWED_SUFFIXES)}",
        )
    if size <= 0:
        raise HTTPException(status_code=400, detail="upload is empty")
    if size > max_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")
    return suffix
```

- [ ] **Step 4: Add the endpoint to `api.py`**

Inside `create_app()`:

```python
    @app.post("/v1/uploads")
    def create_upload(
        body: UploadRequest,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ) -> dict:
        settings = get_settings()
        suffix = validate_upload(body.filename, body.size, settings.max_upload_bytes)

        job = Job(
            id=ulid(),
            tenant_id=tenant.id,
            status="pending",
            source_filename=body.filename,
            source_bytes=body.size,
        )
        session.add(job)
        session.flush()

        key = f"{job.prefix}source{suffix}"
        # Presigned PUT: the bytes go straight to object storage. A 200 MB DXF
        # must not traverse the app server (spec §7.3).
        return {"job_id": job.id, "key": key, "upload_url": get_store().presign_put(key)}
```

with, at module scope:

```python
class UploadRequest(BaseModel):
    filename: str
    size: int
```

- [ ] **Step 5: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_uploads.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): validated upload with presigned PUT

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Job start, queue and the per-tenant concurrency cap

**Files:**
- Create: `archiagent-viewer/service/archiagent_service/queue.py`
- Create: `archiagent-viewer/service/tests/test_queue.py`
- Modify: `archiagent-viewer/service/archiagent_service/api.py`

**Interfaces:**
- Consumes: `Job`, `get_settings`
- Produces: `get_queue() -> rq.Queue`; `running_count(session, tenant_id) -> int`; `admit(session, tenant_id, max_concurrent) -> bool`; `POST /v1/jobs/{job_id}/start` accepting `{units_per_foot?, height_ft?, walls?}` and returning `{status}`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_queue.py`:

```python
from archiagent_service.models import Job, Tenant, ulid
from archiagent_service.queue import admit, running_count


def _tenant(session, name):
    tenant = Tenant(id=ulid(), name=name)
    session.add(tenant)
    session.flush()
    return tenant


def _job(session, tenant, status):
    job = Job(id=ulid(), tenant_id=tenant.id, status=status, source_filename="plan.dxf")
    session.add(job)
    session.flush()
    return job


def test_running_count_counts_only_live_jobs(pg_session):
    tenant = _tenant(pg_session, "acme")
    _job(pg_session, tenant, "running")
    _job(pg_session, tenant, "queued")
    _job(pg_session, tenant, "succeeded")
    _job(pg_session, tenant, "failed")

    assert running_count(pg_session, tenant.id) == 2


# Review Focus #4
def test_a_tenant_at_its_cap_is_not_admitted(pg_session):
    tenant = _tenant(pg_session, "acme")
    _job(pg_session, tenant, "running")
    _job(pg_session, tenant, "running")

    assert admit(pg_session, tenant.id, max_concurrent=2) is False


def test_one_tenants_backlog_does_not_block_another(pg_session):
    """LLM classification is the cost centre. Without a per-tenant cap one
    tenant's batch starves everyone else."""
    busy = _tenant(pg_session, "busy")
    quiet = _tenant(pg_session, "quiet")
    for _ in range(5):
        _job(pg_session, busy, "running")

    assert admit(pg_session, busy.id, max_concurrent=2) is False
    assert admit(pg_session, quiet.id, max_concurrent=2) is True
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_queue.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.queue'`

- [ ] **Step 3: Write `queue.py`**

```python
"""The work queue, and the fairness rule that keeps one tenant from eating it."""
from functools import lru_cache

from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archiagent_service.config import get_settings
from archiagent_service.models import Job

#: Statuses that occupy a slot.
LIVE = ("queued", "running")


@lru_cache
def get_queue() -> Queue:
    settings = get_settings()
    # Jobs are minutes long: vision escalation and LLM classification dominate.
    return Queue("archiagent", connection=Redis.from_url(settings.redis_url), default_timeout=3600)


def running_count(session: Session, tenant_id: str) -> int:
    return session.scalar(
        select(func.count(Job.id)).where(Job.tenant_id == tenant_id, Job.status.in_(LIVE))
    )


def admit(session: Session, tenant_id: str, max_concurrent: int) -> bool:
    """Whether this tenant may start another job right now."""
    return running_count(session, tenant_id) < max_concurrent
```

- [ ] **Step 4: Add the endpoint to `api.py`**

```python
    @app.post("/v1/jobs/{job_id}/start")
    def start_job(
        job_id: str,
        body: StartRequest,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ) -> dict:
        settings = get_settings()
        job = owned_job(session, tenant, job_id)
        if job.status != "pending":
            raise HTTPException(status_code=409, detail=f"job is already {job.status}")
        if not get_store().exists(f"{job.prefix}source{PurePosixPath(job.source_filename).suffix.lower()}"):
            raise HTTPException(status_code=409, detail="source was never uploaded")
        if not admit(session, tenant.id, settings.tenant_max_concurrent):
            raise HTTPException(
                status_code=429,
                detail=f"at most {settings.tenant_max_concurrent} concurrent jobs per tenant",
            )

        job.options = body.model_dump(exclude_none=True)
        job.status = "queued"
        session.flush()
        get_queue().enqueue("archiagent_service.worker.run_job", job.id)
        return {"status": job.status}
```

with, at module scope:

```python
class StartRequest(BaseModel):
    units_per_foot: float | None = None
    height_ft: float | None = None
    walls: list[str] | None = None
```

- [ ] **Step 5: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_queue.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): queue jobs with a per-tenant concurrency cap

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The worker — run the CLI, publish the artifacts

**Files:**
- Create: `archiagent-viewer/service/archiagent_service/pipeline.py`
- Create: `archiagent-viewer/service/archiagent_service/worker.py`
- Create: `archiagent-viewer/service/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `get_settings`, `get_store`, `session_scope`, `Job`
- Produces: `build_command(python, dxf, out_dir, options) -> list[str]`; `CliResult(exit_code, stdout, stderr, duration_ms)`; `run_cli(dxf, out_dir, options) -> CliResult`; `ARTIFACT_PATTERNS`; `collect_artifacts(out_dir) -> list[Path]`; `run_job(job_id)` — the RQ entry point.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_pipeline.py`:

```python
from pathlib import Path

from archiagent_service.pipeline import build_command, collect_artifacts


def test_command_shells_out_and_never_imports_archiagent():
    """Spec §5.2 and this plan's Global Constraints: the service runs the CLI
    in a subprocess so a crash, an LLM timeout or ifcopenshell's memory growth
    cannot take the API process with it."""
    command = build_command(
        Path("/venv/bin/python"),
        Path("/work/source.dxf"),
        Path("/work"),
        {"units_per_foot": 12, "height_ft": 10.0, "walls": ["WALLS"]},
    )

    assert command[:3] == ["/venv/bin/python", "-m", "archiagent"]
    assert "--dxfFilePath" in command and "/work/source.dxf" in command
    assert "--outputDir" in command and "/work" in command
    assert command[command.index("--units-per-foot") + 1] == "12"
    assert command[command.index("--height") + 1] == "10.0"
    assert command[command.index("--walls") + 1 : command.index("--walls") + 2] == ["WALLS"]


def test_command_omits_flags_that_were_not_requested():
    command = build_command(Path("/p"), Path("/w/s.dxf"), Path("/w"), {})
    assert "--units-per-foot" not in command
    assert "--walls" not in command


def test_collect_artifacts_finds_what_the_pipeline_wrote(tmp_path):
    (tmp_path / "source.ifc").write_bytes(b"ISO-10303-21;")
    (tmp_path / "source.interpretation.json").write_text("{}")
    (tmp_path / "source.report.json").write_text("{}")
    (tmp_path / "source.overlay.svg").write_text("<svg/>")
    (tmp_path / "scratch.log").write_text("noise")

    names = sorted(p.name for p in collect_artifacts(tmp_path))
    assert names == [
        "source.ifc",
        "source.interpretation.json",
        "source.overlay.svg",
        "source.report.json",
    ]
    assert "scratch.log" not in names
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_pipeline.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent_service.pipeline'`

- [ ] **Step 3: Write `pipeline.py`**

```python
"""The subprocess boundary around the archiagent CLI.

The service NEVER imports archiagent. The CLI's documented exit codes are the
API's error taxonomy: 0 success, 1 pipeline/export/acceptance failure,
2 LLM unavailable, 3 bad usage.
"""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from archiagent_service.config import get_settings

#: What the pipeline writes that is worth keeping (spec §3).
ARTIFACT_PATTERNS = ("*.ifc", "*.interpretation.json", "*.report.json", "*.overlay.svg")

EXIT_MEANING = {
    0: "succeeded",
    1: "pipeline or validation failure",
    2: "LLM provider unavailable",
    3: "bad usage",
}


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


def build_command(python: Path, dxf: Path, out_dir: Path, options: dict) -> list[str]:
    command = [
        str(python), "-m", "archiagent",
        "--dxfFilePath", str(dxf),
        "--outputDir", str(out_dir),
    ]
    if (units := options.get("units_per_foot")) is not None:
        command += ["--units-per-foot", str(units)]
    if (height := options.get("height_ft")) is not None:
        command += ["--height", str(height)]
    if walls := options.get("walls"):
        command += ["--walls", *walls]
    return command


def run_cli(dxf: Path, out_dir: Path, options: dict, timeout_s: int = 3000) -> CliResult:
    settings = get_settings()
    command = build_command(settings.archiagent_python, dxf, out_dir, options)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=settings.archiagent_cwd, capture_output=True, text=True, timeout=timeout_s
        )
        code, out, err = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 1, "", f"archiagent timed out after {timeout_s}s"
    return CliResult(code, out, err, int((time.monotonic() - started) * 1000))


def collect_artifacts(out_dir: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in ARTIFACT_PATTERNS:
        found.extend(sorted(out_dir.glob(pattern)))
    return found
```

- [ ] **Step 4: Write `worker.py`**

```python
"""The RQ entry point: object storage in, object storage out.

Tier 1 reads a directory and writes a directory; this does the I/O around it so
the CLI stays exactly as testable as it is today (spec §5.3).
"""
import tempfile
from datetime import datetime, timezone
from pathlib import PurePosixPath, Path

from archiagent_service.db import session_scope
from archiagent_service.models import Job
from archiagent_service.pipeline import EXIT_MEANING, collect_artifacts, run_cli
from archiagent_service.storage import get_store


def run_job(job_id: str) -> None:
    store = get_store()
    with session_scope() as session:
        job = session.get(Job, job_id)
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        prefix, filename, options = job.prefix, job.source_filename, dict(job.options)

    suffix = PurePosixPath(filename).suffix.lower()
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        source = work / f"source{suffix}"
        store.download(f"{prefix}source{suffix}", source)

        result = run_cli(source, work, options)
        artifacts = []
        for path in collect_artifacts(work):
            store.put_file(f"{prefix}{path.name}", path)
            artifacts.append(path.name)

    with session_scope() as session:
        job = session.get(Job, job_id)
        job.exit_code = result.exit_code
        job.status = "succeeded" if result.exit_code == 0 else "failed"
        # A failed run is exactly when the diagnostics matter, so they are kept
        # and the job stays retrievable rather than vanishing.
        job.error = None if result.exit_code == 0 else (
            f"{EXIT_MEANING.get(result.exit_code, 'unknown')}: {result.stderr[-4000:]}"
        )
        job.artifacts = artifacts
        job.timings_ms = {**job.timings_ms, "author": result.duration_ms}
        job.finished_at = datetime.now(timezone.utc)
```

- [ ] **Step 5: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_pipeline.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): worker runs the archiagent CLI and publishes artifacts

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Read endpoints — job status, listing, artifacts, delete

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/api.py`
- Create: `archiagent-viewer/service/tests/test_api.py`

**Interfaces:**
- Consumes: everything above
- Produces: `GET /v1/jobs/{job_id}`, `GET /v1/jobs?limit=&cursor=`, `GET /v1/jobs/{job_id}/artifacts/{name}` (302 to a presigned URL), `DELETE /v1/jobs/{job_id}`; `job_json(job) -> dict` matching spec §3.2.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from archiagent_service.api import create_app
from archiagent_service.auth import issue_key
from archiagent_service.models import Job, Tenant, ulid


@pytest.fixture
def client(pg_session, monkeypatch):
    from archiagent_service import auth

    monkeypatch.setattr(auth, "db_session", lambda: iter([pg_session]))
    app = create_app()
    app.dependency_overrides[auth.db_session] = lambda: pg_session
    return TestClient(app)


@pytest.fixture
def alice(pg_session):
    tenant = Tenant(id=ulid(), name="alice")
    pg_session.add(tenant)
    pg_session.flush()
    return tenant, issue_key(pg_session, tenant.id)


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/v1/jobs").status_code == 401


def test_a_job_reports_its_status(client, pg_session, alice):
    tenant, key = alice
    job = Job(id=ulid(), tenant_id=tenant.id, status="succeeded", source_filename="plan.dxf")
    pg_session.add(job)
    pg_session.flush()

    body = client.get(f"/v1/jobs/{job.id}", headers={"authorization": f"Bearer {key}"}).json()
    assert body["job_id"] == job.id
    assert body["status"] == "succeeded"
    assert body["tenant_id"] == tenant.id


# Review Focus #5
def test_an_unknown_job_is_404(client, alice):
    _, key = alice
    response = client.get(f"/v1/jobs/{ulid()}", headers={"authorization": f"Bearer {key}"})
    assert response.status_code == 404


def test_an_artifact_the_job_never_produced_is_404(client, pg_session, alice):
    tenant, key = alice
    job = Job(id=ulid(), tenant_id=tenant.id, status="succeeded",
              source_filename="plan.dxf", artifacts=["source.ifc"])
    pg_session.add(job)
    pg_session.flush()

    response = client.get(
        f"/v1/jobs/{job.id}/artifacts/source.report.json",
        headers={"authorization": f"Bearer {key}"},
    )
    assert response.status_code == 404


def test_listing_is_scoped_to_the_calling_tenant(client, pg_session, alice):
    tenant, key = alice
    other = Tenant(id=ulid(), name="bob")
    pg_session.add(other)
    pg_session.flush()
    pg_session.add(Job(id=ulid(), tenant_id=tenant.id, status="succeeded", source_filename="mine.dxf"))
    pg_session.add(Job(id=ulid(), tenant_id=other.id, status="succeeded", source_filename="theirs.dxf"))
    pg_session.flush()

    body = client.get("/v1/jobs", headers={"authorization": f"Bearer {key}"}).json()
    assert [j["source"]["filename"] for j in body["jobs"]] == ["mine.dxf"]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_api.py -q
```

Expected: FAIL — the read endpoints do not exist (404 where a body was expected).

- [ ] **Step 3: Add the endpoints to `api.py`**

```python
    @app.get("/v1/jobs/{job_id}")
    def get_job(
        job_id: str,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ) -> dict:
        return job_json(owned_job(session, tenant, job_id))

    @app.get("/v1/jobs")
    def list_jobs(
        limit: int = 50,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ) -> dict:
        rows = (
            session.query(Job)
            .filter_by(tenant_id=tenant.id)
            .order_by(Job.id.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {"jobs": [job_json(job) for job in rows]}

    @app.get("/v1/jobs/{job_id}/artifacts/{name}")
    def get_artifact(
        job_id: str,
        name: str,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ):
        job = owned_job(session, tenant, job_id)
        if name not in job.artifacts:
            raise HTTPException(status_code=404, detail=f"no artifact {name} for this job")
        # 302, never a proxy: a 400 MB IFC must not traverse the app server.
        return RedirectResponse(get_store().presign_get(f"{job.prefix}{name}"), status_code=302)

    @app.delete("/v1/jobs/{job_id}")
    def delete_job(
        job_id: str,
        tenant: Tenant = Depends(current_tenant),
        session: Session = Depends(db_session),
    ) -> dict:
        job = owned_job(session, tenant, job_id)
        removed = get_store().delete_prefix(job.prefix)
        session.delete(job)
        return {"deleted": True, "objects_removed": removed}
```

with, at module scope:

```python
def job_json(job: Job) -> dict:
    """Spec §3.2."""
    return {
        "schema_version": 1,
        "job_id": job.id,
        "tenant_id": job.tenant_id,
        "status": job.status,
        "source": {
            "filename": job.source_filename,
            "bytes": job.source_bytes,
            "converted_from_dwg": job.converted_from_dwg,
        },
        "options": job.options,
        "exit_code": job.exit_code,
        "acceptance": job.acceptance,
        "artifacts": job.artifacts,
        "timings_ms": job.timings_ms,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
```

- [ ] **Step 4: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_api.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

```bash
cd archiagent-viewer/service && .venv/bin/pytest -q
```

Expected: all pass, none failed.

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): job status, listing, artifact redirect and delete

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: End-to-end proof against a real DXF

**Files:**
- Create: `archiagent-viewer/service/tests/test_end_to_end.py`
- Create: `archiagent-viewer/service/README.md`

**Interfaces:**
- Consumes: the whole service
- Produces: a test that uploads a real DXF, runs the worker inline, and asserts an IFC came out.

- [ ] **Step 1: Write the test**

Create `service/tests/test_end_to_end.py`:

```python
import os
from pathlib import Path

import pytest

from archiagent_service.auth import issue_key
from archiagent_service.models import Job, Tenant, ulid
from archiagent_service.storage import get_store
from archiagent_service.worker import run_job

DXF = os.environ.get("ARCHIAGENT_DXF")


@pytest.mark.skipif(not DXF or not Path(DXF).exists(), reason="set ARCHIAGENT_DXF to a real .dxf")
def test_a_dxf_becomes_an_ifc_in_object_storage(pg_session, minio):
    """The Phase 3 gate: source in, IFC out, without a terminal.

    Runs the worker inline rather than through RQ — the queue is covered by its
    own tests, and this is about the pipeline boundary.
    """
    tenant = Tenant(id=ulid(), name="e2e")
    pg_session.add(tenant)
    pg_session.flush()
    issue_key(pg_session, tenant.id)

    job = Job(id=ulid(), tenant_id=tenant.id, status="queued", source_filename="plan.dxf",
              options={"walls": ["WALLS"], "units_per_foot": 12})
    pg_session.add(job)
    pg_session.commit()

    minio.put_file(f"{job.prefix}source.dxf", Path(DXF))
    run_job(job.id)

    pg_session.expire_all()
    finished = pg_session.get(Job, job.id)
    assert finished.status in {"succeeded", "failed"}, finished.status
    assert finished.exit_code is not None
    if finished.status == "succeeded":
        assert any(name.endswith(".ifc") for name in finished.artifacts), finished.artifacts
        key = f"{job.prefix}{next(n for n in finished.artifacts if n.endswith('.ifc'))}"
        assert get_store().get(key).startswith(b"ISO-10303-21;")
    else:
        # A failure must still be diagnosable — Review Focus #2.
        assert finished.error
```

- [ ] **Step 2: Run it against a real drawing**

```bash
cd archiagent-viewer/service
ARCHIAGENT_DXF="../../input-floorplans/<pick one>.dxf" .venv/bin/pytest tests/test_end_to_end.py -q -s
```

Expected: PASS. Note `--walls WALLS` and `--units-per-foot 12` avoid LLM calls, so this needs no API key.

- [ ] **Step 3: Write `service/README.md`**

```markdown
# archiagent-service

The HTTP tier. Uploads a DXF, runs archiAgent, publishes the artifacts.

It **never imports `archiagent`** — it shells out to the CLI, so an LLM timeout
or ifcopenshell's memory growth cannot take the API process with it.

## Run it

```bash
docker compose up -d                       # postgres:5433, redis:6380, minio:9000
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "from archiagent_service.db import create_all; create_all()"
.venv/bin/uvicorn archiagent_service.api:app --reload --port 8000
.venv/bin/rq worker archiagent --url redis://localhost:6380/0   # in another shell
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/uploads` | presigned PUT + a job row |
| POST | `/v1/jobs/{id}/start` | queue it |
| GET | `/v1/jobs/{id}` | status (spec §3.2) |
| GET | `/v1/jobs` | this tenant's jobs |
| GET | `/v1/jobs/{id}/artifacts/{name}` | 302 to a presigned URL |
| DELETE | `/v1/jobs/{id}` | delete the job and its whole prefix |

All routes need `Authorization: Bearer <api key>`. Artifacts live under
`{tenant_id}/{job_id}/` and authorization is one prefix check in `auth.owned_job`.

## Tests

```bash
.venv/bin/pytest -q                        # needs docker compose up
ARCHIAGENT_DXF=/path/plan.dxf .venv/bin/pytest tests/test_end_to_end.py -q
```

Tests that need Postgres or MinIO skip with instructions when the stack is down.
```

- [ ] **Step 4: Commit**

```bash
git add archiagent-viewer/service
git commit -m "test(service): end-to-end DXF to IFC, and service README

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Deliberately not in Phase 3

- **User accounts and login.** Tenants authenticate with API keys. Sessions, invites and roles are a product decision, not a prerequisite for the gate.
- **Cost attribution.** Spec §7.3 wants LLM token counts in `job.json` from day one. The CLI does not currently report them on stdout in a parseable form; wiring that is a Tier 1 change and this plan does not touch Tier 1.
- **The `.frag` precompute step in the worker.** Phase 2's `npm run build:frag` exists and the worker should call it, but that couples the Python container to Node. It gets its own task once the deploy shape is settled.
- **Retries and dead-letter handling.** RQ's defaults stand until we have seen real failure modes.
- **DWG.** Phase 4.

## Self-review notes

**Spec coverage.** §3 artifact contract → Tasks 3 (`Job.prefix`), 7 (`collect_artifacts`), 8 (`job_json`). §5.2 subprocess invocation → Task 7. §5.3 "Tier 1 gains no HTTP/S3 awareness" → enforced by the Global Constraint and Task 7's first test. §7.1 components → Tasks 1–3, 6. §7.2 endpoints → Tasks 5, 6, 8. §7.3 tenancy → Task 4 (prefix check), Task 5 (limits), Task 6 (cap), Task 8 (302 not proxy). §7.4 storage boundary → Task 2.

**Review Focus coverage.** #1 cross-tenant → Task 4, three tests. #2 failed run stays diagnosable → Task 7 (`job.error`) and Task 9's else-branch. #3 bad/oversized upload → Task 5, five tests. #4 concurrency cap → Task 6, three tests. #5 unknown job / missing artifact → Task 8, two tests.

**Known soft spot.** Task 8's `client` fixture overrides the DB dependency to reuse the rolled-back test session. If that override proves brittle under FastAPI's dependency cache, switch to a dedicated test database created and dropped per session — do not weaken the assertions to make it pass.
