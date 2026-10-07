# Scale Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive a DXF's scale from the dimensions already in the drawing, require exactly one human assertion when that is not enough, and delete `--units-per-foot` from the CLI so nobody is ever asked how many drawing units make a foot.

**Architecture:** A new `extracted_scale(ps)` reads the drawing's own native `DIMENSION` entities and room-label pairs into an `ExtractedScale` record. It runs on every DXF, whether or not it is the chosen source, because its spans are the independent second witness that lets one human assertion reach a *verified* scale. A four-rung ladder in the CLI picks the source and fails loudly rather than falling back to `$INSUNITS`.

**Tech Stack:** Python 3.12+, pytest, ezdxf, shapely. No new runtime dependencies. The browser picker is deliberately **not** in this plan.

**Spec:** `docs/superpowers/specs/2026-10-06-scale-resolution-design.md`

## Global Constraints

- Tests live in `checks/`, run from the repo root with `source .venv/bin/activate && python -m pytest`.
- Fixtures authored from scratch, never client data. `checks/candidacy_fixtures.py` is the pattern: feet, `units_per_foot=1`.
- **Do not change `infer_associated_scale` or `extract_from_primitives`.** The PDF scale path is out of scope and is regression-pinned in Task 2.
- `extract_from_dxf(units_per_foot=...)` **stays** as a Python parameter. Only the CLI flag is removed. `PlanRegion.units_per_foot` stays untouched.
- One pre-existing test failure is expected and is not yours: `checks/test_wall_candidacy.py::test_a_never_wall_role_excludes_only_at_or_above_the_confidence_floor[0.69-True]`, from an uncommitted `WALL_CONFIDENCE_FLOOR` edit in `archiagent/classify/layers.py`. Leave that file alone.
- `scale_from_reviewed` is delivered by `docs/superpowers/plans/2026-10-06-declared-wall-metrics.md` Task 4. If that plan has not run, implement it first from its Task 4; do not duplicate it here.

---

### Task 1: Prove handles join the browser picker to the pipeline

**Files:**
- Create: `docs/superpowers/notes/2026-10-07-handle-join-spike.md`

This is a **spike**: its output is an answer, not code to keep. It gates the
browser picker's design, which is specified but deliberately not planned here.

**Interfaces:**
- Consumes: `archiagent.ingest.dxf_vector.load_dxf`
- Produces: a written finding — whether a DXF handle identifies the same entity
  in `ezdxf` and in the `dxf` npm parser the picker would use

- [ ] **Step 1: Establish what the Python side records as an entity id**

Run:

```bash
source .venv/bin/activate && python - <<'PY'
from archiagent.ingest.dxf_vector import load_dxf
ps, upf = load_dxf("../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf")
top = [e for e in ps.entities if not e.parent_id and e.kind in ("LINE", "LWPOLYLINE")]
print("top-level line entities:", len(top))
for e in top[:10]:
    print(f"  id={e.id!r} kind={e.kind} layer={e.layer} coords={len(e.coords)}")
PY
```

Expected: ids are bare DXF handles such as `'1F4'`, not synthesised paths.
Synthesised ids like `1F4/0/2:LWPOLYLINE` belong to geometry expanded out of an
INSERT and are the case the picker cannot join on.

- [ ] **Step 2: Record how many entities are joinable**

```bash
source .venv/bin/activate && python - <<'PY'
from archiagent.ingest.dxf_vector import load_dxf
for name in ("Jiju_dxf/MR RAJEEV JI TWANI JI.dxf", "Jiju_dxf/Aiims Road 3BHK Flats-vk.dxf", "Floor Plan.dxf"):
    ps, upf = load_dxf(f"../input-floorplans/dxf/{name}")
    top = [e for e in ps.entities if not e.parent_id]
    nested = [e for e in ps.entities if e.parent_id]
    lines = [e for e in top if e.kind in ("LINE", "LWPOLYLINE")]
    print(f"{name.split('/')[-1][:30]:32s} top={len(top):6d} nested={len(nested):6d} top_lines={len(lines):6d}")
PY
```

