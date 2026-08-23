# W1 — LLM Layer Classification + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-authored layer role map with an LLM-backed
classifier, and expose the whole pipeline as `python -m archiagent plan.pdf out.ifc`.

**Architecture:** A one-method `LLMClient` port with two real-SDK adapters
(Anthropic by default, OpenAI-compatible for Groq/Kimi/DeepSeek) sits behind
`LLMLayerClassifier`, which implements the `LayerClassifier` protocol that
already exists. The classifier sends layer *statistics* and receives *roles*
constrained by a JSON schema built from the `Role` enum. Results are cached on
disk. The classifier's confidence and reason are carried all the way into
`BuildingModel` so W2 can display them.

**Tech Stack:** Python 3.12, `anthropic>=1.0`, `openai>=1.0` (both optional
extras), `argparse`, `pytest`. No new runtime dependency for anyone using
`--walls`.

**Spec:** `docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md`

## Global Constraints

- **Deterministic code owns geometry. The LLM owns vocabulary.** The
  classifier sees `LayerStats` only. It never sees a coordinate and never
  returns one.
- **Nothing is silently guessed.** Every failure names its cause and what to
  do about it. Every fallback becomes an `Issue`.
- **The tool must work with no LLM at all.** `--inspect` and `--walls` require
  no credentials and no network.
- Default model is **`claude-haiku-4-5`**. Default provider is **`anthropic`**.
- Use `output_config={"format": {"type": "json_schema", "schema": ...}}` on
  `messages.create()`. Never the deprecated top-level `output_format`.
- **Send no `effort` and no `thinking`** to the Anthropic API. `effort: "max"`
  errors on Haiku 4.5, and neither parameter helps a shallow classification.
- Structured-output schemas require `"additionalProperties": False` on every
  object and **do not support** `minimum`/`maximum`. Validate and clamp
  `confidence` in our own code.
- `max_tokens` is 2048. A reply that stops on `max_tokens` is truncated JSON —
  raise `LLMSchemaError`, never parse it.
- **Never commit a `.pdf`, `.dwg`, `.dxf`, or `.ifc`.** The sample drawings are
  client property. Tests reach them via the `ARCHIAGENT_FIXTURES` env var and
  **skip** when absent (see `tests/conftest.py`). Generated IFC goes to
  `tmp_path`.
- **No live API call in the test suite.** Every test uses a fake client or no
  client at all.
- Existing behaviour must not regress: `pytest` is at 129 passing tests before
  this plan starts, and every one of them must still pass at every commit.

---

## File Structure

**New — the LLM port and its adapters (`archiagent/llm/`)**

| File | Responsibility |
|---|---|
| `archiagent/llm/__init__.py` | Package marker. Empty. |
| `archiagent/llm/client.py` | The `LLMClient` protocol and the two exceptions. No SDK imports. |
| `archiagent/llm/anthropic_client.py` | `AnthropicClient` — the `anthropic` SDK. Imports it lazily. |
| `archiagent/llm/openai_client.py` | `OpenAICompatClient` — the `openai` SDK. Imports it lazily. |
| `archiagent/llm/config.py` | `LLMConfig`, `config_from_env()`, `build_client()`. |

**New — classification (`archiagent/classify/`)**

| File | Responsibility |
|---|---|
| `archiagent/classify/prompt.py` | The system prompt, the layer table renderer, and the response schema. Pure functions; no I/O. |
| `archiagent/classify/llm_classifier.py` | `LLMLayerClassifier` plus `decisions_from_reply()`, the pure validator. |
| `archiagent/classify/cache.py` | Disk cache keyed by inventory + model + prompt version. |

**New — the CLI**

| File | Responsibility |
|---|---|
| `archiagent/cli.py` | `main(argv) -> int`. Argument parsing, the three modes, exit codes. |
| `archiagent/__main__.py` | Three lines: `sys.exit(main())`. |

**Modified**

| File | Change |
|---|---|
| `archiagent/classify/layers.py` | Add `LayerDecision`; `Classification` becomes `tuple[LayerDecision, ...]`. |
| `archiagent/model.py` | `layer_roles: dict[str, str]` → `layer_decisions: tuple[LayerDecision, ...]`. |
| `archiagent/pipeline.py` | Pass decisions through instead of flattening them. |
| `archiagent/validate.py` | Warn on any layer the model failed to classify. |
| `pyproject.toml` | `llm` optional extra; `archiagent` console script; `packages` list. |
| `README.md` | CLI usage. |
| `PLAN.md` | §7 Stage 1 amended: the stub is replaced. |

**New tests** — flat under `tests/`, matching the existing convention:
`test_layer_decision.py`, `test_prompt.py`, `test_llm_classifier.py`,
`test_anthropic_client.py`, `test_openai_client.py`, `test_llm_config.py`,
`test_layer_cache.py`, `test_cli.py`, plus `tests/fakes.py` for the fake client.

**Why this shape.** `archiagent/llm/` knows nothing about floorplans, and
`archiagent/classify/` knows nothing about HTTP. That boundary is the point:
W3 and W5 will import `archiagent.llm` unchanged and write their own prompt
module beside `prompt.py`. `decisions_from_reply()` is split out from the
classifier so all the validation logic is testable with a plain dict and no
client at all.

---

### Task 1: `LayerDecision` — carry confidence and reason into the model

The pipeline currently throws away the classifier's confidence
(`pipeline.py:47`, the `_` in `for name, (role, _)`) and has never had a place
for a reason. W2 must display both. This task widens the contract and updates
every caller. No LLM involved.

**Files:**
- Modify: `archiagent/classify/layers.py`
- Modify: `archiagent/model.py:29`
- Modify: `archiagent/pipeline.py:47`
- Modify: `archiagent/validate.py`
- Modify: `PLAN.md`
- Test: `tests/test_layer_decision.py` (create)
- Test: `tests/test_layers.py` (update existing assertions)
- Test: `tests/test_validate.py:36`, `tests/test_ifc_author.py:36`, `tests/test_ifc_connections.py:21` (update fixtures)

**Interfaces:**
- Consumes: `Role` from `archiagent.classify.roles`; `LayerStats` from `archiagent.classify.inventory`.
- Produces:
  - `LayerDecision(layer: str, role: Role, confidence: float, reason: str = "", source: str = "llm")` — frozen dataclass in `archiagent/classify/layers.py`
  - `Classification = tuple[LayerDecision, ...]`
  - `layers_for_roles(classification: Classification, roles: set[Role] | frozenset[Role], min_confidence: float = 0.0) -> set[str]` — signature unchanged, body reads the tuple
  - `StubClassifier(mapping: dict[str, tuple[Role, float]])` — **constructor unchanged**, `classify()` now returns `Classification`
  - `BuildingModel.layer_decisions: tuple[LayerDecision, ...]` replaces `layer_roles`
  - `Issue(severity="warn", entity=<layer name>, code="layer_unclassified", ...)` from `validate()`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer_decision.py`:

```python
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import (WALL_ROLES, LayerDecision,
                                        StubClassifier, layers_for_roles)
from archiagent.classify.roles import Role


def _stats(*names):
    return tuple(
        LayerStats(name=n, path_count=1, segment_count=1,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 1.0, 1.0),
                   length_p10=1.0, length_p50=1.0, length_p90=1.0)
        for n in names
    )


def test_decision_defaults_reason_and_source():
    d = LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.9)
    assert d.reason == ""
    assert d.source == "llm"


def test_stub_returns_decisions_in_inventory_order():
    stub = StubClassifier({"b": (Role.WALL_STRUCTURAL, 0.9)})
    out = stub.classify(_stats("a", "b", "c"))
    assert [d.layer for d in out] == ["a", "b", "c"]
    assert out[1].role is Role.WALL_STRUCTURAL
    assert out[1].confidence == 0.9


def test_stub_marks_every_layer_manual_including_unmapped_ones():
    """A partial --walls map is a deliberate statement that the rest are
    not walls. Marking the remainder 'default' would make validate() emit a
    warning per untouched layer and drown the real output."""
    out = StubClassifier({"b": (Role.WALL_STRUCTURAL, 0.9)}).classify(_stats("a", "b"))
    assert {d.source for d in out} == {"manual"}
    assert out[0].role is Role.IGNORE
    assert out[0].confidence == 0.0


