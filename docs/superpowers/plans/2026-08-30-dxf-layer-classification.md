# DXF Layer Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept a DXF via `--dxfFilePath`, classify its layers with a two-stage classifier (features+names, then opt-in vision for ambiguous layers), and drive the existing wall/junction/space/IFC pipeline from it.

**Architecture:** A new DXF front-end emits the existing `PrimitiveSet`, so every downstream stage is untouched. `LayerStats` gains optional DXF-only fields (append-only, defaults keep the PDF path byte-identical). A new two-stage classifier satisfies the existing `LayerClassifier` protocol, so `pipeline.py` never learns that vision happened.

**Tech Stack:** Python 3.14, `ezdxf` 1.4+, `matplotlib` + `Pillow` (thumbnails), existing `anthropic`/`openai` adapters, `ifcopenshell` 0.8.5.

**Spec:** `docs/superpowers/specs/2026-08-30-dxf-layer-classification-design.md`

## Global Constraints

- **NEVER commit** a `.pdf`, `.dwg`, `.dxf`, `.ifc`, or a rendered thumbnail. The sample drawings are confidential client property and this repo is public. Generated artifacts go to `tmp_path` or `.archiagent-cache/` (both git-ignored).
- Tests reach the sample drawings via the `ARCHIAGENT_FIXTURES` env var and **skip** when it is absent.
- **No test makes a live API call.** Ever.
- Vision (stage 2) is **opt-in**: CLI `--vision` or `ARCHIAGENT_VISION=1`. The flag wins when both are set. Default is stage 1 only.
- Escalation cap: **6 layers per drawing**, ordered by descending `entity_share`.
- Escalation triggers (any one): stage-1 confidence `< 0.70`; `entity_share >= 0.10` and role not in `WALL_ROLES | {Role.COLUMN}`; uninformative name (`0`, `Defpoints`, all-digits, single character); two or more layers assigned a role in `WALL_ROLES`.
- New issue codes: `layer_escalation_skipped` (severity `warn`), `layer_role_revised` (severity `info`).
- Layer names in a model reply may arrive wrapped in literal quotes. **Strip surrounding `"` and `'` before matching.** Matching is otherwise exact against the inventory name.
- `LayerDecision`, `Classification`, `LayerClassifier` and `pipeline.py` keep their current shapes. Additions to `LayerStats` are optional fields with defaults only.
- Every `LayerDecision` takes its `layer` from the inventory, never from a model reply.

---

### Task 1: DXF ingest → PrimitiveSet

**Files:**
- Create: `archiagent/ingest/dxf_vector.py`
- Create: `tests/test_dxf_vector.py`
- Modify: `pyproject.toml` (add `ezdxf>=1.4`)

**Interfaces:**
- Consumes: `Primitive`, `TextItem`, `PrimitiveSet` from `archiagent.primitives`.
- Produces:
  - `class DxfUnitsError(RuntimeError)`
  - `def load_dxf(path: str | Path, units_per_foot: float | None = None) -> tuple[PrimitiveSet, float]` — returns the primitive set and the resolved units-per-foot.
  - `def units_from_header(doc) -> float | None` — maps `$INSUNITS` to units-per-foot; `None` when unset or unrecognised.
  - `INSUNITS_PER_FOOT: dict[int, float]`

- [ ] **Step 1: Write the failing test for entity mapping**

```python
# tests/test_dxf_vector.py
import ezdxf
import pytest
from archiagent.ingest.dxf_vector import load_dxf, units_from_header


def _doc(insunits=1):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_line((0, 0), (120, 0), dxfattribs={"layer": "WALL"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "FURN"})
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5)],
                       close=True, dxfattribs={"layer": "COL"})
    msp.add_text("KITCHEN", dxfattribs={"layer": "TEXT"}).set_placement((3, 3))
    return doc


def test_lines_and_polylines_become_primitives(tmp_path):
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, upf = load_dxf(p)
    kinds = {(pr.layer, pr.kind) for pr in ps.primitives}
    assert ("WALL", "line") in kinds
    assert ("FURN", "line") in kinds
    assert ("COL", "rect") in kinds        # closed polyline -> rect, so it closes
    assert upf == 12.0                     # INSUNITS 1 = inches


def test_live_text_is_carried_through(tmp_path):
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, _ = load_dxf(p)
    assert any(t.text == "KITCHEN" and t.layer == "TEXT" for t in ps.texts)


def test_coordinates_are_plain_floats(tmp_path):
    """ezdxf yields numpy floats; ifcopenshell rejects them downstream."""
    p = tmp_path / "t.dxf"
    _doc().saveas(p)
    ps, _ = load_dxf(p)
    for pr in ps.primitives:
        for x, y in pr.coords:
            assert type(x) is float and type(y) is float
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_dxf_vector.py -v`
Expected: FAIL, `ModuleNotFoundError: archiagent.ingest.dxf_vector`

- [ ] **Step 3: Implement `load_dxf`**