Record the numbers. A wall the user can select is almost always a top-level
`LINE`/`LWPOLYLINE`; the nested count is the share the picker could not join by
handle and would have to fall back to coordinate matching for.

- [ ] **Step 3: Confirm the handle survives a round trip through ezdxf**

```bash
source .venv/bin/activate && python - <<'PY'
import ezdxf
doc = ezdxf.readfile("../input-floorplans/dxf/Jiju_dxf/MR RAJEEV JI TWANI JI.dxf")
msp = doc.modelspace()
for e in list(msp.query("LINE"))[:5]:
    print(f"handle={e.dxf.handle!r} layer={e.dxf.layer} start={e.dxf.start} end={e.dxf.end}")
PY
```

Expected: the printed handles appear verbatim in Step 1's id list, and the
coordinates are raw source units. The `dxf` npm parser exposes the same DXF
group code 5 handle, so this is the join key.

- [ ] **Step 4: Write the finding**

Write `docs/superpowers/notes/2026-10-07-handle-join-spike.md` recording: the
three drawings' top/nested/top-line counts, whether handles matched verbatim,
and a one-line verdict — **join on handle**, or **fall back to coordinates**.
State plainly which it is; the picker plan depends on this answer and must not
have to re-derive it.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/notes/2026-10-07-handle-join-spike.md
git commit -m "$(cat <<'EOF'
docs: record whether DXF handles join the browser picker to the pipeline

Spike only, no production code. The picker's provenance design depends on the
selected entity being identifiable on both sides.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Extract scale from the drawing's own native dimensions

**Files:**
- Create: `archiagent/scale/extracted.py`
- Test: `checks/test_extracted_scale.py`

**Interfaces:**
- Consumes: `archiagent.evidence.NativeDimension` (fields `id`, `start`, `end`, `measured_source_units`, `text`, `measurement_type`, `measurement_axis`, `text_evidence`), `archiagent.primitives.PrimitiveSet` (`dimensions`, `entities`), `archiagent.scale.verify.parse_explicit_length`
- Produces: `ExtractedScale(units_per_foot, spans, support, basis, rejected)` and `extracted_scale(ps, tolerance_in=2.0) -> ExtractedScale | None`

- [ ] **Step 1: Write the failing test**

Create `checks/test_extracted_scale.py`:

```python
"""The drawing's own dimensions give a scale, when they agree and are trustworthy."""
import pytest

from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet
from archiagent.scale.extracted import ExtractedScale, extracted_scale


def dim(id, length_units, text, origin="generated", dimlfac="1"):
    """One horizontal native dimension, with the ingest metadata it carries."""
    entity = SourceEntity(id, "DIMENSION", "DIMS", metadata=(
        ("dimension_text_origin", origin), ("dimstyle_dimlfac", dimlfac)))
    native = NativeDimension(id, (0.0, 0.0), (length_units, 0.0), length_units,
                             text, "DIMS", "linear", (1.0, 0.0))
    return entity, native


def drawing(pairs):
    entities = tuple(e for e, _ in pairs)
    dims = tuple(d for _, d in pairs)
    return PrimitiveSet((), (), 500.0, 500.0, "dims.dxf", "sha-dims", entities, dims)


def test_two_agreeing_generated_dimensions_give_the_scale():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\"")])
    result = extracted_scale(ps)
    assert isinstance(result, ExtractedScale)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.support == 2
    assert result.basis == "native-dimension"
    assert len(result.spans) == 2


def test_a_single_dimension_cannot_agree_with_anything():
    assert extracted_scale(drawing([dim("a", 120.0, "10'-0\"")])) is None


def test_an_explicit_override_is_not_used_while_a_generated_pair_exists():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 100.0, "50'-0\"", origin="explicit-override")])
    result = extracted_scale(ps)
    assert result.units_per_foot == pytest.approx(12.0)
    assert result.support == 2
    assert any(rid == "c" for rid, _ in result.rejected)


def test_a_scaled_dimstyle_is_excluded_and_explained():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 120.0, "10'-0\"", dimlfac="25.4")])
    result = extracted_scale(ps)
    rejected = dict(result.rejected)
    assert "c" in rejected
    assert "dimlfac" in rejected["c"]


def test_dimensions_that_disagree_yield_nothing():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "5'-0\"")])
    assert extracted_scale(ps) is None


def test_unparseable_dimension_text_is_rejected_with_a_reason():
    ps = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\""),
                  dim("c", 120.0, "SEE PLAN")])
    rejected = dict(extracted_scale(ps).rejected)
    assert "c" in rejected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_extracted_scale.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'archiagent.scale.extracted'`

