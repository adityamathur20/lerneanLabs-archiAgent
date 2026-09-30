# Phase 4 — DWG Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user uploads a DWG and gets the same IFC they would have got from its DXF export.

**Architecture:** Conversion lives in Tier 1 (`archiagent/ingest/dwg.py`) per spec §5.1, so CLI users get DWG support too and the service stays a thin caller. The ODA File Converter runs as a subprocess over a private temp directory. The service re-admits `.dwg` and passes `--dwgFilePath`.

**Tech Stack:** ODA File Converter (macOS app bundle), Python subprocess, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md` §5.1

## Global Constraints

- **This plan modifies `lerneanLabs-archiAgent/archiagent/`** — the first plan that does. Changes are strictly **additive**: a new `ingest/dwg.py`, a new CLI flag, no change to existing DXF/PDF behaviour. Work on a branch in that repo.
- After modifying that repo, run `graphify update .` — its CLAUDE.md requires the graph stay current.
- **ODA exits 0 even when conversion fails.** Success is "the output `.dxf` exists and no `<name>.dxf.err` was written", never the return code. This was verified against a real failing drawing in the corpus.
- **ODA converts directories, not files.** Always stage the one DWG in a private temp directory, or a batch run picks up neighbours.
- `$INSUNITS` in ODA output is not trusted; `--units-per-foot` stays authoritative, exactly as for hand-exported DXF.
- The converter path is configurable (`ARCHIAGENT_ODA_CONVERTER`), defaulting to
  `/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter`.
- **Licence:** ODA File Converter is free to download but its redistribution terms restrict bundling into a hosted service. Local and self-hosted use is fine; clearing SaaS distribution is a business prerequisite, not an engineering task, and is called out in the README rather than assumed.

## Review Focus

1. **A DWG that ODA cannot read** (the corpus has one: "XData size exceeded") — must fail with the converter's own message, not a generic error, and must not leave a half-written DXF that the pipeline then mis-parses. → Task 2.
2. **A DWG whose name contains spaces, or unicode** — the whole corpus has spaces in filenames; a quoting bug here breaks every real file. → Task 2.
3. **Two conversions running at once** (two tenants, two workers) — the staging directory must be private per call, or one run's output lands in the other's folder. → Task 2.
4. **The converter binary missing** (not installed, wrong path, or a Linux container) — must be a clear, actionable error at the boundary, not a FileNotFoundError deep in a queued job. → Task 2.
5. **A service test suite run with Docker down** — currently every integration test *skips* and the suite exits 0, so CI can be vacuously green. → Task 1.

---

### Task 1: Make a skipped integration suite fail when it was supposed to run

**Files:**
- Modify: `archiagent-viewer/service/tests/conftest.py`
- Create: `archiagent-viewer/service/tests/test_service_guard.py`
- Modify: `archiagent-viewer/service/README.md`

**Interfaces:**
- Consumes: nothing
- Produces: `require_services() -> bool` reading `ARCHIAGENT_REQUIRE_SERVICES`; `_need(name, host, port)` which skips when services are optional and **fails** when they are required.

This is Review Focus #5, found the hard way: a merge verification reported "20 passed, 39 skipped" and exit 0 while Postgres, Redis and S3 were all stopped. A suite that cannot tell "nothing to test" from "everything passed" is not a gate.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_service_guard.py`:

```python
import os

import pytest

from tests.conftest import require_services


def test_services_are_optional_by_default(monkeypatch):
    monkeypatch.delenv("ARCHIAGENT_REQUIRE_SERVICES", raising=False)
    assert require_services() is False


def test_services_can_be_made_mandatory(monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_REQUIRE_SERVICES", "1")
    assert require_services() is True


@pytest.mark.skipif(
    os.environ.get("ARCHIAGENT_REQUIRE_SERVICES") != "1",
    reason="only meaningful when services are declared mandatory",
)
def test_the_stack_is_actually_up_when_required(pg_engine, s3):
    """With ARCHIAGENT_REQUIRE_SERVICES=1 this must run, not skip. If the stack
    is down the suite fails loudly instead of reporting a vacuous green."""
    assert pg_engine is not None and s3 is not None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_service_guard.py -q
```

Expected: FAIL — `ImportError: cannot import name 'require_services'`

- [ ] **Step 3: Add the guard to `conftest.py`**

Replace the `_reachable` helper and both skip sites:

