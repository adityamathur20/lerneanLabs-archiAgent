# Floorplan → IFC Pipeline (M1–M5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a layer-separated vector floorplan PDF into a dimensionally-accurate parametric IFC4 model with fully resolved wall junctions.

**Architecture:** A deterministic vector pipeline. PyMuPDF reads exact line geometry and text with their CAD layer tags; layer *names* are classified by an LLM into a fixed role vocabulary (the only LLM step in M1–M5); scale is recovered by consensus across printed dimensions; paired parallel lines become wall centerlines with measured thickness; junction resolution heals the wall graph; `shapely.polygonize` yields rooms; `ifcopenshell.api` authors IFC4 headlessly. The LLM never emits a coordinate and never writes IFC or `bpy`.

**Tech Stack:** Python 3.14, PyMuPDF 1.28.2, shapely 2.1.2, networkx 3.6.1, ifcopenshell 0.8.5, pytest 9.1.1. Blender 5.1.2 + Bonsai for viewing only (Task 14).

**Spec:** `PLAN.md` (repo root)

## Global Constraints

Every task's requirements implicitly include this section.

- **The `archiagent` package MUST NOT import `bpy`.** Stages 0–8 run on system Python 3.14.7; Blender ships Python 3.13.9. Only `archiagent/blender/` may assume Blender, and it is never imported by the core package.
- **Client confidentiality: the sample floorplan PDFs are NEVER committed.** They are client drawings (named architect and client) and this repo is public. Tests locate them via the `ARCHIAGENT_FIXTURES` environment variable, defaulting to `../input-floorplans`, and **skip** when absent. `.gitignore` must exclude `*.pdf`, `*.dwg`, `*.dxf`.
- **Units:** all internal geometry is in **feet**, with y increasing upward. PDF coordinates are in points with y increasing downward; the flip happens once, in ingest.
- **Snap tolerance default: 1.0 inch.** Expressed in inches, never pixels.
- **Junction extension budget default: 6.0 inches.** A gap larger than this is a doorway or a genuine discontinuity and MUST NOT be closed.
- **Wall thickness defaults, applied only when measurement fails:** interior/partition **4 in**, exterior **8 in**. Always recorded with `thickness_source="default"`.
- **Scale gate (R1): maximum residual < 2.0 inches across at least 3 matched dimensions.** Failing this raises, it does not warn.
- **Printed dimensions are CLEAR (face-to-face) by default.**
- **Layer-separated PDFs only.** A PDF with no Optional Content Groups raises `NoLayersError` — fail fast, never degrade silently.
- **IFC schema is `IFC4`.**
- **ifcopenshell 0.8.5 API traps** (all verified — see `PLAN.md` §4):
  - `ifcopenshell.api.void.*` is now `feature.*` (`feature.add_feature`, `feature.add_filling`)
  - `ShapeBuilder.rectangle` anchors the profile at its **corner**, not its centre — pass `position=(0.0, -thickness/2)`
  - `IfcSpace` uses `aggregate.assign_object`, not `spatial.assign_container`
- **Commit after every task.** Conventional commit prefixes (`feat:`, `test:`, `chore:`).

---

## File Structure

| File | Responsibility |
|---|---|
| `archiagent/primitives.py` | `Primitive`, `TextItem`, `PrimitiveSet` — the format-neutral internal representation |
| `archiagent/ingest/pdf_vector.py` | PyMuPDF front-end; `NoLayersError`; the single y-flip |
| `archiagent/classify/roles.py` | `Role` vocabulary enum |
| `archiagent/classify/inventory.py` | Per-layer statistics for the LLM |
| `archiagent/classify/layers.py` | Classifier protocol + role lookup helpers |
| `archiagent/scale/dimensions.py` | Dimension-string parsing (feet-inches) |
| `archiagent/scale/resolve.py` | Consensus scale + residual table + the R1 gate |
| `archiagent/geometry/walls.py` | Paired-line wall detection |
| `archiagent/geometry/junctions.py` | R2: clustering, extension, splitting, merging, pruning |
| `archiagent/geometry/spaces.py` | `shapely.polygonize` room detection |
| `archiagent/model.py` | `BuildingModel`, `Issue` |
| `archiagent/validate.py` | Stage 7 validators |
| `archiagent/ifc/author.py` | Headless IFC4 authoring incl. `IfcRelConnectsPathElements` |
| `archiagent/blender/load.py` | Bonsai load script (runs inside Blender only) |

---

## Task 1: Package scaffolding and the primitive types

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `archiagent/__init__.py`, `archiagent/primitives.py`
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/test_primitives.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Primitive(kind, coords, layer, stroke_width, color)`, `Primitive.segments() -> list[tuple[Pt, Pt]]`, `TextItem(text, bbox, layer)`, `TextItem.center() -> Pt`, `PrimitiveSet(primitives, texts, width, height, source_path, source_sha256)`, `PrimitiveSet.by_layer(names) -> list[Primitive]`, `PrimitiveSet.layer_names() -> set[str]`. Type alias `Pt = tuple[float, float]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_primitives.py`:

```python
from archiagent.primitives import Primitive, TextItem, PrimitiveSet


def test_line_primitive_yields_one_segment():
    p = Primitive(kind="line", coords=((0.0, 0.0), (3.0, 4.0)),
                  layer="WALLS", stroke_width=0.72, color=(0.0, 0.0, 0.0))
    assert p.segments() == [((0.0, 0.0), (3.0, 4.0))]