```python
"""DXF front-end. Emits the same PrimitiveSet the PDF front-end emits, so
no downstream stage knows which format the drawing came from.

Unlike PDF, DXF declares its units -- but the declaration is not reliable:
`Floor Plan.dxf` declares $INSUNITS=2 (feet) while its columns measure
12.04 x 24.07, i.e. inches. Callers may override, and Task 7 sanity-checks
the resolved value against detected wall thickness.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import ezdxf

from archiagent.primitives import Primitive, PrimitiveSet, TextItem


class DxfUnitsError(RuntimeError):
    """Units could not be resolved and none was supplied."""


# $INSUNITS -> drawing units per foot
INSUNITS_PER_FOOT: dict[int, float] = {
    1: 12.0,          # inches
    2: 1.0,           # feet
    4: 304.8,         # millimetres
    5: 30.48,         # centimetres
    6: 0.3048,        # metres
}


def units_from_header(doc) -> float | None:
    return INSUNITS_PER_FOOT.get(doc.header.get("$INSUNITS", 0))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_dxf(path: str | Path,
             units_per_foot: float | None = None) -> tuple[PrimitiveSet, float]:
    path = Path(path)
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    upf = units_per_foot or units_from_header(doc)
    if not upf or upf <= 0:
        raise DxfUnitsError(
            f"{path.name} declares no usable $INSUNITS and no --units-per-foot "
            "was given; supply one (12 for inches, 1 for feet, 304.8 for mm)")

    prims: list[Primitive] = []
    texts: list[TextItem] = []
    for e in msp:
        t = e.dxftype()
        layer = e.dxf.layer
        if t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            prims.append(Primitive("line",
                                   ((float(a.x), float(a.y)),
                                    (float(b.x), float(b.y))),
                                   layer, None, None))
        elif t == "LWPOLYLINE":
            pts = tuple((float(v[0]), float(v[1])) for v in e)
            if len(pts) < 2:
                continue
            # A closed polyline must close: "rect" is the only kind whose
            # segments() appends the closing edge.
            prims.append(Primitive("rect" if e.closed else "line",
                                   pts, layer, None, None))
        elif t in ("MTEXT", "TEXT"):
            s = e.text if t == "MTEXT" else e.dxf.text
            ip = e.dxf.insert
            x, y = float(ip.x), float(ip.y)
            texts.append(TextItem(str(s), (x, y, x, y), layer))

    xs = [p[0] for pr in prims for p in pr.coords] or [0.0]
    ys = [p[1] for pr in prims for p in pr.coords] or [0.0]
    ps = PrimitiveSet(tuple(prims), tuple(texts),
                      max(xs) - min(xs), max(ys) - min(ys),
                      str(path), _sha256(path))
    return ps, float(upf)
```

- [ ] **Step 4: Add the dependency**

In `pyproject.toml`, add `"ezdxf>=1.4"` to `[project] dependencies`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dxf_vector.py -v`
Expected: 3 passed

- [ ] **Step 6: Add the units-failure test and make it pass**

```python
def test_missing_units_is_a_clear_error(tmp_path):
    from archiagent.ingest.dxf_vector import DxfUnitsError
    p = tmp_path / "t.dxf"
    _doc(insunits=0).saveas(p)
    with pytest.raises(DxfUnitsError, match="units-per-foot"):
        load_dxf(p)


def test_explicit_units_override_the_header(tmp_path):
    p = tmp_path / "t.dxf"
    _doc(insunits=2).saveas(p)          # header claims feet
    _, upf = load_dxf(p, units_per_foot=12.0)
    assert upf == 12.0                   # caller wins
```

Run: `.venv/bin/python -m pytest tests/test_dxf_vector.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add archiagent/ingest/dxf_vector.py tests/test_dxf_vector.py pyproject.toml
git commit -m "feat: DXF front-end emitting PrimitiveSet, with unit resolution"
```

---

### Task 2: DXF layer features

**Files:**
- Modify: `archiagent/classify/inventory.py`
- Create: `archiagent/classify/dxf_inventory.py`
- Create: `tests/test_dxf_inventory.py`

**Interfaces:**
- Consumes: `LayerStats` from Task 0 (existing), `load_dxf` from Task 1.
- Produces: `def build_dxf_inventory(dxf_path, ps) -> tuple[LayerStats, ...]`

- [ ] **Step 1: Extend LayerStats with optional DXF fields**

Append to the `LayerStats` dataclass in `archiagent/classify/inventory.py`. **Defaults only** — the PDF path must be unaffected:

```python
    # DXF-only. The PDF front-end leaves these at their defaults.
    entity_mix: tuple[tuple[str, int], ...] = ()   # ("LINE", 812), top 5
    entity_share: float = 0.0                      # fraction of all entities
    lineweight: int | None = None                  # DXF units, -3 = default
    linetype: str = ""
    is_off: bool = False
    is_frozen: bool = False
    extent_ratio: float = 0.0                      # layer bbox area / drawing bbox area
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_dxf_inventory.py
import ezdxf
from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.ingest.dxf_vector import load_dxf


def _doc(tmp_path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    doc.layers.add("WALL", color=7, lineweight=35)
    doc.layers.add("FURN", color=3, lineweight=9)
    doc.layers.add("GRID", color=8, lineweight=5)
    msp = doc.modelspace()
    for i in range(10):
        msp.add_line((0, i), (100, i), dxfattribs={"layer": "WALL"})
    msp.add_arc((5, 5), 2, 0, 90, dxfattribs={"layer": "FURN"})
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "GRID"})
    doc.layers.get("GRID").freeze()
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def test_entity_mix_and_share(tmp_path):
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert dict(stats["WALL"].entity_mix)["LINE"] == 10
    assert dict(stats["FURN"].entity_mix)["ARC"] == 1
    assert stats["WALL"].entity_share > stats["FURN"].entity_share
    assert abs(sum(s.entity_share for s in stats.values()) - 1.0) < 1e-6


def test_layer_table_attributes_are_carried(tmp_path):
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert stats["WALL"].lineweight == 35
    assert stats["FURN"].lineweight == 9
    assert stats["GRID"].is_frozen is True
    assert stats["WALL"].is_frozen is False


def test_arc_only_layer_is_visible_in_the_mix(tmp_path):
    """ARC-heavy layers are doors. The classifier cannot see that unless the
    entity mix records entity types the PrimitiveSet drops."""
    p = _doc(tmp_path)
    ps, _ = load_dxf(p)
    stats = {s.name: s for s in build_dxf_inventory(p, ps)}
    assert "ARC" in dict(stats["FURN"].entity_mix)
```

- [ ] **Step 3: Run it, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_dxf_inventory.py -v`
Expected: FAIL, `ModuleNotFoundError: archiagent.classify.dxf_inventory`

- [ ] **Step 4: Implement `build_dxf_inventory`**

It calls the existing `build_inventory(ps)` for the geometric fields, then
enriches each `LayerStats` with DXF-only fields via `dataclasses.replace`.
Layers that exist in the DXF layer table but carry no primitives still get a
row (a frozen layer holding geometry the PrimitiveSet skipped must remain
visible to the classifier).

