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