- [ ] **Step 3: Implement the module**

Create `archiagent/scale/extracted.py`:

```python
"""Scale read from the dimensions a drawing already carries.

Generated dimension text is better calibration evidence than an explicit
override: generated text is the CAD rendering the measured span into the
drafter's own unit system, so the ratio reveals the units reliably. An override
is a number a human typed, which need not match the geometry at all. A dimstyle
linear factor other than 1 means displayed and drawn lengths were deliberately
decoupled, so those dimensions cannot calibrate anything.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from archiagent.scale.verify import parse_explicit_length

USABLE_TYPES = ("linear", "aligned", "rotated")


@dataclass(frozen=True)
class ExtractedScale:
    units_per_foot: float
    spans: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    support: int
    basis: str
    rejected: tuple[tuple[str, str], ...] = ()


def _metadata(ps):
    return {e.id: dict(e.metadata) for e in ps.entities if e.kind == "DIMENSION"}


def _usable(ps):
    """(dimension, ratio) for dimensions that can carry a scale, plus rejections."""
    meta = _metadata(ps)
    generated, overridden, rejected = [], [], []
    for d in ps.dimensions:
        if d.measurement_type not in USABLE_TYPES:
            rejected.append((d.id, f"measurement type {d.measurement_type}"))
            continue
        info = meta.get(d.id, {})
        factor = info.get("dimstyle_dimlfac", "")
        if factor not in ("", "1", "1.0"):
            rejected.append((d.id, f"dimlfac {factor}: displayed length is scaled"))
            continue
        feet = parse_explicit_length(d.text)
        if feet is None or feet <= 0:
            rejected.append((d.id, f"text {d.text!r} is not a length"))
            continue
        span = abs(d.measured_source_units)
        if not math.isfinite(span) or span <= 0:
            rejected.append((d.id, "measured span is not positive"))
            continue
        entry = (d, span / feet)
        if info.get("dimension_text_origin") == "explicit-override":
            overridden.append(entry)
        else:
            generated.append(entry)
    return generated, overridden, rejected


def _agree(entries, tolerance_in):
    """Median ratio and the entries supporting it, or None when fewer than two."""
    if len(entries) < 2:
        return None
    ratios = [r for _, r in entries]
    median = statistics.median(ratios)
    if not math.isfinite(median) or median <= 0:
        return None
    supporting, outliers = [], []
    for dimension, ratio in entries:
        feet = abs(dimension.measured_source_units) / median
        expected = parse_explicit_length(dimension.text)
        (supporting if abs(feet - expected) * 12 <= tolerance_in else outliers).append(
            (dimension, ratio))
    if len(supporting) < 2:
        return None
    return median, supporting, outliers


def extracted_scale(ps, tolerance_in=2.0) -> ExtractedScale | None:
    """Source units per foot from the drawing's own dimensions, or None."""
    if not math.isfinite(tolerance_in) or tolerance_in <= 0:
        raise ValueError("scale tolerance_in must be finite and positive")
    generated, overridden, rejected = _usable(ps)
    for entries, basis in ((generated, "native-dimension"),
                           (overridden, "native-dimension-override")):
        agreed = _agree(entries, tolerance_in)
        if agreed is None:
            continue
        median, supporting, outliers = agreed
        unused = [(d.id, "disagrees with the agreed scale") for d, _ in outliers]
        if entries is generated:
            unused += [(d.id, "explicit override not needed; generated text agreed")
                       for d, _ in overridden]
        return ExtractedScale(median,
                              tuple((d.start, d.end) for d, _ in supporting),
                              len(supporting), basis,
                              tuple(sorted(rejected + unused)))
    return None
```