def test_rect_primitive_yields_four_closed_segments():
    p = Primitive(kind="rect", coords=((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),
                  layer="WALLS", stroke_width=None, color=None)
    segs = p.segments()
    assert len(segs) == 4
    assert segs[0] == ((0.0, 0.0), (2.0, 0.0))
    assert segs[3] == ((0.0, 1.0), (0.0, 0.0))


def test_text_item_center():
    t = TextItem(text="14'-5\"", bbox=(10.0, 20.0, 30.0, 40.0), layer="DIM")
    assert t.center() == (20.0, 30.0)


def test_primitive_set_by_layer_is_case_insensitive():
    a = Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "WALLS", None, None)
    b = Primitive("line", ((0.0, 1.0), (1.0, 1.0)), "furn", None, None)
    ps = PrimitiveSet(primitives=(a, b), texts=(), width=100.0, height=200.0,
                      source_path="x.pdf", source_sha256="deadbeef")
    assert ps.by_layer({"walls"}) == [a]
    assert ps.layer_names() == {"WALLS", "furn"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_primitives.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent'`

- [ ] **Step 3: Write the implementation**

Create `.gitignore`:

```
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
*.egg-info/

# Client drawings — NEVER commit (see PLAN.md, this repo is public)
*.pdf
*.dwg
*.dxf
input-floorplans/
```

Create `pyproject.toml`:

```toml
[project]
name = "archiagent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pymupdf>=1.28",
    "shapely>=2.1",
    "networkx>=3.4",
    "ifcopenshell>=0.8.5",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `archiagent/__init__.py` (empty file).

Create `archiagent/primitives.py`:

```python
"""Format-neutral internal representation of a drawing.

Every ingest front-end (PDF, DXF, DWG, and later raster) emits a
PrimitiveSet, so no downstream stage knows which format it came from.

Coordinate convention: source units (PDF points), y increasing UPWARD.
The flip from PDF's downward y happens once, in ingest.
"""

from __future__ import annotations

from dataclasses import dataclass

Pt = tuple[float, float]


@dataclass(frozen=True)
class Primitive:
    """One drawn path, tagged with the CAD layer it came from."""

    kind: str  # "line" | "rect" | "curve" | "fill"
    coords: tuple[Pt, ...]
    layer: str
    stroke_width: float | None
    color: tuple[float, float, float] | None

    def segments(self) -> list[tuple[Pt, Pt]]:
        """Explode into straight segments. Rects close back to the start."""
        if len(self.coords) < 2:
            return []
        pairs = [(self.coords[i], self.coords[i + 1])
                 for i in range(len(self.coords) - 1)]
        if self.kind == "rect":
            pairs.append((self.coords[-1], self.coords[0]))
        return pairs


@dataclass(frozen=True)
class TextItem:
    """A text string with its exact bounding box — no OCR involved."""

    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    layer: str

    def center(self) -> Pt:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


@dataclass(frozen=True)
class PrimitiveSet:
    primitives: tuple[Primitive, ...]
    texts: tuple[TextItem, ...]
    width: float
    height: float
    source_path: str
    source_sha256: str

    def layer_names(self) -> set[str]:
        return {p.layer for p in self.primitives} | {t.layer for t in self.texts}

    def by_layer(self, names: set[str]) -> list[Primitive]:
        """Select primitives whose layer matches any of `names`, case-insensitively."""
        wanted = {n.lower() for n in names}
        return [p for p in self.primitives if p.layer.lower() in wanted]
```

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py`:

```python
"""Shared fixtures.

The sample floorplans are CLIENT DRAWINGS and are never committed to this
repo. Point ARCHIAGENT_FIXTURES at a directory holding them; tests that
need them skip when it is absent.
"""

import os
from pathlib import Path

import pytest

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "input-floorplans"


def fixtures_dir() -> Path:
    return Path(os.environ.get("ARCHIAGENT_FIXTURES", DEFAULT_FIXTURES))


@pytest.fixture(scope="session")
def demolition_pdf() -> Path:
    p = fixtures_dir() / "DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf"
    if not p.exists():
        pytest.skip(f"fixture not available: {p}")
    return p


@pytest.fixture(scope="session")
def ground_floor_pdf() -> Path:
    p = fixtures_dir() / "GROUND FLOOR PLAN_WORKING REVISED.pdf"
    if not p.exists():
        pytest.skip(f"fixture not available: {p}")
    return p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pip install -e ".[dev]" && python3 -m pytest tests/test_primitives.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore archiagent/ tests/
git commit -m "feat: package scaffolding and format-neutral primitive types"
```

---

## Task 2: PDF vector ingest

**Files:**
- Create: `archiagent/ingest/__init__.py`, `archiagent/ingest/pdf_vector.py`
- Create: `tests/test_pdf_vector.py`

**Interfaces:**
- Consumes: `Primitive`, `TextItem`, `PrimitiveSet` from Task 1
- Produces: `load_pdf(path: str | Path, page: int = 0) -> PrimitiveSet`, `NoLayersError(Exception)`

**Why the y-flip lives here:** PDF y grows downward, building coordinates grow upward. Doing it once, at the boundary, means no later stage has to remember.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pdf_vector.py`:

```python
import pytest

from archiagent.ingest.pdf_vector import NoLayersError, load_pdf


def test_loads_layers_from_real_drawing(demolition_pdf):
    ps = load_pdf(demolition_pdf)
    names = {n.lower() for n in ps.layer_names()}
    assert "wall" in names
    assert "furniture" in names
    assert len(ps.primitives) > 1000


def test_extracts_live_text_with_positions(demolition_pdf):
    ps = load_pdf(demolition_pdf)
    strings = [t.text for t in ps.texts]
    assert "BEDROOM" in strings
    assert any("14'-5" in s for s in strings)


def test_y_axis_is_flipped_upward(demolition_pdf):
    """BEDROOM-1 is drawn near the TOP of the page. After the flip its y
    must be in the upper half, i.e. greater than half the page height."""
    ps = load_pdf(demolition_pdf)
    bedroom = next(t for t in ps.texts if t.text == "BEDROOM")
    assert bedroom.center()[1] > ps.height / 2


def test_raises_when_pdf_has_no_layers(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line(pymupdf.Point(0, 0), pymupdf.Point(100, 100))
    flat = tmp_path / "flat.pdf"
    doc.save(flat)
    doc.close()

    with pytest.raises(NoLayersError):
        load_pdf(flat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pdf_vector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.ingest'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/ingest/__init__.py` (empty file).

Create `archiagent/ingest/pdf_vector.py`:

```python
"""PyMuPDF front-end: read exact vector geometry and text from a
layer-separated CAD-exported PDF.

v1 supports layer-separated PDFs only. A flattened PDF raises
NoLayersError rather than degrading silently — see PLAN.md §13.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

from archiagent.primitives import Primitive, PrimitiveSet, TextItem


class NoLayersError(Exception):
    """The PDF carries no Optional Content Groups, so layers cannot be used."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pdf(path: str | Path, page: int = 0) -> PrimitiveSet:
    path = Path(path)
    doc = pymupdf.open(path)
    try:
        if not doc.get_ocgs():
            raise NoLayersError(
                f"{path.name} has no Optional Content Groups (CAD layers). "
                "v1 supports layer-separated PDFs only."
            )
        pg = doc[page]
        height = pg.rect.height

        def fy(y: float) -> float:
            """Flip PDF's downward y into upward building y."""
            return height - y

        primitives: list[Primitive] = []
        for d in pg.get_drawings():
            layer = d.get("layer") or ""
            width = d.get("width")
            color = d.get("color")
            for item in d["items"]:
                if item[0] == "l":
                    coords = ((item[1].x, fy(item[1].y)),
                              (item[2].x, fy(item[2].y)))
                    primitives.append(
                        Primitive("line", coords, layer, width, color))
                elif item[0] == "re":
                    r = item[1]
                    coords = ((r.x0, fy(r.y0)), (r.x1, fy(r.y0)),
                              (r.x1, fy(r.y1)), (r.x0, fy(r.y1)))
                    primitives.append(
                        Primitive("rect", coords, layer, width, color))

        texts: list[TextItem] = []
        for x0, y0, x1, y1, word, *_ in pg.get_text("words"):
            texts.append(TextItem(text=word,
                                  bbox=(x0, fy(y1), x1, fy(y0)),
                                  layer=""))

        return PrimitiveSet(
            primitives=tuple(primitives),
            texts=tuple(texts),
            width=pg.rect.width,
            height=height,
            source_path=str(path),
            source_sha256=_sha256(path),
        )
    finally:
        doc.close()
```

Note: `get_text("words")` does not report a layer, so `TextItem.layer` is `""` here. Layer-aware text selection is not needed by any M1–M5 task; dimension extraction (Task 5) filters by pattern, not layer.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pdf_vector.py -v`
Expected: 4 passed (or skipped if `ARCHIAGENT_FIXTURES` is unset)

- [ ] **Step 5: Commit**

```bash
git add archiagent/ingest/ tests/test_pdf_vector.py
git commit -m "feat: PyMuPDF vector ingest with fail-fast on flattened PDFs"
```

---

## Task 3: Layer inventory

**Files:**
- Create: `archiagent/classify/__init__.py`, `archiagent/classify/inventory.py`
- Create: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `PrimitiveSet` from Task 1
- Produces: `LayerStats(name, path_count, segment_count, axis_aligned_fraction, stroke_widths, dominant_colors, bbox, length_p10, length_p50, length_p90)`, `build_inventory(ps: PrimitiveSet) -> tuple[LayerStats, ...]`

This is the LLM's *only* input in Task 4. It must be compact enough to fit comfortably in a prompt and discriminative enough to classify on.

- [ ] **Step 1: Write the failing test**

Create `tests/test_inventory.py`:

```python
from archiagent.classify.inventory import build_inventory
from archiagent.primitives import Primitive, PrimitiveSet


def _ps(prims):
    return PrimitiveSet(primitives=tuple(prims), texts=(), width=100.0,
                        height=100.0, source_path="x", source_sha256="y")


def test_inventory_groups_by_layer_and_counts():
    prims = [
        Primitive("line", ((0.0, 0.0), (10.0, 0.0)), "WALLS", 0.72, (0.0, 0.0, 0.0)),
        Primitive("line", ((0.0, 1.0), (10.0, 1.0)), "WALLS", 0.72, (0.0, 0.0, 0.0)),
        Primitive("line", ((0.0, 0.0), (3.0, 4.0)), "FURN", 0.29, (1.0, 0.0, 0.0)),
    ]
    inv = {s.name: s for s in build_inventory(_ps(prims))}
    assert inv["WALLS"].path_count == 2
    assert inv["WALLS"].segment_count == 2
    assert inv["FURN"].path_count == 1


def test_axis_aligned_fraction_discriminates_walls_from_diagonals():
    walls = [Primitive("line", ((0.0, float(i)), (10.0, float(i))),
                       "WALLS", 0.72, None) for i in range(4)]
    hatch = [Primitive("line", ((0.0, float(i)), (5.0, float(i) + 5.0)),
                       "HATCH", 0.14, None) for i in range(4)]
    inv = {s.name: s for s in build_inventory(_ps(walls + hatch))}
    assert inv["WALLS"].axis_aligned_fraction == 1.0
    assert inv["HATCH"].axis_aligned_fraction == 0.0


def test_length_percentiles_and_bbox():
    prims = [Primitive("line", ((0.0, 0.0), (float(n), 0.0)), "L", None, None)
             for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    stats = build_inventory(_ps(prims))[0]
    assert stats.length_p50 == 6.0
    assert stats.bbox == (0.0, 0.0, 10.0, 0.0)


def test_dominant_colors_are_ranked():
    prims = ([Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "L", None, (1.0, 0.0, 0.0))] * 3
             + [Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "L", None, (0.0, 0.0, 1.0))])
    stats = build_inventory(_ps(prims))[0]
    assert stats.dominant_colors[0] == (1.0, 0.0, 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_inventory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.classify'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/classify/__init__.py` (empty file).

Create `archiagent/classify/inventory.py`:

```python
"""Summarise each CAD layer into compact statistics.

This is the ONLY thing the layer classifier (Task 4) sees. It must be
small enough to prompt with and discriminative enough to classify on:
walls are long and axis-aligned, hatch is short and diagonal, dimension
layers are thin and numerous.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass

from archiagent.primitives import PrimitiveSet

AXIS_TOL = 0.02  # units; below this a segment counts as axis-aligned


@dataclass(frozen=True)
class LayerStats:
    name: str
    path_count: int
    segment_count: int
    axis_aligned_fraction: float
    stroke_widths: tuple[float, ...]
    dominant_colors: tuple[tuple[float, float, float], ...]
    bbox: tuple[float, float, float, float]
    length_p10: float
    length_p50: float
    length_p90: float


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def build_inventory(ps: PrimitiveSet) -> tuple[LayerStats, ...]:
    by_layer: dict[str, list] = collections.defaultdict(list)
    for p in ps.primitives:
        by_layer[p.layer].append(p)

    out: list[LayerStats] = []
    for name, prims in sorted(by_layer.items()):
        lengths: list[float] = []
        axis = 0
        total = 0
        widths: collections.Counter = collections.Counter()
        colors: collections.Counter = collections.Counter()
        xs: list[float] = []
        ys: list[float] = []

        for p in prims:
            if p.stroke_width is not None:
                widths[round(p.stroke_width, 2)] += 1
            if p.color is not None:
                colors[p.color] += 1
            for (x0, y0), (x1, y1) in p.segments():
                total += 1
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                if dx < AXIS_TOL or dy < AXIS_TOL:
                    axis += 1
                lengths.append(math.hypot(dx, dy))
                xs.extend((x0, x1))
                ys.extend((y0, y1))

        lengths.sort()
        out.append(LayerStats(
            name=name,
            path_count=len(prims),
            segment_count=total,
            axis_aligned_fraction=(axis / total) if total else 0.0,
            stroke_widths=tuple(w for w, _ in widths.most_common(4)),
            dominant_colors=tuple(c for c, _ in colors.most_common(3)),
            bbox=(min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0),
            length_p10=_percentile(lengths, 0.10),
            length_p50=_percentile(lengths, 0.50),
            length_p90=_percentile(lengths, 0.90),
        ))
    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_inventory.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/ tests/test_inventory.py
git commit -m "feat: per-layer statistics inventory for classification"
```

---

## Task 4: Role vocabulary and layer classification

**Files:**
- Create: `archiagent/classify/roles.py`, `archiagent/classify/layers.py`
- Create: `tests/test_layers.py`

**Interfaces:**
- Consumes: `LayerStats` from Task 3
- Produces: `Role` (str-enum), `Classification = dict[str, tuple[Role, float]]`, `LayerClassifier` protocol with `classify(stats: tuple[LayerStats, ...]) -> Classification`, `layers_for_roles(c: Classification, roles: set[Role]) -> set[str]`, `WALL_ROLES: frozenset[Role]`

**Design note:** the classifier is injected as a protocol so every downstream test is deterministic. The real LLM-backed implementation is out of scope for M1–M5 unit tests; a `StubClassifier` covers the tests, and Task 14 wires the real one manually.

- [ ] **Step 1: Write the failing test**

Create `tests/test_layers.py`:

```python
import pytest

from archiagent.classify.layers import (WALL_ROLES, Classification,
                                        layers_for_roles)
from archiagent.classify.roles import Role


def test_role_values_are_stable_strings():
    assert Role.WALL_STRUCTURAL.value == "wall_structural"
    assert Role.BEAM_OVERHEAD.value == "beam_overhead"
    assert Role("ignore") is Role.IGNORE


def test_wall_roles_include_structural_and_partition_but_not_beams():
    assert Role.WALL_STRUCTURAL in WALL_ROLES
    assert Role.WALL_PARTITION in WALL_ROLES
    assert Role.BEAM_OVERHEAD not in WALL_ROLES


def test_layers_for_roles_selects_matching_layers():
    c: Classification = {
        "WALLS": (Role.WALL_STRUCTURAL, 0.98),
        "walll": (Role.WALL_PARTITION, 0.9),
        "BEAM": (Role.BEAM_OVERHEAD, 0.95),
        "0": (Role.IGNORE, 0.5),
    }
    assert layers_for_roles(c, WALL_ROLES) == {"WALLS", "walll"}
    assert layers_for_roles(c, {Role.BEAM_OVERHEAD}) == {"BEAM"}


def test_layers_for_roles_honours_a_confidence_floor():
    c: Classification = {
        "WALLS": (Role.WALL_STRUCTURAL, 0.98),
        "maybe": (Role.WALL_STRUCTURAL, 0.30),
    }
    assert layers_for_roles(c, WALL_ROLES, min_confidence=0.5) == {"WALLS"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_layers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.classify.roles'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/classify/roles.py`:

```python
"""The fixed role vocabulary a drawing's private layer names map onto.

Layer names are a per-drawing vocabulary (WALLS / WALL / Wall / walll /
A-Wall all appear across our three samples). Mapping them onto this fixed
set is the LLM's job — see PLAN.md §7 Stage 1.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    WALL_STRUCTURAL = "wall_structural"
    WALL_PARTITION = "wall_partition"
    COLUMN = "column"
    BEAM_OVERHEAD = "beam_overhead"
    DOOR = "door"
    WINDOW = "window"
    STAIR = "stair"
    RAILING = "railing"
    DIMENSION = "dimension"
    TEXT_LABEL = "text_label"
    GRID = "grid"
    ANNOTATION = "annotation"
    TITLE_BLOCK = "title_block"
    FURNITURE = "furniture"
    ELECTRICAL = "electrical"
    PLUMBING = "plumbing"
    VEHICLE = "vehicle"
    LANDSCAPE = "landscape"
    IGNORE = "ignore"
```

Create `archiagent/classify/layers.py`:

```python
"""Layer classification: the pipeline's only LLM step in M1-M5.

The classifier is a protocol so downstream tests stay deterministic. It
sees LayerStats only — never coordinates, and it never returns any.
"""

from __future__ import annotations

from typing import Protocol

from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role

Classification = dict[str, tuple[Role, float]]

WALL_ROLES: frozenset[Role] = frozenset({Role.WALL_STRUCTURAL, Role.WALL_PARTITION})


class LayerClassifier(Protocol):
    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        """Map each layer name to (role, confidence in 0..1)."""
        ...


class StubClassifier:
    """Deterministic classifier for tests and for replaying a saved mapping."""

    def __init__(self, mapping: Classification) -> None:
        self._mapping = dict(mapping)

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        return {s.name: self._mapping.get(s.name, (Role.IGNORE, 0.0)) for s in stats}


def layers_for_roles(classification: Classification, roles: set[Role] | frozenset[Role],
                     min_confidence: float = 0.0) -> set[str]:
    """Layer names whose assigned role is in `roles` and clears the floor."""
    return {name for name, (role, conf) in classification.items()
            if role in roles and conf >= min_confidence}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_layers.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/classify/roles.py archiagent/classify/layers.py tests/test_layers.py
git commit -m "feat: role vocabulary and injectable layer classifier"
```

---

## Task 5: Dimension-string parsing

**Files:**
- Create: `archiagent/scale/__init__.py`, `archiagent/scale/dimensions.py`
- Create: `tests/test_dimensions.py`

**Interfaces:**
- Consumes: `PrimitiveSet`, `TextItem` from Task 1
- Produces: `parse_dimension(s: str) -> float | None` (returns **feet**), `DimensionText(text, feet, center)`, `extract_dimensions(ps: PrimitiveSet) -> tuple[DimensionText, ...]`

**Real strings observed in the samples** (these are the test cases): `14'-5"X`, `13'-10"`, `7'-0"`, `5'-0"`, `3'-0"`, `11'6"`, `9'4"`, `4'6'` (a typo for `4'6"`, which must still parse), `19'2"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dimensions.py`:

```python
import pytest

from archiagent.scale.dimensions import DimensionText, extract_dimensions, parse_dimension
from archiagent.primitives import PrimitiveSet, TextItem


@pytest.mark.parametrize("text,feet", [
    ("14'-5\"", 14 + 5 / 12),
    ("13'-10\"", 13 + 10 / 12),
    ("7'-0\"", 7.0),
    ("3'-0\"", 3.0),
    ("11'6\"", 11.5),
    ("9'4\"", 9 + 4 / 12),
    ("4'6'", 4.5),          # typo in the source drawing; must still parse
    ("14'-5\"X", 14 + 5 / 12),  # trailing X from "14'-5"X 13'-10""
    ("12'", 12.0),
])
def test_parses_feet_inches(text, feet):
    assert parse_dimension(text) == pytest.approx(feet)


@pytest.mark.parametrize("text", ["BEDROOM", "TOILET", "", "X", "WIDE", "-1", ":-"])
def test_rejects_non_dimensions(text):
    assert parse_dimension(text) is None


def test_rejects_implausible_magnitudes():
    assert parse_dimension("0'-0\"") is None
    assert parse_dimension("9999'") is None


def test_extract_dimensions_keeps_position():
    ps = PrimitiveSet(
        primitives=(),
        texts=(TextItem("BEDROOM", (0.0, 0.0, 10.0, 5.0), ""),
               TextItem("14'-5\"X", (0.0, 10.0, 20.0, 15.0), "")),
        width=100.0, height=100.0, source_path="x", source_sha256="y")
    dims = extract_dimensions(ps)
    assert len(dims) == 1
    assert dims[0].feet == pytest.approx(14 + 5 / 12)
    assert dims[0].center == (10.0, 12.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dimensions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.scale'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/scale/__init__.py` (empty file).

Create `archiagent/scale/dimensions.py`:

```python
"""Parse printed dimension strings into feet.

Source drawings are inconsistent: 14'-5", 11'6", and the typo 4'6' all
appear in our samples and all mean the same kind of thing. Parse
permissively, then reject implausible magnitudes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from archiagent.primitives import Pt, PrimitiveSet

# <feet> ' [ - ] [ <inches> ] [ " or ' ]
_FEET_INCHES = re.compile(r"""^(\d{1,3})\s*'\s*-?\s*(\d{1,2})?\s*["']?$""")

MIN_FEET = 0.5
MAX_FEET = 500.0


@dataclass(frozen=True)
class DimensionText:
    text: str
    feet: float
    center: Pt


def parse_dimension(s: str) -> float | None:
    """Return the dimension in feet, or None if this is not a dimension."""
    t = s.strip().upper().rstrip("X").strip()
    m = _FEET_INCHES.match(t)
    if not m:
        return None
    feet = float(m.group(1))
    if m.group(2) is not None:
        inches = float(m.group(2))
        if inches >= 12:
            return None
        feet += inches / 12.0
    if not (MIN_FEET <= feet <= MAX_FEET):
        return None
    return feet


def extract_dimensions(ps: PrimitiveSet) -> tuple[DimensionText, ...]:
    out: list[DimensionText] = []
    for t in ps.texts:
        feet = parse_dimension(t.text)
        if feet is not None:
            out.append(DimensionText(text=t.text, feet=feet, center=t.center()))
    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dimensions.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/scale/ tests/test_dimensions.py
git commit -m "feat: permissive feet-inches dimension parsing"
```

---

## Task 6: Scale resolution and the R1 gate

**Files:**
- Create: `archiagent/scale/resolve.py`
- Create: `tests/test_resolve_scale.py`

**Interfaces:**
- Consumes: `DimensionText` (Task 5), `PrimitiveSet` (Task 1)
- Produces: `ScaleResult(units_per_foot, convention, residuals_in, max_residual_in, matched_count)`, `ScaleGateError(Exception)`, `candidate_runs(ps, layers) -> tuple[float, ...]`, `resolve_scale(dims, runs, max_residual_in=2.0, min_matches=3) -> ScaleResult`

**Why this is clear-basis by construction:** `candidate_runs` measures raw wall-layer segment lengths, and those segments *are* the wall faces. Matching printed dimensions against them therefore compares face-to-face against face-to-face. This is what produced the sub-inch agreement in `PLAN.md` §3.3, and why the convention is recorded as `"clear"`.

**Algorithm:** for every (dimension, run) pair a candidate scale is `run_pt / dim_ft`. The correct scale is the one supported by the most dimensions. Cluster candidates by relative closeness, take the largest cluster, refine by median, then compute each dimension's residual against its best-matching run.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolve_scale.py`:

```python
import pytest

from archiagent.scale.dimensions import DimensionText
from archiagent.scale.resolve import ScaleGateError, ScaleResult, resolve_scale


def _dim(feet: float) -> DimensionText:
    return DimensionText(text=f"{feet}'", feet=feet, center=(0.0, 0.0))


def test_recovers_an_exact_scale():
    scale = 11.861
    dims = [_dim(f) for f in (14.4167, 13.8333, 7.0, 3.0)]
    runs = tuple(f * scale for f in (14.4167, 13.8333, 7.0, 3.0))
    result = resolve_scale(dims, runs)
    assert result.units_per_foot == pytest.approx(scale, rel=1e-6)
    assert result.max_residual_in < 0.01
    assert result.matched_count == 4
    assert result.convention == "clear"


def test_ignores_distractor_runs():
    """Real drawings have thousands of runs that match nothing."""
    scale = 11.861
    dims = [_dim(f) for f in (14.4167, 13.8333, 7.0)]
    runs = tuple([f * scale for f in (14.4167, 13.8333, 7.0)]
                 + [3.1, 7.7, 101.3, 250.0, 999.0])
    result = resolve_scale(dims, runs)
    assert result.units_per_foot == pytest.approx(scale, rel=1e-6)


def test_tolerates_a_small_per_dimension_error():
    scale = 11.861
    feet = (14.4167, 13.8333, 7.0, 3.0)
    runs = tuple(f * scale + delta
                 for f, delta in zip(feet, (0.0, 0.2, -0.7, 0.1)))
    result = resolve_scale([_dim(f) for f in feet], runs)
    assert result.max_residual_in < 2.0


def test_raises_when_residual_exceeds_the_two_inch_gate():
    scale = 11.861
    feet = (14.4167, 13.8333, 7.0)
    # 40pt error on one dimension is ~3.4ft — far outside the gate
    runs = tuple(f * scale for f in (14.4167, 13.8333)) + (7.0 * scale + 40.0,)
    with pytest.raises(ScaleGateError, match="residual"):
        resolve_scale([_dim(f) for f in feet], runs)


def test_raises_when_too_few_dimensions_match():
    with pytest.raises(ScaleGateError, match="matched"):
        resolve_scale([_dim(14.4167), _dim(13.8333)],
                      (14.4167 * 11.861, 13.8333 * 11.861))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_resolve_scale.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.scale.resolve'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/scale/resolve.py`:

```python
"""Recover the source-unit -> foot scale factor by consensus.

R1 (PLAN.md §2): every printed dimension must be reproduced within 2
inches. This module is where that is measured and enforced. Scale error
is proportional, so it dominates the error budget: over a 100ft span a
2in budget means the factor must be right to ~0.17%.

Clear-basis by construction: candidate runs are raw wall-layer segment
lengths, and those segments are the wall FACES. Printed room dimensions
are face-to-face. Like compares with like.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from archiagent.primitives import PrimitiveSet
from archiagent.scale.dimensions import DimensionText

MIN_RUN_UNITS = 10.0  # ignore hatch ticks and glyph fragments


class ScaleGateError(Exception):
    """Scale could not be resolved within the R1 accuracy gate."""


@dataclass(frozen=True)
class ScaleResult:
    units_per_foot: float
    convention: str  # always "clear" in v1
    residuals_in: tuple[float, ...]
    max_residual_in: float
    matched_count: int


def candidate_runs(ps: PrimitiveSet, layers: set[str]) -> tuple[float, ...]:
    """Lengths of axis-aligned segments on the given layers, in source units."""
    out: list[float] = []
    for p in ps.by_layer(layers):
        for (x0, y0), (x1, y1) in p.segments():
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx > 0.02 and dy > 0.02:
                continue  # not axis-aligned
            length = math.hypot(dx, dy)
            if length >= MIN_RUN_UNITS:
                out.append(length)
    return tuple(out)


def resolve_scale(dims: list[DimensionText] | tuple[DimensionText, ...],
                  runs: tuple[float, ...],
                  max_residual_in: float = 2.0,
                  min_matches: int = 3,
                  cluster_rel_tol: float = 0.01) -> ScaleResult:
    """Find the units-per-foot factor supported by the most dimensions."""
    if not dims or not runs:
        raise ScaleGateError("no dimensions or no candidate runs to match")

    candidates = sorted(run / d.feet for d in dims for run in runs if d.feet > 0)
    if not candidates:
        raise ScaleGateError("no scale candidates could be formed")

    # Largest cluster of mutually-close candidates wins.
    best_lo = best_hi = 0
    lo = 0
    for hi in range(len(candidates)):
        while candidates[hi] - candidates[lo] > cluster_rel_tol * candidates[hi]:
            lo += 1
        if hi - lo > best_hi - best_lo:
            best_lo, best_hi = lo, hi
    scale = statistics.median(candidates[best_lo:best_hi + 1])

    # Residual per dimension against its best-matching run.
    residuals: list[float] = []
    for d in dims:
        predicted = d.feet * scale
        nearest = min(runs, key=lambda r: abs(r - predicted))
        residuals.append(abs(nearest - predicted) / scale * 12.0)

    matched = [r for r in residuals if r <= max_residual_in]
    if len(matched) < min_matches:
        raise ScaleGateError(
            f"only {len(matched)} dimensions matched within "
            f"{max_residual_in}in; need {min_matches}")

    worst = max(matched)
    if worst > max_residual_in:
        raise ScaleGateError(
            f"max residual {worst:.2f}in exceeds the {max_residual_in}in gate")

    return ScaleResult(
        units_per_foot=scale,
        convention="clear",
        residuals_in=tuple(round(r, 3) for r in sorted(matched)),
        max_residual_in=round(worst, 3),
        matched_count=len(matched),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_resolve_scale.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/scale/resolve.py tests/test_resolve_scale.py
git commit -m "feat: consensus scale resolution with the 2in R1 gate"
```

---

## Task 7: Paired-line wall detection

**Files:**
- Create: `archiagent/geometry/__init__.py`, `archiagent/geometry/walls.py`
- Create: `tests/test_walls.py`

**Interfaces:**
- Consumes: `PrimitiveSet` (Task 1), `ScaleResult` (Task 6)
- Produces: `WallSeg(start, end, thickness_ft, source_layer, detector, thickness_source)`, `WallSeg.length_ft`, `WallSeg.is_horizontal`, `detect_walls_paired_lines(ps, wall_layers, units_per_foot, min_t_in=2.0, max_t_in=24.0, min_len_ft=1.0) -> tuple[WallSeg, ...]`

**Algorithm** (validated in `PLAN.md` §4 — it found 49 walls with a measured median thickness of 4.0 in): split segments into horizontal and vertical families; within each, pair segments whose perpendicular offset is a plausible wall thickness and whose parallel extents overlap; emit the midline as the centerline.

- [ ] **Step 1: Write the failing test**

Create `tests/test_walls.py`:

```python
import pytest

from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.primitives import Primitive, PrimitiveSet


def _ps(lines, layer="WALLS"):
    prims = tuple(Primitive("line", (a, b), layer, None, None) for a, b in lines)
    return PrimitiveSet(primitives=prims, texts=(), width=1000.0, height=1000.0,
                        source_path="x", source_sha256="y")


def test_pairs_two_parallel_lines_into_one_wall():
    """Two horizontal lines 4in apart (scale 12pt/ft -> 4in = 4.0pt)."""
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 4.0), (120.0, 4.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    w = walls[0]
    assert w.thickness_ft == pytest.approx(4 / 12, abs=1e-6)
    assert w.start == pytest.approx((0.0, 1 / 6))   # centerline y = 2pt = 1/6 ft
    assert w.length_ft == pytest.approx(10.0)
    assert w.thickness_source == "measured"


def test_detects_vertical_walls():
    ps = _ps([((0.0, 0.0), (0.0, 120.0)), ((4.0, 0.0), (4.0, 120.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    assert not walls[0].is_horizontal
    assert walls[0].length_ft == pytest.approx(10.0)


def test_rejects_pairs_that_are_too_far_apart_to_be_a_wall():
    """36pt at 12pt/ft is 36in — a room, not a wall."""
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 36.0), (120.0, 36.0))])
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_rejects_pairs_with_no_overlap():
    ps = _ps([((0.0, 0.0), (50.0, 0.0)), ((80.0, 4.0), (130.0, 4.0))])
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_ignores_layers_not_requested():
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((0.0, 4.0), (120.0, 4.0))], layer="FURN")
    assert detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0) == ()


def test_wall_spans_only_the_overlapping_extent():
    ps = _ps([((0.0, 0.0), (120.0, 0.0)), ((24.0, 4.0), (180.0, 4.0))])
    walls = detect_walls_paired_lines(ps, {"WALLS"}, units_per_foot=12.0)
    assert len(walls) == 1
    assert walls[0].start[0] == pytest.approx(2.0)   # 24pt = 2ft
    assert walls[0].end[0] == pytest.approx(10.0)    # 120pt = 10ft
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_walls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.geometry'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/geometry/__init__.py` (empty file).

Create `archiagent/geometry/walls.py`:

```python
"""Paired-line wall detection.

A wall drawn in CAD is two parallel lines a wall-thickness apart. Pairing
them recovers both the centerline and the TRUE thickness — no guessing.
Validated in PLAN.md §4: 187 raw segments -> 49 walls, median measured
thickness 4.0in, matching the drawing's partition walls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from archiagent.primitives import Pt, PrimitiveSet

AXIS_TOL = 0.02


@dataclass(frozen=True)
class WallSeg:
    start: Pt
    end: Pt
    thickness_ft: float
    source_layer: str
    detector: str  # "paired-line" | "hatch-body"
    thickness_source: str  # "measured" | "default"

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def is_horizontal(self) -> bool:
        return abs(self.end[1] - self.start[1]) < abs(self.end[0] - self.start[0])


def _pair_family(family: list[tuple[float, float, float, str]],
                 min_t_ft: float, max_t_ft: float,
                 min_len_ft: float) -> list[tuple[float, float, float, float, str]]:
    """Pair (lo, hi, offset, layer) entries into (lo, hi, center, thickness, layer).

    `offset` is the perpendicular coordinate; `lo`/`hi` bound the run along
    the parallel axis.
    """
    order = sorted(range(len(family)), key=lambda i: family[i][2])
    used: set[int] = set()
    out: list[tuple[float, float, float, float, str]] = []

    for pos, i in enumerate(order):
        if i in used:
            continue
        a_lo, a_hi, a_off, a_layer = family[i]
        best = None
        for j in order[pos + 1:]:
            if j in used:
                continue
            b_lo, b_hi, b_off, _ = family[j]
            thickness = abs(b_off - a_off)
            if thickness > max_t_ft:
                break  # sorted by offset, so no later j can be closer
            if thickness < min_t_ft:
                continue
            overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
            if overlap < min_len_ft:
                continue
            if best is None or overlap > best[0]:
                best = (overlap, j, thickness,
                        max(a_lo, b_lo), min(a_hi, b_hi), (a_off + b_off) / 2.0)
        if best is not None:
            _, j, thickness, lo, hi, center = best
            used.add(i)
            used.add(j)
            out.append((lo, hi, center, thickness, a_layer))
    return out


def detect_walls_paired_lines(ps: PrimitiveSet, wall_layers: set[str],
                              units_per_foot: float,
                              min_t_in: float = 2.0, max_t_in: float = 24.0,
                              min_len_ft: float = 1.0) -> tuple[WallSeg, ...]:
    horizontal: list[tuple[float, float, float, str]] = []
    vertical: list[tuple[float, float, float, str]] = []

    for p in ps.by_layer(wall_layers):
        for (x0, y0), (x1, y1) in p.segments():
            fx0, fy0 = x0 / units_per_foot, y0 / units_per_foot
            fx1, fy1 = x1 / units_per_foot, y1 / units_per_foot
            if abs(fy1 - fy0) < AXIS_TOL and abs(fx1 - fx0) >= min_len_ft:
                horizontal.append((min(fx0, fx1), max(fx0, fx1),
                                   (fy0 + fy1) / 2.0, p.layer))
            elif abs(fx1 - fx0) < AXIS_TOL and abs(fy1 - fy0) >= min_len_ft:
                vertical.append((min(fy0, fy1), max(fy0, fy1),
                                 (fx0 + fx1) / 2.0, p.layer))

    min_t_ft, max_t_ft = min_t_in / 12.0, max_t_in / 12.0
    walls: list[WallSeg] = []

    for lo, hi, center, thickness, layer in _pair_family(
            horizontal, min_t_ft, max_t_ft, min_len_ft):
        walls.append(WallSeg((lo, center), (hi, center), thickness,
                             layer, "paired-line", "measured"))

    for lo, hi, center, thickness, layer in _pair_family(
            vertical, min_t_ft, max_t_ft, min_len_ft):
        walls.append(WallSeg((center, lo), (center, hi), thickness,
                             layer, "paired-line", "measured"))

    return tuple(walls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_walls.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/geometry/ tests/test_walls.py
git commit -m "feat: paired-line wall detection with measured thickness"
```

---

## Task 8: Endpoint clustering (R2, step 1)

**Files:**
- Create: `archiagent/geometry/junctions.py`
- Create: `tests/test_junction_clustering.py`

**Interfaces:**
- Consumes: `WallSeg` from Task 7
- Produces: `cluster_endpoints(walls, snap_in=1.0) -> tuple[tuple[WallSeg, ...], tuple[Pt, ...]]` — returns walls with endpoints moved to cluster centroids, plus the node list.

**Why this is first:** CAD drawings are full of near-misses — endpoints 0.3 in apart that were meant to touch. Everything downstream (intersection, splitting, polygonize) assumes exact coincidence. Union-find over endpoints within tolerance makes that true.

- [ ] **Step 1: Write the failing test**

Create `tests/test_junction_clustering.py`:

```python
import pytest

from archiagent.geometry.junctions import cluster_endpoints
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def test_near_miss_endpoints_snap_to_one_node():
    """0.5in apart, inside the 1in tolerance -> one shared node."""
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0 + 0.5 / 12, 8.0))
    walls, nodes = cluster_endpoints([a, b], snap_in=1.0)
    assert walls[0].end == walls[1].start
    assert len(nodes) == 3


def test_endpoints_beyond_tolerance_stay_separate():
    """3in apart -> two distinct nodes; a doorway must not be sealed."""
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 3.0 / 12, 0.0), (10.0 + 3.0 / 12, 8.0))
    walls, nodes = cluster_endpoints([a, b], snap_in=1.0)
    assert walls[0].end != walls[1].start
    assert len(nodes) == 4


def test_four_walls_meeting_at_a_corner_share_one_node():
    walls_in = [
        _w((0.0, 0.0), (10.0, 0.0)),
        _w((10.0 + 0.2 / 12, 0.0), (10.0, 8.0)),
    ]
    walls, nodes = cluster_endpoints(walls_in, snap_in=1.0)
    assert len(nodes) == 3


def test_clustering_is_idempotent():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0, 8.0))
    once, _ = cluster_endpoints([a, b], snap_in=1.0)
    twice, _ = cluster_endpoints(list(once), snap_in=1.0)
    assert once == twice


def test_clustering_is_order_independent():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 0.5 / 12, 0.0), (10.0, 8.0))
    fwd, nodes_fwd = cluster_endpoints([a, b], snap_in=1.0)
    rev, nodes_rev = cluster_endpoints([b, a], snap_in=1.0)
    assert sorted(nodes_fwd) == sorted(nodes_rev)


def test_preserves_wall_attributes():
    a = _w((0.0, 0.0), (10.0, 0.0), t_in=8.0)
    walls, _ = cluster_endpoints([a], snap_in=1.0)
    assert walls[0].thickness_ft == pytest.approx(8 / 12)
    assert walls[0].source_layer == "WALLS"
    assert walls[0].thickness_source == "measured"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_junction_clustering.py -v`
Expected: FAIL with `ImportError: cannot import name 'cluster_endpoints'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/geometry/junctions.py`:

```python
"""Junction resolution — R2, the pipeline's critical path.

Room detection is ENTIRELY downstream of this module: one unhealed
junction leaks two rooms into one and the error propagates silently into
every later phase. See PLAN.md §7 Stage 3b for the taxonomy.

Tolerances are in INCHES, never pixels, and are always reported.
"""

from __future__ import annotations

import math
from dataclasses import replace

from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[max(ri, rj)] = min(ri, rj)


def cluster_endpoints(walls: list[WallSeg] | tuple[WallSeg, ...],
                      snap_in: float = 1.0
                      ) -> tuple[tuple[WallSeg, ...], tuple[Pt, ...]]:
    """Snap near-miss endpoints onto shared nodes.

    Returns the walls with endpoints moved to their cluster centroid, and
    the sorted list of distinct nodes.
    """
    walls = list(walls)
    if not walls:
        return (), ()

    snap_ft = snap_in / 12.0
    points: list[Pt] = []
    for w in walls:
        points.append(w.start)
        points.append(w.end)

    uf = _UnionFind(len(points))
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if math.dist(points[i], points[j]) <= snap_ft:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(len(points)):
        groups.setdefault(uf.find(idx), []).append(idx)

    centroid: dict[int, Pt] = {}
    for root, members in groups.items():
        cx = sum(points[m][0] for m in members) / len(members)
        cy = sum(points[m][1] for m in members) / len(members)
        centroid[root] = (cx, cy)

    out: list[WallSeg] = []
    for k, w in enumerate(walls):
        out.append(replace(w,
                           start=centroid[uf.find(2 * k)],
                           end=centroid[uf.find(2 * k + 1)]))

    return tuple(out), tuple(sorted(set(centroid.values())))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_junction_clustering.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/geometry/junctions.py tests/test_junction_clustering.py
git commit -m "feat: endpoint clustering for near-miss wall junctions"
```

---

## Task 9: Extension to intersections (R2, step 2)

**Files:**
- Modify: `archiagent/geometry/junctions.py`
- Create: `tests/test_junction_extension.py`

**Interfaces:**
- Consumes: `WallSeg` (Task 7), `cluster_endpoints` (Task 8)
- Produces: `extend_to_intersections(walls, extend_in=6.0) -> tuple[WallSeg, ...]`

**The critical guard:** a gap within the extension budget is sloppy drafting and gets closed. A gap **beyond** it is a doorway and must stay open. Sealing doorways would look like success and be badly wrong — this is the single most dangerous failure mode in the pipeline.

- [ ] **Step 1: Write the failing test**

Create `tests/test_junction_extension.py`:

```python
import pytest

from archiagent.geometry.junctions import extend_to_intersections
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def test_undershoot_l_corner_extends_to_meet():
    """Horizontal stops 3in short of the vertical -> extend to the corner."""
    h = _w((0.0, 0.0), (10.0 - 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_overshoot_l_corner_trims_back():
    """Horizontal runs 3in past the vertical -> trim to the corner."""
    h = _w((0.0, 0.0), (10.0 + 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_gap_beyond_budget_is_left_open():
    """A 24in gap is a doorway. It MUST NOT be closed."""
    h = _w((0.0, 0.0), (10.0 - 24.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[0].end == pytest.approx((8.0, 0.0))


def test_t_junction_extends_the_terminating_wall():
    """Vertical stops 2in short of a horizontal's middle."""
    h = _w((0.0, 0.0), (20.0, 0.0))
    v = _w((10.0, 2.0 / 12), (10.0, 8.0))
    out = extend_to_intersections([h, v], extend_in=6.0)
    assert out[1].start == pytest.approx((10.0, 0.0))
    assert out[0].start == pytest.approx((0.0, 0.0))  # through-wall untouched
    assert out[0].end == pytest.approx((20.0, 0.0))


def test_parallel_walls_are_never_joined():
    a = _w((0.0, 0.0), (10.0, 0.0))
    b = _w((10.0 + 2.0 / 12, 0.0), (20.0, 0.0))
    out = extend_to_intersections([a, b], extend_in=6.0)
    assert out[0].end == pytest.approx((10.0, 0.0))


def test_extension_is_idempotent():
    h = _w((0.0, 0.0), (10.0 - 3.0 / 12, 0.0))
    v = _w((10.0, 0.0), (10.0, 8.0))
    once = extend_to_intersections([h, v], extend_in=6.0)
    twice = extend_to_intersections(list(once), extend_in=6.0)
    assert once == twice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_junction_extension.py -v`
Expected: FAIL with `ImportError: cannot import name 'extend_to_intersections'`

- [ ] **Step 3: Write the implementation**

Append to `archiagent/geometry/junctions.py`:

```python
def _line_intersection(a0: Pt, a1: Pt, b0: Pt, b1: Pt) -> Pt | None:
    """Intersection of two INFINITE lines, or None if parallel."""
    x1, y1 = a0
    x2, y2 = a1
    x3, y3 = b0
    x4, y4 = b1
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-12:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
    return (px, py)


def _param_on(p: Pt, a: Pt, b: Pt) -> float:
    """Where p falls along a->b: 0 at a, 1 at b, outside [0,1] beyond the ends."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom < 1e-18:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denom


def extend_to_intersections(walls: list[WallSeg] | tuple[WallSeg, ...],
                            extend_in: float = 6.0) -> tuple[WallSeg, ...]:
    """Close undershoots and trim overshoots at wall intersections.

    A gap within `extend_in` is sloppy drafting and is closed. A gap
    beyond it is a DOORWAY and is left alone — sealing doorways would
    merge rooms and look like success while being badly wrong.
    """
    walls = list(walls)
    budget_ft = extend_in / 12.0
    starts = [w.start for w in walls]
    ends = [w.end for w in walls]

    for i, wi in enumerate(walls):
        for j, wj in enumerate(walls):
            if i == j:
                continue
            hit = _line_intersection(starts[i], ends[i], wj.start, wj.end)
            if hit is None:
                continue

            # The intersection must lie on (or within a hair of) wall j's body,
            # otherwise these two walls do not actually meet.
            tj = _param_on(hit, wj.start, wj.end)
            if not (-1e-9 <= tj <= 1.0 + 1e-9):
                continue

            if math.dist(hit, ends[i]) <= budget_ft:
                ends[i] = hit
            elif math.dist(hit, starts[i]) <= budget_ft:
                starts[i] = hit

    return tuple(replace(w, start=starts[k], end=ends[k])
                 for k, w in enumerate(walls))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_junction_extension.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/geometry/junctions.py tests/test_junction_extension.py
git commit -m "feat: extend/trim walls to intersections, preserving doorways"
```

---

## Task 10: Wall graph assembly (R2, step 3)

**Files:**
- Modify: `archiagent/geometry/junctions.py`
- Create: `tests/test_wall_graph.py`

**Interfaces:**
- Consumes: `cluster_endpoints` (Task 8), `extend_to_intersections` (Task 9)
- Produces: `Junction(point, wall_indices, kind)`, `WallGraph(walls, junctions, unresolved)`, `split_through_walls(walls, nodes, tol_in=1.0) -> tuple[WallSeg, ...]`, `resolve_junctions(walls, snap_in=1.0, extend_in=6.0, min_dangle_ft=0.5) -> WallGraph`

`kind` is one of `"L"`, `"T"`, `"X"`, `"collinear"`, `"end"` — derived from node degree and incident directions, matching the taxonomy table in `PLAN.md` §7 Stage 3b.

- [ ] **Step 1: Write the failing test**

Create `tests/test_wall_graph.py`:

```python
import pytest

from archiagent.geometry.junctions import (WallGraph, resolve_junctions,
                                           split_through_walls)
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def test_split_through_wall_at_a_t_junction():
    through = _w((0.0, 0.0), (20.0, 0.0))
    parts = split_through_walls([through], [(10.0, 0.0)], tol_in=1.0)
    assert len(parts) == 2
    assert parts[0].end == pytest.approx((10.0, 0.0))
    assert parts[1].start == pytest.approx((10.0, 0.0))


def test_split_ignores_nodes_at_the_ends():
    through = _w((0.0, 0.0), (20.0, 0.0))
    parts = split_through_walls([through], [(0.0, 0.0), (20.0, 0.0)], tol_in=1.0)
    assert len(parts) == 1


def test_closed_ring_produces_four_corner_junctions():
    graph = resolve_junctions(_ring())
    assert len(graph.junctions) == 4
    assert all(j.kind == "L" for j in graph.junctions)
    assert graph.unresolved == ()


def test_t_junction_is_classified_and_splits_the_through_wall():
    walls = _ring() + [_w((5.0, 0.0), (5.0, 10.0))]
    graph = resolve_junctions(walls)
    kinds = {j.kind for j in graph.junctions}
    assert "T" in kinds
    # bottom and top walls each split in two -> 4 ring walls become 6
    assert len(graph.walls) == 7


def test_open_ring_reports_an_unresolved_endpoint():
    walls = _ring()[:3]  # leave one side missing
    graph = resolve_junctions(walls)
    assert len(graph.unresolved) == 2


def test_dangle_below_minimum_length_is_pruned():
    walls = _ring() + [_w((5.0, 0.0), (5.0, 0.25))]
    graph = resolve_junctions(walls, min_dangle_ft=0.5)
    assert len(graph.walls) == 4


def test_resolution_is_order_independent():
    fwd = resolve_junctions(_ring())
    rev = resolve_junctions(list(reversed(_ring())))
    assert sorted(j.point for j in fwd.junctions) == \
           sorted(j.point for j in rev.junctions)


def test_resolution_is_idempotent():
    once = resolve_junctions(_ring())
    twice = resolve_junctions(list(once.walls))
    assert sorted(j.point for j in once.junctions) == \
           sorted(j.point for j in twice.junctions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_wall_graph.py -v`
Expected: FAIL with `ImportError: cannot import name 'WallGraph'`

- [ ] **Step 3: Write the implementation**

Append to `archiagent/geometry/junctions.py`:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Junction:
    point: Pt
    wall_indices: tuple[int, ...]
    kind: str  # "L" | "T" | "X" | "collinear" | "end"


@dataclass(frozen=True)
class WallGraph:
    walls: tuple[WallSeg, ...]
    junctions: tuple[Junction, ...]
    unresolved: tuple[Pt, ...] = field(default=())


def split_through_walls(walls: list[WallSeg] | tuple[WallSeg, ...],
                        nodes: list[Pt] | tuple[Pt, ...],
                        tol_in: float = 1.0) -> tuple[WallSeg, ...]:
    """Split any wall whose INTERIOR contains a node, so T and X junctions
    become real graph nodes rather than geometric coincidences."""
    tol_ft = tol_in / 12.0
    out: list[WallSeg] = []

    for w in walls:
        interior: list[tuple[float, Pt]] = []
        for n in nodes:
            if math.dist(n, w.start) <= tol_ft or math.dist(n, w.end) <= tol_ft:
                continue
            t = _param_on(n, w.start, w.end)
            if not (0.0 < t < 1.0):
                continue
            # must lie ON the wall, not merely on its infinite line
            proj = (w.start[0] + t * (w.end[0] - w.start[0]),
                    w.start[1] + t * (w.end[1] - w.start[1]))
            if math.dist(proj, n) > tol_ft:
                continue
            interior.append((t, n))

        if not interior:
            out.append(w)
            continue

        interior.sort()
        cursor = w.start
        for _, n in interior:
            out.append(replace(w, start=cursor, end=n))
            cursor = n
        out.append(replace(w, start=cursor, end=w.end))

    return tuple(out)


def _direction(a: Pt, b: Pt) -> Pt:
    dx, dy = b[0] - a[0], b[1] - a[1]
    mag = math.hypot(dx, dy)
    if mag < 1e-12:
        return (0.0, 0.0)
    return (dx / mag, dy / mag)


def _classify(point: Pt, incident: list[tuple[int, Pt]]) -> str:
    """Name the junction from its degree and the directions leaving it."""
    degree = len(incident)
    if degree <= 1:
        return "end"
    if degree >= 4:
        return "X"
    if degree == 3:
        return "T"
    (_, d0), (_, d1) = incident
    dot = abs(d0[0] * d1[0] + d0[1] * d1[1])
    return "collinear" if dot > 0.99 else "L"


def resolve_junctions(walls: list[WallSeg] | tuple[WallSeg, ...],
                      snap_in: float = 1.0,
                      extend_in: float = 6.0,
                      min_dangle_ft: float = 0.5) -> WallGraph:
    """Full R2 pipeline: cluster -> extend -> split -> re-cluster -> classify.

    Tolerances are in inches and are reported on the result so a run's
    junction behaviour is auditable.
    """
    walls = [w for w in walls if w.length_ft > 1e-9]
    if not walls:
        return WallGraph((), (), ())

    snapped, _ = cluster_endpoints(walls, snap_in=snap_in)
    extended = extend_to_intersections(snapped, extend_in=extend_in)
    resnapped, nodes = cluster_endpoints(extended, snap_in=snap_in)
    split = split_through_walls(resnapped, nodes, tol_in=snap_in)
    final, nodes = cluster_endpoints(split, snap_in=snap_in)

    # prune dangles: short walls with a free end
    incident_count: dict[Pt, int] = {}
    for w in final:
        incident_count[w.start] = incident_count.get(w.start, 0) + 1
        incident_count[w.end] = incident_count.get(w.end, 0) + 1

    kept = tuple(w for w in final
                 if w.length_ft >= min_dangle_ft
                 or (incident_count[w.start] > 1 and incident_count[w.end] > 1))

    incident: dict[Pt, list[tuple[int, Pt]]] = {}
    for idx, w in enumerate(kept):
        incident.setdefault(w.start, []).append((idx, _direction(w.start, w.end)))
        incident.setdefault(w.end, []).append((idx, _direction(w.end, w.start)))

    junctions = tuple(
        Junction(point=pt,
                 wall_indices=tuple(sorted(i for i, _ in inc)),
                 kind=_classify(pt, inc))
        for pt, inc in sorted(incident.items())
        if len(inc) > 1)

    unresolved = tuple(pt for pt, inc in sorted(incident.items()) if len(inc) == 1)

    return WallGraph(walls=kept, junctions=junctions, unresolved=unresolved)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_wall_graph.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/geometry/junctions.py tests/test_wall_graph.py
git commit -m "feat: wall graph assembly with junction classification"
```

---

## Task 11: Space detection

**Files:**
- Create: `archiagent/geometry/spaces.py`
- Create: `tests/test_spaces.py`

**Interfaces:**
- Consumes: `WallGraph` from Task 10
- Produces: `Space(boundary, area_sqft)`, `detect_spaces(graph, min_area_sqft=10.0) -> tuple[Space, ...]`

**This is the step that replaces vision entirely.** Rooms are not guessed — they fall out of wall topology as closed faces of the centerline graph.

- [ ] **Step 1: Write the failing test**

Create `tests/test_spaces.py`:

```python
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def test_closed_ring_yields_exactly_one_space():
    spaces = detect_spaces(resolve_junctions(_ring()))
    assert len(spaces) == 1
    assert spaces[0].area_sqft == pytest.approx(100.0)


def test_ring_with_a_missing_wall_yields_no_space():
    spaces = detect_spaces(resolve_junctions(_ring()[:3]))
    assert spaces == ()


def test_divided_ring_yields_two_spaces():
    walls = _ring(10.0) + [_w((5.0, 0.0), (5.0, 10.0))]
    spaces = detect_spaces(resolve_junctions(walls))
    assert len(spaces) == 2
    assert sorted(s.area_sqft for s in spaces) == pytest.approx([50.0, 50.0])


def test_slivers_below_the_area_floor_are_dropped():
    walls = _ring(10.0) + [_w((0.05, 0.0), (0.05, 10.0))]
    spaces = detect_spaces(resolve_junctions(walls), min_area_sqft=10.0)
    assert all(s.area_sqft >= 10.0 for s in spaces)


def test_boundary_is_a_closed_simple_polygon():
    spaces = detect_spaces(resolve_junctions(_ring()))
    boundary = spaces[0].boundary
    assert boundary[0] == boundary[-1]
    assert len(set(boundary)) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_spaces.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.geometry.spaces'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/geometry/spaces.py`:

```python
"""Room detection by polygonizing the wall centerline graph.

This replaces vision entirely: rooms are not estimated, they are the
bounded faces of the healed wall graph — a mathematical consequence of
Task 10's junction resolution. Every failure here is a junction failure
upstream.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from archiagent.geometry.junctions import WallGraph
from archiagent.primitives import Pt


@dataclass(frozen=True)
class Space:
    boundary: tuple[Pt, ...]  # closed ring: first point repeated at the end
    area_sqft: float


def detect_spaces(graph: WallGraph, min_area_sqft: float = 10.0) -> tuple[Space, ...]:
    if not graph.walls:
        return ()

    lines = [LineString([w.start, w.end]) for w in graph.walls
             if w.start != w.end]
    if not lines:
        return ()

    noded = unary_union(lines)
    out: list[Space] = []
    for poly in polygonize(noded):
        if poly.area < min_area_sqft:
            continue
        ring = tuple((round(x, 6), round(y, 6))
                     for x, y in poly.exterior.coords)
        out.append(Space(boundary=ring, area_sqft=poly.area))

    return tuple(sorted(out, key=lambda s: -s.area_sqft))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_spaces.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/geometry/spaces.py tests/test_spaces.py
git commit -m "feat: room detection by polygonizing the wall graph"
```

---

## Task 12: Building model and validators

**Files:**
- Create: `archiagent/model.py`, `archiagent/validate.py`
- Create: `tests/test_validate.py`

**Interfaces:**
- Consumes: `WallGraph` (Task 10), `Space` (Task 11), `ScaleResult` (Task 6)
- Produces: `Issue(severity, entity, code, msg)`, `BuildingModel(walls, spaces, junctions, unresolved, scale, layer_roles, source_path, source_sha256, wall_height_ft)`, `BuildingModel.envelope() -> tuple[float, float, float, float]`, `validate(model) -> tuple[Issue, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_validate.py`:

```python
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.model import BuildingModel, Issue
from archiagent.scale.resolve import ScaleResult
from archiagent.validate import validate


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def _scale():
    return ScaleResult(units_per_foot=11.861, convention="clear",
                       residuals_in=(0.2, 0.5, 0.7), max_residual_in=0.7,
                       matched_count=3)


def _model(walls, scale=None):
    graph = resolve_junctions(walls)
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=scale or _scale(), layer_roles={"WALLS": "wall_structural"},
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def test_clean_ring_has_no_issues():
    assert validate(_model(_ring())) == ()


def test_unresolved_junction_is_an_error():
    issues = validate(_model(_ring()[:3]))
    codes = {i.code for i in issues}
    assert "unresolved_junction" in codes
    assert any(i.severity == "error" for i in issues)


def test_scale_above_the_gate_is_an_error():
    bad = ScaleResult(units_per_foot=11.861, convention="clear",
                      residuals_in=(0.2, 3.4), max_residual_in=3.4,
                      matched_count=2)
    codes = {i.code for i in validate(_model(_ring(), scale=bad))}
    assert "scale_gate_failed" in codes


def test_zero_thickness_wall_is_reported():
    walls = _ring() + [WallSeg((2.0, 2.0), (4.0, 2.0), 0.0,
                               "WALLS", "paired-line", "measured")]
    codes = {i.code for i in validate(_model(walls))}
    assert "zero_thickness_wall" in codes


def test_model_with_no_spaces_is_reported():
    codes = {i.code for i in validate(_model(_ring()[:2]))}
    assert "no_spaces_detected" in codes


def test_envelope_is_the_bounding_box_of_all_walls():
    model = _model(_ring(10.0))
    assert model.envelope() == (0.0, 0.0, 10.0, 10.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.model'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/model.py`:

```python
"""The assembled building model handed to the IFC author."""

from __future__ import annotations

from dataclasses import dataclass, field

from archiagent.geometry.junctions import Junction
from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt
from archiagent.scale.resolve import ScaleResult


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warn" | "info"
    entity: str
    code: str
    msg: str


@dataclass(frozen=True)
class BuildingModel:
    walls: tuple[WallSeg, ...]
    junctions: tuple[Junction, ...]
    unresolved: tuple[Pt, ...]
    spaces: tuple[Space, ...]
    scale: ScaleResult
    layer_roles: dict[str, str]
    source_path: str
    source_sha256: str
    wall_height_ft: float = 10.0
    issues: tuple[Issue, ...] = field(default=())

    def envelope(self) -> tuple[float, float, float, float]:
        xs = [c for w in self.walls for c in (w.start[0], w.end[0])]
        ys = [c for w in self.walls for c in (w.start[1], w.end[1])]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))
```

Create `archiagent/validate.py`:

```python
"""Stage 7 validators.

Nothing is silently corrected. Every problem becomes an Issue naming the
entity, so a human or the correction loop can act on it.
"""

from __future__ import annotations

from archiagent.model import BuildingModel, Issue

MAX_RESIDUAL_IN = 2.0


def validate(model: BuildingModel) -> tuple[Issue, ...]:
    issues: list[Issue] = []

    if model.scale.max_residual_in > MAX_RESIDUAL_IN:
        issues.append(Issue(
            "error", "scale", "scale_gate_failed",
            f"max residual {model.scale.max_residual_in}in exceeds "
            f"the {MAX_RESIDUAL_IN}in gate (R1)"))

    for pt in model.unresolved:
        issues.append(Issue(
            "error", f"node@{pt[0]:.2f},{pt[1]:.2f}", "unresolved_junction",
            "wall endpoint connects to nothing; rooms downstream may leak"))

    for idx, w in enumerate(model.walls):
        if w.thickness_ft <= 0.0:
            issues.append(Issue(
                "error", f"W{idx:03d}", "zero_thickness_wall",
                "wall has zero or negative thickness"))
        if w.length_ft <= 0.0:
            issues.append(Issue(
                "error", f"W{idx:03d}", "zero_length_wall",
                "wall has zero length"))
        if w.thickness_source == "default":
            issues.append(Issue(
                "warn", f"W{idx:03d}", "default_thickness",
                "thickness could not be measured; a default was applied"))

    if model.walls and not model.spaces:
        issues.append(Issue(
            "error", "model", "no_spaces_detected",
            "walls exist but no closed room was found; check junctions"))

    for i, space in enumerate(model.spaces):
        if space.boundary and space.boundary[0] != space.boundary[-1]:
            issues.append(Issue(
                "error", f"S{i:03d}", "unclosed_space",
                "space boundary is not a closed ring"))

    return tuple(issues)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/model.py archiagent/validate.py tests/test_validate.py
git commit -m "feat: building model and stage-7 validators"
```

---

## Task 13: Headless IFC authoring

**Files:**
- Create: `archiagent/ifc/__init__.py`, `archiagent/ifc/author.py`
- Create: `tests/test_ifc_author.py`

**Interfaces:**
- Consumes: `BuildingModel` from Task 12
- Produces: `author_ifc(model: BuildingModel, out_path: str | Path) -> Path`

**Mind the four verified API traps** (Global Constraints): `feature.*` not `void.*`; `ShapeBuilder.rectangle` needs `position=(0.0, -t/2)` because it anchors at the **corner** (this bug scattered 49 walls in the spike); `IfcSpace` uses `aggregate.assign_object`; walls must be `IfcExtrudedAreaSolid`, never a mesh.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ifc_author.py`:

```python
import math

import ifcopenshell
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.ifc.author import author_ifc
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult

FT = 0.3048


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


@pytest.fixture
def model():
    graph = resolve_junctions(_ring())
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=ScaleResult(11.861, "clear", (0.2,), 0.2, 3),
        layer_roles={"WALLS": "wall_structural"},
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def test_writes_a_valid_ifc4_file(model, tmp_path):
    out = author_ifc(model, tmp_path / "m.ifc")
    f = ifcopenshell.open(out)
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcWall")) == 4
    assert len(f.by_type("IfcBuildingStorey")) == 1


def test_walls_are_parametric_extrusions_not_meshes(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    for wall in f.by_type("IfcWall"):
        item = wall.Representation.Representations[0].Items[0]
        assert item.is_a() == "IfcExtrudedAreaSolid"


def test_wall_height_matches_the_model(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    depth = f.by_type("IfcWall")[0].Representation.Representations[0].Items[0].Depth
    assert depth == pytest.approx(10.0 * FT)


def test_wall_is_placed_at_its_start_point_not_its_midpoint(model, tmp_path):
    """Regression for the ShapeBuilder corner-anchor trap that scattered
    49 walls in the spike. The first ring wall runs (0,0)->(10,0)."""
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    origins = {tuple(round(c, 4) for c in w.ObjectPlacement
                     .RelativePlacement.Location.Coordinates)
               for w in f.by_type("IfcWall")}
    assert (0.0, 0.0, 0.0) in origins


def test_spaces_are_emitted_and_aggregated_to_the_storey(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    spaces = f.by_type("IfcSpace")
    assert len(spaces) == 1
    parents = {rel.RelatingObject.is_a()
               for rel in f.by_type("IfcRelAggregates")
               if spaces[0] in rel.RelatedObjects}
    assert "IfcBuildingStorey" in parents


def test_provenance_property_set_round_trips(model, tmp_path):
    f = ifcopenshell.open(author_ifc(model, tmp_path / "m.ifc"))
    values = {p.NominalValue.wrappedValue for p in f.by_type("IfcPropertySingleValue")}
    assert "WALLS" in values
    assert "paired-line" in values
    assert "measured" in values
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ifc_author.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archiagent.ifc'`

- [ ] **Step 3: Write the implementation**

Create `archiagent/ifc/__init__.py` (empty file).

Create `archiagent/ifc/author.py`:

```python
"""Headless IFC4 authoring via ifcopenshell.api — no Blender required.

Walls are authored parametrically (axis + profile + extrusion), never as
meshes: an IfcFacetedBrep blob has no parametric meaning and would gut
Phase 3, where MEP needs real entities to attach to.

Verified API traps for ifcopenshell 0.8.5 (PLAN.md §4):
  - void.* was renamed feature.*
  - ShapeBuilder.rectangle anchors at the CORNER, so the profile must be
    offset by -thickness/2 and the object placed at the wall's start
  - IfcSpace uses aggregate.assign_object, not spatial.assign_container
"""

from __future__ import annotations

import math
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.util.shape_builder

from archiagent.model import BuildingModel

FT = 0.3048  # metres per foot; IFC is authored in SI

run = ifcopenshell.api.run


def author_ifc(model: BuildingModel, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    f = ifcopenshell.file(schema="IFC4")

    project = run("root.create_entity", f, ifc_class="IfcProject",
                  name=f"ArchiAgent — {Path(model.source_path).stem}")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    ctx = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model",
               context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)

    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey",
                 name="Ground Floor")
    run("aggregate.assign_object", f, products=[site], relating_object=project)
    run("aggregate.assign_object", f, products=[building], relating_object=site)
    run("aggregate.assign_object", f, products=[storey], relating_object=building)

    sb = ifcopenshell.util.shape_builder.ShapeBuilder(f)
    height_m = model.wall_height_ft * FT

    for idx, w in enumerate(model.walls):
        length_m = w.length_ft * FT
        if length_m <= 0.0:
            continue
        thickness_m = w.thickness_ft * FT

        wall = run("root.create_entity", f, ifc_class="IfcWall",
                   name=f"W{idx:03d}")
        run("spatial.assign_container", f, products=[wall],
            relating_structure=storey)

        # TRAP: rectangle() anchors at the corner. Offset by -t/2 so the
        # wall's own axis is its centerline, then place the object at p0.
        profile = sb.rectangle(size=(length_m, thickness_m),
                               position=(0.0, -thickness_m / 2.0))
        solid = sb.extrude(profile, magnitude=height_m,
                           extrusion_vector=(0.0, 0.0, 1.0))
        run("geometry.assign_representation", f, product=wall,
            representation=sb.get_representation(body, [solid]))

        angle = math.atan2(w.end[1] - w.start[1], w.end[0] - w.start[0])
        wall.ObjectPlacement = f.createIfcLocalPlacement(
            None,
            f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(
                    (w.start[0] * FT, w.start[1] * FT, 0.0)),
                f.createIfcDirection((0.0, 0.0, 1.0)),
                f.createIfcDirection((math.cos(angle), math.sin(angle), 0.0))))

        pset = run("pset.add_pset", f, product=wall,
                   name="ArchiAgent_Provenance")
        run("pset.edit_pset", f, pset=pset, properties={
            "SourceLayer": w.source_layer,
            "Detector": w.detector,
            "ThicknessSource": w.thickness_source,
            "ThicknessIn": round(w.thickness_ft * 12.0, 3),
            "ScaleUnitsPerFoot": round(model.scale.units_per_foot, 6),
            "ScaleMaxResidualIn": model.scale.max_residual_in,
        })

    for i, space in enumerate(model.spaces):
        sp = run("root.create_entity", f, ifc_class="IfcSpace",
                 name=f"S{i:03d}")
        # TRAP: IfcSpace decomposes the storey; it is not "contained" in it.
        run("aggregate.assign_object", f, products=[sp],
            relating_object=storey)

    f.write(str(out_path))
    return out_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ifc_author.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add archiagent/ifc/ tests/test_ifc_author.py
git commit -m "feat: headless parametric IFC4 authoring"
```

---

## Task 14: Junction relationships and end-to-end verification

**Files:**
- Modify: `archiagent/ifc/author.py`
- Create: `archiagent/pipeline.py`, `archiagent/blender/__init__.py`, `archiagent/blender/load.py`
- Create: `tests/test_ifc_connections.py`, `tests/test_pipeline_end_to_end.py`

**Interfaces:**
- Consumes: everything above
- Produces: `extract(pdf_path, classification, page=0, wall_height_ft=10.0) -> BuildingModel`, `run_pipeline(pdf_path, classification, out_ifc, page=0) -> tuple[Path, tuple[Issue, ...]]`

**Why `IfcRelConnectsPathElements` matters:** walls that merely touch are not walls that are *connected*. The relationship is what lets Bonsai and other BIM tools perform their own mitering and cleanup, and it is what makes the model genuinely parametric rather than a pile of correctly-positioned boxes (R2).

- [ ] **Step 1: Write the failing test**

Create `tests/test_ifc_connections.py`:

```python
import ifcopenshell
import pytest

from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import WallSeg
from archiagent.ifc.author import author_ifc
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult


def _w(start, end, t_in=4.0):
    return WallSeg(start, end, t_in / 12.0, "WALLS", "paired-line", "measured")


def _model(walls):
    graph = resolve_junctions(walls)
    return BuildingModel(
        walls=graph.walls, junctions=graph.junctions,
        unresolved=graph.unresolved, spaces=detect_spaces(graph),
        scale=ScaleResult(11.861, "clear", (0.2,), 0.2, 3),
        layer_roles={"WALLS": "wall_structural"},
        source_path="x.pdf", source_sha256="abc", wall_height_ft=10.0)


def _ring(size=10.0):
    return [
        _w((0.0, 0.0), (size, 0.0)),
        _w((size, 0.0), (size, size)),
        _w((size, size), (0.0, size)),
        _w((0.0, size), (0.0, 0.0)),
    ]


def test_every_junction_emits_a_connection_relationship(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    rels = f.by_type("IfcRelConnectsPathElements")
    assert len(rels) == 4


def test_connection_types_are_at_start_or_at_end_for_corners(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    for rel in f.by_type("IfcRelConnectsPathElements"):
        assert rel.RelatingConnectionType in ("ATSTART", "ATEND", "ATPATH")
        assert rel.RelatedConnectionType in ("ATSTART", "ATEND", "ATPATH")


def test_t_junction_uses_atpath_on_the_through_wall(tmp_path):
    walls = _ring(10.0) + [_w((5.0, 0.0), (5.0, 10.0))]
    f = ifcopenshell.open(author_ifc(_model(walls), tmp_path / "m.ifc"))
    types = {rel.RelatingConnectionType for rel in
             f.by_type("IfcRelConnectsPathElements")}
    types |= {rel.RelatedConnectionType for rel in
              f.by_type("IfcRelConnectsPathElements")}
    assert types <= {"ATSTART", "ATEND", "ATPATH"}


def test_connections_reference_distinct_walls(tmp_path):
    f = ifcopenshell.open(author_ifc(_model(_ring()), tmp_path / "m.ifc"))
    for rel in f.by_type("IfcRelConnectsPathElements"):
        assert rel.RelatingElement != rel.RelatedElement
```

Create `tests/test_pipeline_end_to_end.py`:

```python
import ifcopenshell
import pytest

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.pipeline import run_pipeline

DEMOLITION_ROLES = {
    "wall": (Role.WALL_STRUCTURAL, 0.95),
    "walll": (Role.WALL_STRUCTURAL, 0.90),
    "window": (Role.WINDOW, 0.90),
    "win": (Role.WINDOW, 0.85),
    "FURNITURE": (Role.FURNITURE, 0.95),
    "Text": (Role.TEXT_LABEL, 0.95),
    "TITLE": (Role.TITLE_BLOCK, 0.95),
}


def test_end_to_end_on_the_real_demolition_plan(demolition_pdf, tmp_path):
    out, issues = run_pipeline(
        demolition_pdf, StubClassifier(DEMOLITION_ROLES),
        tmp_path / "demolition.ifc")

    f = ifcopenshell.open(out)
    assert f.schema == "IFC4"

    walls = f.by_type("IfcWall")
    assert len(walls) >= 30, f"expected a realistic wall count, got {len(walls)}"

    for wall in walls:
        assert wall.Representation.Representations[0].Items[0].is_a() \
            == "IfcExtrudedAreaSolid"

    errors = [i for i in issues if i.severity == "error"
              and i.code == "scale_gate_failed"]
    assert errors == [], f"R1 scale gate failed: {errors}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ifc_connections.py tests/test_pipeline_end_to_end.py -v`
Expected: FAIL — `IfcRelConnectsPathElements` count is 0, and `ModuleNotFoundError: No module named 'archiagent.pipeline'`

- [ ] **Step 3: Write the implementation**

In `archiagent/ifc/author.py`, collect the created walls and emit connections. Change the wall loop to record entities, then add the relationships before `f.write`.

Replace the line `for idx, w in enumerate(model.walls):` block opening so the created entities are captured — add this immediately before the loop:

```python
    wall_entities: dict[int, object] = {}
```

and immediately after `run("pset.edit_pset", ...)` inside the loop, add:

```python
        wall_entities[idx] = wall
```

Then insert this block immediately before the `for i, space in enumerate(model.spaces):` loop:

```python
    def _connection_type(wall_index: int, point) -> str:
        """ATSTART / ATEND if the junction is at a wall's end, else ATPATH."""
        w = model.walls[wall_index]
        if math.dist(point, w.start) < 1e-6:
            return "ATSTART"
        if math.dist(point, w.end) < 1e-6:
            return "ATEND"
        return "ATPATH"

    for junction in model.junctions:
        indices = [i for i in junction.wall_indices if i in wall_entities]
        for a, b in zip(indices, indices[1:]):
            f.create_entity(
                "IfcRelConnectsPathElements",
                GlobalId=ifcopenshell.guid.new(),
                RelatingElement=wall_entities[a],
                RelatedElement=wall_entities[b],
                RelatingPriorities=[],
                RelatedPriorities=[],
                RelatingConnectionType=_connection_type(a, junction.point),
                RelatedConnectionType=_connection_type(b, junction.point),
            )
```

Add `import ifcopenshell.guid` to the imports at the top of the file.

Create `archiagent/pipeline.py`:

```python
"""Stages 0-8 wired together: PDF in, IFC out."""

from __future__ import annotations

from pathlib import Path

from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import (WALL_ROLES, LayerClassifier,
                                        layers_for_roles)
from archiagent.classify.roles import Role
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.spaces import detect_spaces
from archiagent.geometry.walls import detect_walls_paired_lines
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import load_pdf
from archiagent.model import BuildingModel, Issue
from archiagent.scale.dimensions import extract_dimensions
from archiagent.scale.resolve import candidate_runs, resolve_scale
from archiagent.validate import validate


def extract(pdf_path: str | Path, classifier: LayerClassifier, page: int = 0,
            wall_height_ft: float = 10.0) -> BuildingModel:
    ps = load_pdf(pdf_path, page=page)
    classification = classifier.classify(build_inventory(ps))
    wall_layers = layers_for_roles(classification, WALL_ROLES)
    if not wall_layers:
        raise ValueError("no layers were classified as walls")

    scale = resolve_scale(extract_dimensions(ps), candidate_runs(ps, wall_layers))
    walls = detect_walls_paired_lines(ps, wall_layers, scale.units_per_foot)
    graph = resolve_junctions(walls)

    model = BuildingModel(
        walls=graph.walls,
        junctions=graph.junctions,
        unresolved=graph.unresolved,
        spaces=detect_spaces(graph),
        scale=scale,
        layer_roles={name: role.value for name, (role, _) in classification.items()},
        source_path=str(pdf_path),
        source_sha256=ps.source_sha256,
        wall_height_ft=wall_height_ft,
    )
    return model


def run_pipeline(pdf_path: str | Path, classifier: LayerClassifier,
                 out_ifc: str | Path, page: int = 0
                 ) -> tuple[Path, tuple[Issue, ...]]:
    model = extract(pdf_path, classifier, page=page)
    issues = validate(model)
    return author_ifc(model, out_ifc), issues
```

Create `archiagent/blender/__init__.py` (empty file).

Create `archiagent/blender/load.py`:

```python
"""Load an authored IFC into Blender via Bonsai.

RUNS INSIDE BLENDER ONLY. The core archiagent package never imports this
module — Blender ships Python 3.13 while the pipeline targets 3.14.

    blender --python archiagent/blender/load.py -- /path/to/model.ifc
"""

import sys


def load(ifc_path: str) -> int:
    import bpy  # noqa: PLC0415 — Blender-only import, by design

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bim.load_project(filepath=ifc_path)
    walls = [o for o in bpy.data.objects if o.name.startswith("IfcWall")]
    print(f"loaded {len(walls)} IfcWall objects from {ifc_path}")
    return len(walls)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender --python load.py -- <model.ifc>")
    load(argv[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass (end-to-end test skips if `ARCHIAGENT_FIXTURES` is unset)

- [ ] **Step 5: Verify in Blender manually**

With the fixtures available, generate and load:

```bash
ARCHIAGENT_FIXTURES=../input-floorplans python3 -m pytest tests/test_pipeline_end_to_end.py -v
python3 -c "
from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.pipeline import run_pipeline
roles = {'wall': (Role.WALL_STRUCTURAL, 0.95), 'walll': (Role.WALL_STRUCTURAL, 0.9)}
out, issues = run_pipeline('../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf',
                           StubClassifier(roles), '/tmp/demolition.ifc')
print(out); [print(i) for i in issues]
"
blender --python archiagent/blender/load.py -- /tmp/demolition.ifc
```

Expected: Blender reports ≥30 `IfcWall` objects and the viewport shows a coherent floorplan with **no gaps at wall junctions** (unlike the spike render in `PLAN.md` §4, which predates junction resolution).

- [ ] **Step 6: Commit**

```bash
git add archiagent/ifc/author.py archiagent/pipeline.py archiagent/blender/ tests/
git commit -m "feat: IfcRelConnectsPathElements and end-to-end pipeline"
```

---

## Self-Review

**1. Spec coverage (M1–M5):**

| Spec requirement | Task |
|---|---|
| M1 ingest + layer classification | 1, 2, 3, 4 |
| M2 scale <2 in, clear basis | 5, 6 |
| M3 wall graph, junction taxonomy, spaces | 7, 8, 9, 10, 11 |
| M4 model assembly + validators | 12 |
| M5 headless IFC + `IfcRelConnectsPathElements` + Bonsai load | 13, 14 |
| R1 accuracy gate | 6 (enforced), 12 (validated), 14 (end-to-end) |
| R2 junction correctness | 8, 9, 10, 14 |
| Layer-separated PDFs only, fail fast | 2 (`NoLayersError`) |
| Client drawings never committed | 1 (`.gitignore`, `conftest.py`) |
| Core package must not import `bpy` | 14 (`blender/load.py` isolated) |
| Four ifcopenshell API traps | 13 |
| Corner-anchor placement regression | 13 |

**Known gaps, deliberately deferred beyond M1–M5** (these are M6–M8 in `PLAN.md` §12, not omissions): the hatch-body wall detector, opening/door/window detection, the real LLM-backed classifier, room naming and symbol classification, the IFC → Model View projection, `edit_model`, the bpy dump, and thickness defaults (4/8 in) — which only apply once the hatch detector can fail to measure. Task 12 already reports `default_thickness`, so the model is ready for them.

**2. Placeholder scan:** no "TBD", no "add error handling", no "similar to Task N". Every code step carries complete runnable code.

**3. Type consistency:** `WallSeg` fields are identical across Tasks 7–14. `resolve_junctions` returns `WallGraph` in Tasks 10, 11, 12, 14. `ScaleResult.units_per_foot` is used consistently (never `units_per_point`, which appears in `PLAN.md`'s illustrative JSON but is not this code's name). `Classification` is `dict[str, tuple[Role, float]]` in Tasks 4 and 14. `Space.boundary` is a closed ring in Tasks 11 and 12.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-21-floorplan-to-ifc-m1-m5.md`.
