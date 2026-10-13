"""A floor's own drawing, cut from the whole file.

Entities outside the floor's box are deleted from a copy; layers, blocks,
styles and the header are carried exactly as the converter wrote them, so
block names stay stable for symbol matching.

An entity belongs to the floor whose box contains its bounding-box centre, or
failing that to the only box it overlaps. One that overlaps several boxes (a
sheet frame, a dimension across plans) belongs to none and is counted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf import bbox as ezbbox


@dataclass(frozen=True)
class CutReport:
    floor_id: str
    kept: int
    dropped_other_floor: int
    dropped_spanning: int
    dropped_outside: int


def _contains(box, p):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def cut_floor(src, floor, floors, out_path, units_per_foot=None) -> CutReport:
    floors = tuple(floors)
    if floor.id not in {f.id for f in floors}:
        raise ValueError(f"{floor.id} is not one of the detected floors")
    doc = ezdxf.readfile(str(src))
    msp = doc.modelspace()
    cache = ezbbox.Cache()
    kept = other = spanning = outside = 0
    for entity in list(msp):
        extents = ezbbox.extents([entity], cache=cache)
        if not extents.has_data:
            kept += 1                       # nothing to place (e.g. a ray): carried unchanged
            continue
        box = (extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y)
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        owners = [f for f in floors if _contains(f.bounds, centre)]
        if not owners:
            owners = [f for f in floors if _overlaps(f.bounds, box)]
            if len(owners) > 1:
                owners = []
                spanning += 1
                msp.delete_entity(entity)
                continue
        if not owners:
            outside += 1
            msp.delete_entity(entity)
        elif owners[0].id == floor.id:
            kept += 1
        else:
            other += 1
            msp.delete_entity(entity)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return CutReport(floor.id, kept, other, spanning, outside)
