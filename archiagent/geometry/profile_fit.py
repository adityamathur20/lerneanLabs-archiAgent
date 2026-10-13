"""Split walls that straddle an accepted wall profile at the profile's edge.

An accepted wall profile is the authoritative solid for the wall it outlines.
A detected wall that lies wholly inside it is that wall; one wholly outside is
a different wall. Detectors disagree by a few percent, so a wall often lies
partly on a profile, and the IFC guard (ifc/profile_layout.py) refuses to guess
which it is. This module settles it before authoring, with no guess: the part
of the wall that lies on the profile is left for the profile to host, and the
rest stays a wall of its own, cut back to the profile's edge.

Every wall that leaves this module is either covered by one profile or clear
of all of them, which is exactly what the guard checks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.affinity import affine_transform
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

from archiagent.geometry.junctions import RESIDUE_FT

# Junction repair may move a wall end by up to RESIDUE_FT after the walls are
# fitted here, and rounds every coordinate to 1e-9 ft. A wall that lies on a
# profile edge by no more than twice that is touching it, not overlapping it.
TOUCH_FT = 2 * RESIDUE_FT
OVERLAP_AREA_SQFT = 1e-14
# A piece shorter than this is a stub poking out of a profile, not a wall.
MIN_PIECE_LENGTH_FT = 0.25
# A strip thinner than this is a drawing offset, not a wall.
MIN_STRIP_THICKNESS_FT = 1 / 12
# An opening may be cut back to the gap between profiles when at least this
# share of its width remains; less than that is a different opening.
MIN_OPENING_SHARE = 0.5
# The part left inside a profile is kept as a wall record (rooms and junctions
# read it) only if it is at least this share of the thickness, or hosts an opening.
HOSTED_THICKNESS_SHARE = 0.5
OPENING_REACH_FT = 0.05


def wall_footprint(wall) -> Polygon:
    return LineString((wall.start, wall.end)).buffer(wall.thickness_ft / 2, cap_style=2)


def overlapping_profiles(footprint: Polygon, polygons, candidates=None) -> list[int]:
    """Indices of profiles the footprint lies on, beyond merely touching them."""
    core = footprint.buffer(-TOUCH_FT)
    if core.is_empty:
        return []
    indices = range(len(polygons)) if candidates is None else candidates
    return [k for k in indices if core.intersection(polygons[k]).area > OVERLAP_AREA_SQFT]


@dataclass(frozen=True)
class Fit:
    walls: tuple
    trimmed: int = 0                  # walls cut back to a profile's edge
    hosted: int = 0                   # pieces left on a profile for it to host
    dropped: int = 0                  # slivers, stubs and ragged remainders left to the profile
    lost_openings: tuple[str, ...] = ()   # openings no piece can host any more
    openings: tuple = ()              # as given, narrowed where the gap between profiles is smaller
    narrowed: int = 0                 # openings cut back to the gap between profiles


def fit_walls_to_profiles(walls, profiles, openings=()) -> Fit:
    walls = tuple(walls)
    profiles = tuple(profiles)
    openings = tuple(openings)
    if not walls or not profiles:
        return Fit(walls, openings=openings)
    polygons = [Polygon(p.boundary, p.holes) for p in profiles]
    tree = STRtree(polygons)
    out, trimmed, hosted, dropped, lost = [], 0, 0, 0, []
    narrowed_by_id = {}
    for wall in walls:
        footprint = wall_footprint(wall)
        near = tree.query(footprint)
        if (not overlapping_profiles(footprint, polygons, near)
                or any(polygons[k].buffer(TOUCH_FT).covers(footprint) for k in near)):
            out.append(wall)          # clear of every profile, or wholly on one: the guard maps it as is
            continue
        pieces, n_hosted, n_dropped = _split(wall, polygons, tree, openings)
        out.extend(pieces)
        trimmed += 1
        hosted += n_hosted
        dropped += n_dropped
        for o in _unhosted_openings(wall, pieces, openings):
            narrowed = _narrow(o, pieces)
            if narrowed is None:
                lost.append(o.id)
            else:
                narrowed_by_id[o.id] = narrowed
    final = tuple(narrowed_by_id.get(o.id, o) for o in openings)
    return Fit(tuple(out), trimmed, hosted, dropped, tuple(sorted(set(lost))),
               final, len(narrowed_by_id))


def _split(wall, polygons, tree, openings):
    """Pieces of one wall: free ones (clear of every profile) and hosted ones."""
    work, final, hosted, dropped = [wall], [], 0, 0
    while work:
        piece = work.pop()
        footprint = wall_footprint(piece)
        hits = overlapping_profiles(footprint, polygons, tree.query(footprint))
        if not hits:
            final.append(piece)
            continue
        free, kept, lost = _cut(piece, footprint, polygons[min(hits)], openings)
        work.extend(free)
        final.extend(kept)
        hosted += len(kept)
        dropped += lost
    final.sort(key=lambda w: (w.start, w.end))
    return tuple(final), hosted, dropped


def _cut(wall, footprint, polygon, openings):
    """Split a wall against one profile: (free pieces, hosted pieces, dropped count).

    Both the part of the wall off the profile and the part on it are tiled with
    rectangles inscribed in them, so a piece is exactly clear of the profile or
    exactly covered by it, whatever shape the overlap has.
    """
    sx, sy = wall.start
    length = wall.length_ft
    ux, uy = (wall.end[0] - sx) / length, (wall.end[1] - sy) / length
    nx, ny = -uy, ux
    half = wall.thickness_ft / 2
    to_local = (ux, uy, nx, ny, -(sx * ux + sy * uy), -(sx * nx + sy * ny))
    body = Polygon(((0, -half), (length, -half), (length, half), (0, half)))
    on_profile = affine_transform(footprint.intersection(polygon), to_local).intersection(body)
    off_profile = body.difference(on_profile)

    def piece(ua, ub, va, vb):
        vc = (va + vb) / 2
        return replace(wall, start=(sx + ua * ux + vc * nx, sy + ua * uy + vc * ny),
                       end=(sx + ub * ux + vc * nx, sy + ub * uy + vc * ny),
                       thickness_ft=vb - va)

    free, kept, dropped, tiled = [], [], 0, 0.0
    for ua, ub, va, vb in _rectangles(off_profile):
        tiled += (ub - ua) * (vb - va)
        # A strip across the thickness is a wall only if it is thick enough.
        thick = vb - va >= min(MIN_STRIP_THICKNESS_FT, wall.thickness_ft)
        if ub - ua >= MIN_PIECE_LENGTH_FT and thick:
            free.append(piece(ua, ub, va, vb))
        else:
            dropped += 1
    for ua, ub, va, vb in _rectangles(on_profile):
        tiled += (ub - ua) * (vb - va)
        carries = _opening_in(wall, ua, ub, openings, (sx, sy), (ux, uy), (nx, ny), half)
        big = (vb - va >= HOSTED_THICKNESS_SHARE * wall.thickness_ft and ub - ua >= MIN_PIECE_LENGTH_FT)
        if carries or big:
            kept.append(piece(ua, ub, va, vb))
        else:
            dropped += 1
    if body.area - tiled > TOUCH_FT * body.length:      # slivers no rectangle fits in
        dropped += 1
    return free, kept, dropped


def _rectangles(region):
    """Rectangles (u0, u1, v0, v1) inscribed in a region, with equal neighbours merged.

    Cut the region at every vertex's u; in each slab the largest rectangle that
    fits is the overlap of the region's v-extent at the slab's two ends. For the
    rectilinear shapes drawings are made of this tiles the region exactly.
    """
    parts = [g for g in getattr(region, "geoms", (region,)) if g.geom_type == "Polygon" and g.area > 0]
    if not parts:
        return []
    us = sorted({round(x, 9) for g in parts for ring in (g.exterior, *g.interiors)
                 for x, _ in ring.coords})
    low, high = region.bounds[1] - 1.0, region.bounds[3] + 1.0
    slabs = []
    for ua, ub in zip(us, us[1:]):
        if ub - ua < 1e-9:
            continue
        d = min(1e-7, (ub - ua) / 4)
        left, right = (_extents(region, ua + d, low, high), _extents(region, ub - d, low, high))
        for a0, a1 in left:
            for b0, b1 in right:
                lo, hi = max(a0, b0), min(a1, b1)
                if hi - lo > 1e-9:
                    slabs.append([ua, ub, lo, hi])
    slabs.sort(key=lambda s: (round(s[2], 6), round(s[3], 6), s[0]))
    merged = []
    for s in slabs:
        last = merged[-1] if merged else None
        if last and abs(last[2] - s[2]) < 1e-6 and abs(last[3] - s[3]) < 1e-6 and abs(last[1] - s[0]) < 1e-9:
            last[1] = s[1]
        else:
            merged.append(s)
    return [tuple(m) for m in merged]


def _extents(region, u, low, high):
    """The v-intervals of a region along the line at u."""
    cut = LineString([(u, low), (u, high)]).intersection(region)
    lines = [g for g in getattr(cut, "geoms", (cut,)) if g.geom_type == "LineString" and g.length > 0]
    return [(min(c[1] for c in g.coords), max(c[1] for c in g.coords)) for g in lines]


def _opening_in(wall, u0, u1, openings, origin, along, across, half) -> bool:
    for o in openings:
        for p in (o.start, o.end):
            dx, dy = p[0] - origin[0], p[1] - origin[1]
            u = dx * along[0] + dy * along[1]
            v = dx * across[0] + dy * across[1]
            if abs(v) > half + OPENING_REACH_FT or not u0 - 1e-5 <= u <= u1 + 1e-5:
                break
        else:
            return True
    return False


def _covers(wall, segment) -> bool:
    return LineString((wall.start, wall.end)).buffer(1e-5).covers(segment)


def _unhosted_openings(original, pieces, openings) -> list:
    """Openings the original wall's axis covered that no piece's axis covers now."""
    out = []
    for o in openings:
        segment = LineString((o.start, o.end))
        if _covers(original, segment) and not any(_covers(p, segment) for p in pieces):
            out.append(o)
    return out


def _narrow(opening, pieces):
    """The opening cut back to the piece that holds most of it, or None.

    A generated host runs jamb to jamb, so its ends ride into the profiles of the
    walls either side. Cutting the host at the profile's edge leaves the real gap;
    the opening is that gap, on the piece's own axis.
    """
    best, best_width = None, 0.0
    for p in pieces:
        length = p.length_ft
        ux, uy = (p.end[0] - p.start[0]) / length, (p.end[1] - p.start[1]) / length
        a, b = (((q[0] - p.start[0]) * ux + (q[1] - p.start[1]) * uy) for q in (opening.start, opening.end))
        lo, hi = max(min(a, b), 0.0), min(max(a, b), length)
        if hi - lo > best_width:
            best, best_width = (p, lo, hi, (a <= b)), hi - lo
    if best is None or best_width < MIN_OPENING_SHARE * opening.width_ft or best_width < MIN_PIECE_LENGTH_FT:
        return None
    p, lo, hi, forward = best
    ux, uy = (p.end[0] - p.start[0]) / p.length_ft, (p.end[1] - p.start[1]) / p.length_ft
    at = lambda u: (p.start[0] + u * ux, p.start[1] + u * uy)
    start, end = (at(lo), at(hi)) if forward else (at(hi), at(lo))
    return replace(opening, start=start, end=end)


__all__ = ["Fit", "fit_walls_to_profiles", "overlapping_profiles", "wall_footprint",
           "TOUCH_FT", "OVERLAP_AREA_SQFT"]
