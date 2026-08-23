"""Parse printed dimension strings into feet.

Source drawings are inconsistent: 14'-5", 11'6", and the typo 4'6' all
appear in our samples and all mean the same kind of thing. Parse
permissively, then reject implausible magnitudes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from archiagent.primitives import Pt, PrimitiveSet

# Vulgar fractions as CAD emits them. Architectural drawings dimension to
# the half or quarter inch routinely, so rejecting these loses real
# dimensions: the ground-floor plan's overall chain is
# 18'-7½" + 6'-4½" + 15'-0" + 10'-0", and dropping the two fractional
# terms both loses 40% of the chain and breaks its sum against the
# printed 50'-0".
_VULGAR = {
    "½": 0.5,    # ½
    "¼": 0.25,   # ¼
    "¾": 0.75,   # ¾
    "⅛": 0.125,  # ⅛
    "⅜": 0.375,  # ⅜
    "⅝": 0.625,  # ⅝
    "⅞": 0.875,  # ⅞
}

# <feet> ' [-] [<inches>] [<fraction>] ["]
# The fraction is either a vulgar glyph or an ASCII n/m. Whole inches are
# optional so that 7'-1/2" parses; the engine backtracks out of reading the
# numerator as whole inches when the fraction alternative is the only fit.
_FEET_INCHES = re.compile(
    r"""^(\d{1,3})\s*'\s*-?\s*"""                      # feet
    r"""(?:(\d{1,2})\s*)?"""                           # optional whole inches
    r"""(?:([""" + "".join(_VULGAR) + r"""])"""        # vulgar fraction
    r"""|(\d{1,2})\s*/\s*(\d{1,2}))?"""                # or ASCII n/m
    r"""\s*["']?$"""
)

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

    inches = float(m.group(2)) if m.group(2) is not None else 0.0
    if m.group(3) is not None:
        inches += _VULGAR[m.group(3)]
    elif m.group(4) is not None:
        denominator = float(m.group(5))
        if denominator == 0:
            return None
        inches += float(m.group(4)) / denominator

    # 12in or more should have been written as another foot. Checked AFTER
    # the fraction is added, so 11¾" passes and 12½" does not.
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
