# Declared Wall Metrics Implementation Plan

> **Superseded 2026-10-09 by `2026-10-09-declared-wall-thickness.md`**, which reuses this plan's Tasks 1, 2, 3 and 5 verbatim, drops Task 4 (delivered by the scale work) and rewrites Task 6. Do not execute Tasks 4, 6 or 7 from here.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user declare the wall thickness set and a true wall length on the CLI, so wall candidacy stops guessing the thickness set and DXF scale stops depending on a possibly-wrong file header.

**Architecture:** Declared thicknesses travel as new defaulted fields on `candidacy.Context`, replacing `_thickness_modes`' inference when present; an optional exhaustive flag forces a `reject` band *after* scoring so the tuned weights and score normalisation are untouched. A new `scale_from_reviewed` accepts a single human-asserted length as calibration, leaving the existing `infer_associated_scale` and the PDF path byte-identical.

**Tech Stack:** Python 3.12+, pytest, shapely, ezdxf. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-declared-wall-metrics-design.md`

## Global Constraints

- Tests live in `checks/`, run with `python -m pytest` from the repo root. Activate the venv first: `source .venv/bin/activate`.
- Fixtures must be authored from scratch, never client data. Follow `checks/candidacy_fixtures.py`, which works in feet at `units_per_foot=1`.
- `Context` stores **feet only**. Inch-to-foot conversion happens once, at the CLI boundary. Field is named `declared_thickness_ft`.
- Do **not** change `WEIGHTS`, `ACCEPT_FLOOR`, `REJECT_CEILING`, or `combine`. Changing one weight rescales every score and re-bands unrelated drawings.
- Do **not** change `infer_associated_scale` or `extract_from_primitives`. The PDF scale path stays as it is.
- Every new `Context` field must be defaulted so existing callers keep working.
- One pre-existing test failure is expected and is not yours: `checks/test_wall_candidacy.py::test_a_never_wall_role_excludes_only_at_or_above_the_confidence_floor[0.69-True]`, caused by an uncommitted `WALL_CONFIDENCE_FLOOR` edit in `archiagent/classify/layers.py`. Leave that file alone.

---

### Task 1: Declared thicknesses replace inferred modes

**Files:**
- Modify: `archiagent/geometry/candidacy.py:37-45` (`Context`), `:48-49` (`build_context`), `:216-230` (`_thickness_modes`, `_thickness`)
- Test: `checks/test_declared_thickness.py`

**Interfaces:**
- Consumes: `candidacy.build_context`, `candidacy.score_candidates`, `checks/candidacy_fixtures.wall`, `checks/candidacy_fixtures.source`, `checks/candidacy_fixtures.CLASSIFICATION`
- Produces: `Context.declared_thickness_ft: tuple[float, ...]`, `Context.thickness_tolerance_ft: float`, `Context.thickness_exhaustive: bool`; `build_context(ps, classification, units_per_foot, *, declared_thickness_ft=(), thickness_tolerance_ft=MODE_TOLERANCE_FT, thickness_exhaustive=False)`

- [ ] **Step 1: Write the failing test**

Create `checks/test_declared_thickness.py`:

```python
"""A declared thickness set replaces the set candidacy would infer."""
from archiagent.geometry.candidacy import (MODE_TOLERANCE_FT, build_context,
                                           score_candidates)
from checks.candidacy_fixtures import CLASSIFICATION, source, wall


def signals_for(walls, **kwargs):
    ctx = build_context(source(), CLASSIFICATION, 1.0, **kwargs)
    return {c.wall.thickness_ft: dict(c.signals)["thickness"]
            for c in score_candidates(walls, ctx)}


def test_a_declared_set_replaces_the_inferred_one():
    # Six-inch junk carries most of the length, so inference would make 6in the
    # mode and penalise the real 4in and 8in walls.
    walls = [wall((0, 0), (40, 0), 0.5), wall((0, 5), (40, 5), 0.5),
             wall((0, 10), (10, 10), 4 / 12), wall((0, 15), (10, 15), 8 / 12)]
    inferred = signals_for(walls)
    assert inferred[4 / 12] == -0.5
    assert inferred[0.5] == 1.0

    declared = signals_for(walls, declared_thickness_ft=(4 / 12, 8 / 12),
                           thickness_tolerance_ft=0.5 / 12)
    assert declared[4 / 12] == 1.0
    assert declared[8 / 12] == 1.0
    assert declared[0.5] == -0.5


def test_the_declared_tolerance_is_honoured():
    inside = signals_for([wall((0, 0), (10, 0), 4.4 / 12)],
                         declared_thickness_ft=(4 / 12,),
                         thickness_tolerance_ft=0.5 / 12)
    assert inside[4.4 / 12] == 1.0
    outside = signals_for([wall((0, 0), (10, 0), 4.6 / 12)],
                          declared_thickness_ft=(4 / 12,),
                          thickness_tolerance_ft=0.5 / 12)
    assert outside[4.6 / 12] == -0.5


def test_inference_keeps_its_original_one_inch_tolerance():
    ctx = build_context(source(), CLASSIFICATION, 1.0)
    assert ctx.thickness_tolerance_ft == MODE_TOLERANCE_FT
    assert ctx.declared_thickness_ft == ()
    assert ctx.thickness_exhaustive is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py -v`
Expected: FAIL — `TypeError: build_context() got an unexpected keyword argument 'declared_thickness_ft'`

- [ ] **Step 3: Add the Context fields**

In `archiagent/geometry/candidacy.py`, extend the `Context` dataclass (after `glyph_points`):

```python
    glyph_points: tuple[Point, ...]
    # Declared by the user, in feet. Empty means candidacy infers the set from
    # the drawing, as it always has.
    declared_thickness_ft: tuple[float, ...] = ()
    thickness_tolerance_ft: float = MODE_TOLERANCE_FT
    thickness_exhaustive: bool = False