- [ ] **Step 4: Run the tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_extracted_scale.py -v`
Expected: all PASS.

- [ ] **Step 5: Pin the PDF path against regression**

Append to `checks/test_extracted_scale.py`:

```python
def test_the_pdf_scale_path_is_untouched():
    # infer_associated_scale still refuses a single span and still ignores OCR.
    from archiagent.scale.verify import Measurement, infer_associated_scale
    one = Measurement("m1", (0, 0), (120, 0), 10.0, "face", "reviewed-measurement", None)
    assert infer_associated_scale((one,)) is None
```

Run: `source .venv/bin/activate && python -m pytest checks/test_extracted_scale.py checks/test_regions_scale.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add archiagent/scale/extracted.py checks/test_extracted_scale.py
git commit -m "$(cat <<'EOF'
feat: read scale from the native dimensions a drawing already carries

Generated dimension text calibrates better than an explicit override: it is the
CAD rendering the measured span into the drafter's units, while an override is a
typed number that need not match the geometry. dimlfac other than 1 excludes a
dimension outright, since displayed and drawn lengths were decoupled on purpose.

Rejections travel with the result so a drawing full of dimensions that still
yields no scale can explain itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Fail loudly instead of trusting the header

**Files:**
- Modify: `archiagent/cli.py` (scale selection around `:804`, flag definitions around `:151`)
- Test: `checks/test_scale_ladder.py`

**Interfaces:**
- Consumes: `archiagent.scale.extracted.extracted_scale`, `archiagent.scale.verify.scale_from_reviewed` (from the declared-wall-metrics plan, Task 4), `cli._wall_length_measurements`
- Produces: `cli._resolve_scale(args, ps, is_dxf, region, header_units) -> tuple[float, str, tuple[Issue, ...]]` returning the chosen scale, the rung that supplied it, and issues; raises `ValueError` when nothing establishes a scale

- [ ] **Step 1: Write the failing test**

Create `checks/test_scale_ladder.py`:

