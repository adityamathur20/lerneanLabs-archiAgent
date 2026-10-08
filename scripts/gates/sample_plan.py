"""Write a small synthetic floor plan DXF for the Phase 0 gates.

Not client data, and not meant to look like a real house: it carries the entity
kinds a real plan does, so the gates exercise each of them.

    python scripts/gates/sample_plan.py OUT.dxf

Inches (`$INSUNITS` 1). Exterior walls are closed LWPOLYLINEs, interior walls
LINE pairs, doors are INSERTs of a block (nested geometry), plus native linear
DIMENSIONs, room TEXT, an ARC swing and a HATCH.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf


def write(path: Path) -> None:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    for name, color in (("WALLS", 7), ("DOORS", 3), ("DIMS", 2), ("TEXT", 4), ("HATCH", 8)):
        doc.layers.add(name, color=color)

    door = doc.blocks.new("DOOR36")
    door.add_line((0, 0), (36, 0), dxfattribs={"layer": "0"})
    door.add_lwpolyline([(0, 0), (0, 36)], dxfattribs={"layer": "0"})
    door.add_arc((0, 0), 36, 0, 90, dxfattribs={"layer": "0"})

    msp = doc.modelspace()
    # Exterior wall faces: 40 ft x 30 ft, 6 in thick.
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 360), (0, 360)], close=True,
                       dxfattribs={"layer": "WALLS"})
    msp.add_lwpolyline([(6, 6), (474, 6), (474, 354), (6, 354)], close=True,
                       dxfattribs={"layer": "WALLS"})
    # Interior walls, 4.5 in, as line pairs.
    for x in (180, 300):
        msp.add_line((x, 6), (x, 354), dxfattribs={"layer": "WALLS"})
        msp.add_line((x + 4.5, 6), (x + 4.5, 354), dxfattribs={"layer": "WALLS"})
    msp.add_line((6, 200), (180, 200), dxfattribs={"layer": "WALLS"})
    msp.add_line((6, 204.5), (180, 204.5), dxfattribs={"layer": "WALLS"})
    # A wall with a non-axis-aligned run and a bulge-free open polyline.
    msp.add_lwpolyline([(304.5, 120), (380, 120), (474, 60)], dxfattribs={"layer": "WALLS"})

    for x, y in ((60, 0), (220, 6), (340, 6)):
        msp.add_blockref("DOOR36", (x, y), dxfattribs={"layer": "DOORS"})

    for p1, p2, base in (((0, 0), (480, 0), (240, -36)), ((0, 0), (0, 360), (-36, 180))):
        dim = msp.add_linear_dim(base=base, p1=p1, p2=p2,
                                 angle=0 if p1[1] == p2[1] else 90,
                                 dxfattribs={"layer": "DIMS"})
        dim.render()

    for text, at in (("LIVING", (90, 100)), ("BED 1", (240, 250)), ("KITCHEN", (390, 250))):
        msp.add_text(text, height=9, dxfattribs={"layer": "TEXT"}).set_placement(at)

    msp.add_arc((400, 300), 30, 180, 270, dxfattribs={"layer": "WALLS"})
    hatch = msp.add_hatch(color=8, dxfattribs={"layer": "HATCH"})
    hatch.paths.add_polyline_path([(0, 0), (480, 0), (480, 6), (0, 6)], is_closed=True)

    doc.saveas(path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    write(Path(sys.argv[1]))