```

`MODE_TOLERANCE_FT` is defined below `Context` in the module, so move the
`MODE_SHARE` / `MODE_TOLERANCE_FT` constant pair above the `Context` definition
without changing their values.

- [ ] **Step 4: Thread the fields through build_context**

Change the signature and the final `Context(...)` construction:

```python
def build_context(ps: PrimitiveSet, classification: Classification,
                  units_per_foot: float, *,
                  declared_thickness_ft: tuple[float, ...] = (),
                  thickness_tolerance_ft: float = MODE_TOLERANCE_FT,
                  thickness_exhaustive: bool = False) -> Context:
```

and pass the three values through to the returned `Context`, keeping every
existing positional argument and computed field exactly as it is.

- [ ] **Step 5: Use them in the thickness signal**

```python
def _thickness_modes(walls, ctx: Context) -> tuple[float, ...]:
    """Declared thicknesses when the user supplied them, else the inferred set.

    Thicknesses carrying at least MODE_SHARE of confident wall-layer run length.
    """
    if ctx.declared_thickness_ft:
        return ctx.declared_thickness_ft
    lengths: Counter = Counter()
    for w in walls:
        role, confidence = ctx.roles.get(w.source_layer.casefold(), (Role.IGNORE, 0.0))
        if role in WALL_ROLES and confidence >= WALL_CONFIDENCE_FLOOR:
            lengths[round(w.thickness_ft * 48) / 48] += w.length_ft
    total = sum(lengths.values())
    return tuple(t for t, length in sorted(lengths.items()) if total and length / total >= MODE_SHARE)


def _thickness(wall: WallSeg, modes: tuple[float, ...], tolerance_ft: float) -> float:
    if not modes:
        return 0.0
    return 1.0 if any(abs(wall.thickness_ft - m) <= tolerance_ft for m in modes) else -0.5
```

In `score_candidates`, pass the tolerance:

```python
            ("thickness", _thickness(w, modes, ctx.thickness_tolerance_ft)))
```

- [ ] **Step 6: Run the new test and the existing candidacy suite**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py checks/test_wall_candidacy.py checks/test_wall_runs.py -v`
Expected: the new tests PASS; `test_wall_candidacy.py` and `test_wall_runs.py` unchanged except the one pre-existing `confidence_floor[0.69-True]` failure named in Global Constraints.

- [ ] **Step 7: Commit**

```bash
git add archiagent/geometry/candidacy.py checks/test_declared_thickness.py
git commit -m "$(cat <<'EOF'
feat: let a declared wall thickness set replace candidacy's inferred one

Inference counts thickness by run length over wall-role layers, so a drawing
whose non-wall parallel pairs dominate makes a false thickness the mode and
penalises the real walls. A declared set removes that circularity, and gets a
tighter tolerance than inference can safely use.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The exhaustive veto, as a band override

**Files:**
- Modify: `archiagent/geometry/candidacy.py:233-245` (`score_candidates`)
- Test: `checks/test_declared_thickness.py`

**Interfaces:**
- Consumes: `Context.thickness_exhaustive`, `Context.declared_thickness_ft`, `Context.thickness_tolerance_ft` from Task 1; `candidacy.ScoredCandidate(id, wall, signals, score, band)`
- Produces: no new names; `score_candidates` may now return `band == "reject"` for a candidate whose `score` is above `ACCEPT_FLOOR`

- [ ] **Step 1: Write the failing test**

Append to `checks/test_declared_thickness.py`:

```python
from archiagent.geometry.candidacy import ACCEPT_FLOOR
from checks.candidacy_fixtures import house


def banded(walls, **kwargs):
    ctx = build_context(source(), CLASSIFICATION, 1.0, **kwargs)
    return {c.wall.thickness_ft: (c.band, c.score) for c in score_candidates(walls, ctx)}


def test_an_exhaustive_set_vetoes_a_thickness_it_does_not_contain():
    # The house is a closed, connected 9in rectangle, so it scores well.
    walls = house()
    declared = {"declared_thickness_ft": (4 / 12, 8 / 12),
                "thickness_tolerance_ft": 0.5 / 12}

    scored = banded(walls, **declared)
    assert any(score >= ACCEPT_FLOOR for _, score in scored.values())

    vetoed = banded(walls, **declared, thickness_exhaustive=True)
    assert {band for band, _ in vetoed.values()} == {"reject"}
    # The pre-veto score is retained so a reviewer can see what was removed.
    assert vetoed[0.75][1] == scored[0.75][1]


def test_without_the_exhaustive_flag_the_scored_band_stands():
    walls = house()
    scored = banded(walls, declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12)
    assert "reject" not in {band for band, _ in scored.values()}


def test_an_exhaustive_set_still_accepts_a_declared_thickness():
    walls = [wall(a, b, 8 / 12) for a, b in
             (((0, 20), (30, 20)), ((30, 0), (30, 20)), ((0, 0), (30, 0)), ((0, 0), (0, 20)))]
    kept = banded(walls, declared_thickness_ft=(4 / 12, 8 / 12),
                  thickness_tolerance_ft=0.5 / 12, thickness_exhaustive=True)
    assert "reject" not in {band for band, _ in kept.values()}