```python
"""Scale comes from the drawing, or from one assertion, but never silently from the header."""
import pytest

from archiagent.cli import _parser, _resolve_scale
from archiagent.evidence import NativeDimension, SourceEntity
from archiagent.primitives import PrimitiveSet

BASE = ["--dxfFilePath", "plan.dxf", "--outputDir", "out"]


def parse(extra=()):
    return _parser().parse_args(BASE + list(extra))


def dim(id, length_units, text):
    entity = SourceEntity(id, "DIMENSION", "DIMS", metadata=(
        ("dimension_text_origin", "generated"), ("dimstyle_dimlfac", "1")))
    native = NativeDimension(id, (0.0, 0.0), (length_units, 0.0), length_units,
                             text, "DIMS", "linear", (1.0, 0.0))
    return entity, native


def drawing(pairs=()):
    return PrimitiveSet((), (), 500.0, 500.0, "plan.dxf", "sha",
                        tuple(e for e, _ in pairs), tuple(d for _, d in pairs))


DIMENSIONED = drawing([dim("a", 120.0, "10'-0\""), dim("b", 240.0, "20'-0\"")])
BARE = drawing()


def test_a_bare_drawing_with_no_scale_input_is_refused():
    with pytest.raises(ValueError) as caught:
        _resolve_scale(parse(), BARE, True, None, 1.0)
    message = str(caught.value)
    assert "--scale-from-wall" in message
    assert "--trust-extracted-scale" in message


def test_the_refusal_reports_what_was_extracted_and_what_the_header_said():
    with pytest.raises(ValueError) as caught:
        _resolve_scale(parse(), DIMENSIONED, True, None, 1.0)
    message = str(caught.value)
    assert "12" in message          # the extracted estimate
    assert "1" in message           # the header's claim


def test_trusting_the_extracted_scale_accepts_it():
    scale, rung, _ = _resolve_scale(parse(["--trust-extracted-scale"]), DIMENSIONED, True, None, 1.0)
    assert scale == pytest.approx(12.0)
    assert rung == "extracted"


def test_trusting_extraction_with_nothing_extractable_is_refused():
    with pytest.raises(ValueError):
        _resolve_scale(parse(["--trust-extracted-scale"]), BARE, True, None, 1.0)


def test_one_assertion_sets_the_scale():
    args = parse(["--scale-from-wall", "0", "0", "120", "0", "10ft"])
    scale, rung, _ = _resolve_scale(args, BARE, True, None, 1.0)
    assert scale == pytest.approx(12.0)
    assert rung == "asserted"


def test_an_assertion_overrides_a_disagreeing_extraction_with_a_warning():
    args = parse(["--scale-from-wall", "0", "0", "254", "0", "10ft"])
    scale, rung, issues = _resolve_scale(args, DIMENSIONED, True, None, 1.0)
    assert scale == pytest.approx(25.4)
    assert rung == "asserted"
    codes = {i.code for i in issues}
    assert "asserted_scale_overrides_extracted" in codes


def test_a_reviewed_region_scale_wins_without_any_assertion():
    class Region:
        units_per_foot = 304.8
    scale, rung, _ = _resolve_scale(parse(), BARE, True, Region(), 1.0)
    assert scale == pytest.approx(304.8)
    assert rung == "region"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_scale_ladder.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_scale'`

- [ ] **Step 3: Add the two new flags**

In `archiagent/cli.py`, beside `--scale-from-wall`:

```python
    p.add_argument("--trust-extracted-scale", action="store_true",
                   help="accept the scale derived from the drawing's own dimensions "
                        "without asserting a wall length")
    p.add_argument("--scale-tolerance-in", type=float, default=2.0, metavar="IN",
                   help="how far two spans may disagree before they are not agreeing "
                        "on a scale (default 2.0)")
```

- [ ] **Step 4: Implement the ladder**

```python
def _resolve_scale(args, ps, is_dxf, region, header_units):
    """The chosen scale, the rung that supplied it, and any issues.

    Never falls back to the file header on its own. A header is a declaration,
    not a measurement, and two corpus drawings declare units they are not drawn
    in -- which is how they produced confidently wrong models.
    """
    from archiagent.scale.extracted import extracted_scale
    from archiagent.scale.verify import scale_from_reviewed

    issues = []
    region_scale = getattr(region, "units_per_foot", None) if region is not None else None
    extracted = extracted_scale(ps, args.scale_tolerance_in) if is_dxf else None
    asserted = scale_from_reviewed(_wall_length_measurements(args), args.scale_tolerance_in)

    if region_scale is not None:
        return float(region_scale), "region", tuple(issues)

    if asserted is not None:
        if extracted is not None:
            ratio = max(asserted, extracted.units_per_foot) / min(asserted, extracted.units_per_foot)
            if abs(asserted - extracted.units_per_foot) * 12 > args.scale_tolerance_in:
                issues.append(Issue(
                    "warn", "scale", "asserted_scale_overrides_extracted",
                    f"asserted wall length implies {asserted:g} units/foot; the drawing's own "
                    f"dimensions imply {extracted.units_per_foot:g} (from {extracted.support} "
                    f"agreeing). Using {asserted:g} as asserted. Ratio {ratio:.3g}"
                    + _unit_factor_hint(ratio)))
        return asserted, "asserted", tuple(issues)

    if args.trust_extracted_scale:
        if extracted is None:
            raise ValueError(
                "--trust-extracted-scale was given but no scale could be read from this "
                "drawing's dimensions. Assert one instead: --scale-from-wall X1 Y1 X2 Y2 LENGTH")
        return extracted.units_per_foot, "extracted", tuple(issues)

    if not is_dxf:
        return header_units, "pdf", tuple(issues)

    estimate = (f"  the drawing's own dimensions suggest {extracted.units_per_foot:g} units/foot "
                f"(from {extracted.support} agreeing, unverified)\n"
                if extracted is not None else
                "  no usable dimensions were found in the drawing\n")
    raise ValueError(
        f"scale is not established for this drawing.\n{estimate}"
        f"  the file header declares {header_units:g} units/foot\n"
        "Supply one wall you can identify:\n"
        "  --scale-from-wall X1 Y1 X2 Y2 LENGTH\n"
        "or accept the drawing's own dimensions:\n"
        "  --trust-extracted-scale")


def _unit_factor_hint(ratio):
    """Name the ratio when it is a unit confusion rather than a disagreement."""
    for factor, text in ((25.4, "mm read as inches"), (12.0, "feet read as inches"),
                         (304.8, "mm read as feet"), (30.48, "cm read as feet")):
        if abs(ratio - factor) / factor <= 0.01:
            return f" -- that is {factor:g}, which usually means {text}"
    return ""
```

