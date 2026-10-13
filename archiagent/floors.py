"""Floors in a drawing: plan clusters named by their captions.

A file often holds several plans ("GROUND FLOOR REVISED", "FIRST FLOOR PLAN"),
which today land on top of one another as one storey. Detection only proposes:
the user's approval is what makes a floor real.

Elevation is never required. A level marker with an explicit unit sets it
(`+3.6m FIRST FFL`); otherwise ground is 0 and each next floor sits one wall
height above the previous, marked "assumed". A marker without a unit is
ignored, because the unit would be a guess.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from archiagent.primitives import Pt, PrimitiveSet
from archiagent.regions import propose_regions

MIN_PLAN_FT = (25.0, 15.0)     # a cluster smaller than this in both senses is a fragment
MIN_PRIMITIVES = 12
CAPTION_REACH_FT = 15.0        # how far from a plan a caption may sit and still belong to it
FT_PER_M = 3.28084

# (canonical name, rank). Rank orders floors from the ground up.
FLOOR_WORDS = {
    "basement": ("Basement", 0), "stilt": ("Stilt", 1), "parking": ("Parking", 1),
    "ground": ("Ground floor", 2), "mezzanine": ("Mezzanine", 3),
    "first": ("First floor", 4), "second": ("Second floor", 5), "third": ("Third floor", 6),
    "fourth": ("Fourth floor", 7), "fifth": ("Fifth floor", 8), "sixth": ("Sixth floor", 9),
    "seventh": ("Seventh floor", 10), "terrace": ("Terrace", 20), "roof": ("Roof", 21),
}
_WORD = re.compile(r"\b(" + "|".join(FLOOR_WORDS) + r")\b", re.I)
_KIND = re.compile(r"\b(floor|plan|level)\b", re.I)
_NOTE = re.compile(r"\b(up\s*to|upto|section|elevation|staircase|stair\s*case|below|above|existing)\b", re.I)
# One plan standing for several floors: the drawing says so itself.
_REPEATED = re.compile(r"\btypical\b|\b\d+\s*(?:st|nd|rd|th)?\s*floors?\s+to\b|\bto\s+\d+\s*(?:st|nd|rd|th)\s*floors?\b", re.I)
_LEVEL = re.compile(r"\b(ffl|fgl|lvl|level|rl)\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*(mtrs?|mt|m|mm|ft|')?(?![\w])", re.I)
_UNIT_FT = {"m": FT_PER_M, "mt": FT_PER_M, "mtr": FT_PER_M, "mtrs": FT_PER_M,
            "mm": 1 / 304.8, "ft": 1.0, "'": 1.0}


@dataclass(frozen=True)
class Floor:
    id: str
    name: str
    bounds: tuple[float, float, float, float]       # source coordinates
    elevation_ft: float
    elevation_source: str                           # "assumed" | "level-marker"
    label_text: str = ""
    label_center: Pt | None = None
    confidence: float = 0.0


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _floor_word(s: str):
    if _NOTE.search(s) or not _KIND.search(s):
        return None
    m = _WORD.search(s)
    return m.group(1).lower() if m else None


def _placement(center, bounds, reach):
    """(class, distance): 0 below, 1 inside, 2 above, 3 beside; None when unrelated."""
    x, y = center
    x0, y0, x1, y1 = bounds
    in_x = x0 - reach / 3 <= x <= x1 + reach / 3
    if in_x and x0 <= x <= x1 and y0 <= y <= y1:
        return 1, 0.0
    if in_x and y < y0 and y0 - y <= reach:
        return 0, y0 - y
    if in_x and y > y1 and y - y1 <= reach:
        return 2, y - y1
    if y0 <= y <= y1:
        gap = x0 - x if x < x0 else x - x1
        if 0 < gap <= reach:
            return 3, gap
    return None


def _clusters(ps: PrimitiveSet, upf: float, gap_ft: float):
    out = []
    min_w, min_h = MIN_PLAN_FT[0] * upf, MIN_PLAN_FT[1] * upf
    for region in propose_regions(ps, upf, gap_ft=gap_ft):
        x0, y0, x1, y1 = region.bounds
        w, h = x1 - x0, y1 - y0
        if max(w, h) < max(min_w, min_h) or min(w, h) < min(min_w, min_h):
            continue
        count = sum(1 for p in ps.primitives
                    if p.coords and x0 <= p.coords[0][0] <= x1 and y0 <= p.coords[0][1] <= y1)
        if count >= MIN_PRIMITIVES:
            out.append(region.bounds)
    return out


@dataclass(frozen=True)
class LevelMarker:
    word: str                 # "first", "terrace", ...
    elevation_ft: float
    text: str


def level_markers(ps: PrimitiveSet) -> tuple[LevelMarker, ...]:
    """Finished-floor-level marks such as `+3.6m FIRST FFL`, in feet, lowest first.

    A mark's unit must be written. One that is not takes the unit the other
    marks of the drawing agree on (Aiims Road writes `+3.6 FIRST FFL` and only
    the terrace says `M`); with no written unit anywhere the number would be a
    guess, so it is left out.
    """
    raw = []
    for t in ps.texts:
        s = _squash(t.text)
        word, number = _WORD.search(s), _NUMBER.search(s)
        if word and number and _LEVEL.search(s) and not _NOTE.search(s):
            unit = (number.group(2) or "").lower() or None
            raw.append((word.group(1).lower(), float(number.group(1)), unit, s))
    units = {u for _, _, u, _ in raw if u}
    shared = next(iter(units)) if len(units) == 1 else None
    found = {}
    for word, value, unit, text in raw:
        unit = unit or shared
        if unit is None:
            continue
        feet = value * _UNIT_FT[unit]
        if math.isfinite(feet):
            found.setdefault(word, LevelMarker(word, feet, text))
    return tuple(sorted(found.values(), key=lambda m: (m.elevation_ft, m.word)))


def implied_wall_height(markers, tolerance_ft: float = 0.35) -> float | None:
    """The floor-to-floor spacing the marks agree on, 1 to 50 ft, else None."""
    levels = [m.elevation_ft for m in markers]
    steps = [b - a for a, b in zip(levels, levels[1:])]
    if not steps:
        return None
    middle = sorted(steps)[len(steps) // 2]
    if not 1.0 <= middle <= 50.0 or any(abs(s - middle) > tolerance_ft for s in steps):
        return None
    return round(middle, 3)


def detect_floors(ps: PrimitiveSet, units_per_foot: float, wall_height_ft: float = 10.0,
                  gap_ft: float = 10.0) -> tuple[Floor, ...]:
    """The floors this drawing appears to hold; `()` when it is a single plan."""
    if not all(math.isfinite(v) and v > 0 for v in (units_per_foot, wall_height_ft, gap_ft)):
        raise ValueError("scale, wall height and gap must be finite and positive")
    boxes = _clusters(ps, units_per_foot, gap_ft)
    if len(boxes) < 2:
        return ()
    if any(_REPEATED.search(_squash(t.text)) for t in ps.texts):
        return ()                 # "typical floor": one plan for many floors is not a split
    reach = CAPTION_REACH_FT * units_per_foot
    # Each caption goes to the plan it sits best against; each plan keeps its best caption.
    best: dict[int, tuple] = {}
    for t in ps.texts:
        text = _squash(t.text)
        word = _floor_word(text)
        if word is None or len(text) > 60:
            continue
        center = t.center()
        height = t.bbox[3] - t.bbox[1]
        options = [(p[0], p[1], i) for i, b in enumerate(boxes) if (p := _placement(center, b, reach))]
        if not options:
            continue
        klass, distance, index = min(options)
        rank = (klass, -height, distance)
        if index not in best or rank < best[index][0]:
            best[index] = (rank, text, word, center, klass)

    # Two plans captioned alike: the better caption keeps the name, the other is unnamed.
    owner = {}
    for index, (rank, _, word, _, _) in best.items():
        if word not in owner or rank < best[owner[word]][0]:
            owner[word] = index
    best = {i: v for i, v in best.items() if owner[v[2]] == i}

    def area(b):
        return (b[2] - b[0]) * (b[3] - b[1])
    named_areas = sorted(area(boxes[i]) for i in best)
    if not named_areas:
        return ()                 # no caption anywhere: nothing says these plans are floors
    median = named_areas[len(named_areas) // 2]
    entries = []
    for i, bounds in enumerate(boxes):
        if i not in best and area(bounds) < 0.5 * median:
            continue              # an uncaptioned fragment, a detail, not a floor
        if i in best:
            _, text, word, center, klass = best[i]
            name, order = FLOOR_WORDS[word]
            entries.append((order, i, bounds, name, word, text, center, (0.95, 0.8, 0.55, 0.5)[klass]))
        else:
            entries.append((100 + i, i, bounds, "", None, "", None, 0.2))
    if len(entries) < 2:
        return ()
    entries.sort(key=lambda e: (e[0], e[2][1], e[2][0]))

    markers = {m.word: m.elevation_ft for m in level_markers(ps)}
    floors, seen, previous, unnamed = [], {}, None, 0
    for _, _, bounds, name, word, text, center, confidence in entries:
        if not name:
            unnamed += 1
            name = f"Unnamed plan {len(floors) + 1}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"
        if word in markers:
            elevation, source = markers[word], "level-marker"
        else:
            elevation, source = (0.0 if previous is None else previous + wall_height_ft), "assumed"
        previous = elevation
        floors.append(Floor(f"floor-{len(floors) + 1:02d}", name, tuple(bounds), round(elevation, 6),
                            source, text, center, confidence))
    return tuple(floors)