```python
"""DXF layer features: everything the DXF layer table and entity types know
that a PrimitiveSet cannot express.

ARC counts identify doors; MTEXT counts identify text layers; lineweight
separates walls (35-40) from furniture (9) and windows (5). None of this
survives into PrimitiveSet, so it is gathered here from the DXF directly.
"""

from __future__ import annotations

import collections
from dataclasses import replace
from pathlib import Path

import ezdxf

from archiagent.classify.inventory import LayerStats, build_inventory
from archiagent.primitives import PrimitiveSet


def build_dxf_inventory(dxf_path: str | Path,
                        ps: PrimitiveSet) -> tuple[LayerStats, ...]:
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    mix: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    bbox: dict[str, list[float]] = {}
    total = 0
    for e in msp:
        total += 1
        lay = e.dxf.layer
        mix[lay][e.dxftype()] += 1

    for pr in ps.primitives:
        b = bbox.setdefault(pr.layer, [1e30, 1e30, -1e30, -1e30])
        for x, y in pr.coords:
            b[0] = min(b[0], x); b[1] = min(b[1], y)
            b[2] = max(b[2], x); b[3] = max(b[3], y)

    draw_area = max(ps.width * ps.height, 1e-9)
    base = {s.name: s for s in build_inventory(ps)}
    table = {l.dxf.name: l for l in doc.layers}

    out: list[LayerStats] = []
    for name in sorted(set(base) | set(mix)):
        s = base.get(name) or LayerStats(
            name=name, path_count=0, segment_count=0, axis_aligned_fraction=0.0,
            stroke_widths=(), dominant_colors=(), bbox=(0.0, 0.0, 0.0, 0.0),
            length_p10=0.0, length_p50=0.0, length_p90=0.0)
        lay = table.get(name)
        b = bbox.get(name)
        ratio = (((b[2] - b[0]) * (b[3] - b[1])) / draw_area) if b else 0.0
        out.append(replace(
            s,
            entity_mix=tuple(mix[name].most_common(5)),
            entity_share=(sum(mix[name].values()) / total) if total else 0.0,
            lineweight=(int(lay.dxf.lineweight) if lay is not None
                        and lay.dxf.hasattr("lineweight") else None),
            linetype=(str(lay.dxf.linetype) if lay is not None else ""),
            is_off=(bool(lay.is_off()) if lay is not None else False),
            is_frozen=(bool(lay.is_frozen()) if lay is not None else False),
            extent_ratio=min(ratio, 1.0),
        ))
    return tuple(out)
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dxf_inventory.py tests/test_inventory.py -v`
Expected: all pass — including the existing PDF inventory tests, which must be unaffected by the new optional fields.

- [ ] **Step 6: Commit**

```bash
git add archiagent/classify/inventory.py archiagent/classify/dxf_inventory.py tests/test_dxf_inventory.py
git commit -m "feat: DXF layer features -- entity mix, lineweight, linetype, freeze state"
```

---

### Task 3: The escalation rule

**Files:**
- Create: `archiagent/classify/escalate.py`
- Create: `tests/test_escalate.py`

**Interfaces:**
- Consumes: `LayerStats` (Task 2), `LayerDecision`, `WALL_ROLES`, `Role`.
- Produces:
  - `ESCALATION_CAP: int = 6`
  - `CONFIDENCE_FLOOR: float = 0.70`
  - `SHARE_FLOOR: float = 0.10`
  - `BODY_ROLES: frozenset[Role]` — `WALL_ROLES | {Role.COLUMN}`
  - `def is_uninformative(name: str) -> bool`
  - `def escalation_candidates(decisions, stats) -> tuple[tuple[str, str], ...]` — `(layer_name, trigger)` pairs, ordered by descending `entity_share`, **uncapped**
  - `def select_for_escalation(decisions, stats) -> tuple[tuple[str, str], ...]` — the same, capped at `ESCALATION_CAP`

Two functions, not one, so a caller can report what the cap dropped.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_escalate.py
import pytest
from archiagent.classify.escalate import (
    ESCALATION_CAP, escalation_candidates, is_uninformative,
    select_for_escalation)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=1.0, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 1, 1),
                      length_p10=0.0, length_p50=1.0, length_p90=1.0,
                      entity_share=share)


def _d(name, role, conf):
    return LayerDecision(name, role, conf, "", "llm")


@pytest.mark.parametrize("name,expected", [
    ("0", True), ("Defpoints", True), ("123", True), ("A", True),
    ("WALLS", False), ("COLUM HATCH", False), ("win", False),
])
def test_uninformative_names(name, expected):
    assert is_uninformative(name) is expected


def test_low_confidence_escalates():
    d = (_d("FURN", Role.FURNITURE, 0.69),)
    s = (_s("FURN"),)
    assert [n for n, _ in escalation_candidates(d, s)] == ["FURN"]


def test_confidence_exactly_at_the_floor_does_not_escalate():
    d = (_d("FURN", Role.FURNITURE, 0.70),)
    assert escalation_candidates(d, (_s("FURN"),)) == ()


def test_big_non_wall_layer_escalates():
    """The layer-0 trap: 54.9% of Floor Plan.dxf sits on layer '0', and the
    walls are there. Confidence alone would never flag it."""
    d = (_d("0", Role.IGNORE, 0.95),)
    s = (_s("0", share=0.549),)
    assert [t for _, t in escalation_candidates(d, s)][0] == "large_non_wall_layer"


def test_big_wall_layer_does_not_escalate_on_share():
    d = (_d("WALLS", Role.WALL_STRUCTURAL, 0.95),)
    s = (_s("WALLS", share=0.549),)
    assert escalation_candidates(d, s) == ()


def test_column_role_counts_as_structural_for_the_share_trigger():
    d = (_d("COL", Role.COLUMN, 0.95),)
    assert escalation_candidates(d, (_s("COL", share=0.3),)) == ()


def test_two_wall_layers_both_escalate():
    """WALL vs WALLS: the electrical plan's open question."""
    d = (_d("WALL", Role.WALL_STRUCTURAL, 0.95),
         _d("WALLS", Role.WALL_PARTITION, 0.95))
    s = (_s("WALL", 0.02), _s("WALLS", 0.03))
    assert sorted(n for n, _ in escalation_candidates(d, s)) == ["WALL", "WALLS"]


def test_ordering_is_by_descending_entity_share():
    d = tuple(_d(f"L{i}", Role.FURNITURE, 0.5) for i in range(3))
    s = (_s("L0", 0.1), _s("L1", 0.5), _s("L2", 0.3))
    assert [n for n, _ in escalation_candidates(d, s)] == ["L1", "L2", "L0"]