- [ ] **Step 5: Run the tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_scale_ladder.py -v`
Expected: all PASS.

- [ ] **Step 6: Use it at the call site**

Replace the `selected_scale = ...` expression around `:804` with a call to
`_resolve_scale(args, ps, is_dxf, region, units_per_foot)`, wrapped in the
existing `_configuration(...)` helper so a `ValueError` becomes a clean
configuration exit rather than a traceback. Extend `classifier_issues` with the
returned issues. When the rung is `"asserted"` and exactly one measurement was
supplied and no extraction corroborated it, print to stderr:

```python
            print("note: scale set from one asserted length and nothing in the drawing "
                  "corroborates it; a second span would allow it to be verified",
                  file=sys.stderr)
```

- [ ] **Step 7: Verify on real drawings**

```bash
source .venv/bin/activate
# Refuses rather than trusting a header known to be wrong:
python -m archiagent --dxfFilePath "../input-floorplans/dxf/PLAN.dxf" \
  --outputDir /tmp/ladder-a --rules --no_vision 2>&1 | tail -8
# Accepts the drawing's own dimensions:
python -m archiagent --dxfFilePath "../input-floorplans/dxf/Jiju_dxf/Aiims Road 3BHK Flats-vk.dxf" \
  --outputDir /tmp/ladder-b --rules --no_vision --no-wall-adjudication \
  --trust-extracted-scale 2>&1 | tail -4
```

Expected: the first exits with the refusal naming both the extracted estimate
and the header; the second resolves a scale from its 417 dimensions. Record both
outputs in the commit message.

- [ ] **Step 8: Commit**

```bash
git add archiagent/cli.py checks/test_scale_ladder.py
git commit -m "$(cat <<'EOF'
feat: resolve DXF scale from the drawing, or one assertion, never the header alone

A header is a declaration, not a measurement, and two corpus drawings declare
units they are not drawn in -- which is how they produced confidently wrong
models at exit 0. A DXF run now refuses rather than falling back to it, and the
refusal prints the scale the drawing's own dimensions imply next to what the
header claims, so accepting the former is one flag away.

An assertion still wins over extraction, by decision, but the warning names both
scales and their ratio, and says when that ratio is a known unit confusion.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Delete `--units-per-foot` from the CLI

**Files:**
- Modify: `archiagent/cli.py` (flag definition `:151-154`, the incompatible-flag set `:607`, any remaining uses), `README.md`
- Test: `checks/test_scale_ladder.py`