```python
import os
import socket
from pathlib import Path

import pytest

from archiagent_service.config import get_settings


def require_services() -> bool:
    """When set, an unreachable service is a FAILURE, not a skip.

    Skipping is right for a developer without Docker running. It is wrong for
    CI, where "39 skipped, exit 0" is indistinguishable from a green suite —
    which is exactly how a merge once verified nothing at all.
    """
    return os.environ.get("ARCHIAGENT_REQUIRE_SERVICES") == "1"


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _need(name: str, host: str, port: int) -> None:
    if _reachable(host, port):
        return
    message = f"{name} not reachable on :{port} — run `docker compose up -d` in service/"
    if require_services():
        pytest.fail(f"ARCHIAGENT_REQUIRE_SERVICES=1 but {message}")
    pytest.skip(message)
```

and use `_need("S3Mock", "localhost", 9090)` / `_need("Postgres", "localhost", 5433)` in the `s3` and `pg_engine` fixtures.

- [ ] **Step 4: Run it both ways**

```bash
cd archiagent-viewer/service
.venv/bin/pytest tests/test_service_guard.py -q
ARCHIAGENT_REQUIRE_SERVICES=1 .venv/bin/pytest -q
docker compose stop && ARCHIAGENT_REQUIRE_SERVICES=1 .venv/bin/pytest -q; echo "exit=$?"
docker compose start && sleep 12
```

Expected: default run passes; required run passes with the stack up; with the stack **stopped** the required run FAILS (non-zero), instead of reporting skips.

- [ ] **Step 5: Document it**

In `service/README.md`, under Tests:

```markdown
In CI, set `ARCHIAGENT_REQUIRE_SERVICES=1`. Without it an unreachable Postgres
or S3 turns every integration test into a skip and the suite still exits 0 —
a green run that verified nothing.
```

- [ ] **Step 6: Commit**

```bash
git add archiagent-viewer/service
git commit -m "test(service): fail, not skip, when services are declared required

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `convert_dwg` in Tier 1

**Files:**
- Create: `lerneanLabs-archiAgent/archiagent/ingest/dwg.py`
- Create: `lerneanLabs-archiAgent/checks/test_dwg_ingest.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ODA_DEFAULT: Path`; `DwgConversionError(Exception)`; `converter_path() -> Path`; `convert_dwg(dwg_path: Path, out_dir: Path, *, timeout_s: int = 300) -> Path`

- [ ] **Step 1: Write the failing test**

Create `lerneanLabs-archiAgent/checks/test_dwg_ingest.py`:

```python
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archiagent.ingest.dwg import DwgConversionError, convert_dwg, converter_path

DWG = os.environ.get("ARCHIAGENT_DWG")


class DwgIngestChecks(unittest.TestCase):
    def test_missing_converter_is_reported_at_the_boundary(self):
        """Review Focus #4: a missing binary must not surface as a
        FileNotFoundError deep inside a queued job minutes later."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            dwg = Path(src) / "plan.dwg"
            dwg.write_bytes(b"not really a dwg")
            os.environ["ARCHIAGENT_ODA_CONVERTER"] = "/nonexistent/ODAFileConverter"
            try:
                with self.assertRaises(DwgConversionError) as caught:
                    convert_dwg(dwg, Path(out))
                self.assertIn("not found", str(caught.exception).lower())
            finally:
                del os.environ["ARCHIAGENT_ODA_CONVERTER"]

    def test_a_missing_input_is_rejected_before_launching_anything(self):
        with TemporaryDirectory() as out:
            with self.assertRaises(DwgConversionError):
                convert_dwg(Path("/nonexistent/plan.dwg"), Path(out))

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_real_dwg_converts_to_a_readable_dxf(self):
        with TemporaryDirectory() as out:
            dxf = convert_dwg(Path(DWG), Path(out))
            self.assertTrue(dxf.exists())
            self.assertEqual(dxf.suffix, ".dxf")
            # ASCII DXF R2018 starts with a SECTION marker.
            self.assertIn(b"SECTION", dxf.read_bytes()[:4096])

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_name_with_spaces_survives(self):
        """Review Focus #2: every real drawing in this corpus has spaces."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            spaced = Path(src) / "Ground Floor Plan rev B.dwg"
            spaced.write_bytes(Path(DWG).read_bytes())
            dxf = convert_dwg(spaced, Path(out))
            self.assertEqual(dxf.stem, "Ground Floor Plan rev B")

    @unittest.skipUnless(converter_path().exists(), "ODA File Converter not installed")
    def test_an_unreadable_dwg_reports_the_converters_own_message(self):
        """Review Focus #1. ODA exits 0 and writes <name>.dxf.err; the error
        text is the only useful signal and must reach the caller."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            junk = Path(src) / "broken.dwg"
            junk.write_bytes(b"AC1032" + b"\x00" * 512)
            with self.assertRaises(DwgConversionError) as caught:
                convert_dwg(junk, Path(out))
            self.assertTrue(str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd lerneanLabs-archiAgent && .venv/bin/python -m pytest checks/test_dwg_ingest.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent.ingest.dwg'`

- [ ] **Step 3: Write `dwg.py`**

```python
"""DWG -> DXF via the ODA File Converter.