def test_layers_for_roles_reads_the_tuple():
    c = (
        LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.98, "", "llm"),
        LayerDecision("walll", Role.WALL_PARTITION, 0.90, "", "llm"),
        LayerDecision("BEAM", Role.BEAM_OVERHEAD, 0.95, "", "llm"),
    )
    assert layers_for_roles(c, WALL_ROLES) == {"WALLS", "walll"}
    assert layers_for_roles(c, WALL_ROLES, min_confidence=0.95) == {"WALLS"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_layer_decision.py -v`
Expected: FAIL with `ImportError: cannot import name 'LayerDecision'`.

- [ ] **Step 3: Add `LayerDecision` and rewrite `Classification`**

Replace lines 14-39 of `archiagent/classify/layers.py` with:

```python
@dataclass(frozen=True)
class LayerDecision:
    """One layer's assigned role, with everything W2 needs to review it.

    `source` is exact:
      "llm"     the model answered for this layer
      "manual"  a human supplied it -- including the layers a partial
                --walls map leaves unmentioned, since naming some layers as
                walls is a deliberate statement that the rest are not
      "default" the model was ASKED about this layer and did not answer, so
                IGNORE was applied. Only this value warrants a warning.
    """

    layer: str
    role: Role
    confidence: float
    reason: str = ""
    source: str = "llm"


Classification = tuple[LayerDecision, ...]

WALL_ROLES: frozenset[Role] = frozenset({Role.WALL_STRUCTURAL, Role.WALL_PARTITION})


class LayerClassifier(Protocol):
    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        """One LayerDecision per input layer, in the inventory's order."""
        ...


class StubClassifier:
    """Deterministic classifier for tests and for replaying a saved mapping."""

    def __init__(self, mapping: dict[str, tuple[Role, float]]) -> None:
        self._mapping = dict(mapping)

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        out: list[LayerDecision] = []
        for s in stats:
            role, conf = self._mapping.get(s.name, (Role.IGNORE, 0.0))
            out.append(LayerDecision(layer=s.name, role=role, confidence=conf,
                                     reason="", source="manual"))
        return tuple(out)


def layers_for_roles(classification: Classification,
                     roles: set[Role] | frozenset[Role],
                     min_confidence: float = 0.0) -> set[str]:
    """Layer names whose assigned role is in `roles` and clears the floor."""
    return {d.layer for d in classification
            if d.role in roles and d.confidence >= min_confidence}
```

Add `from dataclasses import dataclass` to the imports at the top of the file.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_layer_decision.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Update `tests/test_layers.py` to the new shape**

Replace the two `Classification`-dict tests (lines 20-36) with:

```python
def test_layers_for_roles_selects_matching_layers():
    c: Classification = (
        LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.98),
        LayerDecision("walll", Role.WALL_PARTITION, 0.9),
        LayerDecision("BEAM", Role.BEAM_OVERHEAD, 0.95),
        LayerDecision("0", Role.IGNORE, 0.5),
    )
    assert layers_for_roles(c, WALL_ROLES) == {"WALLS", "walll"}
    assert layers_for_roles(c, {Role.BEAM_OVERHEAD}) == {"BEAM"}


def test_layers_for_roles_honours_a_confidence_floor():
    c: Classification = (
        LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.98),
        LayerDecision("maybe", Role.WALL_STRUCTURAL, 0.30),
    )
    assert layers_for_roles(c, WALL_ROLES, min_confidence=0.5) == {"WALLS"}
```

Update the import at line 3-4 to:

```python
from archiagent.classify.layers import (WALL_ROLES, Classification,
                                        LayerDecision, layers_for_roles)
```

- [ ] **Step 6: Swap `layer_roles` for `layer_decisions` on the model**

In `archiagent/model.py`, replace line 29 (`layer_roles: dict[str, str]`) with:

```python
    layer_decisions: tuple[LayerDecision, ...]
```

and add to the imports:

```python
from archiagent.classify.layers import LayerDecision
```

In `archiagent/pipeline.py`, replace line 47 with:

```python
        layer_decisions=classification,
```

- [ ] **Step 7: Report layers the model failed to classify**

In `archiagent/validate.py`, insert this block immediately before the
`for pt in model.unresolved:` loop:

```python
    for d in model.layer_decisions:
        if d.source == "default":
            issues.append(Issue(
                "warn", d.layer, "layer_unclassified",
                "the classifier returned no role for this layer; it was "
                "defaulted to ignore and contributes no geometry"))
```

- [ ] **Step 8: Update the three `BuildingModel` test fixtures**

In `tests/test_validate.py` (line 36), `tests/test_ifc_author.py` (line 36),
and `tests/test_ifc_connections.py` (line 21), replace

```python
        layer_roles={"WALLS": "wall_structural"},
```

with

```python
        layer_decisions=(LayerDecision("WALLS", Role.WALL_STRUCTURAL, 0.95,
                                       "", "manual"),),
```

and add to each file's imports:

```python
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
```

- [ ] **Step 9: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 133 tests (129 existing + 4 new), 0 failures. The
fixture-gated tests skip if `ARCHIAGENT_FIXTURES` is unset — that is normal.

- [ ] **Step 10: Amend PLAN.md**

In `PLAN.md`, find the Building Model schema section that names
`layer_roles` and change the field to `layer_decisions`, with this note
beneath it:

```markdown
`layer_decisions` carries one `LayerDecision` per layer — role,
confidence, reason and source. The earlier `layer_roles: dict[str, str]`
discarded the classifier's confidence and had no field for a reason, which
left the W2 review surface with nothing to display. See
`docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md` §3.
```

- [ ] **Step 11: Commit**

```bash
git add archiagent/classify/layers.py archiagent/model.py archiagent/pipeline.py \
        archiagent/validate.py PLAN.md tests/test_layer_decision.py \
        tests/test_layers.py tests/test_validate.py tests/test_ifc_author.py \
        tests/test_ifc_connections.py
git commit -m "feat: LayerDecision carries confidence and reason into the model"
```

---

### Task 2: The prompt and the response schema

Pure functions that turn a `LayerStats` inventory into a system prompt, a user
prompt, and a JSON schema. No network, no client, no I/O. Isolating this makes
the prompt reviewable on its own and lets W3/W5 copy the pattern.

**Files:**
- Create: `archiagent/classify/prompt.py`
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `Role` from `archiagent.classify.roles`; `LayerStats` from `archiagent.classify.inventory`.
- Produces:
  - `PROMPT_VERSION: str` — bump on any prompt or schema change; the cache key includes it
  - `SYSTEM_PROMPT: str`
  - `build_user_prompt(stats: tuple[LayerStats, ...]) -> str`
  - `response_schema() -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prompt.py`:

```python
import json

from archiagent.classify.inventory import LayerStats
from archiagent.classify.prompt import (SYSTEM_PROMPT, build_user_prompt,
                                        response_schema)
from archiagent.classify.roles import Role


def _stat(name, **kw):
    base = dict(path_count=187, segment_count=187, axis_aligned_fraction=1.0,
                stroke_widths=(0.0,), dominant_colors=((0.0, 0.0, 0.0),),
                bbox=(0.0, 0.0, 1024.5, 780.25),
                length_p10=4.3, length_p50=23.2, length_p90=71.0)
    base.update(kw)
    return LayerStats(name=name, **base)


def test_schema_enumerates_exactly_the_role_vocabulary():
    """A role added to Role but missing from the schema is a silent gap:
    the model could never return it and no test would notice."""
    enum = response_schema()["properties"]["layers"]["items"]["properties"]["role"]["enum"]
    assert set(enum) == {r.value for r in Role}
    assert len(enum) == len(Role)


def test_schema_forbids_additional_properties_on_every_object():
    """Structured outputs REQUIRE additionalProperties: false on each object."""
    schema = response_schema()
    item = schema["properties"]["layers"]["items"]
    assert schema["additionalProperties"] is False
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"name", "role", "confidence", "reason"}


def test_schema_carries_no_numeric_constraints():
    """minimum/maximum are unsupported and get stripped; leaving them in
    would imply a guarantee the API does not make. Confidence is clamped in
    decisions_from_reply instead."""
    blob = json.dumps(response_schema())
    assert "minimum" not in blob
    assert "maximum" not in blob


def test_user_prompt_quotes_layer_names_so_spaces_are_unambiguous():
    prompt = build_user_prompt((_stat("WALL HATCH"), _stat("walll")))
    assert '"WALL HATCH"' in prompt
    assert '"walll"' in prompt


def test_user_prompt_reports_the_layer_count_and_one_row_each():
    stats = (_stat("a"), _stat("b"), _stat("c"))
    prompt = build_user_prompt(stats)
    assert "3 layers" in prompt
    rows = [ln for ln in prompt.splitlines() if ln.startswith('"')]
    assert len(rows) == 3


def test_user_prompt_renders_the_discriminating_numbers():
    prompt = build_user_prompt((_stat("walll"),))
    row = next(ln for ln in prompt.splitlines() if ln.startswith('"walll"'))
    assert "187" in row       # path count
    assert "100" in row       # axis-aligned percent
    assert "23.2" in row      # median segment length
    assert "1024.5" in row    # bbox width


def test_system_prompt_warns_that_hatch_is_not_a_wall():
    """Measured fact, not prompt-tuning: classifying the demolition plan's
    hatch layers as walls produced a 40% scale error that still passed the
    R1 gate (PLAN.md, Finding C)."""
    assert "hatch" in SYSTEM_PROMPT.lower()
    assert "scale" in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.classify.prompt'`.

- [ ] **Step 3: Write `archiagent/classify/prompt.py`**

