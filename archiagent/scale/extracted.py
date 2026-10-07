"""Scale read from the dimensions a drawing already carries.

Generated dimension text is better calibration evidence than an explicit
override. Generated text is the CAD rendering the measured span into the
drafter's own unit system, so the ratio of span to text reveals the units
reliably because a machine produced it. An override is a number a human typed,
which need not match the geometry at all -- overriding dimension text to make a
drawing read correctly is ordinary practice. A dimstyle linear factor other than
1 means displayed and drawn lengths were deliberately decoupled, so such a
dimension cannot calibrate anything and is excluded outright.

Rejections travel with the result, so a drawing full of dimensions that still
yields no scale can explain itself rather than failing mutely.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from archiagent.scale.verify import parse_explicit_length

USABLE_TYPES = ("linear", "aligned", "rotated")
NEUTRAL_FACTORS = ("", "1", "1.0")

# Metres per unit, for the units a floor plan is actually drawn in.
UNITS_IN_METRES = {"in": 0.0254, "ft": 0.3048, "mm": 0.001, "cm": 0.01, "m": 1.0}
FACTOR_TOLERANCE = 0.01


def dimstyle_units_per_foot(factor: float) -> float | None:
    """The drawing's unit implied by a dimstyle linear factor, or None.

    DIMLFAC multiplies a measured span to produce the number the CAD displays,
    so it is the ratio between the drawing's unit and the unit the drafter reads
    in. Aiims Road's 424 dimensions all carry 0.0254 and no text at all: that is
    inches per metre, so the drawing is in inches and displays metres.

    Only an unambiguous factor is accepted. A factor of 1 is every unit shown as
    itself and identifies nothing, so it returns None rather than guessing.
    """
    if isinstance(factor, bool) or not isinstance(factor, (int, float)):
        return None
    if not math.isfinite(factor) or factor <= 0:
        return None
    drawn = set()
    for drawing_unit, drawing_m in UNITS_IN_METRES.items():
        for display_m in UNITS_IN_METRES.values():
            if abs(factor - drawing_m / display_m) <= FACTOR_TOLERANCE * factor:
                drawn.add(drawing_unit)
    if len(drawn) != 1:
        return None
    return UNITS_IN_METRES["ft"] / UNITS_IN_METRES[drawn.pop()]


@dataclass(frozen=True)
class ExtractedScale:
    units_per_foot: float
    spans: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    support: int
    basis: str
    rejected: tuple[tuple[str, str], ...] = ()


def _metadata(ps):
    return {e.id: dict(e.metadata) for e in ps.entities if e.kind == "DIMENSION"}


def _usable(ps):
    """Split dimensions into generated, overridden and unusable."""
    meta = _metadata(ps)
    generated, overridden, rejected = [], [], []
    for d in ps.dimensions:
        if d.measurement_type not in USABLE_TYPES:
            rejected.append((d.id, f"measurement type {d.measurement_type}"))
            continue
        info = meta.get(d.id, {})
        factor = str(info.get("dimstyle_dimlfac", ""))
        if factor not in NEUTRAL_FACTORS:
            rejected.append((d.id, f"dimlfac {factor}: displayed length is scaled"))
            continue
        feet = parse_explicit_length(d.text)
        if feet is None or feet <= 0:
            rejected.append((d.id, f"text {d.text!r} is not a length"))
            continue
        span = abs(d.measured_source_units)
        if not math.isfinite(span) or span <= 0:
            rejected.append((d.id, "measured span is not positive"))
            continue
        entry = (d, feet, span / feet)
        if info.get("dimension_text_origin") == "explicit-override":
            overridden.append(entry)
        else:
            generated.append(entry)
    return generated, overridden, rejected


def _agree(entries, tolerance_in):
    """(median ratio, supporting, outliers), or None when fewer than two agree."""
    if len(entries) < 2:
        return None
    median = statistics.median([ratio for _, _, ratio in entries])
    if not math.isfinite(median) or median <= 0:
        return None
    supporting, outliers = [], []
    for dimension, feet, ratio in entries:
        measured_ft = abs(dimension.measured_source_units) / median
        target = supporting if abs(measured_ft - feet) * 12 <= tolerance_in else outliers
        target.append((dimension, feet, ratio))
    if len(supporting) < 2:
        return None
    # Re-centre on the agreeing subset so one wild outlier cannot bias the result.
    median = statistics.median([ratio for _, _, ratio in supporting])
    return median, supporting, outliers


def extracted_scale(ps, tolerance_in: float = 2.0) -> ExtractedScale | None:
    """Source units per foot from the drawing's own dimensions, or None.

    Prefers generated dimension text; falls back to explicit overrides only when
    no generated pair agrees. Returns None when nothing agrees, which is a
    normal outcome for a drawing that carries no dimensions.
    """
    if not isinstance(tolerance_in, (int, float)) or isinstance(tolerance_in, bool) \
            or not math.isfinite(tolerance_in) or tolerance_in <= 0:
        raise ValueError("scale tolerance_in must be finite and positive")
    generated, overridden, rejected = _usable(ps)
    for entries, basis in ((generated, "native-dimension"),
                           (overridden, "native-dimension-override")):
        agreed = _agree(entries, tolerance_in)
        if agreed is None:
            continue
        median, supporting, outliers = agreed
        unused = [(d.id, "disagrees with the agreed scale") for d, _, _ in outliers]
        if entries is generated:
            unused += [(d.id, "explicit override unused; generated text agreed")
                       for d, _, _ in overridden]
        return ExtractedScale(median,
                              tuple((d.start, d.end) for d, _, _ in supporting),
                              len(supporting), basis,
                              tuple(sorted(rejected + unused)))
    return _from_dimstyle(ps, rejected)


def _from_dimstyle(ps, rejected):
    """Fall back to what the dimstyle factors say the drawing's unit is.

    This is weaker than a measured ratio and is used only when no dimension
    carries usable text -- which is the normal case, because generated text is
    rendered at display time and is not stored in the file at all.
    """
    meta = _metadata(ps)
    implied = {}
    for d in ps.dimensions:
        if d.measurement_type not in USABLE_TYPES:
            continue
        raw = meta.get(d.id, {}).get("dimstyle_dimlfac", "")
        try:
            factor = float(raw)
        except (TypeError, ValueError):
            continue
        scale = dimstyle_units_per_foot(factor)
        if scale is not None:
            implied.setdefault(round(scale, 6), []).append(d)
    if len(implied) != 1:
        return None
    scale, supporting = next(iter(implied.items()))
    return ExtractedScale(scale, tuple((d.start, d.end) for d in supporting),
                          len(supporting), "dimstyle-factor", tuple(sorted(rejected)))