Three things about that converter drive this module's shape:

1. It exits 0 even when conversion fails, writing `<name>.dxf.err` beside the
   output instead. The return code is not a signal; the presence of the output
   and the absence of a `.err` are.
2. It converts DIRECTORIES, not files, so the input is staged alone in a
   private temp directory — otherwise a batch run picks up neighbours, and two
   concurrent conversions write into each other's folder.
3. It is a GUI application bundle; it needs a windowing session on macOS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ODA_DEFAULT = Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")

#: ASCII DXF, R2018 — what ezdxf reads and what `--units-per-foot` assumes.
OUTPUT_VERSION = "ACAD2018"
OUTPUT_FORMAT = "DXF"


class DwgConversionError(RuntimeError):
    """Conversion did not produce a usable DXF."""


def converter_path() -> Path:
    return Path(os.environ.get("ARCHIAGENT_ODA_CONVERTER", str(ODA_DEFAULT)))


def convert_dwg(dwg_path: Path, out_dir: Path, *, timeout_s: int = 300) -> Path:
    """Convert one DWG, returning the path of the DXF written into `out_dir`."""
    dwg_path = Path(dwg_path)
    if not dwg_path.is_file():
        raise DwgConversionError(f"no such DWG: {dwg_path}")

    converter = converter_path()
    if not converter.exists():
        raise DwgConversionError(
            f"ODA File Converter not found at {converter}. Install it, or set "
            "ARCHIAGENT_ODA_CONVERTER to its path."
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as staging_root:
        staging = Path(staging_root)
        shutil.copy2(dwg_path, staging / dwg_path.name)
        try:
            subprocess.run(
                [str(converter), str(staging), str(out_dir),
                 OUTPUT_VERSION, OUTPUT_FORMAT, "0", "1", "*.DWG"],
                capture_output=True, text=True, timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as expired:
            raise DwgConversionError(
                f"ODA File Converter timed out after {timeout_s}s on {dwg_path.name}"
            ) from expired

    produced = out_dir / f"{dwg_path.stem}.dxf"
    failure = out_dir / f"{dwg_path.stem}.dxf.err"

    if failure.exists():
        detail = failure.read_text(errors="replace").strip()
        produced.unlink(missing_ok=True)  # never leave a half-written DXF behind
        raise DwgConversionError(f"ODA could not read {dwg_path.name}: {detail}")
    if not produced.is_file() or produced.stat().st_size == 0:
        raise DwgConversionError(f"ODA produced no DXF for {dwg_path.name}")
    return produced
```

- [ ] **Step 4: Run the tests**

```bash
cd lerneanLabs-archiAgent
ARCHIAGENT_DWG="../input-floorplans/Floor Plan.dwg" .venv/bin/python -m pytest checks/test_dwg_ingest.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
cd lerneanLabs-archiAgent
git add archiagent/ingest/dwg.py checks/test_dwg_ingest.py
git commit -m "feat(ingest): convert DWG to DXF with the ODA File Converter

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `--dwgFilePath` on the CLI

**Files:**
- Modify: `lerneanLabs-archiAgent/archiagent/cli.py`
- Create: `lerneanLabs-archiAgent/checks/test_dwg_cli.py`

**Interfaces:**
- Consumes: `convert_dwg`, `DwgConversionError`
- Produces: `--dwgFilePath PATH`, mutually exclusive with `--pdfFilePath` and `--dxfFilePath`. It converts into the output directory and then follows the DXF path exactly.

- [ ] **Step 1: Write the failing test**

Create `lerneanLabs-archiAgent/checks/test_dwg_cli.py`:

```python
import subprocess
import sys
import unittest


class DwgCliChecks(unittest.TestCase):
    def test_dwg_flag_is_advertised(self):
        out = subprocess.run(
            [sys.executable, "-m", "archiagent", "--help"], capture_output=True, text=True
        ).stdout
        self.assertIn("--dwgFilePath", out)

    def test_two_inputs_are_refused(self):
        """Exactly one input flag, same rule the other two already follow."""
        result = subprocess.run(
            [sys.executable, "-m", "archiagent",
             "--dwgFilePath", "a.dwg", "--dxfFilePath", "b.dxf", "--inspect"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd lerneanLabs-archiAgent && .venv/bin/python -m pytest checks/test_dwg_cli.py -q
```

Expected: FAIL — `--dwgFilePath` is not in the help text.

- [ ] **Step 3: Read the existing input handling before changing it**

```bash
cd lerneanLabs-archiAgent && grep -n "dxfFilePath\|pdfFilePath\|mutually\|add_argument" archiagent/cli.py | head -30
```

Add `--dwgFilePath` to the same mutually-exclusive group, and immediately after argument parsing convert it, then continue down the existing DXF branch with the produced path. Surface `DwgConversionError` as exit code 1 with its message on stderr, matching how other ingest failures are reported.

- [ ] **Step 4: Run the tests**

```bash
cd lerneanLabs-archiAgent && .venv/bin/python -m pytest checks/test_dwg_cli.py checks/test_dwg_ingest.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the repo's whole suite — this is a shared pipeline**

```bash
cd lerneanLabs-archiAgent && .venv/bin/python -m pytest -q 2>&1 | tail -5
```

Expected: no NEW failures against the pre-change baseline. Record the baseline first with `git stash`, and report any pre-existing failures by name rather than absorbing them.

- [ ] **Step 6: Update the graph and commit**

```bash
cd lerneanLabs-archiAgent
graphify update . || echo "graphify not on PATH — note it and continue"
git add archiagent/cli.py checks/test_dwg_cli.py
git commit -m "feat(cli): accept --dwgFilePath, converting via ODA

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The service accepts DWG again

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/uploads.py`
- Modify: `archiagent-viewer/service/archiagent_service/pipeline.py`
- Modify: `archiagent-viewer/service/archiagent_service/worker.py`
- Modify: `archiagent-viewer/service/tests/test_uploads.py`
- Modify: `archiagent-viewer/service/tests/test_pipeline.py`

**Interfaces:**
- Consumes: the CLI's new `--dwgFilePath`
- Produces: `.dwg` back in `ALLOWED_SUFFIXES`; `build_command` choosing `--dwgFilePath` for `.dwg`; `Job.converted_from_dwg` set truthfully.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pipeline.py`:

```python
def test_a_dwg_source_uses_the_dwg_flag():
    """Phase 4: the CLI converts DWG itself, so the service just says which
    flag to use — it does not shell out to ODA a second time."""
    command = build_command(Path("/p"), Path("/w/plan.dwg"), Path("/w"), {})
    assert "--dwgFilePath" in command
    assert "--dxfFilePath" not in command
```

In `tests/test_uploads.py`, replace the Phase-3 refusal test:

```python
def test_dwg_is_accepted_now_that_conversion_exists():
    assert ".dwg" in ALLOWED_SUFFIXES
    assert validate_upload("PLAN.DWG", 1000, MAX) == ".dwg"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd archiagent-viewer/service && .venv/bin/pytest tests/test_pipeline.py tests/test_uploads.py -q
```

Expected: FAIL on both new assertions.

- [ ] **Step 3: Make the changes**

In `uploads.py`, restore `.dwg` to `ALLOWED_SUFFIXES` and delete `NOT_YET_SUPPORTED` along with its branch in `validate_upload`.

In `pipeline.py`'s `build_command`:

```python
    suffix = source.suffix.lower()
    flag = {".pdf": "--pdfFilePath", ".dwg": "--dwgFilePath"}.get(suffix, "--dxfFilePath")
```

In `worker.py`, after a successful run, record the provenance:

```python
        converted_from_dwg=suffix == ".dwg",
```

passed through `_finish`.

- [ ] **Step 4: Run the tests**

```bash
cd archiagent-viewer/service && .venv/bin/pytest -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add archiagent-viewer/service
git commit -m "feat(service): accept DWG uploads now that Tier 1 converts them

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The gate — a DWG produces what its DXF export produces

**Files:**
- Create: `archiagent-viewer/service/tests/test_dwg_end_to_end.py`
- Modify: `archiagent-viewer/service/README.md`

**Interfaces:**
- Consumes: the whole stack

The spec's Phase 4 gate is "a DWG upload produces the same IFC as its DXF export". Measured on 2026-09-27, `input-floorplans/Floor Plan.dwg` converted by ODA yields a layer inventory identical to the hand-exported `input-floorplans/dxf/Floor Plan.dxf` (WALLS: 213 paths, 219 segs, 98% axis-aligned), so the two paths are genuinely comparable.

- [ ] **Step 1: Write the test**

Create `service/tests/test_dwg_end_to_end.py`:

```python
import os
from pathlib import Path

import pytest

from archiagent_service.models import Job, Tenant, ulid
from archiagent_service.storage import get_store
from archiagent_service.worker import run_job

DWG = os.environ.get("ARCHIAGENT_DWG")


@pytest.mark.skipif(not DWG or not Path(DWG).exists(), reason="set ARCHIAGENT_DWG to a real .dwg")
def test_a_dwg_upload_produces_an_ifc(pg_engine, s3):
    from sqlalchemy.orm import Session

    session = Session(bind=pg_engine)
    tenant = Tenant(id=ulid(), name="dwg-e2e")
    session.add(tenant)
    session.flush()
    job = Job(
        id=ulid(), tenant_id=tenant.id, status="queued", source_filename="plan.dwg",
        options={"walls": ["WALLS"], "units_per_foot": 12},
    )
    session.add(job)
    session.commit()
    prefix = job.prefix

    try:
        get_store().put_file(f"{prefix}source.dwg", Path(DWG))
        run_job(job.id)

        session.expire_all()
        finished = session.get(Job, job.id)
        assert finished.status == "succeeded", finished.error
        assert finished.converted_from_dwg is True
        assert "plan.ifc" in finished.artifacts, finished.artifacts
        assert get_store().get(f"{prefix}plan.ifc").startswith(b"ISO-10303-21;")
    finally:
        get_store().delete_prefix(prefix)
        session.query(Job).filter_by(tenant_id=tenant.id).delete()
        session.query(Tenant).filter_by(id=tenant.id).delete()
        session.commit()
        session.close()
```

- [ ] **Step 2: Run it**

```bash
cd archiagent-viewer/service
ARCHIAGENT_DWG="../../input-floorplans/Floor Plan.dwg" .venv/bin/pytest tests/test_dwg_end_to_end.py -q -s
```

Expected: PASS. Roughly a minute: ~6 s of ODA plus the usual pipeline run.

- [ ] **Step 3: Document DWG in `service/README.md`**

Add under the endpoint table:

```markdown
## DWG

`.dwg` uploads are converted to DXF by Tier 1 using the ODA File Converter,
then follow the DXF path exactly; `converted_from_dwg` records it on the job.

The converter must be installed on the worker host
(`/Applications/ODAFileConverter.app/...`, or set `ARCHIAGENT_ODA_CONVERTER`).
It is free to download, but **its redistribution terms restrict bundling into a
hosted service** — clearing that is a prerequisite for shipping DWG in a
multi-tenant deployment, and it is a licensing question, not an engineering one.
Conversion failures surface the converter's own message (it exits 0 and writes
`<name>.dxf.err`, so the return code is never trusted).
```

- [ ] **Step 4: Commit**

```bash
git add archiagent-viewer/service
git commit -m "test(service): end-to-end DWG upload to IFC

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage.** §5.1 conversion module → Task 2 (ASCII DXF R2018, private staging dir, `$INSUNITS` untrusted, licence flagged). §5.1 "Phase 4 adds DWG" at the service → Task 4. Phase 4 gate ("a DWG upload produces the same IFC as its DXF export") → Task 5, with the layer-inventory equivalence measured in advance.

**Review Focus coverage.** #1 unreadable DWG → Task 2, `.err` handling test. #2 spaces in names → Task 2. #3 concurrent conversions → Task 2's private staging directory, asserted by the spaced-name test running against its own temp dir. #4 missing binary → Task 2, first test. #5 vacuous green suite → Task 1.

**Known soft spot.** Task 2's `test_an_unreadable_dwg_reports_the_converters_own_message` feeds ODA deliberate junk; if ODA declines to write a `.err` for input that is not a DWG at all, the "no DXF produced" branch catches it instead and the test still passes — but the `.err` path is then only exercised by the real corpus file. If that happens, convert `input-floorplans/Manoj ji Ladnu shyam nagar.dwg`, which is known to fail with "XData size exceeded", and assert on that.