```python
"""What the classifier says to the model, and what shape it accepts back.

Pure functions only -- no client, no network, no disk. Keeping the prompt
here means it can be reviewed and diffed on its own, and it is the template
W3 (glyph identification) and W5 (symbol naming) will copy.

The layer table is deliberately compact: the whole inventory has to fit in
one cheap call, and the statistics are already strongly discriminative.
Measured on the demolition plan, walls are 100% axis-aligned with a median
segment length of 23.2 units against ~1.1 for furniture and 0.0 for hatch.
Bounding-box width and height are included because they are the main signal
separating a title block or sheet border from plan geometry.
"""

from __future__ import annotations

import json

from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role

# Bump on ANY change to the prompt text or the schema. The classification
# cache key includes this, so a stale answer is never served for a new
# prompt.
PROMPT_VERSION = "1"

SYSTEM_PROMPT = """\
You classify CAD layers from a single architectural floorplan.

You are given one row per layer, summarising the vector geometry on that
layer. You never see the drawing itself. Assign every layer exactly one role
from the fixed vocabulary, with a confidence and a one-clause reason.

Columns (lengths are in the drawing's own units, NOT feet -- the scale is
unknown at this stage, so only RELATIVE magnitudes are meaningful):
  paths   separate vector paths on the layer
  segs    total line segments across those paths
  axis%   percent of segments that are exactly horizontal or vertical
  p10 p50 p90   10th/50th/90th percentile segment length
  w h     bounding-box width and height
  widths  distinct stroke widths, most common first (0 = hairline or filled)
  colors  dominant RGB colours in 0-1, most common first

What the numbers usually mean:
- Walls: near 100% axis-aligned, p50 long relative to other layers, bounding
  box covering most of the plan.
- Hatch (wall poche, fill): very many segments that are either near-zero
  length (degenerate points) or short diagonal strokes at one consistent
  angle. HATCH IS NOT A WALL. Wall detection pairs opposing wall FACES, and
  feeding it hatch strokes corrupts the drawing's recovered scale. Classify
  hatch as annotation.
- Dimensions: many short segments (ticks and arrowheads) mixed with long
  thin runs, concentrated around the edges of the plan.
- Furniture, fixtures, vehicles, landscape: low axis%, short p50, often a
  distinct colour.
- Title block and sheet border: few paths, bounding box larger than or
  offset from the plan geometry.
- Grid: very few paths, long axis-aligned runs crossing the whole sheet.

Rules:
- Use only roles from the enum. There is no "other" -- use `ignore` when
  nothing fits, including empty, construction and scratch layers.
- Return one entry for EVERY layer you are given, with the name copied
  verbatim, including its exact case and any spaces.
- `confidence` is your calibrated probability that the role is correct. Do
  not inflate it. 0.5 means genuinely unsure, and being unsure is useful --
  a human reviews low-confidence layers.
- `reason` is one short clause citing the numbers that decided it.
"""


def response_schema() -> dict:
    """The JSON schema the reply is constrained to.

    The role enum is generated from Role, so the vocabulary cannot drift
    out of sync with the code. No numeric constraints: structured outputs
    do not support minimum/maximum, and claiming them here would imply a
    guarantee the API does not make.
    """
    return {
        "type": "object",
        "properties": {
            "layers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string",
                                 "enum": [r.value for r in Role]},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["name", "role", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["layers"],
        "additionalProperties": False,
    }


def _row(s: LayerStats) -> str:
    x0, y0, x1, y1 = s.bbox
    widths = " ".join(f"{w:g}" for w in s.stroke_widths) or "-"
    colors = " ".join(f"({r:.2f},{g:.2f},{b:.2f})"
                      for r, g, b in s.dominant_colors) or "-"
    return " | ".join((
        json.dumps(s.name),
        str(s.path_count),
        str(s.segment_count),
        f"{s.axis_aligned_fraction * 100:.0f}",
        f"{s.length_p10:.1f}",
        f"{s.length_p50:.1f}",
        f"{s.length_p90:.1f}",
        f"{x1 - x0:.1f}",
        f"{y1 - y0:.1f}",
        widths,
        colors,
    ))


def build_user_prompt(stats: tuple[LayerStats, ...]) -> str:
    """Render the inventory as one row per layer.

    Layer names are JSON-quoted: real drawings carry names like
    "WALL HATCH" and "SPLIT AC", and an unquoted space-separated table
    would be ambiguous.
    """
    header = "name | paths | segs | axis% | p10 | p50 | p90 | w | h | widths | colors"
    rows = "\n".join(_row(s) for s in stats)
    return (f"This drawing has {len(stats)} layers.\n\n"
            f"{header}\n{rows}\n\n"
            "Classify every one of them.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prompt.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/prompt.py tests/test_prompt.py
git commit -m "feat: layer-classification prompt and Role-derived response schema"
```

---

### Task 3: The `LLMClient` port and `LLMLayerClassifier`

The port is one method — that is the entire surface W1, W3 and W5 need. The
classifier's validation logic lives in a separate pure function so it can be
tested with a plain dict.

**Files:**
- Create: `archiagent/llm/__init__.py`
- Create: `archiagent/llm/client.py`
- Create: `archiagent/classify/llm_classifier.py`
- Create: `tests/fakes.py`
- Test: `tests/test_llm_classifier.py`

**Interfaces:**
- Consumes: `LayerDecision`, `Classification` from `archiagent.classify.layers` (Task 1); `SYSTEM_PROMPT`, `build_user_prompt`, `response_schema` from `archiagent.classify.prompt` (Task 2).
- Produces:
  - `LLMUnavailable(RuntimeError)` and `LLMSchemaError(RuntimeError)` in `archiagent/llm/client.py`
  - `LLMClient` protocol with `classify_json(*, system: str, user: str, schema: dict, max_tokens: int = 2048) -> dict`
  - `decisions_from_reply(reply: dict, stats: tuple[LayerStats, ...]) -> Classification`
  - `LLMLayerClassifier(client: LLMClient, max_tokens: int = 2048)`
  - `FakeLLMClient(reply: dict | None = None, error: Exception | None = None)` in `tests/fakes.py`, recording `.calls: list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `tests/fakes.py`:

```python
"""Test doubles. No network, ever."""

from __future__ import annotations


class FakeLLMClient:
    """Returns a canned reply, or raises a canned error.

    Records every call so tests can assert on the request shape without a
    live provider.
    """

    def __init__(self, reply: dict | None = None,
                 error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error
        self.calls: list[dict] = []

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        self.calls.append({"system": system, "user": user,
                           "schema": schema, "max_tokens": max_tokens})
        if self._error is not None:
            raise self._error
        assert self._reply is not None, "FakeLLMClient needs a reply or an error"
        return self._reply
```

Create `tests/test_llm_classifier.py`:

```python
import pytest

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.llm_classifier import (LLMLayerClassifier,
                                                decisions_from_reply)
from archiagent.classify.roles import Role
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from tests.fakes import FakeLLMClient


def _stats(*names):
    return tuple(
        LayerStats(name=n, path_count=1, segment_count=1,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 1.0, 1.0),
                   length_p10=1.0, length_p50=1.0, length_p90=1.0)
        for n in names
    )


def _reply(*entries):
    return {"layers": [
        {"name": n, "role": r, "confidence": c, "reason": why}
        for n, r, c, why in entries
    ]}


def test_happy_path_maps_roles_and_keeps_the_reason():
    out = decisions_from_reply(
        _reply(("walll", "wall_structural", 0.95, "long axis-aligned runs")),
        _stats("walll"))
    assert out == (
        LayerDecision("walll", Role.WALL_STRUCTURAL, 0.95,
                      "long axis-aligned runs", "llm"),
    )


def test_output_follows_inventory_order_not_reply_order():
    out = decisions_from_reply(
        _reply(("c", "ignore", 0.5, ""), ("a", "furniture", 0.8, "")),
        _stats("a", "b", "c"))
    assert [d.layer for d in out] == ["a", "b", "c"]


def test_a_layer_the_model_omitted_defaults_to_ignore_and_is_marked():
    out = decisions_from_reply(_reply(("a", "furniture", 0.8, "")),
                               _stats("a", "b"))
    b = out[1]
    assert b.role is Role.IGNORE
    assert b.confidence == 0.0
    assert b.source == "default"
    assert b.reason != ""


def test_a_layer_the_model_invented_is_dropped():
    """A name that is not in the drawing cannot affect geometry. Dropping it
    is safer than failing a whole run over one hallucinated row."""
    out = decisions_from_reply(
        _reply(("a", "furniture", 0.8, ""), ("ghost", "furniture", 0.8, "")),
        _stats("a"))
    assert [d.layer for d in out] == ["a"]


def test_confidence_is_clamped_because_the_schema_cannot_constrain_it():
    out = decisions_from_reply(
        _reply(("a", "furniture", 1.7, ""), ("b", "furniture", -0.2, "")),
        _stats("a", "b"))
    assert out[0].confidence == 1.0
    assert out[1].confidence == 0.0


def test_a_role_outside_the_vocabulary_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="not_a_role"):
        decisions_from_reply(_reply(("a", "not_a_role", 0.9, "")), _stats("a"))


def test_a_reply_without_a_layers_list_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="layers"):
        decisions_from_reply({"result": []}, _stats("a"))


def test_a_non_numeric_confidence_is_a_schema_error():
    with pytest.raises(LLMSchemaError, match="confidence"):
        decisions_from_reply(_reply(("a", "furniture", "high", "")), _stats("a"))


def test_classifier_sends_the_schema_and_both_prompts():
    fake = FakeLLMClient(_reply(("a", "furniture", 0.8, "short segments")))
    LLMLayerClassifier(fake).classify(_stats("a"))
    call = fake.calls[0]
    assert call["max_tokens"] == 2048
    assert "classify" in call["system"].lower()
    assert '"a"' in call["user"]
    assert "wall_structural" in str(call["schema"])


