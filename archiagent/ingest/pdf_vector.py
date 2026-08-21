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