def test_cap_keeps_the_largest_and_drops_the_rest():
    d = tuple(_d(f"L{i}", Role.FURNITURE, 0.5) for i in range(9))
    s = tuple(_s(f"L{i}", (9 - i) / 100) for i in range(9))
    assert len(escalation_candidates(d, s)) == 9
    picked = select_for_escalation(d, s)
    assert len(picked) == ESCALATION_CAP
    assert [n for n, _ in picked] == [f"L{i}" for i in range(ESCALATION_CAP)]


def test_a_layer_escalates_once_even_when_several_triggers_fire():
    d = (_d("0", Role.IGNORE, 0.2),)          # low confidence AND uninformative
    s = (_s("0", share=0.5),)                  # AND large non-wall
    assert len(escalation_candidates(d, s)) == 1
```

- [ ] **Step 2: Run, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_escalate.py -v`
Expected: FAIL, `ModuleNotFoundError: archiagent.classify.escalate`

- [ ] **Step 3: Implement**

```python
"""Which layers does stage 1 not deserve the last word on?

Deterministic and image-free by design: the rule is the expensive part of
the classifier to get wrong, so it is testable without an LLM or a renderer.
"""

from __future__ import annotations

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import Classification, WALL_ROLES
from archiagent.classify.roles import Role

ESCALATION_CAP = 6
CONFIDENCE_FLOOR = 0.70
SHARE_FLOOR = 0.10

# A column is structural: a large COLUMN layer is not suspicious the way a
# large FURNITURE layer is.
BODY_ROLES: frozenset[Role] = WALL_ROLES | {Role.COLUMN}


def is_uninformative(name: str) -> bool:
    n = name.strip()
    return (n == "" or n.lower() == "defpoints" or n.isdigit() or len(n) == 1)


def escalation_candidates(decisions: Classification,
                          stats: tuple[LayerStats, ...]
                          ) -> tuple[tuple[str, str], ...]:
    share = {s.name: s.entity_share for s in stats}
    wall_layers = [d.layer for d in decisions if d.role in WALL_ROLES]

    picked: dict[str, str] = {}
    for d in decisions:
        if d.confidence < CONFIDENCE_FLOOR:
            picked.setdefault(d.layer, "low_confidence")
        elif (share.get(d.layer, 0.0) >= SHARE_FLOOR
              and d.role not in BODY_ROLES):
            picked.setdefault(d.layer, "large_non_wall_layer")
        elif is_uninformative(d.layer):
            picked.setdefault(d.layer, "uninformative_name")
        elif len(wall_layers) >= 2 and d.layer in wall_layers:
            picked.setdefault(d.layer, "competing_wall_layers")

    return tuple(sorted(picked.items(),
                        key=lambda kv: (-share.get(kv[0], 0.0), kv[0])))


def select_for_escalation(decisions: Classification,
                          stats: tuple[LayerStats, ...]
                          ) -> tuple[tuple[str, str], ...]:
    return escalation_candidates(decisions, stats)[:ESCALATION_CAP]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_escalate.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/escalate.py tests/test_escalate.py
git commit -m "feat: deterministic escalation rule with a 6-layer cap"
```

---

### Task 4: Stage-1 prompt for DXF features

**Files:**
- Modify: `archiagent/classify/prompt.py`
- Create: `tests/test_dxf_prompt.py`

**Interfaces:**
- Consumes: `LayerStats` with DXF fields (Task 2).
- Produces: `def build_dxf_user_prompt(stats: tuple[LayerStats, ...]) -> str`, and `DXF_SYSTEM_PROMPT`. `PROMPT_VERSION` stays content-derived and must now hash the DXF prompt too.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dxf_prompt.py
from archiagent.classify.inventory import LayerStats
from archiagent.classify.prompt import (
    DXF_SYSTEM_PROMPT, PROMPT_VERSION, build_dxf_user_prompt)


def _s(name, **kw):
    base = dict(path_count=3, segment_count=9, axis_aligned_fraction=0.9,
                stroke_widths=(), dominant_colors=(), bbox=(0, 0, 10, 10),
                length_p10=1.0, length_p50=5.0, length_p90=9.0)
    base.update(kw)
    return LayerStats(name=name, **base)


def test_entity_mix_is_rendered():
    s = (_s("doors", entity_mix=(("LINE", 1111), ("ARC", 147)), entity_share=0.2),)
    out = build_dxf_user_prompt(s)
    assert "LINE:1111" in out and "ARC:147" in out


def test_lineweight_and_linetype_are_rendered():
    s = (_s("wall", lineweight=35, linetype="Continuous"),
         _s("BEAM", lineweight=30, linetype="HIDDEN"))
    out = build_dxf_user_prompt(s)
    assert "35" in out and "HIDDEN" in out


def test_layer_names_are_json_quoted():
    """Names contain spaces: 'COLUM HATCH', 'DB TO SHAFT CONDUIT'."""
    out = build_dxf_user_prompt((_s("COLUM HATCH"),))
    assert '"COLUM HATCH"' in out


def test_frozen_and_off_are_stated():
    out = build_dxf_user_prompt((_s("GRID", is_frozen=True),))
    assert "frozen" in out.lower()


def test_entity_share_is_rendered_as_a_percentage():
    out = build_dxf_user_prompt((_s("0", entity_share=0.549),))
    assert "54.9" in out or "55" in out


def test_no_raw_coordinates_leak_into_the_prompt():
    """As in W1: derived width/height only, never absolute coordinates."""
    out = build_dxf_user_prompt((_s("X", bbox=(1234.5, 6789.0, 1240.0, 6800.0)),))
    assert "1234.5" not in out and "6789" not in out


def test_prompt_version_covers_the_dxf_prompt(monkeypatch):
    """Editing DXF_SYSTEM_PROMPT without bumping the version would serve
    stale cached classifications forever. Recompute the hash with the DXF
    prompt perturbed and assert the version moves."""
    import hashlib
    import json as _json

    from archiagent.classify import prompt as mod

    def _version(dxf_prompt: str) -> str:
        return hashlib.sha256(
            (mod.SYSTEM_PROMPT + dxf_prompt
             + _json.dumps(mod.response_schema(), sort_keys=True)).encode("utf-8")
        ).hexdigest()[:12]

    assert _version(mod.DXF_SYSTEM_PROMPT) == PROMPT_VERSION
    assert _version(mod.DXF_SYSTEM_PROMPT + " ") != PROMPT_VERSION