def test_classifier_lets_unavailability_through_untouched():
    fake = FakeLLMClient(error=LLMUnavailable("no key"))
    with pytest.raises(LLMUnavailable, match="no key"):
        LLMLayerClassifier(fake).classify(_stats("a"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.llm'`.

- [ ] **Step 3: Write the port**

Create `archiagent/llm/__init__.py` as an empty file.

Create `archiagent/llm/client.py`:

```python
"""The application-level port for talking to a language model.

One method. That is the whole surface W1 (layer roles), W3 (glyph
identification) and W5 (symbol naming) need -- every one of them is
"classify this into a fixed vocabulary and hand me structured JSON back".

This module imports no vendor SDK. Each adapter uses its own vendor's real
SDK; we never route one provider through another's wire format.
"""

from __future__ import annotations

from typing import Protocol


class LLMUnavailable(RuntimeError):
    """The model could not be reached: missing credentials, transport
    failure, auth rejection, quota exhaustion, or a missing SDK package.

    The message must name what to set or install, and mention that --walls
    bypasses the LLM entirely.
    """


class LLMSchemaError(RuntimeError):
    """The provider answered, but not with something the schema allows.

    This is a bug worth surfacing -- in the prompt, the schema, or the
    provider -- not something to silently retry around.
    """


class LLMClient(Protocol):
    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        """Return a JSON object conforming to `schema`.

        Raises LLMUnavailable on transport, auth or quota failure.
        Raises LLMSchemaError if the provider returns something the schema
        rejects, including a reply truncated by max_tokens.
        """
        ...
```

- [ ] **Step 4: Write the classifier**

Create `archiagent/classify/llm_classifier.py`:

```python
"""The pipeline's LLM-backed layer classifier.

The LLM owns vocabulary and nothing else: it sees LayerStats, it returns
roles. Every coordinate in this system is computed by deterministic code
that never consults a model.

`decisions_from_reply` is split out from the classifier deliberately -- all
the validation lives there, so it can be tested with a plain dict and no
client at all.
"""

from __future__ import annotations

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import Classification, LayerDecision
from archiagent.classify.prompt import (SYSTEM_PROMPT, build_user_prompt,
                                        response_schema)
from archiagent.classify.roles import Role
from archiagent.llm.client import LLMClient, LLMSchemaError

MAX_TOKENS = 2048

UNANSWERED = "the classifier returned no entry for this layer"


def decisions_from_reply(reply: dict,
                         stats: tuple[LayerStats, ...]) -> Classification:
    """Turn a validated JSON reply into one decision per inventory layer.

    Output order follows `stats`, not the reply -- the model's ordering is
    not something to depend on, and W2 wants a stable sequence.
    """
    entries = reply.get("layers") if isinstance(reply, dict) else None
    if not isinstance(entries, list):
        raise LLMSchemaError(
            "reply has no 'layers' list; got keys "
            f"{sorted(reply) if isinstance(reply, dict) else type(reply).__name__}")

    answered: dict[str, tuple[Role, float, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise LLMSchemaError(f"layer entry is not an object: {entry!r}")
        name = entry.get("name")
        if not isinstance(name, str):
            raise LLMSchemaError(f"layer entry has no string name: {entry!r}")

        raw_role = entry.get("role")
        try:
            role = Role(raw_role)
        except ValueError as e:
            raise LLMSchemaError(
                f"layer {name!r} got role {raw_role!r}, which is not in the "
                "role vocabulary") from e

        raw_conf = entry.get("confidence")
        if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
            raise LLMSchemaError(
                f"layer {name!r} got confidence {raw_conf!r}, which is not a "
                "number")
        # The schema cannot express minimum/maximum, so the range is ours
        # to enforce.
        confidence = min(1.0, max(0.0, float(raw_conf)))

        reason = entry.get("reason")
        answered[name] = (role, confidence,
                          reason if isinstance(reason, str) else "")

    out: list[LayerDecision] = []
    for s in stats:
        if s.name in answered:
            role, confidence, reason = answered[s.name]
            out.append(LayerDecision(s.name, role, confidence, reason, "llm"))
        else:
            # Asked and not answered. Marked "default" so validate() reports
            # it -- a layer silently dropped to ignore is exactly the kind of
            # invisible failure this project exists to avoid.
            out.append(LayerDecision(s.name, Role.IGNORE, 0.0,
                                     UNANSWERED, "default"))
    return tuple(out)


class LLMLayerClassifier:
    """LayerClassifier backed by an LLMClient."""

    def __init__(self, client: LLMClient, max_tokens: int = MAX_TOKENS) -> None:
        self._client = client
        self._max_tokens = max_tokens

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        reply = self._client.classify_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(stats),
            schema=response_schema(),
            max_tokens=self._max_tokens,
        )
        return decisions_from_reply(reply, stats)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_llm_classifier.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 150 tests, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add archiagent/llm/__init__.py archiagent/llm/client.py \
        archiagent/classify/llm_classifier.py tests/fakes.py \
        tests/test_llm_classifier.py
git commit -m "feat: LLMClient port and LLMLayerClassifier"
```

---

### Task 4: The Anthropic adapter

The default provider, using the real `anthropic` SDK. Verified against
`anthropic` 1.0.0: `messages.create` accepts `output_config`.

**Files:**
- Create: `archiagent/llm/anthropic_client.py`
- Modify: `pyproject.toml`
- Test: `tests/test_anthropic_client.py`

**Interfaces:**
- Consumes: `LLMUnavailable`, `LLMSchemaError` from `archiagent.llm.client` (Task 3).
- Produces: `AnthropicClient(model: str = "claude-haiku-4-5", api_key: str | None = None, base_url: str | None = None)` with `classify_json(...)`, and `DEFAULT_MODEL = "claude-haiku-4-5"`.

- [ ] **Step 1: Add the optional dependency**

In `pyproject.toml`, replace the `[project.optional-dependencies]` block with:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
llm = ["anthropic>=1.0", "openai>=1.0"]
```

Replace the `[tool.setuptools]` block entirely. It currently reads
`packages = ["archiagent"]`, which names only the top-level package — the
existing subpackages (`archiagent.classify`, `archiagent.ingest`, …) are
reachable today only because the project is installed editable, and the new
`archiagent.llm` would be missing from any real build. Use discovery:

```toml
[tool.setuptools.packages.find]
include = ["archiagent*"]
```

and add a console script:

```toml
[project.scripts]
archiagent = "archiagent.cli:main"
```

Then install the extra:

```bash
.venv/bin/pip install -e ".[llm,dev]"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_anthropic_client.py`:

```python
"""No live calls. These assert the REQUEST SHAPE and the error mapping --
the two things that break silently against a real provider."""

import types

import pytest

from archiagent.llm.anthropic_client import DEFAULT_MODEL, AnthropicClient
from archiagent.llm.client import LLMSchemaError, LLMUnavailable

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason


def _client_with(monkeypatch, response=None, raises=None):
    """Build an AnthropicClient whose messages.create is ours."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return response

    c = AnthropicClient(model="claude-haiku-4-5", api_key="test-key")
    monkeypatch.setattr(c, "_client",
                        types.SimpleNamespace(
                            messages=types.SimpleNamespace(create=create)))
    return c, captured


def test_default_model_is_haiku_4_5():
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_missing_key_names_the_variable_and_the_bypass(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as e:
        AnthropicClient()
    assert "ANTHROPIC_API_KEY" in str(e.value)
    assert "--walls" in str(e.value)


def test_request_uses_output_config_not_the_deprecated_output_format(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=2048)

    assert captured["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "output_format" not in captured


def test_request_sends_no_effort_and_no_thinking(monkeypatch):
    """effort: max errors on Haiku 4.5, and neither parameter helps a
    shallow classification. Sending them is a 400 waiting to happen."""
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA)

    assert "thinking" not in captured
    assert "effort" not in captured.get("output_config", {})


def test_request_passes_model_system_and_max_tokens(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=99)

    assert captured["model"] == "claude-haiku-4-5"
    assert captured["system"] == "sys"
    assert captured["max_tokens"] == 99
    assert captured["messages"] == [{"role": "user", "content": "usr"}]


def test_parses_the_json_out_of_the_text_block(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"name": "a"}]}'))
    assert c.classify_json(system="s", user="u", schema=SCHEMA) == {
        "layers": [{"name": "a"}]}


def test_truncation_is_a_schema_error_not_a_parse_attempt(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"na',
                                           stop_reason="max_tokens"))
    with pytest.raises(LLMSchemaError, match="max_tokens"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_unparseable_text_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp("not json at all"))
    with pytest.raises(LLMSchemaError, match="valid JSON"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_a_reply_with_no_text_block_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp(None, stop_reason="refusal"))
    with pytest.raises(LLMSchemaError, match="refusal"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_sdk_error_becomes_unavailable_with_the_message_preserved(monkeypatch):
    import anthropic
    boom = anthropic.APIConnectionError(request=None)
    c, _ = _client_with(monkeypatch, raises=boom)
    with pytest.raises(LLMUnavailable, match="Anthropic"):
        c.classify_json(system="s", user="u", schema=SCHEMA)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_anthropic_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.llm.anthropic_client'`.

- [ ] **Step 4: Write `archiagent/llm/anthropic_client.py`**

```python
"""LLMClient over the official `anthropic` SDK. The default provider.

Verified against anthropic 1.0.0 on 2026-08-23: messages.create accepts
output_config. The SDK is imported lazily so that `--walls` and `--inspect`
keep working in an install without the `llm` extra.
"""

from __future__ import annotations

import json
import os

from archiagent.llm.client import LLMSchemaError, LLMUnavailable

DEFAULT_MODEL = "claude-haiku-4-5"

_NO_KEY = (
    "ANTHROPIC_API_KEY is not set. Export it, or pass --walls LAYER... to "
    "name the wall layers yourself and skip the LLM entirely."
)

_NO_SDK = (
    "the 'anthropic' package is not installed. Run "
    "`pip install 'archiagent[llm]'`, or pass --walls LAYER... to skip the "
    "LLM entirely."
)


class AnthropicClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise LLMUnavailable(_NO_SDK) from e

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable(_NO_KEY)

        self._sdk = anthropic
        self._model = model
        kwargs = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
        except self._sdk.APIError as e:
            raise LLMUnavailable(f"Anthropic API call failed: {e}") from e

        stop = getattr(resp, "stop_reason", None)
        if stop == "max_tokens":
            raise LLMSchemaError(
                f"the reply was truncated at max_tokens={max_tokens}, so the "
                "JSON is incomplete. Raise max_tokens.")

        text = next((b.text for b in resp.content
                     if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise LLMSchemaError(
                f"the reply contained no text block (stop_reason={stop!r})")

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMSchemaError(
                f"the reply was not valid JSON: {e}") from e
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_anthropic_client.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add archiagent/llm/anthropic_client.py tests/test_anthropic_client.py pyproject.toml
git commit -m "feat: Anthropic adapter using structured outputs"
```

---

### Task 5: The OpenAI-compatible adapter and provider configuration

Groq, Kimi (Moonshot) and DeepSeek all publish OpenAI-compatible endpoints, so
one adapter with a configurable `base_url` reaches all of them through their
own documented interface.

**Files:**
- Create: `archiagent/llm/openai_client.py`
- Create: `archiagent/llm/config.py`
- Test: `tests/test_openai_client.py`
- Test: `tests/test_llm_config.py`

**Interfaces:**
- Consumes: `LLMUnavailable`, `LLMSchemaError`, `LLMClient` from `archiagent.llm.client` (Task 3); `DEFAULT_MODEL` from `archiagent.llm.anthropic_client` (Task 4).
- Produces:
  - `OpenAICompatClient(model: str, api_key: str | None = None, base_url: str | None = None)` with `classify_json(...)`
  - `LLMConfig(provider: str, model: str, base_url: str | None)` — frozen dataclass
  - `config_from_env(env: Mapping[str, str] | None = None, provider: str | None = None, model: str | None = None) -> LLMConfig`
  - `build_client(cfg: LLMConfig) -> LLMClient`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_openai_client.py`:

```python
import types

import pytest

from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.openai_client import OpenAICompatClient

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


def _client_with(monkeypatch, response=None, raises=None):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return response

    c = OpenAICompatClient(model="llama-3.3-70b", api_key="test-key",
                           base_url="https://api.groq.com/openai/v1")
    monkeypatch.setattr(c, "_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create))))
    return c, captured


def test_missing_key_names_the_variable_and_the_bypass(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as e:
        OpenAICompatClient(model="gpt-4o")
    assert "OPENAI_API_KEY" in str(e.value)
    assert "--walls" in str(e.value)


def test_request_sends_the_schema_as_a_strict_json_schema(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=2048)

    fmt = captured["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == SCHEMA
    assert fmt["json_schema"]["strict"] is True


def test_request_sends_system_and_user_as_two_messages(monkeypatch):
    c, captured = _client_with(monkeypatch, _Resp('{"layers": []}'))
    c.classify_json(system="sys", user="usr", schema=SCHEMA, max_tokens=77)

    assert captured["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert captured["model"] == "llama-3.3-70b"
    assert captured["max_tokens"] == 77


def test_parses_the_message_content(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"layers": [{"name": "a"}]}'))
    assert c.classify_json(system="s", user="u", schema=SCHEMA) == {
        "layers": [{"name": "a"}]}


def test_truncation_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp('{"lay', finish_reason="length"))
    with pytest.raises(LLMSchemaError, match="truncated"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_unparseable_content_is_a_schema_error(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp("not json"))
    with pytest.raises(LLMSchemaError, match="valid JSON"):
        c.classify_json(system="s", user="u", schema=SCHEMA)


def test_an_sdk_error_becomes_unavailable(monkeypatch):
    import openai
    boom = openai.APIConnectionError(request=None)
    c, _ = _client_with(monkeypatch, raises=boom)
    with pytest.raises(LLMUnavailable, match="OpenAI-compatible"):
        c.classify_json(system="s", user="u", schema=SCHEMA)
```

Create `tests/test_llm_config.py`:

```python
import pytest

from archiagent.llm.client import LLMUnavailable
from archiagent.llm.config import LLMConfig, build_client, config_from_env


def test_defaults_are_anthropic_and_haiku():
    cfg = config_from_env(env={})
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.base_url is None


def test_environment_overrides_the_defaults():
    cfg = config_from_env(env={
        "ARCHIAGENT_LLM_PROVIDER": "openai",
        "ARCHIAGENT_LLM_MODEL": "moonshotai/kimi-k2",
        "ARCHIAGENT_LLM_BASE_URL": "https://api.groq.com/openai/v1",
    })
    assert cfg == LLMConfig("openai", "moonshotai/kimi-k2",
                            "https://api.groq.com/openai/v1")


def test_explicit_arguments_override_the_environment():
    cfg = config_from_env(
        env={"ARCHIAGENT_LLM_PROVIDER": "anthropic",
             "ARCHIAGENT_LLM_MODEL": "claude-haiku-4-5"},
        provider="openai", model="gpt-4o")
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"


def test_switching_provider_without_a_model_is_refused(monkeypatch):
    """claude-haiku-4-5 on an OpenAI endpoint is a 404 with a confusing
    message. Catch it here, where we can say what to set."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(LLMUnavailable, match="ARCHIAGENT_LLM_MODEL"):
        build_client(LLMConfig("openai", "claude-haiku-4-5", None))


def test_an_unknown_provider_lists_the_ones_that_exist():
    with pytest.raises(LLMUnavailable) as e:
        build_client(LLMConfig("bedrock", "some-model", None))
    assert "anthropic" in str(e.value)
    assert "openai" in str(e.value)


def test_build_client_returns_an_anthropic_adapter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = build_client(config_from_env(env={}))
    assert type(client).__name__ == "AnthropicClient"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_openai_client.py tests/test_llm_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.llm.openai_client'`.

- [ ] **Step 3: Write `archiagent/llm/openai_client.py`**

```python
"""LLMClient over the official `openai` SDK.

One adapter serves OpenAI, Groq, Kimi (Moonshot) and DeepSeek: all four
publish an OpenAI-compatible endpoint, so a configurable base_url reaches
each of them through its own documented interface. We are not shimming one
vendor's wire format onto another's.

Schema enforcement is best-effort here. Not every OpenAI-compatible
endpoint honours `strict`, which is exactly why decisions_from_reply
validates the reply again on our side regardless of provider.
"""

from __future__ import annotations

import json
import os

from archiagent.llm.client import LLMSchemaError, LLMUnavailable

_NO_KEY = (
    "OPENAI_API_KEY is not set. Export it (it is also the key for Groq, "
    "Kimi and DeepSeek when used through ARCHIAGENT_LLM_BASE_URL), or pass "
    "--walls LAYER... to name the wall layers yourself and skip the LLM."
)

_NO_SDK = (
    "the 'openai' package is not installed. Run "
    "`pip install 'archiagent[llm]'`, or pass --walls LAYER... to skip the "
    "LLM entirely."
)


class OpenAICompatClient:
    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        try:
            import openai
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise LLMUnavailable(_NO_SDK) from e

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMUnavailable(_NO_KEY)

        self._sdk = openai
        self._model = model
        kwargs = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "layer_classification",
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
        except self._sdk.APIError as e:
            raise LLMUnavailable(
                f"OpenAI-compatible API call failed: {e}") from e

        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMSchemaError(
                f"the reply was truncated at max_tokens={max_tokens}, so the "
                "JSON is incomplete. Raise max_tokens.")

        content = choice.message.content
        if content is None:
            raise LLMSchemaError(
                f"the reply had no content (finish_reason="
                f"{choice.finish_reason!r})")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMSchemaError(f"the reply was not valid JSON: {e}") from e
```

- [ ] **Step 4: Write `archiagent/llm/config.py`**

```python
"""Where the provider, model and endpoint come from.

Environment variables, with the provider-native key names honoured so an
existing ANTHROPIC_API_KEY or OPENAI_API_KEY just works. CLI flags override
the environment. No config file: one is easy to add later and premature now.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from archiagent.llm.anthropic_client import DEFAULT_MODEL
from archiagent.llm.client import LLMClient, LLMUnavailable

ENV_PROVIDER = "ARCHIAGENT_LLM_PROVIDER"
ENV_MODEL = "ARCHIAGENT_LLM_MODEL"
ENV_BASE_URL = "ARCHIAGENT_LLM_BASE_URL"

PROVIDERS = ("anthropic", "openai")


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str | None = None


def config_from_env(env: Mapping[str, str] | None = None,
                    provider: str | None = None,
                    model: str | None = None) -> LLMConfig:
    """Resolve configuration. Explicit arguments beat the environment."""
    e = os.environ if env is None else env
    return LLMConfig(
        provider=provider or e.get(ENV_PROVIDER) or "anthropic",
        model=model or e.get(ENV_MODEL) or DEFAULT_MODEL,
        base_url=e.get(ENV_BASE_URL) or None,
    )


def build_client(cfg: LLMConfig) -> LLMClient:
    if cfg.provider == "anthropic":
        from archiagent.llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=cfg.model, base_url=cfg.base_url)

    if cfg.provider == "openai":
        if cfg.model == DEFAULT_MODEL:
            # An Anthropic model id on an OpenAI endpoint is a 404 with a
            # message that blames the model, not the config. Say the useful
            # thing here instead.
            raise LLMUnavailable(
                f"provider is 'openai' but the model is still the Anthropic "
                f"default {DEFAULT_MODEL!r}. Set {ENV_MODEL} (or --model) to "
                "a model your endpoint serves.")
        from archiagent.llm.openai_client import OpenAICompatClient
        return OpenAICompatClient(model=cfg.model, base_url=cfg.base_url)

    raise LLMUnavailable(
        f"unknown provider {cfg.provider!r}. Supported: "
        f"{', '.join(PROVIDERS)}. Groq, Kimi and DeepSeek are reached with "
        f"provider 'openai' plus {ENV_BASE_URL}.")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_openai_client.py tests/test_llm_config.py -v`
Expected: PASS (13 tests).

- [ ] **Step 6: Commit**

```bash
git add archiagent/llm/openai_client.py archiagent/llm/config.py \
        tests/test_openai_client.py tests/test_llm_config.py
git commit -m "feat: OpenAI-compatible adapter and provider configuration"
```

---

### Task 6: Disk cache for classifications

Re-running the same drawing must cost nothing and produce an identical model.
The key includes the model id and `PROMPT_VERSION`, not just the inventory —
otherwise improving the prompt silently serves the old answer.

**Files:**
- Create: `archiagent/classify/cache.py`
- Test: `tests/test_layer_cache.py`

**Interfaces:**
- Consumes: `LayerStats` (inventory); `LayerDecision`, `Classification` from `archiagent.classify.layers` (Task 1); `PROMPT_VERSION` from `archiagent.classify.prompt` (Task 2); `Role`.
- Produces:
  - `cache_dir() -> Path`
  - `inventory_key(stats: tuple[LayerStats, ...], model: str) -> str`
  - `read_cache(key: str) -> Classification | None`
  - `write_cache(key: str, decisions: Classification) -> None`
  - `CachingClassifier(inner: LayerClassifier, model: str, enabled: bool = True)` implementing `LayerClassifier`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer_cache.py`:

```python
import pytest

from archiagent.classify.cache import (CachingClassifier, cache_dir,
                                       inventory_key, read_cache, write_cache)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision, StubClassifier
from archiagent.classify.roles import Role


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


def _stats(*names, p50=23.2):
    return tuple(
        LayerStats(name=n, path_count=187, segment_count=187,
                   axis_aligned_fraction=1.0, stroke_widths=(0.0,),
                   dominant_colors=((0.0, 0.0, 0.0),),
                   bbox=(0.0, 0.0, 10.0, 10.0),
                   length_p10=4.3, length_p50=p50, length_p90=71.0)
        for n in names
    )


class _CountingClassifier:
    def __init__(self, decisions):
        self._decisions = decisions
        self.calls = 0

    def classify(self, stats):
        self.calls += 1
        return self._decisions


def test_cache_dir_honours_the_environment(isolated_cache):
    assert cache_dir() == isolated_cache


def test_key_is_stable_for_the_same_inventory_and_model():
    a = inventory_key(_stats("walll", "FURNITURE"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll", "FURNITURE"), "claude-haiku-4-5")
    assert a == b


def test_key_changes_when_the_geometry_changes():
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll", p50=99.9), "claude-haiku-4-5")
    assert a != b


def test_key_changes_when_the_model_changes():
    """A different model is a different answer. Serving one model's cached
    classification for another is silently wrong."""
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    b = inventory_key(_stats("walll"), "gpt-4o")
    assert a != b


def test_key_changes_when_the_prompt_version_changes(monkeypatch):
    a = inventory_key(_stats("walll"), "claude-haiku-4-5")
    monkeypatch.setattr("archiagent.classify.cache.PROMPT_VERSION", "99")
    b = inventory_key(_stats("walll"), "claude-haiku-4-5")
    assert a != b


def test_round_trips_every_field():
    decisions = (
        LayerDecision("walll", Role.WALL_STRUCTURAL, 0.95, "long runs", "llm"),
        LayerDecision("HATCH1", Role.ANNOTATION, 0.7, "zero-length", "llm"),
    )
    write_cache("k1", decisions)
    assert read_cache("k1") == decisions


def test_a_miss_returns_none():
    assert read_cache("never-written") is None


def test_a_corrupt_entry_is_a_miss_not_a_crash(isolated_cache):
    """A half-written cache file must not take down the pipeline."""
    isolated_cache.mkdir(parents=True, exist_ok=True)
    (isolated_cache / "broken.json").write_text("{not json")
    assert read_cache("broken") is None


def test_caching_classifier_calls_through_once_then_serves_the_cache():
    decisions = (LayerDecision("a", Role.FURNITURE, 0.8, "short", "llm"),)
    inner = _CountingClassifier(decisions)
    stats = _stats("a")

    first = CachingClassifier(inner, model="m").classify(stats)
    second = CachingClassifier(inner, model="m").classify(stats)

    assert inner.calls == 1
    assert first == second == decisions


def test_caching_classifier_disabled_always_calls_through():
    decisions = (LayerDecision("a", Role.FURNITURE, 0.8, "short", "llm"),)
    inner = _CountingClassifier(decisions)
    stats = _stats("a")

    CachingClassifier(inner, model="m", enabled=False).classify(stats)
    CachingClassifier(inner, model="m", enabled=False).classify(stats)

    assert inner.calls == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_layer_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.classify.cache'`.

- [ ] **Step 3: Write `archiagent/classify/cache.py`**

```python
"""On-disk cache for layer classifications.

Re-running the same drawing should cost nothing and produce a byte-identical
model -- which is also what keeps a golden test on real output deterministic.

The key covers the inventory AND the model id AND the prompt version.
Keying on the inventory alone would serve a stale answer after any prompt
change, which is the kind of silent staleness this project keeps
eliminating elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import (Classification, LayerClassifier,
                                        LayerDecision)
from archiagent.classify.prompt import PROMPT_VERSION
from archiagent.classify.roles import Role

ENV_CACHE_DIR = "ARCHIAGENT_CACHE_DIR"


def cache_dir() -> Path:
    override = os.environ.get(ENV_CACHE_DIR)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "archiagent" / "layers"


def inventory_key(stats: tuple[LayerStats, ...], model: str) -> str:
    """A stable digest of everything that could change the answer."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "layers": [
            {
                "name": s.name,
                "paths": s.path_count,
                "segs": s.segment_count,
                "axis": round(s.axis_aligned_fraction, 4),
                "p10": round(s.length_p10, 3),
                "p50": round(s.length_p50, 3),
                "p90": round(s.length_p90, 3),
                "bbox": [round(v, 2) for v in s.bbox],
                "widths": [round(w, 2) for w in s.stroke_widths],
                "colors": [[round(c, 3) for c in rgb]
                           for rgb in s.dominant_colors],
            }
            for s in stats
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_cache(key: str) -> Classification | None:
    path = cache_dir() / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            LayerDecision(layer=d["layer"], role=Role(d["role"]),
                          confidence=float(d["confidence"]),
                          reason=d["reason"], source=d["source"])
            for d in raw
        )
    except (OSError, ValueError, KeyError, TypeError):
        # A missing, truncated or hand-edited entry is a miss, not a crash.
        return None


def write_cache(key: str, decisions: Classification) -> None:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = [
        {"layer": d.layer, "role": d.role.value, "confidence": d.confidence,
         "reason": d.reason, "source": d.source}
        for d in decisions
    ]
    (directory / f"{key}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")


class CachingClassifier:
    """Wraps any LayerClassifier with the disk cache."""

    def __init__(self, inner: LayerClassifier, model: str,
                 enabled: bool = True) -> None:
        self._inner = inner
        self._model = model
        self._enabled = enabled

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        if not self._enabled:
            return self._inner.classify(stats)

        key = inventory_key(stats, self._model)
        hit = read_cache(key)
        if hit is not None:
            return hit

        decisions = self._inner.classify(stats)
        write_cache(key, decisions)
        return decisions
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_layer_cache.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/cache.py tests/test_layer_cache.py
git commit -m "feat: disk cache keyed by inventory, model and prompt version"
```

---

### Task 7: The CLI

Turns the library into a tool. `--inspect` and `--walls` must work with no
credentials and no network — that is what keeps the tool usable when the LLM
is not.

**Files:**
- Create: `archiagent/cli.py`
- Create: `archiagent/__main__.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6, plus `load_pdf` from `archiagent.ingest.pdf_vector`, `build_inventory`, `extract`/`run_pipeline` from `archiagent.pipeline`, `author_ifc`, `ScaleGateError`.
- Produces: `main(argv: list[str] | None = None) -> int`; exit codes `EXIT_OK = 0`, `EXIT_PIPELINE = 1`, `EXIT_LLM = 2`, `EXIT_USAGE = 3`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
"""The CLI's contract: modes, exit codes, and working with no credentials.

The fixture-gated tests use the real drawings and no LLM at all.
"""

import pytest

from archiagent.cli import EXIT_LLM, EXIT_OK, EXIT_PIPELINE, EXIT_USAGE, main


def test_no_arguments_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_authoring_without_an_output_path_is_a_usage_error(capsys):
    assert main(["plan.pdf"]) == EXIT_USAGE
    assert "OUT_IFC" in capsys.readouterr().err


def test_a_missing_pdf_is_a_pipeline_error_naming_the_path(capsys):
    assert main(["nope.pdf", "out.ifc", "--walls", "WALLS"]) == EXIT_PIPELINE
    assert "nope.pdf" in capsys.readouterr().err


def test_an_unknown_provider_exits_with_the_llm_code(capsys, tmp_path):
    code = main(["nope.pdf", str(tmp_path / "o.ifc"), "--provider", "bedrock"])
    assert code == EXIT_LLM
    err = capsys.readouterr().err
    assert "anthropic" in err and "openai" in err


def test_inspect_needs_no_credentials(demolition_pdf, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert main([str(demolition_pdf), "--inspect"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "walll" in out
    assert "FURNITURE" in out


def test_inspect_does_not_write_an_ifc(demolition_pdf, tmp_path):
    out_ifc = tmp_path / "should-not-exist.ifc"
    assert main([str(demolition_pdf), str(out_ifc), "--inspect"]) == EXIT_OK
    assert not out_ifc.exists()


def test_walls_runs_the_whole_pipeline_with_no_llm(demolition_pdf, tmp_path,
                                                   monkeypatch):
    """Measured: this drawing yields 51 walls, 1 space, and 42 'error'
    issues -- all unresolved_junction, i.e. dangling wall ends, which are
    normal on a real drawing and do not stop the IFC being written. Exit
    status therefore tracks the R1 scale gate, not the issue list."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out_ifc = tmp_path / "demolition.ifc"

    code = main([str(demolition_pdf), str(out_ifc), "--walls", "walll"])

    assert code == EXIT_OK
    assert out_ifc.exists()
    import ifcopenshell
    f = ifcopenshell.open(out_ifc)
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcWall")) >= 30


def test_naming_a_layer_that_is_not_in_the_drawing_is_a_pipeline_error(
        demolition_pdf, tmp_path, capsys):
    code = main([str(demolition_pdf), str(tmp_path / "o.ifc"),
                 "--walls", "NO-SUCH-LAYER"])
    assert code == EXIT_PIPELINE
    assert "no layers were classified as walls" in capsys.readouterr().err


def test_classify_only_prints_confidence_and_source(demolition_pdf, capsys):
    assert main([str(demolition_pdf), "--classify-only",
                 "--walls", "walll"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "wall_structural" in out
    assert "manual" in out
    assert "1.00" in out


def test_verbose_reports_issues_by_severity(demolition_pdf, tmp_path, capsys):
    main([str(demolition_pdf), str(tmp_path / "o.ifc"),
          "--walls", "walll", "-v"])
    err = capsys.readouterr().err
    assert "warn" in err


def test_a_drawing_with_no_readable_text_fails_at_the_scale_gate(
        ground_floor_pdf, tmp_path, capsys):
    """W1 does not pretend to fix what W3 addresses. The failure must name
    the scale gate, not the classifier.

    The layer is 'WALLS' -- verified against the drawing. ScaleGateError's
    own message is 'no dimensions or no candidate runs to match', which
    never says 'scale', so the CLI has to supply that framing itself.
    """
    code = main([str(ground_floor_pdf), str(tmp_path / "o.ifc"),
                 "--walls", "WALLS"])
    assert code == EXIT_PIPELINE
    err = capsys.readouterr().err.lower()
    assert "scale gate" in err
    assert "classif" not in err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.cli'`.

- [ ] **Step 3: Write `archiagent/cli.py`**

```python
"""`python -m archiagent plan.pdf out.ifc` -- the tool surface.

Three modes:
  --inspect        print the layer inventory and stop. No LLM, no output file.
  --classify-only  classify and print the roles. No output file.
  (default)        classify, build, author the IFC.

--inspect and --walls exist so the tool stays usable with no credentials and
no network. --inspect is also how you find out what a drawing contains
before spending a call on it.
"""

from __future__ import annotations

import argparse
import collections
import sys

from archiagent.classify.cache import CachingClassifier
from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import LayerClassifier, StubClassifier
from archiagent.classify.llm_classifier import LLMLayerClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import NoLayersError, load_pdf
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.config import build_client, config_from_env
from archiagent.pipeline import extract
from archiagent.scale.resolve import ScaleGateError

EXIT_OK = 0
EXIT_PIPELINE = 1
EXIT_LLM = 2
EXIT_USAGE = 3

MANUAL_WALL_CONFIDENCE = 1.0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="archiagent",
        description="Turn a layered 2D floorplan PDF into a to-scale IFC4 model.")
    p.add_argument("pdf", metavar="PDF", help="input floorplan PDF")
    p.add_argument("out_ifc", metavar="OUT_IFC", nargs="?",
                   help="output .ifc path (not needed with --inspect or "
                        "--classify-only)")
    p.add_argument("--page", type=int, default=0, help="page index (default 0)")
    p.add_argument("--height", type=float, default=10.0,
                   metavar="FT", help="wall height in feet (default 10.0)")
    p.add_argument("--walls", nargs="+", metavar="NAME", default=None,
                   help="skip the LLM; treat these layers as walls")
    p.add_argument("--provider", default=None,
                   help="override ARCHIAGENT_LLM_PROVIDER")
    p.add_argument("--model", default=None,
                   help="override ARCHIAGENT_LLM_MODEL")
    p.add_argument("--inspect", action="store_true",
                   help="print the layer inventory and exit")
    p.add_argument("--classify-only", action="store_true",
                   help="classify, print the roles, and exit")
    p.add_argument("--no-cache", action="store_true",
                   help="ignore the classification cache")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="report issues by severity")
    return p


def _print_inventory(stats) -> None:
    print(f"{'layer':<24} {'paths':>7} {'segs':>7} {'axis%':>6} "
          f"{'p50':>8} {'p90':>8}")
    for s in stats:
        print(f"{s.name:<24} {s.path_count:>7} {s.segment_count:>7} "
              f"{s.axis_aligned_fraction * 100:>5.0f}% "
              f"{s.length_p50:>8.1f} {s.length_p90:>8.1f}")


def _print_decisions(decisions) -> None:
    print(f"{'layer':<24} {'role':<18} {'conf':>5}  {'source':<8} reason")
    for d in decisions:
        print(f"{d.layer:<24} {d.role.value:<18} {d.confidence:>5.2f}  "
              f"{d.source:<8} {d.reason}")


def _print_issues(issues) -> None:
    for severity in ("error", "warn", "info"):
        matching = [i for i in issues if i.severity == severity]
        if not matching:
            continue
        print(f"\n{severity} ({len(matching)}):", file=sys.stderr)
        for i in matching:
            print(f"  [{i.code}] {i.entity}: {i.msg}", file=sys.stderr)


def _classifier(args) -> LayerClassifier:
    """Manual map, or the LLM behind the cache.

    Raises LLMUnavailable, which main() maps to EXIT_LLM.
    """
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model)
    inner = LLMLayerClassifier(build_client(cfg))
    return CachingClassifier(inner, model=cfg.model, enabled=not args.no_cache)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    authoring = not (args.inspect or args.classify_only)
    if authoring and not args.out_ifc:
        print("error: OUT_IFC is required unless --inspect or "
              "--classify-only is given", file=sys.stderr)
        return EXIT_USAGE

    # The classifier is built BEFORE the PDF is read, so a bad provider or a
    # missing key fails fast with EXIT_LLM instead of after a slow parse.
    classifier: LayerClassifier | None = None
    if not args.inspect:
        try:
            classifier = _classifier(args)
        except LLMUnavailable as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_LLM

    try:
        if args.inspect:
            ps = load_pdf(args.pdf, page=args.page)
            _print_inventory(build_inventory(ps))
            return EXIT_OK

        if args.classify_only:
            ps = load_pdf(args.pdf, page=args.page)
            _print_decisions(classifier.classify(build_inventory(ps)))
            return EXIT_OK

        model = extract(args.pdf, classifier, page=args.page,
                        wall_height_ft=args.height)
    except (LLMUnavailable, LLMSchemaError) as e:
        # Both subclass RuntimeError, so this must precede the RuntimeError
        # catch below.
        print(f"error: {e}", file=sys.stderr)
        return EXIT_LLM
    except ScaleGateError as e:
        # ScaleGateError's own message ("no dimensions or no candidate runs
        # to match") never says "scale", and the user needs to know WHICH
        # gate stopped them -- not to suspect the classifier.
        print(f"error: scale gate (R1) failed: {e}", file=sys.stderr)
        print("hint: this drawing may have no live text -- dimensions "
              "converted to vector outlines cannot be read yet.",
              file=sys.stderr)
        return EXIT_PIPELINE
    except NoLayersError as e:
        print(f"error: {args.pdf} has no CAD layers (PDF optional content "
              f"groups): {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except (OSError, RuntimeError) as e:
        print(f"error: could not read {args.pdf}: {e}", file=sys.stderr)
        return EXIT_PIPELINE

    out = author_ifc(model, args.out_ifc)
    counts = collections.Counter(i.severity for i in model.issues)
    print(f"wrote {out}")
    print(f"  scale   {model.scale.units_per_foot:.4f} units/ft, "
          f"max residual {model.scale.max_residual_in:.3f}in")
    print(f"  walls   {len(model.walls)}")
    print(f"  spaces  {len(model.spaces)}")
    print(f"  issues  {counts['error']} error, {counts['warn']} warn"
          f"{'' if args.verbose else '  (-v to list)'}")

    if args.verbose:
        _print_issues(model.issues)

    # Exit status tracks the R1 gate ALONE. A real drawing routinely carries
    # dozens of unresolved_junction errors -- the demolition plan has 42 --
    # and the IFC is written and usable regardless. Failing the exit code on
    # those would make a successful run indistinguishable from a broken one.
    gate = [i for i in model.issues if i.code == "scale_gate_failed"]
    for i in gate:
        print(f"error: scale gate (R1) failed: {i.msg}", file=sys.stderr)
    return EXIT_PIPELINE if gate else EXIT_OK
```

- [ ] **Step 4: Write `archiagent/__main__.py`**

```python
import sys

from archiagent.cli import main

sys.exit(main())
```

- [ ] **Step 5: Run the CLI tests**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (11 tests; 4 passed + 7 skipped when `ARCHIAGENT_FIXTURES` is
unset, since seven of them use the real drawings).

- [ ] **Step 6: Run the CLI by hand against a real drawing**

```bash
ARCHIAGENT_FIXTURES=../input-floorplans \
  .venv/bin/python -m archiagent \
  "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" --inspect
```

Expected: a table of layers including `walll`, `FURNITURE`, `HATCH1`. No
credentials needed. If this prints a traceback instead of a table, fix it
before committing.

- [ ] **Step 7: Document the CLI in README.md**

Append to `README.md`:

````markdown
## Usage

```
python -m archiagent PDF OUT_IFC [options]

  --page N            page index (default 0)
  --height FT         wall height in feet (default 10.0)
  --walls NAME...     skip the LLM; treat these layers as walls
  --provider NAME     anthropic (default) | openai
  --model NAME        model id (default claude-haiku-4-5)
  --inspect           print the layer inventory and exit
  --classify-only     classify, print the roles, and exit
  --no-cache          ignore the classification cache
  -v                  report issues by severity
```

Exit codes: `0` success, `1` pipeline error (scale gate, no walls, unreadable
PDF), `2` LLM unavailable, `3` bad usage.

### Without an API key

`--inspect` and `--walls` need no credentials and no network:

```bash
python -m archiagent plan.pdf --inspect
python -m archiagent plan.pdf out.ifc --walls WALLS PARTITION
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ARCHIAGENT_LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ARCHIAGENT_LLM_MODEL` | `claude-haiku-4-5` | model id |
| `ARCHIAGENT_LLM_BASE_URL` | unset | Groq / Kimi / DeepSeek, via the `openai` provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | credentials |
| `ARCHIAGENT_CACHE_DIR` | `~/.cache/archiagent/layers` | classification cache |

Install the LLM extra with `pip install -e ".[llm]"`.
````

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 194 tests, 0 failures.

- [ ] **Step 9: Commit**

```bash
git add archiagent/cli.py archiagent/__main__.py tests/test_cli.py README.md
git commit -m "feat: CLI -- inspect, classify-only, and full pipeline"
```

---

### Task 8: Live verification and documentation

Everything so far is proven against fakes. This task proves it against real
providers and real drawings, and records the numbers. **No live call belongs
in the test suite** — this is a one-time manual verification whose output is a
written record.

**Files:**
- Create: `docs/superpowers/reports/2026-08-23-w1-verification.md`
- Modify: `PLAN.md`

**Interfaces:**
- Consumes: the CLI from Task 7.
- Produces: a verification report. No code.

- [ ] **Step 1: Verify `--inspect` and `--walls` with no credentials**

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY \
  .venv/bin/python -m archiagent \
  "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" --inspect
```

Record the exit code and the layer count.

- [ ] **Step 2: Classify the demolition plan with the default provider**

```bash
.venv/bin/python -m archiagent \
  "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
  --classify-only
```

Record every layer, role, confidence and reason verbatim.

- [ ] **Step 3: Build the demolition plan end to end**

```bash
.venv/bin/python -m archiagent \
  "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
  /tmp/demolition.ifc -v
```

Record: exit code, `units_per_foot`, `max_residual_in`, wall count, space
count, and the issue summary.

**Reference figures from the hand-written map, for comparison only:** scale
11.6667 pt/ft, 51 walls, 28 junctions, 1 space.

**The bar is NOT reproducing those numbers.** That map was one person's
judgement and one of its calls was demonstrably wrong — classifying the hatch
layers as walls produced a 40% scale error that still passed the R1 gate
(`PLAN.md`, Finding C). Any difference must be **explainable from the `reason`
field and must not degrade the measured result**. Do not tune the prompt to
reproduce a specific wall count; if the classification differs, write down
what it said and why.

- [ ] **Step 4: Build the electrical plan end to end**

```bash
.venv/bin/python -m archiagent \
  "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" /tmp/electrical.ifc -v
```

Reference figures: scale 6.8267 pt/ft, 258 walls, 14 spaces. Record the
actual numbers.

- [ ] **Step 5: Confirm the ground-floor plan fails at the scale gate**

```bash
.venv/bin/python -m archiagent \
  "../input-floorplans/GROUND FLOOR PLAN_WORKING REVISED.pdf" \
  /tmp/ground.ifc -v
echo "exit: $?"
```

Expected: exit 1, and the error names the **scale gate**, not the classifier.
That drawing's text is outlined; recovering it is W3's job. If the error
blames the classifier instead, the message is wrong — fix it.

- [ ] **Step 6: Verify the cache**

Re-run Step 3 and confirm the second run makes no API call and produces
identical numbers. Then re-run with `--no-cache` and confirm it does call out.

- [ ] **Step 7: Verify one OpenAI-compatible provider**

Use whichever of Groq / Kimi / OpenAI / DeepSeek you hold a key for:

```bash
ARCHIAGENT_LLM_PROVIDER=openai \
ARCHIAGENT_LLM_BASE_URL=https://api.groq.com/openai/v1 \
ARCHIAGENT_LLM_MODEL=moonshotai/kimi-k2-instruct \
OPENAI_API_KEY=... \
  .venv/bin/python -m archiagent \
  "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" --classify-only
```

Record whether the endpoint honoured `strict` schema enforcement. If it did
not, note it — our own validation is what catches it, and that is the design
working, not a bug.

If you hold no second-provider key, write "not verified — no key available"
rather than claiming it works.

- [ ] **Step 8: Write the verification report**

Create `docs/superpowers/reports/2026-08-23-w1-verification.md` with one
section per step above, each showing the exact command and its actual output.
Include a table:

```markdown
| Drawing | Scale (u/ft) | Max residual | Walls | Spaces | Exit |
|---|---|---|---|---|---|
| Demolition | ... | ... | ... | ... | ... |
| Electrical | ... | ... | ... | ... | ... |
| Ground floor | — | — | — | — | 1 (scale gate) |
```

Add a section **"Where the LLM disagreed with the hand map"** listing each
difference with the model's own stated reason, and a verdict on whether the
measured result got better, worse, or stayed the same.

**Do not commit the generated `.ifc` files.** Report the numbers, not the
artifacts.

- [ ] **Step 9: Amend PLAN.md**

In `PLAN.md` §7 Stage 1, replace the note that the classifier is a stub with:

```markdown
Implemented in W1 as `LLMLayerClassifier`
(`archiagent/classify/llm_classifier.py`), backed by the `LLMClient` port in
`archiagent/llm/`. `StubClassifier` is retained for tests and for the CLI's
`--walls` bypass. Default model `claude-haiku-4-5`; the provider is
configurable, and `--inspect` / `--walls` work with no credentials at all.
See `docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md`
and the measured results in
`docs/superpowers/reports/2026-08-23-w1-verification.md`.
```

- [ ] **Step 10: Run the whole suite one last time**

Run: `.venv/bin/pytest -q`
Expected: PASS, 194 tests, 0 failures.

- [ ] **Step 11: Commit**

```bash
git add docs/superpowers/reports/2026-08-23-w1-verification.md PLAN.md
git commit -m "docs: W1 live verification against the real drawings"
```

---

## Verification Summary

| Task | Deliverable | Proven by |
|---|---|---|
| 1 | Confidence and reason reach `BuildingModel` | `test_layer_decision.py` + 129 existing tests still green |
| 2 | Prompt and `Role`-derived schema | `test_prompt.py` — schema enum equals the enum |
| 3 | Port + classifier + reply validation | `test_llm_classifier.py` — every failure mode, no network |
| 4 | Anthropic adapter | `test_anthropic_client.py` — request shape and error mapping |
| 5 | OpenAI-compatible adapter + config | `test_openai_client.py`, `test_llm_config.py` |
| 6 | Deterministic re-runs | `test_layer_cache.py` |
| 7 | The tool itself | `test_cli.py` — including two modes that need no credentials |
| 8 | It works against real providers and drawings | The verification report's recorded numbers |

## Known Limitations Carried Forward

These are deliberately not addressed by W1 and should not be "fixed" by an
implementer who notices them:

- **No retry or backoff** beyond what each vendor SDK already does.
- **No thumbnails.** Statistics only. If measured accuracy is inadequate, the
  port already accommodates adding them as a bounded follow-up.
- **No confidence gating.** A layer at 0.3 confidence is used and surfaced as
  an issue. Deciding what is too low is W2's job, once a human has somewhere
  to adjudicate.
- **No heuristic pre-classification.** A rule that quietly misclassifies is a
  new silent-failure mode, and avoiding those is the whole point of this
  design.
- **The ground-floor plan still fails.** Its text is outlined; W3 addresses it.