**Interfaces:**
- Consumes: Task 3's `_resolve_scale`
- Produces: no new names. `extract_from_dxf(units_per_foot=...)` and `PlanRegion.units_per_foot` are unchanged.

- [ ] **Step 1: Write the failing test**

Append to `checks/test_scale_ladder.py`:

```python
def test_the_units_per_foot_flag_is_gone():
    # Removed deliberately: it was an unattributed number that silently beat
    # every other source, including the drawing's own dimensions.
    with pytest.raises(SystemExit):
        _parser().parse_args(BASE + ["--units-per-foot", "12"])


def test_the_python_api_still_takes_a_scale():
    # Only the user-facing flag goes; tests and library callers keep theirs.
    import inspect

    from archiagent.pipeline import extract_from_dxf
    assert "units_per_foot" in inspect.signature(extract_from_dxf).parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_scale_ladder.py -k units_per_foot -v`
Expected: FAIL — `test_the_units_per_foot_flag_is_gone` does not raise, because the flag still parses.

- [ ] **Step 3: Remove the flag**

Delete the `p.add_argument("--units-per-foot", ...)` block. Remove
`"--units-per-foot"` from the `incompatible` set near `:607`. Replace every
remaining `args.units_per_foot` read with the Task 3 ladder result; the one in
`load_dxf(input_path, units_per_foot=args.units_per_foot)` becomes
`load_dxf(input_path)`, letting the header supply a provisional value that
`_resolve_scale` then judges.

- [ ] **Step 4: Run the whole suite and fix fallout**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: failures only in tests that passed `--units-per-foot` on a CLI
invocation. Convert each to `--trust-extracted-scale` or an explicit
`--scale-from-wall`, whichever matches what that test is actually about. Do
**not** convert tests that call `extract_from_dxf(units_per_foot=...)` directly —
that parameter stays.

- [ ] **Step 5: Update the documentation**

In `README.md` and the `archiagent/cli.py` module docstring, remove
`--units-per-foot` and describe the ladder instead:

```markdown
### Scale

The tool reads the drawing's own dimensions and uses them. When they are absent
or disagree, it asks you to identify one wall:

    --scale-from-wall 0 0 120 0 "10'-6\""

or, to accept what the drawing says about itself:

    --trust-extracted-scale

It will not silently fall back to the file header: a header is a declaration,
not a measurement, and drawings whose headers are wrong are common enough that
trusting one produced confidently wrong models.
```

- [ ] **Step 6: Commit**

```bash
git add archiagent/cli.py README.md checks/
git commit -m "$(cat <<'EOF'
feat!: remove --units-per-foot from the CLI

BREAKING: the flag is gone. It was rung one of the scale ladder -- beating every
other source, carrying no provenance and never cross-checked -- which is exactly
how a wrong number produced a confident model. Anyone who knows the scale can
assert it about a span they can point at, which is attributable and gets
cross-checked against the drawing's own dimensions.

extract_from_dxf(units_per_foot=...) and PlanRegion.units_per_foot are unchanged:
one is how tests and library callers inject a scale, the other is reviewed data
with an author.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Deferred to its own plan: the browser picker

Specified in the spec's §5 and **not** planned here, because Task 1 gates its
design. It is `ieskudero/three-dxf-viewer` (MIT; deps `dxf@5.3.0`, `three`,
`rtf.js`), which supplies `Select`, `Hover`, `Snap` and layer control but **no
measure tool** — that is the part to build. Its plan needs: a vendored bundle so
no run depends on a registry, a measure mode that records the selected entity's
handle, an explicit `face` versus `centerline` choice, a live implied-scale
readout as the length is typed, and export of `{"measurements": [...]}` that the
existing `--measurements` path already reads.

Also deferred: the **room-label pair** extraction source (spec §3 step 3). It
depends on space polygons and on the declared wall thickness to convert between
a label's clear interior and a centreline enclosure, so it belongs with
Sub-project B rather than here.