def test_system_prompt_warns_that_the_default_layer_may_hold_the_building():
    assert "0" in DXF_SYSTEM_PROMPT
    low = DXF_SYSTEM_PROMPT.lower()
    assert "default layer" in low or "layer 0" in low
```

- [ ] **Step 2: Run, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_dxf_prompt.py -v`
Expected: FAIL, `ImportError: cannot import name 'DXF_SYSTEM_PROMPT'`

- [ ] **Step 3: Implement**

Add to `archiagent/classify/prompt.py`:

```python
DXF_SYSTEM_PROMPT = """\
You are classifying the layers of a DXF architectural floorplan so a
deterministic pipeline can build a 3D model of the WALLS.

You are given one row per layer. Use every column:

- name: the drafter's own label. Usually the strongest signal, but CAD
  offices do not follow the AIA/NCS standard -- expect names like
  "NEW WALLS", "walll", "COLUM HATCH", "win", "FURN".
- entities: counts by DXF entity type. ARC-heavy layers are usually doors
  (door swings are arcs). MTEXT/TEXT-heavy layers are labels. DIMENSION
  means a dimension layer. HATCH means fill, not wall faces.
- lineweight: walls and columns are drafted heavy (30-40); doors, windows
  and furniture light (5-9). A value of -3 means "default", i.e. no signal.
- linetype: HIDDEN usually means an element above the cut plane, such as a
  beam.
- share: the fraction of the drawing's entities on this layer.
- frozen/off: the drafter turned this layer off. Weak evidence it is not
  part of the plan.

Two warnings drawn from real drawings:

1. The default layer "0" is sometimes where the entire building is drawn,
   holding more than half the entities. Its name tells you nothing. Judge it
   on its share and its entity mix, not on its name.
2. A drawing often splits walls across several layers. Classify EVERY
   wall-carrying layer as a wall, not just the best one.

Return one entry per layer. Do not invent layers.
"""


def _dxf_row(s: LayerStats) -> str:
    x0, y0, x1, y1 = s.bbox
    mix = " ".join(f"{k}:{v}" for k, v in s.entity_mix) or "-"
    lw = "default" if s.lineweight in (None, -3) else str(s.lineweight)
    flags = ",".join(f for f, on in (("frozen", s.is_frozen), ("off", s.is_off)) if on) or "-"
    return (f'{json.dumps(s.name)} | entities={mix} | share={s.entity_share * 100:.1f}% '
            f'| lineweight={lw} | linetype={s.linetype or "-"} | flags={flags} '
            f'| size={x1 - x0:.0f}x{y1 - y0:.0f} | axis={s.axis_aligned_fraction:.2f} '
            f'| len_p50={s.length_p50:.1f}')


def build_dxf_user_prompt(stats: tuple[LayerStats, ...]) -> str:
    rows = "\n".join(_dxf_row(s) for s in stats)
    return (f"INVENTORY ({len(stats)} layers)\n{rows}\n\n"
            "Classify every layer listed above.")
```

Then extend `PROMPT_VERSION` so it hashes the DXF prompt too:

```python
PROMPT_VERSION = hashlib.sha256(
    (SYSTEM_PROMPT + DXF_SYSTEM_PROMPT
     + json.dumps(response_schema(), sort_keys=True)).encode("utf-8")
).hexdigest()[:12]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dxf_prompt.py tests/test_prompt.py -v`
Expected: all pass. `tests/test_prompt.py` asserts `PROMPT_VERSION` is derived from its inputs; it must still pass with the new input folded in.

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/prompt.py tests/test_dxf_prompt.py
git commit -m "feat: DXF stage-1 prompt over entity mix, lineweight and share"
```

---

### Task 5: Layer thumbnail renderer

**Files:**
- Create: `archiagent/classify/thumbnails.py`
- Create: `tests/test_thumbnails.py`
- Modify: `pyproject.toml` (optional extra `vision`: `matplotlib`, `Pillow`)

**Interfaces:**
- Produces:
  - `class RenderUnavailable(RuntimeError)` — matplotlib/Pillow missing
  - `def render_layer(dxf_path, layer: str | None, out_png: Path, size_inches=(7,7), dpi=140) -> Path` — `layer=None` renders every layer (the reference image)
  - `def render_for_escalation(dxf_path, layers: Sequence[str], cache_dir: Path) -> tuple[Path, dict[str, Path]]` — returns the reference image path and a per-layer map. Layers whose render raises are **omitted** from the map, not fatal.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_thumbnails.py
import ezdxf
import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")

from archiagent.classify.thumbnails import render_for_escalation, render_layer


def _doc(tmp_path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for i in range(20):
        msp.add_line((0, i * 5), (200, i * 5), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 0), (3, 3), dxfattribs={"layer": "TINY"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def test_renders_a_single_layer_to_png(tmp_path):
    p = _doc(tmp_path)
    out = render_layer(p, "WALL", tmp_path / "w.png")
    assert out.exists() and out.stat().st_size > 1000


def test_reference_render_uses_every_layer(tmp_path):
    p = _doc(tmp_path)
    ref = render_layer(p, None, tmp_path / "ref.png")
    only = render_layer(p, "TINY", tmp_path / "tiny.png")
    assert ref.stat().st_size > only.stat().st_size


def test_render_for_escalation_returns_reference_plus_map(tmp_path):
    p = _doc(tmp_path)
    ref, per_layer = render_for_escalation(p, ["WALL", "TINY"], tmp_path / "cache")
    assert ref.exists()
    assert set(per_layer) == {"WALL", "TINY"}
    assert all(v.exists() for v in per_layer.values())


def test_a_layer_that_does_not_exist_is_omitted_not_fatal(tmp_path):
    p = _doc(tmp_path)
    ref, per_layer = render_for_escalation(p, ["WALL", "NOPE"], tmp_path / "cache")
    assert "WALL" in per_layer
    assert ref.exists()


def test_output_stays_inside_the_cache_dir(tmp_path):
    """Thumbnails are pictures of confidential drawings; they must land only
    where .gitignore covers them."""
    p = _doc(tmp_path)
    cache = tmp_path / "cache"
    ref, per_layer = render_for_escalation(p, ["WALL"], cache)
    assert cache in ref.parents
    assert all(cache in v.parents for v in per_layer.values())
```

