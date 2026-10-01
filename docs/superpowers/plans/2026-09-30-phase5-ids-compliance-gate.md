# Phase 5 — IDS compliance gate before delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "BIM compliant" a machine-checkable claim that archiAgent enforces on itself: ship a buildingSMART IDS file describing what an archiAgent IFC guarantees, validate every authored IFC against it inside the existing pre-delivery gate, and refuse to deliver a file that fails.

**Architecture:** The check runs in Tier 1, in Python, in-process with the ifcopenshell that authored the file. `archiagent/ifc/ids.py` wraps `ifctester` (buildingSMART's reference IDS implementation, from the IfcOpenShell project) and is called from the same place `validate_export` is already called — after `author_ifc`, before the CLI returns success. A failing gate is an existing, tested refusal path, not a new artifact nobody reads.

**Tech Stack:** Python 3.14, `ifctester` 0.9.0, `ifcopenshell` 0.8.5 (unchanged), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md` (§2.1 invariants, §2.2 — **amended by this plan**, §5 Tier 1, §11 Phase 5)

---

## Why this plan replaces the previous Phase 5 design

The first version of this plan put IDS validation and a plan-vs-DXF visual diff
in a new Node "Tier 3" built on `@ifc-lite/*`, invoked by the worker after the
job finished. Two things were wrong with it.

**1. Validation after delivery is the wrong moment.** The old design produced a
report *beside* a finished `plan.ifc`. If the model was non-compliant the user
received the bad file anyway, plus a JSON document explaining that it was bad.
The gate belongs before delivery. archiAgent already has exactly that gate:
`validate_export(out, models)` runs immediately after `author_ifc` at both CLI
call sites (`cli.py:508` and `cli.py:821`) and a failure already returns
`EXIT_PIPELINE` without handing over the file. IDS belongs there.

**2. Nobody was going to supply an IDS file, and nobody was going to look at an
SVG.** The product's user brings a floorplan, not a buildingSMART Information
Delivery Specification — that is an artifact a client-side BIM manager writes.
And a visual diff cannot fail a build, so in an automated pipeline it is a
picture no one opens. The visual diff is dropped. The IDS file becomes
**archiAgent's own**, versioned in the repo: the machine-readable statement of
what the product promises. A client-supplied IDS stays possible (Task 4) but is
optional and additive.

Consequences, stated plainly because the spec is the binding authority:

- **Phase 5 no longer uses ifc-lite at all.** `@ifc-lite/ids` is replaced by
  `ifctester`, which is the buildingSMART reference implementation, runs
  in-process with the ifcopenshell already in the dependency tree, needs no
  Node, no WASM and no subprocess, and can therefore gate authoring rather than
  report on it. Spec §2.2's `@ifc-lite/ids` bullet is superseded; its
  `@ifc-lite/drawing-2d` bullet is dropped.
- **ifc-lite's remaining justification is `@ifc-lite/mcp` alone** (Phase 7).
  That is a real narrowing of the three-tier design and the spec records it.
- **No new tier is created.** There is no `archiagent-viewer/analysis/`.

## Findings from the pre-plan spike (2026-09-29/30)

Measured, not assumed. All three drive the tasks below.

1. **`ifctester` works against the pinned `ifcopenshell` 0.8.5** — no upgrade
   needed. Its `flask`, `odfpy` and `bcf-client` dependencies are for optional
   report formats; installing with `--no-deps` plus
   `python-dateutil xmlschema elementpath numpy pystache typing_extensions`
   imports and validates correctly.
2. **IDS entity matching has no subtype inheritance, and a zero-match
   specification PASSES SILENTLY.** Against a real archiAgent IFC
   (`wallUpdate_dxfBased_ifcOutput/MR RAJEEV JI TWANI JI.ifc`, 169 walls), one
   specification requiring `ArchiAgent_Provenance.SourceLayer`:

   | IDS `<entity><name>` | applicable entities | passed | failed | `spec.status` |
   |---|---|---|---|---|
   | `IFCWALL` | **0** | 0 | 0 | **`True`** |
   | `IFCWALLSTANDARDCASE` | 169 | 169 | 0 | `True` |

   Both `ifctester` and `@ifc-lite/ids` agree, so this is the IDS standard's
   behaviour and not an implementation quirk. `ifcopenshell`'s own
   `f.by_type("IfcWall")` returns all 169, so the IFC is schema-correct — the
   asymmetry is IDS's rule alone. **A specification that matched nothing must never
   be reported as a pass.** It is `inconclusive`, and the report says in words
   that it may be a false pass. By deliberate decision (spec 5.4) it is
   *disclosed, not blocking* -- a plan may legitimately have no doors. Task 1's
   core behaviour, Review Focus #1.
3. **archiAgent writes exactly one property set, `ArchiAgent_Provenance`**
   (`ifc/author.py:190`), and no standard psets at all — no `Pset_WallCommon`,
   no `Pset_DoorCommon`. The classes it authors are `IfcWall`,
   `IfcWallStandardCase`, `IfcOpeningElement`, `IfcDoor`, `IfcWindow`,
   `IfcSpace`, `IfcSlab`, `IfcColumn`, `IfcBeam`. The built-in IDS must describe
   *this*, not an aspiration; Task 2 records the standard-pset gap as a known
   limitation rather than asserting something untrue.

## Global Constraints

- **Invariant I1 — one IFC writer.** This plan adds a reader and a gate. `ifctester` must never be given a file handle opened for writing.
- **`ifctester` is added with the light dependency set only.** `flask`, `odfpy` and `bcf-client` must not enter the runtime dependency tree; they serve report formats this plan does not emit.
- **`ifcopenshell` stays at 0.8.5.** If a task appears to need 0.9, stop and ledger it — an authoring-kernel upgrade is out of scope and would need its own validation of every authored output.
- **A specification that matches zero entities is never reported as a pass.** Its status is `inconclusive` and the report carries an explicit `disclaimer` naming it as a possible false pass. **It does not refuse delivery** (spec §5.4): a plan legitimately may have no doors, and blocking over an absent class would be wrong. A specification with real failures *does* refuse delivery. The constant `BLOCK_ON_INCONCLUSIVE = False` holds that decision in one place.
- **The built-in IDS names both wall classes explicitly** (`IfcWall` *and* `IfcWallStandardCase`), because IDS has no inheritance and archiAgent authors both.
- **No new CLI artifact by default.** The IDS outcome goes into the existing report that `record_export_validation` already writes. `--idsFilePath` (Task 4) is the only thing that adds a file.
- **The gate respects the existing contract.** An IDS failure behaves exactly as an export-validation failure does today: report written, `EXIT_PIPELINE`, no IFC handed over.
- **Determinism.** The IDS report embedded in the report JSON must be JSON-safe and stable across runs on the same input, like every other report section.

## Review Focus

The five input classes the spec implies that no task's own tests would otherwise exercise, most likely to bite first.

1. **An IDS specification that matches zero entities** — finding 2. A reasonable person expects "your IDS checked nothing, this may be a false pass" and not a green tick. The disclaimer must also reach stderr, not only the report file. Tests in Task 1 and Task 4.
2. **An IFC that legitimately has no instances of a class the IDS names** — a plan with no doors, which this corpus contains. This is exactly why an inconclusive result does not block, and why the built-in IDS marks class-specific specifications `minOccurs="0"` so their absence is `skipped-empty` rather than disclaimed noise. Test in Task 2.
3. **A user-supplied IDS that is not IDS at all** (arbitrary XML, an empty file, a `.zip`) — expect a usage error naming the file, not a traceback. Test in Task 4.
4. **A user-supplied IDS written for IFC2X3 against an IFC4 output** — `ifcVersion` mismatch. Expect the schema mismatch named as the cause, rather than a pile of inconclusive specifications that read as the model's fault. Test in Task 4.
5. **An IDS with hundreds of specifications over a large model** — `ifctester` re-walks entities per specification. Expect bounded runtime and the gate not to become the slowest stage. Test in Task 1 with a timing assertion.

## File Structure

| Path | Responsibility |
|---|---|
| `archiagent/ifc/ids.py` | `validate_ids(ifc_path, ids_path)` → JSON-safe report with the zero-match rule. The only module that imports `ifctester`. |
| `archiagent/data/archiagent.ids` | archiAgent's own IDS: the machine-readable statement of what an authored IFC guarantees. Versioned, reviewable, diffable. |
| `archiagent/ifc/inspect.py` | Modified: `validate_export` gains an `ids` section so both CLI call sites inherit the gate. |
| `archiagent/cli.py` | Modified: `--idsFilePath`, `--no-ids-check`. |
| `checks/test_ids_gate.py` | The gate's behaviour: zero-match, failure, optionality, timing. |
| `checks/test_builtin_ids.py` | The shipped IDS is true of real authored output. |
| `pyproject.toml` | Modified: `ifctester` dependency. |

---

## Task 1: The IDS validator, with the zero-match rule

**Files:**
- Create: `archiagent/ifc/ids.py`
- Modify: `pyproject.toml`
- Test: `checks/test_ids_gate.py`

**Interfaces:**
- Consumes: nothing.
- Produces:

```python
def validate_ids(ifc_path: Path | str, ids_path: Path | str) -> dict
```

returning a JSON-safe dict:

```python
{
  "passed": bool,          # False only when a specification actually failed
  "verdict": str,         # "passed" | "inconclusive" | "failed"
  "disclaimer": str,      # non-empty when verdict is "inconclusive"; names the false-pass risk
  "ids": str,             # basename of the IDS file
  "title": str,
  "specifications": [
     {"name": str, "applicable": int, "passed": int, "failed": int,
      "status": "passed" | "failed" | "inconclusive" | "skipped-empty",
      "optional": bool, "disclaimer": str,   # "" unless inconclusive
      "failures": [str]}                     # capped at MAX_FAILURES
  ],
  "counts": {"specifications": int, "inconclusive": int, "failed": int},
  "errors": [str],        # real failures only -- these refuse delivery
  "warnings": [str],      # inconclusive specifications -- disclosed, not blocking
}
```

`IdsInputError` is raised for an unreadable or non-IDS file. Tasks 2–4 consume this shape.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to the runtime dependencies:

```toml
  "ifctester>=0.9.0",
```

Then install without the report-format extras, which are not used:

```bash
.venv/bin/pip install --no-deps ifctester
.venv/bin/pip install python-dateutil xmlschema elementpath pystache
```

Run: `.venv/bin/python -c "from ifctester import ids; import ifcopenshell; print(ifcopenshell.version)"`
Expected: prints `0.8.5` with no ImportError. If it demands 0.9, stop and ledger — the constraint forbids the upgrade.

- [ ] **Step 2: Write the failing test**

`checks/test_ids_gate.py`:

```python
"""The IDS gate refuses to deliver a non-compliant IFC, and never reports a false pass.

A specification that matches zero entities is the quiet failure mode of IDS:
matching has no subtype inheritance, so an IDS naming IfcWall checks none of
archiAgent's straight wall runs (which are IfcWallStandardCase) and reports a
PASS. Measured against both ifctester and @ifc-lite/ids on 2026-09-29. A gate
that accepts that is worse than no gate, because it certifies nothing while
looking green.
"""
import time
from pathlib import Path

import pytest

from archiagent.ifc.ids import IdsInputError, validate_ids

IFC = Path(__file__).resolve().parents[2] / "wallUpdate_dxfBased_ifcOutput" / "MR RAJEEV JI TWANI JI.ifc"
needs_ifc = pytest.mark.skipif(not IFC.is_file(), reason="corpus IFC not present")


def ids_xml(entity, pset="ArchiAgent_Provenance", prop="SourceLayer", optional=False):
    cardinality = "optional" if optional else "required"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS">
  <info><title>test</title></info>
  <specifications>
    <specification name="walls carry provenance" ifcVersion="IFC4"
                   minOccurs="{0 if optional else 1}" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>{entity}</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="{cardinality}">
          <propertySet><simpleValue>{pset}</simpleValue></propertySet>
          <baseName><simpleValue>{prop}</simpleValue></baseName>
        </property>
      </requirements>
    </specification>
  </specifications>
</ids>"""


def write(tmp_path, xml, name="test.ids"):
    path = tmp_path / name
    path.write_text(xml)
    return path


@needs_ifc
def test_a_specification_that_matches_the_authored_class_passes(tmp_path):
    report = validate_ids(IFC, write(tmp_path, ids_xml("IFCWALLSTANDARDCASE")))
    assert report["passed"] is True
    spec = report["specifications"][0]
    assert spec["status"] == "passed"
    assert spec["applicable"] == 169 and spec["passed"] == 169


@needs_ifc
def test_a_specification_that_matches_nothing_is_disclaimed_not_passed(tmp_path):
    """The headline behaviour: ifctester alone calls this a PASS.

    We do not call it a pass and we do not call it a failure. It is
    inconclusive, and the disclaimer has to say out loud that this may be a
    false pass -- that is the whole point of the rule.
    """
    report = validate_ids(IFC, write(tmp_path, ids_xml("IFCWALL")))
    assert report["verdict"] == "inconclusive"
    assert report["disclaimer"], "an inconclusive result must carry a disclaimer"
    assert "false pass" in report["disclaimer"].lower()

    spec = report["specifications"][0]
    assert spec["status"] == "inconclusive"
    assert spec["applicable"] == 0
    assert "false pass" in spec["disclaimer"].lower()
    assert report["counts"]["inconclusive"] == 1
    assert any("checked nothing" in w for w in report["warnings"])


@needs_ifc
def test_an_inconclusive_specification_does_not_refuse_delivery(tmp_path):
    """Disclosed, not blocking (spec 5.4). A plan may legitimately lack a class."""
    report = validate_ids(IFC, write(tmp_path, ids_xml("IFCWALL")))
    assert report["errors"] == [], "inconclusive belongs in warnings, not errors"
    assert report["passed"] is True


@needs_ifc
def test_an_unmet_requirement_does_fail_the_gate(tmp_path):
    """A real failure is different from a vacuous one and must refuse delivery."""
    report = validate_ids(IFC, write(tmp_path, ids_xml("IFCWALLSTANDARDCASE", prop="NoSuchProperty")))
    assert report["verdict"] == "failed"
    assert report["passed"] is False
    assert report["specifications"][0]["status"] == "failed"
    assert report["counts"]["failed"] == 1
    assert report["errors"], "a real failure belongs in errors"


@needs_ifc
def test_an_optional_specification_may_match_nothing(tmp_path):
    """Review Focus #2: a plan with no doors must not be a hard failure."""
    report = validate_ids(IFC, write(tmp_path, ids_xml("IFCFOOTING", optional=True)))
    assert report["verdict"] == "passed"
    assert report["specifications"][0]["status"] == "skipped-empty"
    assert report["specifications"][0]["optional"] is True
    assert report["specifications"][0]["disclaimer"] == "", \
        "an explicitly optional absence is not a false pass"


def test_a_non_ids_file_is_an_input_error(tmp_path):
    with pytest.raises(IdsInputError, match="broken.ids"):
        validate_ids(IFC, write(tmp_path, "<html>not ids</html>", "broken.ids"))


def test_an_empty_ids_file_is_an_input_error(tmp_path):
    with pytest.raises(IdsInputError):
        validate_ids(IFC, write(tmp_path, "", "empty.ids"))


def test_a_missing_ids_file_names_the_path(tmp_path):
    with pytest.raises(IdsInputError, match="absent.ids"):
        validate_ids(IFC, tmp_path / "absent.ids")


@needs_ifc
def test_the_report_is_json_safe_and_stable(tmp_path):
    import json
    path = write(tmp_path, ids_xml("IFCWALLSTANDARDCASE"))
    first = json.dumps(validate_ids(IFC, path), sort_keys=True, allow_nan=False)
    assert first == json.dumps(validate_ids(IFC, path), sort_keys=True, allow_nan=False)


@needs_ifc
def test_the_gate_is_not_the_slowest_stage(tmp_path):
    """Review Focus #5: ifctester re-walks entities per specification."""
    xml = ids_xml("IFCWALLSTANDARDCASE")
    many = xml.replace("</specifications>", "".join(
        xml.split("<specifications>")[1].split("</specifications>")[0]
           .replace("walls carry provenance", f"spec {i}") for i in range(30)
    ) + "</specifications>")
    started = time.monotonic()
    report = validate_ids(IFC, write(tmp_path, many, "many.ids"))
    elapsed = time.monotonic() - started
    assert report["counts"]["specifications"] == 31
    assert elapsed < 60, f"31 specifications took {elapsed:.1f}s"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python -m pytest checks/test_ids_gate.py -q`
Expected: collection error — `No module named 'archiagent.ifc.ids'`.

- [ ] **Step 4: Implement**

`archiagent/ifc/ids.py`:

```python
"""Validate an authored IFC against a buildingSMART IDS document.

IDS answers a different question from `inspect.validate_export`: that one asks
whether the geometry is right, this one asks whether the *data* the client asked
for is present. It runs in-process with the ifcopenshell that authored the file,
before the CLI hands anything over.

The one rule this module adds to `ifctester` is the important one. IDS entity
matching has no subtype inheritance -- buildingSMART's own specification says
every entity must be listed explicitly -- so a specification naming `IfcWall`
matches none of archiAgent's straight wall runs, which are authored as
`IfcWallStandardCase`. `ifctester` reports that specification as PASSED, because
nothing it checked failed. Measured 2026-09-29: 0 applicable entities, status
True.

A specification that checked nothing is therefore reported as `inconclusive`,
never as a pass, and carries a disclaimer saying it may be a false pass. It does
NOT refuse delivery: a plan may legitimately have no doors, and blocking over an
absent class would be wrong. A specification with real failures does refuse
delivery. See spec 5.4 -- the choice is held in `BLOCK_ON_INCONCLUSIVE` so it is
one edit, not a rewrite, if the disclaimer proves too quiet.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

#: Per-specification cap on recorded failure strings, so one bad model cannot
#: produce a report larger than the IFC it describes.
MAX_FAILURES = 25

#: Whether a specification that checked nothing refuses delivery. False by
#: deliberate decision (spec 5.4): it is disclosed, not blocking. One edit.
BLOCK_ON_INCONCLUSIVE = False

#: Said out loud wherever a vacuous specification is reported. The phrase
#: "false pass" is asserted by the tests -- a reader skimming a green report
#: must trip over it.
FALSE_PASS_DISCLAIMER = (
    "possible FALSE PASS: this specification matched no entities, so nothing "
    "was actually checked. IDS entity matching is exact and does not include "
    "subtypes, so an entity name that looks right may still match nothing -- "
    "archiAgent authors straight walls as IfcWallStandardCase, which an IDS "
    "naming only IfcWall will skip entirely. Treat this as unverified, not as "
    "compliant."
)


class IdsInputError(RuntimeError):
    """The IDS file is missing, empty, or not an IDS document."""


def _load(ids_path: Path):
    from ifctester import ids as ids_module

    if not ids_path.is_file():
        raise IdsInputError(f"no such IDS file: {ids_path}")
    if ids_path.stat().st_size == 0:
        raise IdsInputError(f"{ids_path.name} is empty")
    try:
        document = ids_module.open(str(ids_path))
    except Exception as cause:            # ifctester raises xmlschema/lxml types
        raise IdsInputError(f"{ids_path.name} is not a valid IDS document: {cause}") from cause
    if not getattr(document, "specifications", None):
        raise IdsInputError(f"{ids_path.name} declares no specifications")
    return document


def _optional(spec) -> bool:
    """Whether matching nothing is acceptable for this specification.

    An IDS author says so with `minOccurs="0"` on the specification, which
    ifctester exposes as a falsy `minOccurs`. A plan with no footings is not a
    non-compliant plan.
    """
    return not getattr(spec, "minOccurs", 1)


def validate_ids(ifc_path, ids_path) -> dict:
    ifc_path, ids_path = Path(ifc_path), Path(ids_path)
    document = _load(ids_path)
    model = ifcopenshell.open(str(ifc_path))
    document.validate(model)

    specifications, errors, warnings = [], [], []
    inconclusive = failed = 0
    for spec in document.specifications:
        applicable = len(spec.applicable_entities)
        passed_n = len(spec.passed_entities)
        failed_n = len(spec.failed_entities)
        optional = _optional(spec)
        disclaimer = ""

        if failed_n:
            # A real failure: entities were checked and did not comply.
            status = "failed"
            failed += 1
            errors.append(f"{spec.name!r}: {failed_n} of {applicable} entities failed")
        elif applicable == 0 and optional:
            # The IDS author said absence is acceptable here.
            status = "skipped-empty"
        elif applicable == 0:
            status = "inconclusive"
            inconclusive += 1
            disclaimer = FALSE_PASS_DISCLAIMER
            warnings.append(f"{spec.name!r}: checked nothing -- {FALSE_PASS_DISCLAIMER}")
            if BLOCK_ON_INCONCLUSIVE:
                errors.append(f"{spec.name!r}: checked nothing")
        else:
            status = "passed"

        specifications.append({
            "name": str(spec.name),
            "applicable": applicable,
            "passed": passed_n,
            "failed": failed_n,
            "status": status,
            "optional": optional,
            "disclaimer": disclaimer,
            "failures": [str(reason) for reason in spec.failed_entities[:MAX_FAILURES]],
        })

    # "passed" drives the delivery gate and so tracks errors only. "verdict" is
    # what a human reads, and it must never say "passed" over an empty check.
    if errors:
        verdict = "failed"
    elif inconclusive:
        verdict = "inconclusive"
    else:
        verdict = "passed"

    return {
        "passed": not errors,
        "verdict": verdict,
        "disclaimer": FALSE_PASS_DISCLAIMER if verdict == "inconclusive" else "",
        "ids": ids_path.name,
        "title": str(getattr(document.info, "title", "") or ""),
        "specifications": specifications,
        "counts": {"specifications": len(specifications),
                   "inconclusive": inconclusive, "failed": failed},
        "errors": errors,
        "warnings": warnings,
    }
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest checks/test_ids_gate.py -q`
Expected: PASS 10/10.

`ifctester`'s attribute names are the risk here: `spec.applicable_entities`,
`spec.passed_entities`, `spec.failed_entities` and `spec.minOccurs` were
confirmed on 0.9.0 in the spike, and `ids.open` is asserted by Step 4 rather
than verified. If any differs, read
`.venv/lib/python3.14/site-packages/ifctester/ids.py` and use the real names —
do not weaken a test to match a guess. `spec.failed_entities` may hold objects
rather than strings; `str()` is deliberate, but if the result is unhelpful,
extract the reason field the real type exposes.

- [ ] **Step 6: Commit**

```bash
git add archiagent/ifc/ids.py checks/test_ids_gate.py pyproject.toml
git commit -m "feat(ifc): validate an authored IFC against IDS, failing vacuous specifications"
```

---

## Task 2: archiAgent's own IDS

The product claims "BIM compliant". This file is that claim, written down in a
standard format, versioned and diffable. It must assert only what archiAgent
actually authors (finding 3) — an IDS that overstates is worse than none.

**Files:**
- Create: `archiagent/data/archiagent.ids`
- Modify: `archiagent/ifc/ids.py` (add `builtin_ids_path()`)
- Test: `checks/test_builtin_ids.py`

**Interfaces:**
- Consumes: `validate_ids` from Task 1.
- Produces: `builtin_ids_path() -> Path`, and the IDS file itself. Task 3 consumes both.

- [ ] **Step 1: Write the failing test**

`checks/test_builtin_ids.py`:

```python
"""The shipped IDS must be true of real authored output, and must not overstate.

An IDS that asserts things archiAgent does not author would fail every job; an
IDS whose specifications match nothing would pass every job while checking
nothing. Both are caught here, against real files.
"""
from pathlib import Path

import pytest

from archiagent.ifc.ids import builtin_ids_path, validate_ids

CORPUS = Path(__file__).resolve().parents[2]
PLANS = [
    CORPUS / "wallUpdate_dxfBased_ifcOutput" / "MR RAJEEV JI TWANI JI.ifc",
    CORPUS / "new_dxfBased_ifcOutput" / "MR RAJEEV JI TWANI JI.ifc",
]
AVAILABLE = [p for p in PLANS if p.is_file()]


def test_the_builtin_ids_ships_and_parses():
    assert builtin_ids_path().is_file()
    assert builtin_ids_path().suffix == ".ids"


def test_it_names_both_wall_classes_because_ids_has_no_inheritance():
    """archiAgent authors IfcWall AND IfcWallStandardCase; IDS matches exactly."""
    text = builtin_ids_path().read_text()
    assert "IFCWALLSTANDARDCASE" in text.upper()
    assert "IFCWALL<" in text.upper().replace(" ", "") or "IFCWALL " in text.upper() \
        or ">IFCWALL<" in text.upper()


@pytest.mark.skipif(not AVAILABLE, reason="no corpus IFC present")
@pytest.mark.parametrize("plan", AVAILABLE, ids=lambda p: p.parent.name)
def test_every_authored_plan_satisfies_the_builtin_ids(plan):
    report = validate_ids(plan, builtin_ids_path())
    assert report["verdict"] == "passed", (report["errors"], report["warnings"])


@pytest.mark.skipif(not AVAILABLE, reason="no corpus IFC present")
def test_no_required_specification_is_vacuous_on_a_real_plan():
    """A required specification matching nothing means the IDS is stale."""
    report = validate_ids(AVAILABLE[0], builtin_ids_path())
    vacuous = [s["name"] for s in report["specifications"] if s["status"] == "inconclusive"]
    assert vacuous == [], f"required specifications checked nothing: {vacuous}"


@pytest.mark.skipif(not AVAILABLE, reason="no corpus IFC present")
def test_class_specific_specifications_are_optional_so_absence_is_not_failure():
    """A plan with no columns or no doors is still a compliant plan."""
    report = validate_ids(AVAILABLE[0], builtin_ids_path())
    by_name = {s["name"]: s for s in report["specifications"]}
    for name, spec in by_name.items():
        if spec["applicable"] == 0:
            assert spec["optional"], f"{name} matched nothing and is not optional"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest checks/test_builtin_ids.py -q`
Expected: FAIL — `cannot import name 'builtin_ids_path'`.

- [ ] **Step 3: Write the IDS**

`archiagent/data/archiagent.ids`. Every specification is `minOccurs="0"`
(optional) *except* the wall provenance one, because a plan archiAgent accepted
always has walls, and nothing else is guaranteed: a plan may have no doors, no
columns, no spaces. The wall specification names both authored classes.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  What an archiAgent-authored IFC guarantees.

  This is the machine-readable form of the product's "BIM compliant" claim, so
  it asserts only what archiagent/ifc/author.py actually writes. Two rules
  govern edits:

  1. IDS entity matching is EXACT -- there is no subtype inheritance. Every
     class must be listed. archiAgent authors straight wall runs as
     IfcWallStandardCase and profile walls as IfcWall, so both appear below.
     Omitting one silently checks nothing.
  2. Only the wall specification is required. A plan with no doors, columns or
     spaces is still a valid plan, so those specifications are minOccurs="0";
     the gate treats a required specification that matched nothing as an error.

  Known gap, deliberate: archiAgent authors no standard property sets
  (Pset_WallCommon and friends). A client IDS asking for those will fail, which
  is the honest answer -- see docs/superpowers/plans for the follow-up.
-->
<ids xmlns="http://standards.buildingsmart.org/IDS"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://standards.buildingsmart.org/IDS http://standards.buildingsmart.org/IDS/1.0/ids.xsd">
  <info>
    <title>archiAgent authored output</title>
    <description>What an IFC authored by archiAgent guarantees about its own data.</description>
  </info>
  <specifications>

    <specification name="Walls record the source they were derived from"
                   ifcVersion="IFC4" minOccurs="1" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCWALL</simpleValue></name></entity>
        <entity><name><simpleValue>IFCWALLSTANDARDCASE</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourcePath</simpleValue></baseName>
        </property>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceSHA256</simpleValue></baseName>
        </property>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceIds</simpleValue></baseName>
        </property>
        <property dataType="IFCREAL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>ScaleUnitsPerFoot</simpleValue></baseName>
        </property>
      </requirements>
    </specification>

    <specification name="Doors record the source they were derived from"
                   ifcVersion="IFC4" minOccurs="0" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCDOOR</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceSHA256</simpleValue></baseName>
        </property>
      </requirements>
    </specification>

    <specification name="Columns record the source they were derived from"
                   ifcVersion="IFC4" minOccurs="0" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCCOLUMN</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceSHA256</simpleValue></baseName>
        </property>
      </requirements>
    </specification>

    <specification name="Slabs record the source they were derived from"
                   ifcVersion="IFC4" minOccurs="0" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCSLAB</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>SourceSHA256</simpleValue></baseName>
        </property>
      </requirements>
    </specification>

  </specifications>
</ids>
```

- [ ] **Step 4: Expose it**

Append to `archiagent/ifc/ids.py`:

```python
#: archiAgent's own IDS: the machine-readable form of what it guarantees.
BUILTIN_IDS = Path(__file__).resolve().parent.parent / "data" / "archiagent.ids"


def builtin_ids_path() -> Path:
    return BUILTIN_IDS
```

Confirm `pyproject.toml` already ships `archiagent/data/*` as package data — the
symbol library lives there, so it almost certainly does. If the pattern is
`*.json`, widen it to include `*.ids` and say so in the ledger.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest checks/test_builtin_ids.py -q`
Expected: PASS. **If `test_every_authored_plan_satisfies_the_builtin_ids`
fails, that is a finding about the IFC or the IDS, not a reason to delete the
assertion.** Read `report["errors"]`, decide which side is wrong, and ledger it:
a property archiAgent does not actually author must come out of the IDS; a
property it should author but does not is a Tier 1 bug worth its own commit.

- [ ] **Step 6: Commit**

```bash
git add archiagent/data/archiagent.ids archiagent/ifc/ids.py checks/test_builtin_ids.py
git commit -m "feat(ifc): ship archiAgent's own IDS as the BIM-compliance claim"
```

---

## Task 3: Gate delivery on the built-in IDS

**Files:**
- Modify: `archiagent/ifc/inspect.py`
- Modify: `archiagent/cli.py`
- Test: `checks/test_ids_gate.py` (extend)

**Interfaces:**
- Consumes: `validate_ids`, `builtin_ids_path` from Tasks 1–2.
- Produces: `validate_export(path, models, *, ids_path=None)` whose report gains
  an `"ids"` key and whose `"passed"` is `False` when the IDS gate fails.
  `--no-ids-check` disables it.

- [ ] **Step 1: Write the failing test**

Append to `checks/test_ids_gate.py`:

```python
# --- the delivery gate --------------------------------------------------------

@needs_ifc
def test_validate_export_includes_the_ids_section_by_default():
    """Both CLI call sites already gate on validate_export; IDS rides along."""
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    report = validate_export(IFC, fixture_model())
    assert "ids" in report
    assert report["ids"]["ids"] == "archiagent.ids"
    assert report["ids"]["counts"]["specifications"] >= 1


@needs_ifc
def test_a_real_ids_failure_fails_export_validation(tmp_path):
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    bad = write(tmp_path, ids_xml("IFCWALLSTANDARDCASE", prop="NoSuchProperty"), "bad.ids")
    report = validate_export(IFC, fixture_model(), ids_path=bad)
    assert report["ids"]["verdict"] == "failed"
    assert report["passed"] is False
    assert report["errors"]


@needs_ifc
def test_a_vacuous_ids_warns_without_blocking_delivery(tmp_path):
    """Disclosed, not blocking -- but the disclaimer must reach the report."""
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    vacuous = write(tmp_path, ids_xml("IFCWALL"), "vacuous.ids")
    report = validate_export(IFC, fixture_model(), ids_path=vacuous)
    assert report["ids"]["verdict"] == "inconclusive"
    assert "false pass" in report["ids"]["disclaimer"].lower()
    assert report["passed"] is True, "a vacuous specification must not withhold the IFC"
    assert any("false pass" in w.lower() for w in report["warnings"])


@needs_ifc
def test_ids_checking_can_be_switched_off():
    from archiagent.ifc.inspect import validate_export
    from checks.test_semantic_ifc import fixture_model

    report = validate_export(IFC, fixture_model(), ids_path=False)
    assert report["ids"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest checks/test_ids_gate.py -q -k "export or switched"`
Expected: FAIL — `validate_export() got an unexpected keyword argument 'ids_path'`.

- [ ] **Step 3: Implement in `inspect.py`**

Change the signature to `def validate_export(path, models, *, ids_path=None):`
and, after the existing checks have populated `report["errors"]` and just before
`report["passed"]` is computed, add:

```python
    # IDS asks whether the data the client asked for is present, which
    # validate_export's geometric checks cannot answer. `ids_path=False`
    # switches it off (--no-ids-check); None means the built-in IDS.
    if ids_path is False:
        report["ids"] = None
    else:
        from archiagent.ifc.ids import builtin_ids_path, validate_ids
        ids_report = validate_ids(path, builtin_ids_path() if ids_path is None else ids_path)
        report["ids"] = ids_report
        report["errors"].extend(ids_report["errors"])
        # The disclaimer is the deliverable for an inconclusive result; losing it
        # here would turn a disclosed false pass back into a silent one.
        report.setdefault("warnings", []).extend(ids_report["warnings"])
```

`report["passed"]` is already derived from `report["errors"]`; confirm that by
reading the line that sets it, and if it is set before this point, move this
block above it rather than duplicating the logic.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest checks/test_ids_gate.py -q`
Expected: PASS 14/14.

- [ ] **Step 5: Wire the CLI flag**

In `cli.py`, beside `--require-accepted`:

```python
    p.add_argument("--no-ids-check", action="store_true",
                   help="skip IDS validation of the authored IFC (it is on by default, "
                        "using archiAgent's own IDS)")
```

and at both `validate_export` call sites (`cli.py:508`, `cli.py:821`) pass
`ids_path=False if args.no_ids_check else None`. The surrounding
`if not result["passed"]: return EXIT_PIPELINE` needs no change — that is the
whole point of putting the check here.

- [ ] **Step 6: Prove the gate end to end on a real drawing**

```bash
rm -rf /tmp/idsgate && .venv/bin/python -m archiagent --rules --no_vision \
  --dxfFilePath "../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf" \
  --outputDir /tmp/idsgate --units-per-foot 12
.venv/bin/python -c "
import json,glob
r=json.load(open(glob.glob('/tmp/idsgate/*.report.json')[0]))
ids=r['export_validation']['ids']
print('ids passed:', ids['passed'], '| specs:', ids['counts'])
for s in ids['specifications']: print(' ', s['status'], s['applicable'], s['name'])
"
```

Expected: `ids passed: True`, the wall specification matching >0 entities, and
every zero-match specification reported as `skipped-empty`. Exit code 0.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: the Phase 4 count plus the new tests. The pre-existing
`test_wall_candidacy.py::...[0.69-True]` failure is caused by an uncommitted
`archiagent/classify/layers.py` edit and is not this plan's; report it by name
rather than fixing or hiding it.

- [ ] **Step 8: Commit**

```bash
git add archiagent/ifc/inspect.py archiagent/cli.py checks/test_ids_gate.py
git commit -m "feat(cli): gate IFC delivery on IDS compliance"
```

---

## Task 4: Accept a client-supplied IDS

Optional, additive, and the answer to "must the user provide an IDS?" — no. This
exists for the BIM manager who has one, and the zero-match rule protects them
from the trap in finding 2.

**Files:**
- Modify: `archiagent/cli.py`
- Test: `checks/test_ids_cli.py`

**Interfaces:**
- Consumes: Task 3's `ids_path` parameter.
- Produces: `--idsFilePath PATH`. When given, **both** the built-in IDS and the
  client's are validated: ours states what we guarantee, theirs what they need.

- [ ] **Step 1: Write the failing test**

`checks/test_ids_cli.py`:

```python
"""A client IDS is optional and additive; the built-in one always runs."""
import json
from pathlib import Path

import pytest

from archiagent import cli

CORPUS = Path(__file__).resolve().parents[2]
DXF = CORPUS / "input-floorplans" / "dxf" / "Jiju_dxf" / "MR RAJEEV JI TWANI JI.dxf"
needs_dxf = pytest.mark.skipif(not DXF.is_file(), reason="corpus DXF not present")


def client_ids(entity="IFCWALLSTANDARDCASE", prop="SourceLayer"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS">
  <info><title>client</title></info>
  <specifications>
    <specification name="client rule" ifcVersion="IFC4" minOccurs="1" maxOccurs="unbounded">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>{entity}</simpleValue></name></entity>
      </applicability>
      <requirements>
        <property dataType="IFCLABEL" cardinality="required">
          <propertySet><simpleValue>ArchiAgent_Provenance</simpleValue></propertySet>
          <baseName><simpleValue>{prop}</simpleValue></baseName>
        </property>
      </requirements>
    </specification>
  </specifications>
</ids>"""


def run(tmp_path, *extra):
    out = tmp_path / "out"
    return cli.main(["--rules", "--no_vision", "--dxfFilePath", str(DXF),
                     "--outputDir", str(out), "--units-per-foot", "12", *extra]), out


def report_of(out):
    return json.load(open(next(out.glob("*.report.json"))))


@needs_dxf
def test_a_client_ids_that_the_model_satisfies_lets_the_job_through(tmp_path):
    ids = tmp_path / "client.ids"
    ids.write_text(client_ids())
    code, out = run(tmp_path, "--idsFilePath", str(ids))
    assert code == 0
    sections = report_of(out)["export_validation"]["ids_documents"]
    assert {s["ids"] for s in sections} == {"archiagent.ids", "client.ids"}
    assert all(s["verdict"] == "passed" for s in sections)


@needs_dxf
def test_a_client_ids_the_model_fails_refuses_delivery(tmp_path):
    ids = tmp_path / "client.ids"
    ids.write_text(client_ids(prop="FireRating"))
    code, out = run(tmp_path, "--idsFilePath", str(ids))
    assert code != 0
    assert not list(out.glob("*.ifc")), "a non-compliant IFC must not be delivered"


@needs_dxf
def test_a_client_ids_that_matches_nothing_is_disclaimed_to_the_user(tmp_path, capsys):
    """Review Focus #1 reaching the user: the IfcWall trap.

    The job succeeds -- this is disclosed, not blocking -- but the operator must
    be told on stderr that their IDS verified nothing, or the disclosure is
    buried in a report nobody opens.
    """
    ids = tmp_path / "client.ids"
    ids.write_text(client_ids(entity="IFCWALL"))
    code, out = run(tmp_path, "--idsFilePath", str(ids))
    assert code == 0
    assert "false pass" in capsys.readouterr().err.lower()
    section = [s for s in report_of(out)["export_validation"]["ids_documents"]
               if s["ids"] == "client.ids"][0]
    assert section["verdict"] == "inconclusive"


@needs_dxf
def test_a_malformed_client_ids_is_a_usage_error_naming_the_file(tmp_path, capsys):
    ids = tmp_path / "broken.ids"
    ids.write_text("<html>nope</html>")
    code, _ = run(tmp_path, "--idsFilePath", str(ids))
    assert code == cli.EXIT_USAGE
    assert "broken.ids" in capsys.readouterr().err


def test_an_ids_for_the_wrong_schema_is_reported(tmp_path):
    """Review Focus #4: IFC2X3 IDS against IFC4 output must not read as a model fault."""
    from archiagent.ifc.ids import validate_ids
    ifc = CORPUS / "wallUpdate_dxfBased_ifcOutput" / "MR RAJEEV JI TWANI JI.ifc"
    if not ifc.is_file():
        pytest.skip("corpus IFC not present")
    ids = tmp_path / "old.ids"
    ids.write_text(client_ids().replace('ifcVersion="IFC4"', 'ifcVersion="IFC2X3"'))
    report = validate_ids(ifc, ids)
    assert report["passed"] is False
    assert any("IFC2X3" in e or "schema" in e.lower() for e in report["errors"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest checks/test_ids_cli.py -q`
Expected: FAIL — unrecognised argument `--idsFilePath`.

- [ ] **Step 3: Implement**

Add the argument:

```python
    p.add_argument("--idsFilePath", metavar="PATH", dest="ids_file_path",
                   help="validate the authored IFC against this buildingSMART IDS "
                        "file as well as archiAgent's own; delivery is refused if "
                        "either fails")
```

Change `validate_export` to accept `extra_ids` and report a list rather than one
document. Rename the report key to `"ids_documents"` (a list) and keep `"ids"` as
the built-in document for the tests written in Task 3 — or update those tests to
the list shape. **Pick one and ledger it; do not ship both keys.** The list shape
is the better end state, so prefer updating Task 3's three assertions.

At both `validate_export` call sites, after the `passed` check, surface any
disclaimer on stderr -- a report file nobody opens is not a disclosure:

```python
    for document in result.get("ids_documents", ()):
        if document["verdict"] == "inconclusive":
            print(f"warning: {document['ids']}: {document['disclaimer']}", file=sys.stderr)
```

Validate the schema version explicitly in `validate_ids`, which Review Focus #4
needs:

```python
    declared = {str(getattr(s, "ifcVersion", "") or "") for s in document.specifications}
    actual = model.schema
    stale = {v for v in declared if v and actual not in v}
    if stale:
        errors.append(f"{ids_path.name} targets {sorted(stale)} but the model is {actual}")
```

`ifcVersion` may be a list on an ifctester specification; check the real type
before writing the comparison.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest checks/test_ids_cli.py checks/test_ids_gate.py checks/test_builtin_ids.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add archiagent/cli.py archiagent/ifc/ids.py archiagent/ifc/inspect.py checks/
git commit -m "feat(cli): accept a client IDS alongside archiAgent's own"
```

---

## Task 5: Surface it through the service

**Files:**
- Modify: `archiagent-viewer/service/archiagent_service/pipeline.py`
- Modify: `archiagent-viewer/service/archiagent_service/uploads.py`
- Test: `archiagent-viewer/service/tests/test_ids_upload.py`

**Interfaces:**
- Consumes: the CLI's `--idsFilePath` and the report's `ids_documents` section.
- Produces: `.ids` as an accepted secondary upload, `read_ids_status(report)`, and an `ids` block in `job.json`.

- [ ] **Step 1: Write the failing test**

`service/tests/test_ids_upload.py`:

```python
"""A job may carry an IDS file; its outcome is visible in job.json."""
import json

import pytest

from archiagent_service.pipeline import build_command, read_ids_status
from archiagent_service.uploads import ALLOWED_SUFFIXES, validate_upload


def test_ids_is_an_accepted_upload_suffix():
    assert ".ids" in ALLOWED_SUFFIXES


def test_build_command_passes_an_ids_when_the_job_has_one(tmp_path):
    ids = tmp_path / "plan.ids"; ids.write_text("<ids/>")
    cmd = build_command(tmp_path / "plan.dxf", tmp_path, units_per_foot=12, ids=ids)
    assert cmd[cmd.index("--idsFilePath") + 1] == str(ids)


def test_build_command_omits_the_flag_without_one(tmp_path):
    cmd = build_command(tmp_path / "plan.dxf", tmp_path, units_per_foot=12)
    assert "--idsFilePath" not in cmd


def test_read_ids_status_reduces_pessimistically(tmp_path):
    report = tmp_path / "plan.report.json"
    report.write_text(json.dumps({"export_validation": {"ids_documents": [
        {"ids": "archiagent.ids", "verdict": "passed", "passed": True,
         "counts": {"failed": 0, "inconclusive": 0}},
        {"ids": "client.ids", "verdict": "inconclusive", "passed": True,
         "counts": {"failed": 0, "inconclusive": 1}},
    ]}}))
    status = read_ids_status(report)
    # Pessimistic: one inconclusive document makes the job's IDS verdict
    # inconclusive, and job.json must carry the disclaimer to the UI.
    assert status["verdict"] == "inconclusive"
    assert status["documents"] == 2
    assert "false pass" in status["disclaimer"].lower()


def test_read_ids_status_is_none_when_the_section_is_absent(tmp_path):
    report = tmp_path / "plan.report.json"
    report.write_text(json.dumps({"export_validation": {}}))
    assert read_ids_status(report) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ../archiagent-viewer/service && python -m pytest tests/test_ids_upload.py -q`
Expected: FAIL — `cannot import name 'read_ids_status'`.

- [ ] **Step 3: Implement**

Add `".ids"` to `ALLOWED_SUFFIXES`; give `build_command` an `ids: Path | None = None`
parameter appending `["--idsFilePath", str(ids)]`; add `read_ids_status(report_path)`
mirroring `read_acceptance`'s pessimistic reduction over `verdict`:
`failed` beats `inconclusive` beats `passed`, and the returned block carries the
`disclaimer` of the worst document so the UI can show it. Include the block in
`job_json()`. A job whose IDS verdict is `inconclusive` still succeeds -- the
disclaimer is the deliverable, not a failure.

Note that an IDS upload is a **second** file for one job, which the current
single-upload flow does not model. If that turns out to need an endpoint change,
stop at the seam: implement `build_command` and `read_ids_status`, ledger the
upload-flow gap as deferred, and leave the endpoint for its own increment. The
CLI gate is already delivering the value.

- [ ] **Step 4: Run the tests**

Run: `cd ../archiagent-viewer/service && python -m pytest tests/test_ids_upload.py -q`
Expected: PASS 5/5.

- [ ] **Step 5: Run the whole service suite**

Run: `cd ../archiagent-viewer/service && ARCHIAGENT_REQUIRE_SERVICES=1 python -m pytest -q`
Expected: the Phase 4 count plus the new tests, no failures, Docker up.

- [ ] **Step 6: Amend the spec**

In `docs/superpowers/specs/2026-09-26-webapp-three-tier-design.md`:
§2.2 — replace the `@ifc-lite/ids` bullet with a note that IDS validation ships
in Tier 1 via `ifctester`, and why (it gates authoring rather than reporting
after it); delete the `@ifc-lite/drawing-2d` bullet; state that
`@ifc-lite/mcp` is now ifc-lite's sole justification. §11 — restate Phase 5's
deliverable as "IDS compliance gate before delivery" and note that Phase 7 no
longer depends on a Phase 5 Node tier, because none was built.

- [ ] **Step 7: Commit**

```bash
git add ../archiagent-viewer/service docs/superpowers/specs/
git commit -m "feat(service): carry an IDS through a job and report its outcome"
```

---

## Self-review notes

- **Spec coverage.** §2.2's IDS capability → Tasks 1–4, relocated to Tier 1 with the reasoning recorded and the spec amended in Task 5 Step 6. §2.2's `drawing-2d` capability → dropped, with the reason stated. §5's Tier 1 authority → strengthened, not weakened: the gate lives beside `validate_export`. §11's Phase 5 row → rewritten in Task 5 Step 6.
- **Invariants.** I1 holds: nothing here writes IFC. I4 holds trivially and more strongly than before, since no second kernel is introduced at all. I6 holds: no MCP server.
- **Type consistency.** The one known inconsistency is deliberate and flagged: Task 3 introduces `report["ids"]` as a single document and Task 4 needs `report["ids_documents"]` as a list. Task 4 Step 3 says to pick the list and update Task 3's three assertions, rather than shipping both keys.
- **Speculative surfaces, named.** `ifctester`'s `ids.open`, `spec.minOccurs` and `spec.ifcVersion` types are asserted from a spike that exercised `applicable_entities`, `passed_entities`, `failed_entities` and `status` but not those three. Each step that uses them says to read `ifctester/ids.py` and correct the code rather than the test.
- **Review Focus mapping.** #1 → Task 1 `test_a_specification_that_matches_nothing_is_disclaimed_not_passed`, `test_an_inconclusive_specification_does_not_refuse_delivery`, and Task 4's stderr test. #2 → Task 1 `test_an_optional_specification_may_match_nothing` and Task 2's optionality test. #3 → Task 1's three input-error tests and Task 4's CLI test. #4 → Task 4 `test_an_ids_for_the_wrong_schema_is_reported`. #5 → Task 1 `test_the_gate_is_not_the_slowest_stage`.
- **Out of scope, deliberately:** authoring standard property sets (`Pset_WallCommon`) — see "The property gap" below; changing the wall class away from `IfcWallStandardCase`; and any IDS authoring UI.

---

## The property gap — the follow-up this plan sets up but does not do

Spec §5.5 is the authority; this is the sequencing. archiAgent authors one
property set, `ArchiAgent_Provenance`, and no standard ones, so a client IDS
asking for `Pset_WallCommon.FireRating` fails today. That failure is *correct*
and must not be closed by inventing values — the output's entire value is that
its claims are traceable.

The gap is three problems, and only the first two are archiAgent's:

**Phase 5a — author what is already known.** Each of these has a real basis in
`BuildingModel` plus an existing provenance flag to qualify it, so nothing is
invented:

| Property | Basis | Qualifier already in the model |
|---|---|---|
| `Pset_WallCommon.LoadBearing` | `Role.WALL_STRUCTURAL` vs `WALL_PARTITION` | `LayerDecision.confidence`, `.basis` |
| `Pset_WallCommon.IsExternal` | centreline vs `BuildingModel.footprints` | `footprint_verified` |
| `Qto_WallBaseQuantities.Length` / `.Width` / `.Height` | `WallSeg.length_ft`, `.thickness_ft`, `wall_height_ft` | `WallSeg.thickness_source` (`measured`/`default`) |
| `IfcDoor.OverallWidth` / `.OverallHeight` | `Opening.start/end`, `.height_ft` | `Opening.assumed_height`, `.evidence` |
| `Qto_SpaceBaseQuantities` | `BuildingModel.spaces` polygons | `Space` provenance |

The discipline is the one already in the code: `AppearancePreset` ships beside
`AppearanceBasis` ("Illustrative class palette; not a verified material..."). A
derived `LoadBearing` is a layer-name inference, not a structural determination,
and the IFC has to say so. Every property authored here becomes a new
specification in `archiagent.ids` in the same commit — the IDS grows with the
guarantee, never ahead of it.

**Phase 5b — accept what cannot be derived.** Fire and acoustic ratings, U-values,
real material assemblies. No geometry yields these; they need an input channel —
an operator- or client-supplied overlay keyed by wall role, source layer or
thickness class, recorded as *declared by the operator*, never inferred. The
precedent is `--units-per-foot`, which already overrides a header archiAgent
refuses to trust. This needs its own small design document: the overlay format,
how it is reviewed, how it reaches the worker.

**Neither — fail with a sentence, not a verdict.** When a client IDS requires a
property in neither category, the gate must say "required by your IDS; not
derivable from a 2D plan; not supplied". `FAIL` is not actionable; that is.
Worth adding to `validate_ids` once 5a and 5b define the two categories
concretely — before then it cannot tell the three cases apart, which is why it
is not in Task 1.
