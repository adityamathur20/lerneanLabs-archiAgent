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
            s = e.plain_text() if t == "MTEXT" else e.dxf.text
            ip = e.dxf.insert
            x, y = float(ip.x), float(ip.y)
            texts.append(TextItem(str(s), (x, y, x, y), layer))

    xs = [p[0] for pr in prims for p in pr.coords] or [0.0]
    ys = [p[1] for pr in prims for p in pr.coords] or [0.0]
    ps = PrimitiveSet(tuple(prims), tuple(texts),
                      max(xs) - min(xs), max(ys) - min(ys),
                      str(path), _sha256(path))
    return ps, float(upf)