- [ ] **Step 2: Run, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_thumbnails.py -v`
Expected: FAIL, `ModuleNotFoundError: archiagent.classify.thumbnails`

- [ ] **Step 3: Implement**

```python
"""Render a DXF layer to a PNG for the vision stage.

Uses ezdxf's matplotlib backend in-process: no Blender, no IFC round-trip.
matplotlib and Pillow are optional -- absence degrades to stage 1, it does
not fail the run.

The output is a picture of a confidential client drawing. It is written only
under a caller-supplied cache directory, which .gitignore covers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence


class RenderUnavailable(RuntimeError):
    """matplotlib and/or Pillow are not installed."""


def _backend():
    try:
        import matplotlib
        matplotlib.use("Agg")
        from ezdxf.addons.drawing.matplotlib import qsave
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        raise RenderUnavailable(
            "layer thumbnails need matplotlib and Pillow; install the "
            "'vision' extra, or run without --vision") from exc
    return qsave


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "_"


def render_layer(dxf_path: str | Path, layer: str | None, out_png: Path,
                 size_inches: tuple[float, float] = (7.0, 7.0),
                 dpi: int = 140) -> Path:
    import ezdxf
    qsave = _backend()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    ff = None if layer is None else (lambda e: e.dxf.layer == layer)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    qsave(msp, str(out_png), bg="#FFFFFF", fg="#000000", dpi=dpi,
          filter_func=ff, size_inches=size_inches)
    return out_png


def render_for_escalation(dxf_path: str | Path, layers: Sequence[str],
                          cache_dir: Path) -> tuple[Path, dict[str, Path]]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    ref = render_layer(dxf_path, None, cache_dir / "_reference.png")
    out: dict[str, Path] = {}
    for lay in layers:
        try:
            out[lay] = render_layer(dxf_path, lay,
                                    cache_dir / f"layer_{_safe(lay)}.png")
        except RenderUnavailable:
            raise
        except Exception:                          # noqa: BLE001 - one bad layer is not fatal
            continue
    return ref, out
```

- [ ] **Step 4: Add the optional extra**

In `pyproject.toml`:

```toml
[project.optional-dependencies]
vision = ["matplotlib>=3.8", "Pillow>=10"]
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_thumbnails.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add archiagent/classify/thumbnails.py tests/test_thumbnails.py pyproject.toml
git commit -m "feat: in-process DXF layer thumbnails for the vision stage"
```

---

### Task 6: Vision transport and the two-stage classifier

**Files:**
- Modify: `archiagent/llm/client.py`
- Modify: `archiagent/llm/anthropic_client.py`
- Modify: `archiagent/llm/openai_client.py`
- Create: `archiagent/classify/dxf_classifier.py`
- Create: `tests/test_dxf_classifier.py`

**Interfaces:**
- Consumes: `LLMClient`, `decisions_from_reply` (existing, in `archiagent/classify/llm_classifier.py`), `select_for_escalation`, `render_for_escalation`, `build_dxf_user_prompt`.
- Produces:
  - `LLMClient.classify_json_vision(*, system, user, schema, images: list[tuple[str, bytes]], max_tokens=2048) -> dict` added to the protocol and both adapters. `images` is a list of `(label, png_bytes)`.
  - `class VisionUnsupported(RuntimeError)`
  - `class DxfLayerClassifier` implementing `LayerClassifier`, constructed with `(client, dxf_path, *, vision=False, cache_dir=None, on_issue=None)`.

- [ ] **Step 1: Write the failing tests (fakes only — no live calls)**

```python
# tests/test_dxf_classifier.py
import pytest
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role


def _s(name, share=0.0):
    return LayerStats(name=name, path_count=1, segment_count=1,
                      axis_aligned_fraction=0.9, stroke_widths=(),
                      dominant_colors=(), bbox=(0, 0, 10, 10),
                      length_p10=1.0, length_p50=5.0, length_p90=9.0,
                      entity_share=share)


class FakeClient:
    def __init__(self, stage1, stage2=None):
        self.stage1, self.stage2 = stage1, stage2
        self.vision_calls = 0

    def classify_json(self, **kw):
        return self.stage1

    def classify_json_vision(self, **kw):
        self.vision_calls += 1
        self.last_images = kw["images"]
        return self.stage2


def _reply(pairs):
    return {"layers": [{"name": n, "role": r, "confidence": c, "reason": "x"}
                       for n, r, c in pairs]}


def test_stage1_only_when_vision_is_off(tmp_path):
    c = FakeClient(_reply([("0", "ignore", 0.95), ("WALLS", "wall_structural", 0.9)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549), _s("WALLS", 0.05)))}
    assert out["0"].role is Role.IGNORE
    assert c.vision_calls == 0


def test_escalation_skipped_is_reported_when_vision_is_off(tmp_path):
    issues = []
    c = FakeClient(_reply([("0", "ignore", 0.95)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False,
                             on_issue=issues.append)
    clf.classify((_s("0", 0.549),))
    codes = [i.code for i in issues]
    assert "layer_escalation_skipped" in codes
    assert any("0" in i.entity for i in issues)


def test_stage2_revises_the_role_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"\x89PNG ref")
    (tmp_path / "0.png").write_bytes(b"\x89PNG zero")

    issues = []
    c = FakeClient(_reply([("0", "ignore", 0.95)]),
                   _reply([("0", "wall_structural", 0.92)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.WALL_STRUCTURAL
    assert c.vision_calls == 1
    assert "layer_role_revised" in [i.code for i in issues]


def test_stage2_sees_the_reference_image_first(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"REF")
    (tmp_path / "0.png").write_bytes(b"ZERO")
    c = FakeClient(_reply([("0", "ignore", 0.5)]), _reply([("0", "ignore", 0.6)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True, cache_dir=tmp_path)
    clf.classify((_s("0", 0.5),))
    assert c.last_images[0][1] == b"REF"


def test_a_broken_stage2_reply_keeps_stage1(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda *a, **k: (tmp_path / "ref.png", {"0": tmp_path / "0.png"}))
    (tmp_path / "ref.png").write_bytes(b"R"); (tmp_path / "0.png").write_bytes(b"Z")
    issues = []
    c = FakeClient(_reply([("0", "furniture", 0.95)]), {"garbage": True})
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.FURNITURE          # stage 1 survives
    assert issues                                    # and it was reported


def test_missing_renderer_degrades_to_stage1(tmp_path, monkeypatch):
    from archiagent.classify.thumbnails import RenderUnavailable

    def boom(*a, **k):
        raise RenderUnavailable("no matplotlib")

    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation", boom)
    issues = []
    c = FakeClient(_reply([("0", "furniture", 0.95)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=True,
                             cache_dir=tmp_path, on_issue=issues.append)
    out = {d.layer: d for d in clf.classify((_s("0", 0.549),))}
    assert out["0"].role is Role.FURNITURE
    assert issues


def test_quoted_layer_names_in_a_reply_still_match(tmp_path):
    """Live testing showed models echoing our JSON quotes into the name."""
    c = FakeClient(_reply([('"WALLS"', "wall_structural", 0.9)]))
    clf = DxfLayerClassifier(c, tmp_path / "x.dxf", vision=False)
    out = {d.layer: d for d in clf.classify((_s("WALLS", 0.4),))}
    assert out["WALLS"].role is Role.WALL_STRUCTURAL
    assert out["WALLS"].source == "llm"
```

- [ ] **Step 2: Run, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_dxf_classifier.py -v`
Expected: FAIL, `ModuleNotFoundError: archiagent.classify.dxf_classifier`

- [ ] **Step 3: Add quote-stripping to the shared reply parser**

In `archiagent/classify/llm_classifier.py`, inside `decisions_from_reply`, normalise the name before it is used as a key:

```python
        name = entry.get("name")
        if not isinstance(name, str):
            raise LLMSchemaError(f"layer entry has no string name: {entry!r}")
        # Our prompt JSON-quotes layer names so that names with spaces are
        # unambiguous. Some models echo those quotes back inside the value.
        # Matching is exact against the inventory, so an echoed quote would
        # silently default EVERY layer to ignore.
        name = name.strip()
        if len(name) >= 2 and name[0] == name[-1] and name[0] in ('"', "'"):
            name = name[1:-1]
```

- [ ] **Step 4: Add the vision method to the port and both adapters**

`archiagent/llm/client.py`:

```python
class VisionUnsupported(RuntimeError):
    """This client cannot accept images."""


class LLMClient(Protocol):
    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict: ...

    def classify_json_vision(self, *, system: str, user: str, schema: dict,
                             images: list[tuple[str, bytes]],
                             max_tokens: int = 2048) -> dict: ...
```

Anthropic adapter: each image becomes a content block
`{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}`,
preceded by a `{"type": "text", "text": label}` block, with the user prompt last.

OpenAI-compatible adapter: each image becomes
`{"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}`
in the user message's content array, again label-then-image, prompt last.

Both raise `LLMUnavailable` on transport failure exactly as `classify_json` does,
so the caller's error handling is unchanged.

- [ ] **Step 5: Implement `DxfLayerClassifier`**

Stage 1 → `decisions_from_reply`. Then `select_for_escalation`. If `vision` is
false, emit `layer_escalation_skipped` per candidate and return stage 1. If
true, render, build the image list `[("reference", ref_bytes), (f"layer {name}", bytes), ...]`,
call `classify_json_vision`, parse with `decisions_from_reply` restricted to
the escalated layers, and splice the revisions in, emitting `layer_role_revised`
for each role that actually changed. Any exception from rendering, transport
or parsing is caught, reported as an issue, and stage 1 is returned unchanged.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dxf_classifier.py tests/test_llm_classifier.py -v`
Expected: all pass, including the existing W1 parser tests.

- [ ] **Step 7: Commit**

```bash
git add archiagent/llm archiagent/classify/dxf_classifier.py archiagent/classify/llm_classifier.py tests/test_dxf_classifier.py
git commit -m "feat: vision transport and the two-stage DXF classifier"
```

---

### Task 7: CLI and pipeline wiring

**Files:**
- Modify: `archiagent/cli.py`
- Modify: `archiagent/pipeline.py`
- Create: `tests/test_cli_dxf.py`

**Interfaces:**
- Produces:
  - CLI flags `--dxfFilePath PATH`, `--vision`, `--units-per-foot FLOAT`
  - `pipeline.extract_from_dxf(ps: PrimitiveSet, classifier, *, units_per_foot, wall_height_ft=10.0) -> BuildingModel` — takes an already-loaded PrimitiveSet, mirroring `extract_from_primitives`, so the CLI pays `load_dxf` once and Task 8's tests can drive it directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_dxf.py
import ezdxf
import pytest
from archiagent.cli import EXIT_OK, EXIT_PIPELINE, EXIT_USAGE, main


def _dxf(tmp_path, insunits=1):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for y in (0, 96):                       # two faces 8in apart -> one wall
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "WALLS"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def test_neither_input_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_both_inputs_is_a_usage_error(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["some.pdf", "--dxfFilePath", str(d), str(tmp_path / "o.ifc")])
    assert rc == EXIT_USAGE
    assert "one of" in capsys.readouterr().err.lower()


def test_dxf_inspect_lists_layers(tmp_path, capsys):
    d = _dxf(tmp_path)
    assert main(["--dxfFilePath", str(d), "--inspect"]) == EXIT_OK
    assert "WALLS" in capsys.readouterr().out


def test_dxf_walls_override_builds_an_ifc(tmp_path):
    d = _dxf(tmp_path)
    out = tmp_path / "o.ifc"
    assert main(["--dxfFilePath", str(d), str(out), "--walls", "WALLS"]) == EXIT_OK
    assert out.exists()


def test_units_per_foot_overrides_the_header(tmp_path, capsys):
    d = _dxf(tmp_path, insunits=2)          # header lies: says feet
    assert main(["--dxfFilePath", str(d), "--inspect",
                 "--units-per-foot", "12"]) == EXIT_OK


def test_vision_flag_is_off_by_default(tmp_path, monkeypatch):
    """ARCHIAGENT_VISION unset and no --vision means stage 1 only."""
    monkeypatch.delenv("ARCHIAGENT_VISION", raising=False)
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=False) is False


def test_vision_env_var_enables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "1")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=False) is True


def test_explicit_flag_wins_over_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "0")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=True) is True


def test_a_bad_dxf_wall_name_is_caught(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["--dxfFilePath", str(d), str(tmp_path / "o.ifc"),
               "--walls", "NOPE"])
    assert rc == EXIT_USAGE
    assert "NOPE" in capsys.readouterr().err
```

- [ ] **Step 2: Run, watch it fail**

Run: `.venv/bin/python -m pytest tests/test_cli_dxf.py -v`
Expected: FAIL — flags do not exist.

- [ ] **Step 3: Implement**

In `archiagent/cli.py`:
- Change the `pdf` positional to `nargs="?"` and add `--dxfFilePath` (exact spelling — the user asked for it), `--vision`, `--units-per-foot`.
- Require **exactly one** of {positional PDF, `--dxfFilePath`}; neither or both is `EXIT_USAGE` with a message naming both options.
- Add `def _vision_enabled(vision_flag: bool) -> bool` — returns `True` when the flag is set, else `os.environ.get("ARCHIAGENT_VISION", "") not in ("", "0")`.
- On the DXF path: `load_dxf` → `build_dxf_inventory` → check `--walls` names against real layer names exactly as the PDF path does → classify → `extract_from_dxf`.

In `archiagent/pipeline.py`, add:

```python
def extract_from_dxf(ps, classifier, *, units_per_foot,
                     wall_height_ft: float = 10.0) -> BuildingModel:
    """DXF path: units are known, so scale resolution is skipped entirely.

    The PDF path infers units_per_foot from printed dimension text and gates
    it on R1. A DXF declares its units, so there is nothing to infer and no
    residual to gate -- ScaleResult records the supplied value with an empty
    residual set.
    """
```

It mirrors `extract_from_primitives` but builds `ScaleResult(units_per_foot=units_per_foot, convention="clear", residuals_in=(), max_residual_in=0.0, matched_count=0, total_dimensions=0)` instead of calling `resolve_scale`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_cli_dxf.py tests/test_cli.py -v`
Expected: all pass — the existing PDF CLI tests must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add archiagent/cli.py archiagent/pipeline.py tests/test_cli_dxf.py
git commit -m "feat: --dxfFilePath, --vision and --units-per-foot"
```

---

### Task 8: Integration against the real drawings, and the verification record

**Files:**
- Create: `tests/test_dxf_end_to_end.py`
- Create: `docs/superpowers/reports/2026-08-30-dxf-verification.md`

- [ ] **Step 1: Write the integration tests**

```python
# tests/test_dxf_end_to_end.py
import os
from pathlib import Path

import pytest

FIX = os.environ.get("ARCHIAGENT_FIXTURES")
pytestmark = pytest.mark.skipif(not FIX, reason="ARCHIAGENT_FIXTURES not set")


@pytest.fixture
def floor_plan_dxf():
    p = Path(FIX) / "dxf" / "Floor Plan.dxf"
    if not p.exists():
        pytest.skip(f"{p} not present")
    return p


def test_layer_zero_is_escalated_by_its_share(floor_plan_dxf):
    """54.9% of this drawing sits on layer '0', and the walls are there."""
    from archiagent.classify.dxf_inventory import build_dxf_inventory
    from archiagent.classify.escalate import escalation_candidates
    from archiagent.classify.layers import LayerDecision
    from archiagent.classify.roles import Role
    from archiagent.ingest.dxf_vector import load_dxf

    ps, _ = load_dxf(floor_plan_dxf, units_per_foot=12.0)
    stats = build_dxf_inventory(floor_plan_dxf, ps)
    zero = next(s for s in stats if s.name == "0")
    assert zero.entity_share > 0.5

    decisions = tuple(LayerDecision(s.name, Role.IGNORE, 0.95, "", "llm")
                      for s in stats)
    triggers = dict(escalation_candidates(decisions, stats))
    assert triggers.get("0") == "large_non_wall_layer"


def test_walls_come_out_of_layer_zero(floor_plan_dxf, tmp_path):
    """Measured on 2026-08-30: layer '0' yields 748 walls and 27 rooms;
    the layer named 'WALLS' yields 57 walls and none."""
    from archiagent.classify.layers import StubClassifier
    from archiagent.classify.roles import Role
    from archiagent.ingest.dxf_vector import load_dxf
    from archiagent.pipeline import extract_from_dxf

    ps, upf = load_dxf(floor_plan_dxf, units_per_foot=12.0)
    roles = {"0": (Role.WALL_STRUCTURAL, 0.9), "WALLS": (Role.WALL_STRUCTURAL, 0.9)}
    model = extract_from_dxf(ps, StubClassifier(roles), units_per_foot=upf)
    assert len(model.walls) > 500
    assert len(model.spaces) >= 20


def test_no_scale_gate_failure_on_the_dxf_path(floor_plan_dxf):
    """The PDF twin of this drawing fails the R1 gate outright. The DXF path
    skips scale resolution, so that failure cannot occur."""
    from archiagent.classify.layers import StubClassifier
    from archiagent.classify.roles import Role
    from archiagent.ingest.dxf_vector import load_dxf
    from archiagent.pipeline import extract_from_dxf

    ps, upf = load_dxf(floor_plan_dxf, units_per_foot=12.0)
    model = extract_from_dxf(ps, StubClassifier({"0": (Role.WALL_STRUCTURAL, 0.9)}),
                             units_per_foot=upf)
    assert [i for i in model.issues if i.code == "scale_gate_failed"] == []
```

- [ ] **Step 2: Run with fixtures set**

Run:
```bash
ARCHIAGENT_FIXTURES=/Users/adityamathur/Desktop/blender-experiment/input-floorplans \
  .venv/bin/python -m pytest tests/test_dxf_end_to_end.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: every pre-existing test still passes. The PDF path must be untouched.

- [ ] **Step 4: Write the verification record**

Create `docs/superpowers/reports/2026-08-30-dxf-verification.md` recording, for each of the four DXFs: resolved units (and whether `$INSUNITS` was trusted or overridden), layer count, the layers stage 1 chose as walls, which layers escalated and why, and the resulting wall/space counts. Include an "Outstanding: live vision verification" section listing the exact `--vision` commands a human with credentials must run, since no test may make a live call.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dxf_end_to_end.py docs/superpowers/reports/2026-08-30-dxf-verification.md
git commit -m "test: DXF integration against the real drawings; verification record"
```
