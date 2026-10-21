"""Windows drawn as thin parallel lines across a gap in a wall, with no symbol.

Most windows on a plan are not blocks: the wall stops, and two or three thin
lines (frame, glazing, sill) bridge the gap. Symbol matching never sees them, so
they stayed open holes. Here such a gap becomes a window symbol with its span
stated, and the existing opening logic hosts it like any other.

Evidence is local to the gap: two or more thin lines, parallel to the wall,
inside its thickness band, each spanning most of the gap, not already used by a
wall or symbol. A gap with nothing across it is left as the open passage it is.
"""
from __future__ import annotations

import hashlib
import json
import math

from archiagent.geometry.room_gaps import MAX_GAP_FT, find_room_gaps
from archiagent.semantic import SymbolInstance

MIN_GAP_FT = 1.5
BAND_FT = 0.5               # lines this close to the wall axis lie in the wall, not beside it
SPAN_SHARE = 0.7            # a line must cover this much of the gap
PARALLEL_TOL = 0.05         # |sin| of the angle to the gap axis
MIN_LINES = 2
MIN_SEPARATION_FT = 0.02    # coincident lines are one line drawn twice


def find_gap_windows(ps, walls, symbols, units_per_foot: float,
                     max_gap_ft: float = MAX_GAP_FT) -> tuple[SymbolInstance, ...]:
    gaps = [g for g in find_room_gaps(walls, symbols, max_gap_ft)
            if g.kind == "collinear" and MIN_GAP_FT <= g.length_ft <= max_gap_ft]
    if not gaps:
        return ()
    used = {sid for w in walls for sid in w.source_ids} | {sid for s in symbols for sid in s.source_ids}
    segments = []
    for p in ps.primitives:
        if p.source_id in used or p.kind in ("curve", "fill"):
            continue
        for a, b in p.segments():
            segments.append((tuple(v / units_per_foot for v in a), tuple(v / units_per_foot for v in b),
                             p.source_id))
    out = []
    for gap in gaps:
        n = gap.length_ft
        ux, uy = (gap.end[0] - gap.start[0]) / n, (gap.end[1] - gap.start[1]) / n
        offsets, ids = [], []
        for a, b, sid in segments:
            length = math.dist(a, b)
            if length < SPAN_SHARE * n * 0.5:
                continue
            dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            if abs(dx * uy - dy * ux) > PARALLEL_TOL:
                continue
            across = [((p[0] - gap.start[0]) * -uy + (p[1] - gap.start[1]) * ux) for p in (a, b)]
            along = sorted((p[0] - gap.start[0]) * ux + (p[1] - gap.start[1]) * uy for p in (a, b))
            if max(abs(c) for c in across) > BAND_FT:
                continue
            covered = min(along[1], n) - max(along[0], 0.0)
            if covered < SPAN_SHARE * n:
                continue
            offsets.append(sum(across) / 2)
            ids.append(sid)
        distinct = sorted(offsets)
        count = sum(1 for i, v in enumerate(distinct) if i == 0 or v - distinct[i - 1] >= MIN_SEPARATION_FT)
        if count < MIN_LINES:
            continue
        mid = ((gap.start[0] + gap.end[0]) / 2, (gap.start[1] + gap.end[1]) / 2)
        digest = hashlib.sha256(repr((gap.start, gap.end)).encode()).hexdigest()[:12]
        out.append(SymbolInstance(
            f"gapwin-{digest}", "window", mid, n, max(distinct) - min(distinct) or 0.25,
            rotation_rad=math.atan2(uy, ux), source_ids=tuple(sorted(set(ids))),
            evidence="wall-gap-lines", confidence=0.6,
            boundary=(gap.start, gap.end),
            properties=(("opening_start_ft", json.dumps(list(gap.start))),
                        ("opening_end_ft", json.dumps(list(gap.end))),
                        ("assumed", "window from thin lines across a wall gap"))))
    return tuple(out)