def test_the_scoring_weights_and_normalisation_are_unchanged():
    # Pinned deliberately. combine() divides by sum(WEIGHTS.values()), so
    # changing any single weight rescales every score and re-bands every
    # candidate in every drawing against floors tuned to this normalisation.
    # The declared-thickness veto exists precisely to avoid touching these.
    from archiagent.geometry.candidacy import REJECT_CEILING, WEIGHTS
    assert WEIGHTS == {"layer_role": 1.0, "length": 1.0, "connectivity": 2.0,
                       "closure": 2.0, "thickness": 0.5, "instancing": 1.5,
                       "glyph": 1.5, "nested_outline": 2.5}
    assert sum(WEIGHTS.values()) == 12.0
    assert (ACCEPT_FLOOR, REJECT_CEILING) == (0.65, 0.35)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py -k exhaustive -v`
Expected: FAIL — `test_an_exhaustive_set_vetoes_a_thickness_it_does_not_contain` asserts all bands are `reject`, but the 9in house currently bands `accept`.

- [ ] **Step 3: Apply the veto in score_candidates**

```python
def score_candidates(walls, ctx: Context) -> tuple[ScoredCandidate, ...]:
    walls = tuple(walls)
    touches = _touches(walls)
    connectivity, closure = _connectivity(walls, touches), _closure(walls, touches)
    modes = _thickness_modes(walls, ctx)
    out = []
    for i, w in enumerate(walls):
        signals = candidate_signals(w, ctx) + (
            ("connectivity", connectivity[i]), ("closure", closure[i]),
            ("thickness", _thickness(w, modes, ctx.thickness_tolerance_ft)))
        score = combine(signals)
        # A declared set the user calls complete is ground truth, so a run
        # matching none of it is not a wall. This overrides the band rather
        # than raising the thickness weight: combine() normalises by the sum of
        # WEIGHTS, so reweighting would re-band every candidate in every
        # drawing against floors that were tuned to the current normalisation.
        vetoed = (ctx.thickness_exhaustive and ctx.declared_thickness_ft
                  and _thickness(w, modes, ctx.thickness_tolerance_ft) < 0)
        out.append(ScoredCandidate(candidate_id(w), w, signals, score,
                                   "reject" if vetoed else band(score)))
    return tuple(out)
```

- [ ] **Step 4: Run the tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py checks/test_wall_candidacy.py -v`
Expected: all new tests PASS; existing candidacy tests unchanged but for the known pre-existing failure.

- [ ] **Step 5: Verify the adjudicator never sees a vetoed candidate**

Add to `checks/test_declared_thickness.py`:

```python
import pytest

from archiagent.geometry.candidacy import select_walls


# Adjudicator is a plain Callable[[PrimitiveSet, tuple[ScoredCandidate, ...], float], ...]
# returning (verdicts, issues) -- not an object with a method.
@pytest.mark.xfail(raises=TypeError, strict=True,
                   reason="select_walls gains the declared-set keywords in Task 3")
def test_a_vetoed_candidate_is_never_sent_to_the_adjudicator():
    seen = []

    def recorder(ps, candidates, units_per_foot):
        seen.extend(candidates)
        return {}, ()

    select_walls(source(), house(), CLASSIFICATION, 1.0, adjudicator=recorder,
                 declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12,
                 thickness_exhaustive=True)
    assert seen == []
```

The `xfail` marker is removed in Task 3 Step 4, once `select_walls` accepts the
keywords. `strict=True` means the test fails if it unexpectedly passes, so the
marker cannot be left behind silently.

- [ ] **Step 6: Commit**

