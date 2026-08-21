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
