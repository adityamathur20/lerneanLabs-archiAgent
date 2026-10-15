"""What a room label says about the room.

Drawings state these in text next to the room: "LVL + 17'-0"" (finished level),
"Clear Ht. - 12'" (ceiling), "(DOUBLE HIGHT)", "LIVING ROOM BELOW" (a void on this
floor). They were ignored, so a void got a slab and ceiling-high walls.

Everything here is deterministic parsing of the drawing's own words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon

#: A guard rail, not a storey: the height of a wall on a void's edge.
RAILING_HEIGHT_FT = 3.5
#: A wall at most this thick on a void's edge is a railing; a thicker one is a real wall.
RAILING_MAX_THICKNESS_FT = 4.5 / 12
_FT_IN = r"(\d+(?:\.\d+)?)\s*'\s*(?:-?\s*(\d+(?:\.\d+)?)\s*(?:\d+/\d+\s*)?\"?)?"
_LEVEL = re.compile(r"\bLVL\.?\s*([+-])\s*" + _FT_IN, re.I)
_CLEAR = re.compile(r"\bCLEAR\s*H(?:T|EIGHT)\.?\s*[-:=]?\s*" + _FT_IN, re.I)
_DOUBLE = re.compile(r"\bDOUBLE\s*H[A-Z]*T\b|\bOPEN\s+TO\s+ABOVE\b", re.I)
_BELOW = re.compile(r"\bBELOW\b|\bOPEN\s+TO\s+SKY\b", re.I)


@dataclass(frozen=True)
class LabelAttributes:
    label: str = ""
    level_offset_ft: float | None = None
    clear_height_ft: float | None = None
    double_height: bool = False
    below: bool = False          # a void on this floor: the room is on the floor below


def clean_text(raw: str) -> str:
    """MTEXT without its formatting codes, on one line."""
    s = re.sub(r"\\[PpNn]", " ", raw)
    s = re.sub(r"\\S([^;^/#]*)[/^#]([^;]*);", r"\1/\2", s)
    s = re.sub(r"\\[A-Za-z][^;\\]*;", "", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def _feet(feet: str, inches: str | None) -> float:
    return float(feet) + (float(inches) / 12 if inches else 0.0)


def label_attributes(raw: str) -> LabelAttributes | None:
    """The attributes a label states, or None when it states none."""
    text = clean_text(raw)
    level = _LEVEL.search(text)
    clear = _CLEAR.search(text)
    attrs = LabelAttributes(
        label=text,
        level_offset_ft=((-1 if level.group(1) == "-" else 1) * _feet(level.group(2), level.group(3))
                         if level else None),
        clear_height_ft=_feet(clear.group(1), clear.group(2)) if clear else None,
        double_height=bool(_DOUBLE.search(text)),
        below=bool(_BELOW.search(text)),
    )
    if (attrs.level_offset_ft is None and attrs.clear_height_ft is None
            and not attrs.double_height and not attrs.below):
        return None
    return attrs


def space_attributes(spaces, texts, units_per_foot: float):
    """One entry per space: its label and what the label states, or None.

    A label belongs to the room containing its centre; of several, the longest
    text names the room and every one contributes its attributes.
    """
    polygons = [Polygon(s.boundary, s.holes) for s in spaces]
    found: list[list[str]] = [[] for _ in spaces]
    for t in texts:
        c = t.center()
        point = Point(c[0] / units_per_foot, c[1] / units_per_foot)
        for i, poly in enumerate(polygons):
            if poly.contains(point):
                found[i].append(clean_text(t.text))
                break
    out = []
    for words in found:
        words = [w for w in words if w]
        if not words:
            out.append(None)
            continue
        merged = label_attributes(" ".join(words)) or LabelAttributes()
        out.append(LabelAttributes(max(words, key=len), merged.level_offset_ft, merged.clear_height_ft,
                                   merged.double_height, merged.below))
    return tuple(out)


def railing_wall_indices(walls, void_rings, tolerance_ft: float = 0.15) -> set[int]:
    """Walls that are railings: thin, and lying along the edge of a void."""
    edges = [LineString(list(r) + [r[0]]) for r in void_rings if len(r) >= 3]
    out = set()
    for i, w in enumerate(walls):
        if w.thickness_ft > RAILING_MAX_THICKNESS_FT or not edges:
            continue
        mid = ((w.start[0] + w.end[0]) / 2, (w.start[1] + w.end[1]) / 2)
        reach = max(tolerance_ft, w.thickness_ft)
        if any(all(e.distance(Point(p)) <= reach for p in (w.start, mid, w.end)) for e in edges):
            out.add(i)
    return out