```bash
git add archiagent/geometry/candidacy.py checks/test_declared_thickness.py
git commit -m "$(cat <<'EOF'
feat: veto wall candidates outside an exhaustive declared thickness set

Applied as a band override after scoring, not by raising the thickness weight:
combine() normalises by the sum of WEIGHTS, so reweighting would silently
re-band every candidate in every drawing against floors tuned to the current
normalisation. The pre-veto score is retained for review.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Thread the declared set from the pipeline

**Files:**
- Modify: `archiagent/geometry/candidacy.py:317-328` (`select_walls`), `archiagent/pipeline.py:24` (`_assemble` signature), `archiagent/pipeline.py` (the `select_walls` call site), `archiagent/pipeline.py:217` (`extract_from_dxf`)
- Test: `checks/test_declared_thickness.py`

**Interfaces:**
- Consumes: Task 1's `build_context` keyword arguments
- Produces: `select_walls(ps, proposed, classification, units_per_foot, *, adjudicator=None, declared_thickness_ft=(), thickness_tolerance_ft=MODE_TOLERANCE_FT, thickness_exhaustive=False)`; `_assemble(..., declared_thickness_ft=(), thickness_tolerance_ft=MODE_TOLERANCE_FT, thickness_exhaustive=False)`; `extract_from_dxf(..., declared_thickness_ft=(), thickness_tolerance_ft=MODE_TOLERANCE_FT, thickness_exhaustive=False)`

- [ ] **Step 1: Write the failing test**

Append to `checks/test_declared_thickness.py`:

```python
def test_select_walls_passes_the_declared_set_through():
    # CandidateDecision carries `verdict` ("accept" | "reject"), not `band`.
    kept, decisions, _ = select_walls(
        source(), house(), CLASSIFICATION, 1.0,
        declared_thickness_ft=(4 / 12,), thickness_tolerance_ft=0.5 / 12,
        thickness_exhaustive=True)
    assert kept == ()
    assert {d.verdict for d in decisions} == {"reject"}
    assert all(d.verdict_source == "deterministic" for d in decisions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py::test_select_walls_passes_the_declared_set_through -v`
Expected: FAIL — `TypeError: select_walls() got an unexpected keyword argument 'declared_thickness_ft'`

- [ ] **Step 3: Add the keyword arguments to select_walls**

```python
def select_walls(ps: PrimitiveSet, proposed, classification: Classification, units_per_foot: float,
                 *, adjudicator: Adjudicator | None = None,
                 declared_thickness_ft: tuple[float, ...] = (),
                 thickness_tolerance_ft: float = MODE_TOLERANCE_FT,
                 thickness_exhaustive: bool = False
                 ) -> tuple[tuple[WallSeg, ...], tuple[CandidateDecision, ...], tuple[Issue, ...]]:
```

and pass them into the existing `build_context` call:

```python
    scored = score_candidates(established + tuple(kept),
                              build_context(ps, classification, units_per_foot,
                                            declared_thickness_ft=declared_thickness_ft,
                                            thickness_tolerance_ft=thickness_tolerance_ft,
                                            thickness_exhaustive=thickness_exhaustive))
```

- [ ] **Step 4: Remove the xfail marker from Task 2 Step 5**

Delete the `@pytest.mark.xfail(...)` line above
`test_a_vetoed_candidate_is_never_sent_to_the_adjudicator` and its `pytest`
import if nothing else uses it.

- [ ] **Step 5: Thread through _assemble and extract_from_dxf**

In `archiagent/pipeline.py`, add to `_assemble`'s keyword-only parameters:

```python
def _assemble(ps, classification, scale, wall_height_ft, *, region=None,
              measurements=(), review=None, adjudicator=None, symbol_library=(),
              declared_thickness_ft=(), thickness_tolerance_ft=None,
              thickness_exhaustive=False):
```

Inside, resolve the default without importing the constant at module scope
(`_assemble` already imports from `candidacy` locally):

```python
    from archiagent.geometry.candidacy import MODE_TOLERANCE_FT
    tolerance_ft = MODE_TOLERANCE_FT if thickness_tolerance_ft is None else thickness_tolerance_ft
```

and pass all three into the existing `select_walls(...)` call. Add the same
three keyword arguments to `extract_from_dxf` and forward them to `_assemble`.
Leave `extract_from_primitives` alone.

- [ ] **Step 6: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all pass except the one pre-existing `confidence_floor[0.69-True]` failure.

- [ ] **Step 7: Commit**

```bash
git add archiagent/geometry/candidacy.py archiagent/pipeline.py checks/test_declared_thickness.py
git commit -m "$(cat <<'EOF'
feat: thread the declared thickness set from the pipeline into candidacy

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Calibrate scale from one human-asserted length

**Files:**
- Modify: `archiagent/scale/verify.py` (add `scale_from_reviewed` after `infer_associated_scale` at `:215-229`)
- Test: `checks/test_scale_from_wall.py`

**Interfaces:**
- Consumes: `verify.Measurement`, `verify._span`, `verify._positive`
- Produces: `scale_from_reviewed(measurements, tolerance_in=2.0) -> float | None`; the recognised human-assertion sources are exactly `{"reviewed-measurement", "cli-wall-length"}`

- [ ] **Step 1: Write the failing test**

Create `checks/test_scale_from_wall.py`:

```python
"""One human-asserted wall length is enough to calibrate; it cannot verify."""
import pytest

from archiagent.scale.verify import (Measurement, infer_associated_scale,
                                     scale_from_reviewed)


def asserted(id, start, end, expected_ft, source="cli-wall-length"):
    return Measurement(id, start, end, expected_ft, "face", source, None)


def test_one_asserted_length_sets_the_scale():
    # A 120-unit span the user says is 10ft means 12 units per foot.
    m = asserted("w1", (0, 0), (120, 0), 10.0)
    assert scale_from_reviewed((m,)) == pytest.approx(12.0)


def test_infer_associated_scale_still_refuses_a_single_span():
    # Regression pin: the PDF path's behaviour must not change.
    m = asserted("w1", (0, 0), (120, 0), 10.0, source="reviewed-measurement")
    assert infer_associated_scale((m,)) is None


def test_native_dimension_and_ocr_sources_are_ignored():
    native = asserted("d1", (0, 0), (120, 0), 10.0, source="native-dimension")
    ocr = asserted("o1", (0, 0), (120, 0), 10.0, source="ocr-associated")
    assert scale_from_reviewed((native, ocr)) is None


def test_two_agreeing_spans_give_the_same_scale():
    a = asserted("w1", (0, 0), (120, 0), 10.0)
    b = asserted("w2", (0, 50), (0, 290), 20.0)
    assert scale_from_reviewed((a, b)) == pytest.approx(12.0)


def test_two_disagreeing_spans_are_refused():
    a = asserted("w1", (0, 0), (120, 0), 10.0)
    b = asserted("w2", (0, 50), (0, 290), 10.0)
    with pytest.raises(ValueError, match="disagree"):
        scale_from_reviewed((a, b))


def test_no_asserted_lengths_yields_none():
    assert scale_from_reviewed(()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_scale_from_wall.py -v`
Expected: FAIL — `ImportError: cannot import name 'scale_from_reviewed'`

- [ ] **Step 3: Implement scale_from_reviewed**

Add to `archiagent/scale/verify.py`, directly after `infer_associated_scale`:

```python
ASSERTED_SOURCES = ("reviewed-measurement", "cli-wall-length")


def scale_from_reviewed(measurements, tolerance_in=2.0):
    """Source units per foot from human-asserted lengths; one span is enough.

    infer_associated_scale needs two independent spans because it works from
    dimension text the pipeline itself associated, which can be matched to the
    wrong geometry. A length a reviewer states about a span they picked is an
    assertion, not an inference, so one calibrates. It still cannot VERIFY:
    scale_verified requires two distinct verified spans and that is unchanged.
    """
    _positive(tolerance_in, "dimension tolerance_in")
    asserted = [m for m in measurements
                if m.source in ASSERTED_SOURCES
                and m.expected_ft is not None and m.expected_ft > 0]
    if not asserted:
        return None
    ratios = sorted(_span(m.start, m.end, m.axis) / m.expected_ft for m in asserted)
    scale = ratios[len(ratios) // 2]
    _positive(scale, "asserted source units per foot")
    if any(abs(_span(m.start, m.end, m.axis) / scale - m.expected_ft) * 12 > tolerance_in
           for m in asserted):
        raise ValueError("asserted wall lengths disagree on scale; review the lengths or the units")
    return scale
```

- [ ] **Step 4: Run the tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_scale_from_wall.py checks/test_regions_scale.py checks/test_ocr_dimensions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add archiagent/scale/verify.py checks/test_scale_from_wall.py
git commit -m "$(cat <<'EOF'
feat: calibrate scale from a single human-asserted wall length

infer_associated_scale needs two spans because it works from dimension text the
pipeline associated itself, which can attach to the wrong geometry. A length a
reviewer states about a span they chose is an assertion, so one is enough to
calibrate -- it still cannot verify, which needs two distinct spans.

Left infer_associated_scale untouched so the PDF path is unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Thickness-versus-scale cross-check

**Files:**
- Modify: `archiagent/geometry/candidacy.py` (add `thickness_scale_check` after `_thickness_modes`)
- Test: `checks/test_declared_thickness.py`

**Interfaces:**
- Consumes: `WallSeg.thickness_ft`, `WallSeg.length_ft`
- Produces: `thickness_scale_check(walls, declared_ft, tolerance_ft) -> float | None` returning the implied uniform correction factor, or `None` when the observed thicknesses already fit

- [ ] **Step 1: Write the failing test**

Append to `checks/test_declared_thickness.py`:

```python
from archiagent.geometry.candidacy import thickness_scale_check


def test_a_uniform_factor_is_reported_when_nothing_matches():
    # Authored in inches but read as feet: every thickness is 12x too large.
    walls = [wall((0, 0), (10, 0), 4.0), wall((0, 5), (10, 5), 8.0)]
    factor = thickness_scale_check(walls, (4 / 12, 8 / 12), 0.5 / 12)
    assert factor == pytest.approx(12.0, rel=0.02)


def test_no_factor_is_reported_when_thicknesses_already_fit():
    walls = [wall((0, 0), (10, 0), 4 / 12), wall((0, 5), (10, 5), 8 / 12)]
    assert thickness_scale_check(walls, (4 / 12, 8 / 12), 0.5 / 12) is None


def test_no_factor_is_reported_without_a_declared_set():
    assert thickness_scale_check([wall((0, 0), (10, 0), 4.0)], (), 0.5 / 12) is None
```

Add `import pytest` at the top of the file if it is not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py -k scale_check -v`
Expected: FAIL — `ImportError: cannot import name 'thickness_scale_check'`

- [ ] **Step 3: Implement the check**

Add to `archiagent/geometry/candidacy.py`:

```python
# A declared set makes observed thickness a free check on scale: if every
# thickness is off by one factor, the scale is wrong, not the drawing.
SCALE_CHECK_FACTORS = (1 / 304.8, 1 / 30.48, 1 / 12, 12.0, 30.48, 304.8)
SCALE_CHECK_SHARE = 0.60


def thickness_scale_check(walls, declared_ft, tolerance_ft):
    """The uniform factor that would align observed thicknesses to the declared set.

    Returns None when the observed thicknesses already fit, or when no single
    factor explains most of the run length. Never changes the scale: a silent
    automatic rescale is the kind of invisible decision this pipeline avoids.
    """
    walls = tuple(walls)
    if not declared_ft or not walls:
        return None

    def share(factor):
        matched = sum(w.length_ft for w in walls
                      if any(abs(w.thickness_ft / factor - d) <= tolerance_ft for d in declared_ft))
        total = sum(w.length_ft for w in walls)
        return matched / total if total else 0.0

    if share(1.0) >= SCALE_CHECK_SHARE:
        return None
    best = max(SCALE_CHECK_FACTORS, key=share)
    return best if share(best) >= SCALE_CHECK_SHARE else None
```

- [ ] **Step 4: Run the tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_thickness.py -v`
Expected: all PASS.

- [ ] **Step 5: Emit it as an Issue from the pipeline**

In `archiagent/pipeline.py`, inside `_assemble` after `select_walls` has run and
`declared_thickness_ft` is known, add to the issue aggregation:

```python
    thickness_issues = ()
    if declared_thickness_ft:
        from archiagent.geometry.candidacy import thickness_scale_check
        factor = thickness_scale_check(graph.walls, declared_thickness_ft, tolerance_ft)
        if factor is not None:
            thickness_issues = (Issue("warn", "scale", "declared_thickness_scale_mismatch",
                                      f"observed wall thicknesses fit the declared set only after "
                                      f"dividing by {factor:g}; units_per_foot="
                                      f"{scale.units_per_foot} is probably wrong by that factor"),)
```

and append `thickness_issues` to the `issues=` sum in the final `replace(model, ...)`.

- [ ] **Step 6: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all pass except the known pre-existing failure.

- [ ] **Step 7: Commit**

```bash
git add archiagent/geometry/candidacy.py archiagent/pipeline.py checks/test_declared_thickness.py
git commit -m "$(cat <<'EOF'
feat: warn when declared thicknesses only fit after a uniform rescale

If every observed thickness is off by one factor the scale is wrong, not the
drawing. Reported as a warning naming the factor; never rescaled automatically.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: CLI flags

**Files:**
- Modify: `archiagent/cli.py` (flag definitions near `:155`, validation in the argument-check block near `:349`, scale selection at `:804`, the `extract_from_dxf` call at `:690`)
- Test: `checks/test_declared_metrics_cli.py`

**Interfaces:**
- Consumes: `candidacy.MODE_TOLERANCE_FT`, `verify.Measurement`, `verify.parse_explicit_length`, `verify.scale_from_reviewed`, `pipeline.extract_from_dxf`'s Task 3 keyword arguments
- Produces: `cli._declared_thickness_ft(args) -> tuple[float, ...]`, `cli._wall_length_measurements(args) -> tuple[Measurement, ...]`

- [ ] **Step 1: Write the failing test**

Create `checks/test_declared_metrics_cli.py`:

```python
"""The CLI turns inches into feet once, and a wall length into a Measurement."""
import pytest

from archiagent.cli import _declared_thickness_ft, _parser, _wall_length_measurements


def parse(argv):
    return _parser().parse_args(argv)


BASE = ["--dxfFilePath", "plan.dxf", "--outputDir", "out"]


def test_thicknesses_are_converted_to_feet_once():
    args = parse(BASE + ["--wall-thickness", "4", "8"])
    assert _declared_thickness_ft(args) == (4 / 12, 8 / 12)


def test_no_declared_thicknesses_yields_an_empty_tuple():
    assert _declared_thickness_ft(parse(BASE)) == ()


def test_a_non_positive_thickness_is_refused():
    with pytest.raises(ValueError, match="positive"):
        _declared_thickness_ft(parse(BASE + ["--wall-thickness", "0"]))


def test_a_tolerance_that_merges_two_classes_is_refused():
    args = parse(BASE + ["--wall-thickness", "4", "5", "--wall-thickness-tolerance-in", "1.0"])
    with pytest.raises(ValueError, match="overlap"):
        _declared_thickness_ft(args)


def test_exhaustive_without_a_set_is_refused():
    with pytest.raises(ValueError, match="--wall-thickness"):
        _declared_thickness_ft(parse(BASE + ["--wall-thickness-exhaustive"]))


def test_a_wall_length_becomes_a_measurement_in_feet():
    args = parse(BASE + ["--scale-from-wall", "0", "0", "120", "0", "10ft"])
    measurement, = _wall_length_measurements(args)
    assert measurement.start == (0.0, 0.0)
    assert measurement.end == (120.0, 0.0)
    assert measurement.expected_ft == pytest.approx(10.0)
    assert measurement.source == "cli-wall-length"


def test_feet_inch_and_metric_lengths_are_accepted():
    for text, expected in (("10'-6\"", 10.5), ("3.048m", 10.0), ("3048mm", 10.0), ("120in", 10.0)):
        args = parse(BASE + ["--scale-from-wall", "0", "0", "120", "0", text])
        assert _wall_length_measurements(args)[0].expected_ft == pytest.approx(expected, rel=1e-3)


def test_an_unparseable_length_is_refused():
    with pytest.raises(ValueError, match="length"):
        _wall_length_measurements(parse(BASE + ["--scale-from-wall", "0", "0", "120", "0", "ten"]))


def test_coincident_points_are_refused():
    with pytest.raises(ValueError):
        _wall_length_measurements(parse(BASE + ["--scale-from-wall", "5", "5", "5", "5", "10ft"]))


def test_repeating_the_flag_gives_several_measurements():
    args = parse(BASE + ["--scale-from-wall", "0", "0", "120", "0", "10ft",
                         "--scale-from-wall", "0", "0", "0", "240", "20ft"])
    assert len(_wall_length_measurements(args)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_metrics_cli.py -v`
Expected: FAIL — `ImportError: cannot import name '_declared_thickness_ft'`

- [ ] **Step 3: Add the flags**

In `archiagent/cli.py`, beside the other mode flags (after the `--symbol-library` group added by the symbol-library work):

```python
    p.add_argument("--wall-thickness", nargs="+", type=float, metavar="IN", default=None,
                   help="declared wall thicknesses in INCHES, e.g. --wall-thickness 4 8. "
                        "Replaces the thickness set candidacy would infer from the drawing.")
    p.add_argument("--wall-thickness-exhaustive", action="store_true",
                   help="the --wall-thickness list is complete: reject any wall candidate "
                        "whose thickness matches none of it. Parapets, kerbs, cladding and "
                        "stud partitions often differ, so an incomplete list loses real walls.")
    p.add_argument("--wall-thickness-tolerance-in", type=float, default=0.5, metavar="IN",
                   help="how far a measured thickness may sit from a declared one (default 0.5in)")
    p.add_argument("--scale-from-wall", nargs=5, action="append", default=None,
                   metavar=("X1", "Y1", "X2", "Y2", "LENGTH"),
                   help="two SOURCE-coordinate points along one wall and its true length, "
                        "e.g. --scale-from-wall 0 0 120 0 \"10'-6\\\"\". Accepts 10, 10ft, "
                        "3.05m, 3050mm, 120in. Repeatable; two or more spans can also verify "
                        "the scale, one can only set it.")
```

- [ ] **Step 4: Add the two helpers**

```python
def _declared_thickness_ft(args) -> tuple[float, ...]:
    """Declared thicknesses in FEET. Inches are converted exactly once, here."""
    values = args.wall_thickness
    if not values:
        if args.wall_thickness_exhaustive:
            raise ValueError("--wall-thickness-exhaustive needs --wall-thickness; "
                             "a complete list of nothing would reject every wall")
        return ()
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("--wall-thickness values must be finite and positive, in inches")
    tolerance = args.wall_thickness_tolerance_in
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("--wall-thickness-tolerance-in must be finite and positive")
    ordered = sorted(set(values))
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    if gaps and tolerance >= min(gaps) / 2:
        raise ValueError(
            f"--wall-thickness-tolerance-in {tolerance} would overlap declared "
            f"thicknesses {min(gaps)}in apart; use less than {min(gaps) / 2}")
    return tuple(v / 12 for v in ordered)


def _wall_length_measurements(args) -> tuple[Measurement, ...]:
    """Turn each --scale-from-wall into a human-asserted Measurement."""
    out = []
    for n, (x1, y1, x2, y2, text) in enumerate(args.scale_from_wall or (), 1):
        try:
            start = (float(x1), float(y1))
            end = (float(x2), float(y2))
        except ValueError as exc:
            raise ValueError(f"--scale-from-wall coordinates must be numbers: {exc}") from exc
        expected = parse_explicit_length(text)
        if expected is None:
            raise ValueError(
                f"--scale-from-wall length {text!r} is not a length; use 10, 10ft, "
                "3.05m, 3050mm, 120in or 10'-6\"")
        out.append(Measurement(f"cli-wall-length-{n}", start, end, expected,
                               "face", "cli-wall-length", None))
    return tuple(out)
```

`Measurement.__post_init__` already refuses a non-positive span, so coincident
points raise without extra code. Add `Measurement`, `parse_explicit_length` and
`scale_from_reviewed` to the existing `archiagent.scale.verify` import line.

- [ ] **Step 5: Run the CLI tests**

Run: `source .venv/bin/activate && python -m pytest checks/test_declared_metrics_cli.py -v`
Expected: all PASS.

- [ ] **Step 6: Wire the values into the run**

At the scale-selection site (`selected_scale`, around `:804`), insert the
asserted-length fallback between `--units-per-foot` and the header. The existing
`units_per_foot` local already holds `--units-per-foot` or the header, so
compare against `args.units_per_foot` to tell them apart:

```python
            wall_lengths = _configuration(lambda: _wall_length_measurements(args),
                                          "--scale-from-wall")
            asserted_scale = scale_from_reviewed(wall_lengths + tuple(measurements)) \
                if (wall_lengths or measurements) else None
            if args.units_per_foot is not None and asserted_scale is not None:
                classifier_issues.append(Issue(
                    "info", "scale", "asserted_length_unused",
                    "--units-per-foot was given, so the supplied wall length set no scale"))
            selected_scale = region_scale if region_scale is not None else (
                args.units_per_foot if args.units_per_foot is not None
                else asserted_scale if asserted_scale is not None
                else (units_per_foot if is_dxf else args.units_per_foot))
            if asserted_scale is not None and len(wall_lengths) + len(measurements) == 1:
                print("note: one asserted length sets the scale but cannot verify it; "
                      "supply two or more spans for a verified scale", file=sys.stderr)
```

Pass the declared set into the DXF call:

```python
            if is_dxf:
                model = extract_from_dxf(source, selected_classifier, units_per_foot=selected_scale,
                                         adjudicator=adjudicator, symbol_library=symbol_library,
                                         declared_thickness_ft=declared_thickness_ft,
                                         thickness_tolerance_ft=args.wall_thickness_tolerance_in / 12,
                                         thickness_exhaustive=args.wall_thickness_exhaustive,
                                         **kwargs)
```

with `declared_thickness_ft = _configuration(lambda: _declared_thickness_ft(args), "--wall-thickness")`
computed once beside `symbol_library`, before the region loop.

Also add `wall_lengths` to the measurements handed to `_assemble` so they reach
`verify_dimensions`, by including them in the existing `measurements` value.

- [ ] **Step 7: Verify end to end on a real drawing**

Run:
```bash
source .venv/bin/activate
python -m archiagent --dxfFilePath "../input-floorplans/dxf/Jiju_dxf/SANJANA SURESH JI.dxf" \
  --outputDir /tmp/declared-run --units-per-foot 12 --rules --no_vision \
  --no-wall-adjudication --wall-thickness 4 8 9 --wall-thickness-exhaustive 2>&1 | tail -5
```
Expected: the run completes; compare `walls` and the per-candidate decisions in
`/tmp/declared-run/*.report.json` against the same command without the two
thickness flags. Record both wall counts in the commit message. A large drop
means the declared list is incomplete for this drawing, which is information,
not a bug.

Then confirm the one-span rule, using a header-units drawing and a single
asserted length instead of `--units-per-foot`:

```bash
python -m archiagent --dxfFilePath "../input-floorplans/dxf/Jiju_dxf/SANJANA SURESH JI.dxf" \
  --outputDir /tmp/asserted-run --rules --no_vision --no-wall-adjudication \
  --scale-from-wall 0 0 120 0 "10ft" 2>&1 | tail -5
python - <<'PY'
import json
from pathlib import Path
m = json.loads(Path("/tmp/asserted-run/SANJANA SURESH JI.report.json").read_text())["regions"][0]["model"]
print("units_per_foot:", m["scale"]["units_per_foot"], "verified:", m["scale_verified"])
PY
```
Expected: `units_per_foot: 12.0` and `verified: False` — one asserted span sets
the scale and deliberately cannot verify it. The stderr note about needing two
spans must appear.

- [ ] **Step 8: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all pass except the known pre-existing failure.

- [ ] **Step 9: Commit**

```bash
git add archiagent/cli.py checks/test_declared_metrics_cli.py
git commit -m "$(cat <<'EOF'
feat: accept declared wall thicknesses and a true wall length on the CLI

--wall-thickness takes inches and converts to feet once, at this boundary;
--wall-thickness-exhaustive opts into vetoing unmatched candidates;
--scale-from-wall takes two source-coordinate points and a length in any unit
parse_explicit_length already understands.

For DXF this is new capability, not a convenience: infer_associated_scale is
called only from the PDF path, so before this a DXF run could only take scale
from --units-per-foot or the file header.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Document the flags

**Files:**
- Modify: `README.md`, `archiagent/cli.py:1-26` (module docstring)

- [ ] **Step 1: Add this section to README.md**

```markdown
### Telling the tool what you already know

Two facts you can read off a drawing in seconds are worth more than anything
the pipeline can infer from the geometry.

**The wall thickness set.** Without it, candidacy infers the set by counting run
length per thickness over wall-role layers, which goes wrong when non-wall
parallel pairs — dimension witness lines, hatch bands — outweigh the real walls.

    --wall-thickness 4 8                 # inches
    --wall-thickness-tolerance-in 0.5    # default

Add `--wall-thickness-exhaustive` to reject any candidate matching none of the
list. **This removes real walls if the list is incomplete** — parapets, kerbs,
cladding and stud partitions routinely differ from the main set. Every veto is
recorded against its candidate with the score it had before the veto, so the
report shows exactly what was dropped.

**The true length of one wall.** For DXF this is the only alternative to
`--units-per-foot` and the file header, and headers are not always right.

    --scale-from-wall 0 0 120 0 "10'-6\""

The first four numbers are two points in the drawing's own source coordinates
along one wall; the last is its real length (`10`, `10ft`, `3.05m`, `3050mm`,
`120in` and `10'-6"` all parse). Repeat the flag for more walls.

One length **sets** the scale but cannot **verify** it: a verified scale needs
two or more independent spans that agree. Supply two or three to get both.

If you also pass `--wall-thickness`, the run cross-checks them: when every
observed thickness fits the declared set only after dividing by one common
factor, it warns that `units_per_foot` is probably wrong by that factor.
```

- [ ] **Step 2: Extend the cli.py module docstring**

Add this paragraph to the module docstring in `archiagent/cli.py`, after the
VISION paragraph:

```
DECLARED METRICS: --wall-thickness takes the drawing's real wall thicknesses in
inches and replaces the set candidacy would infer; with
--wall-thickness-exhaustive it also vetoes any candidate matching none of them.
--scale-from-wall takes two source-coordinate points along one wall plus its
true length, which for a DXF is the only way to set scale other than
--units-per-foot or trusting the file header.
```

- [ ] **Step 3: Commit**

```bash
git add README.md archiagent/cli.py
git commit -m "$(cat <<'EOF'
docs: describe declared wall thicknesses and asserted wall lengths

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Scope boundary with the scale-resolution spec

`docs/superpowers/specs/2026-10-06-scale-resolution-design.md` supersedes the
scale half of this plan's parent spec. The split is:

**This plan keeps:**
- Tasks 1, 2, 3, 5 — declared wall thicknesses, the exhaustive veto, the
  pipeline threading and the thickness-versus-scale cross-check.
- Task 4 — `scale_from_reviewed`, unchanged. It is the building block the scale
  ladder sits on, and its contract is the same.
- Task 6's `--scale-from-wall` flag and `_wall_length_measurements` helper,
  unchanged. They are the input the ladder consumes.

**The scale-resolution plan will own:**
- `extracted_scale(ps, tolerance_in)` and the `ExtractedScale` record — reading
  the drawing's own native `DIMENSION` entities and text consensus.
- The four-rung ladder, and making a DXF run **fail** rather than silently fall
  back to `$INSUNITS` when no scale is established.
- The mandatory-one / invited-two assertion rule, and the override-with-warning
  behaviour when an assertion contradicts the extracted dimensions.
- `--measure-workbench`, `--trust-extracted-scale`, `--scale-tolerance-in`, and
  the `--units-per-foot` help-text demotion.
- `write_measure_workbench(ps, output_path)` and the two-click Measure tool.

**Therefore in Task 6 Step 6 of this plan**, wire `--scale-from-wall` as a
*fallback after* `--units-per-foot` exactly as written, and leave the extracted
dimensions, the mandatory assertion and the failure path to the scale plan. The
two changes compose: this plan makes an asserted length able to set a DXF's
scale; the scale plan decides when one is required and what checks it.
