"""Geometry-first wall candidacy: score every proposed wall run, keep the walls.

Layer role is one weighted signal, never a gate. Scoring is pure and
deterministic; only runs the score cannot settle may go to an adjudicator.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

import networkx as nx
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from archiagent.classify.layers import WALL_CONFIDENCE_FLOOR, WALL_ROLES, Classification
from archiagent.classify.roles import Role
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import PrimitiveSet

SMALL_FT = 4.0        # a closed outline or run below this is fixture/glyph scale
REPEAT_MIN = 3        # a block inserted this often is a symbol, not a one-off
SHORT_FT = 1.5
WALL_SCALE_FT = 8.0
NEGATIVE_ROLES: frozenset[Role] = frozenset({
    Role.FURNITURE, Role.STAIR, Role.ANNOTATION, Role.PLUMBING, Role.ELECTRICAL,
    Role.VEHICLE, Role.LANDSCAPE, Role.RAILING, Role.DOOR, Role.WINDOW})
_GLYPH_TYPES = frozenset({"CIRCLE", "HATCH"})


@dataclass(frozen=True)
class Context:
    roles: dict                # casefolded layer -> (Role, confidence)
    outlines: dict             # source id -> closed non-glyph polygons, in feet
    siblings: dict             # entity id -> ids sharing its parent block instance
    parents: dict              # entity id -> parent entity id
    block_of: dict             # INSERT entity id -> block name
    insert_counts: Counter     # block name -> INSERT entity count
    glyph_tree: STRtree | None
    glyph_points: tuple[Point, ...]


def build_context(ps: PrimitiveSet, classification: Classification,
                  units_per_foot: float) -> Context:
    roles = {d.layer.casefold(): (d.role, d.confidence) for d in classification}
    outlines: dict[str, list[Polygon]] = {}
    glyphs: list[Point] = []
    for i, p in enumerate(ps.primitives):
        sid = p.source_id or f"primitive-{i}"
        pts = [(x / units_per_foot, y / units_per_foot) for x, y in p.coords]
        if p.entity_type in _GLYPH_TYPES or p.kind == "fill":
            glyphs.append(Point(sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)))
            continue
        if p.closed and len(pts) >= 3:
            poly = Polygon(pts)
            if poly.is_valid and poly.area > 0:
                outlines.setdefault(sid, []).append(poly)
    parents = {e.id: e.parent_id for e in ps.entities if e.parent_id}
    children: dict[str, list[str]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    block_of = {e.id: e.block_name for e in ps.entities if e.kind == "INSERT" and e.block_name}
    return Context(roles, {k: tuple(v) for k, v in outlines.items()},
                   {child: tuple(children[parent]) for child, parent in parents.items()},
                   parents, block_of, Counter(block_of.values()),
                   STRtree(glyphs) if glyphs else None, tuple(glyphs))


def _layer_role(wall: WallSeg, ctx: Context) -> float:
    role, confidence = ctx.roles.get(wall.source_layer.casefold(), (Role.IGNORE, 0.0))
    if role in WALL_ROLES:
        return confidence
    if role in NEGATIVE_ROLES:
        return -0.5 * confidence
    return 0.0


def _length(wall: WallSeg) -> float:
    ramp = (wall.length_ft - SHORT_FT) / (WALL_SCALE_FT - SHORT_FT) * 2 - 1
    return max(-1.0, min(1.0, ramp))


def _small_outlines(wall: WallSeg, ctx: Context) -> list[Polygon]:
    """Closed outlines of this run's faces and of their siblings in one block instance."""
    ids = set(wall.source_ids)
    for sid in wall.source_ids:
        ids.update(ctx.siblings.get(sid, ()))
    return [poly for sid in sorted(ids) for poly in ctx.outlines.get(sid, ())
            if max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) < SMALL_FT]


def _nested_outline(wall: WallSeg, ctx: Context) -> float:
    small = _small_outlines(wall, ctx)
    nested = any(i != j and a.contains(b) for i, a in enumerate(small) for j, b in enumerate(small))
    return -1.0 if nested else 0.0


def _glyph(wall: WallSeg, ctx: Context) -> float:
    if ctx.glyph_tree is None:
        return 0.0
    for poly in _small_outlines(wall, ctx):
        if any(poly.contains(ctx.glyph_points[int(k)]) for k in ctx.glyph_tree.query(poly)):
            return -1.0
    return 0.0


def _instancing(wall: WallSeg, ctx: Context) -> float:
    if wall.length_ft >= SMALL_FT:
        return 0.0
    for sid in wall.source_ids:
        node = ctx.parents.get(sid, "")
        while node and node not in ctx.block_of:
            node = ctx.parents.get(node, "")
        if node and ctx.insert_counts[ctx.block_of[node]] >= REPEAT_MIN:
            return -1.0
    return 0.0


def candidate_signals(wall: WallSeg, ctx: Context) -> tuple[tuple[str, float], ...]:
    """The signals that need only this run and the drawing, not the other runs."""
    return (("layer_role", _layer_role(wall, ctx)), ("length", _length(wall)),
            ("instancing", _instancing(wall, ctx)), ("glyph", _glyph(wall, ctx)),
            ("nested_outline", _nested_outline(wall, ctx)))


ACCEPT_FLOOR = 0.65      # starting values; tuned only against wall-coverage metrics
REJECT_CEILING = 0.35
LOOP_MIN_FT = 10.0       # a closed loop smaller than this is furniture/fixture scale
JOIN_SLACK_FT = 1 / 12
MODE_SHARE = 0.10
MODE_TOLERANCE_FT = 1 / 12
# Connectivity, closure and nested outlines separate the two reported defects;
# layer role is mid-weight so geometry can outvote it.
WEIGHTS = {"layer_role": 1.0, "length": 1.0, "connectivity": 2.0, "closure": 2.0,
           "thickness": 0.5, "instancing": 1.5, "glyph": 1.5, "nested_outline": 2.5}


@dataclass(frozen=True)
class ScoredCandidate:
    id: str
    wall: WallSeg
    signals: tuple[tuple[str, float], ...]
    score: float
    band: str  # "accept" | "reject" | "ambiguous"


def candidate_id(wall: WallSeg) -> str:
    key = repr((wall.start, wall.end, round(wall.thickness_ft, 9), wall.source_layer,
                wall.detector, wall.source_ids))
    return "cand-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def combine(signals: tuple[tuple[str, float], ...]) -> float:
    weighted = sum(WEIGHTS[name] * value for name, value in signals) / sum(WEIGHTS.values())
    return round((weighted + 1) / 2, 6)


def band(score: float) -> str:
    if score >= ACCEPT_FLOOR:
        return "accept"
    if score <= REJECT_CEILING:
        return "reject"
    return "ambiguous"


def _touches(walls: tuple[WallSeg, ...]) -> list[tuple[frozenset[int], frozenset[int]]]:
    """Per run, the other runs touched at its start and at its end."""
    lines = [LineString([w.start, w.end]) for w in walls]
    tree = STRtree(lines)
    reach = max((w.thickness_ft for w in walls), default=0.0) + JOIN_SLACK_FT
    out = []
    for i, w in enumerate(walls):
        ends = []
        for end in (w.start, w.end):
            point = Point(end)
            ends.append(frozenset(
                int(j) for j in tree.query(point.buffer(reach)) if int(j) != i and
                lines[int(j)].distance(point) <= max(w.thickness_ft, walls[int(j)].thickness_ft) + JOIN_SLACK_FT))
        out.append((ends[0], ends[1]))
    return out


def _connectivity(walls, touches) -> list[float]:
    """Ends joined to a run built from DIFFERENT source geometry; a glyph's own sides don't count."""
    out = []
    for i, w in enumerate(walls):
        own = set(w.source_ids)
        joined = sum(1 for end in touches[i]
                     if any(not own.intersection(walls[j].source_ids) for j in end))
        out.append((-1.0, 0.3, 1.0)[joined])
    return out


def _closure(walls, touches) -> list[float]:
    """+1 on a building-scale loop, -1 only on fixture-scale loops, 0 on no loop."""
    graph = nx.Graph()
    graph.add_nodes_from(range(len(walls)))
    graph.add_edges_from((i, j) for i, ends in enumerate(touches) for end in ends for j in end)
    out = [0.0] * len(walls)
    for component in nx.biconnected_components(graph):
        if len(component) < 3:
            continue
        xs = [c for k in component for c in (walls[k].start[0], walls[k].end[0])]
        ys = [c for k in component for c in (walls[k].start[1], walls[k].end[1])]
        large = max(max(xs) - min(xs), max(ys) - min(ys)) >= LOOP_MIN_FT
        for k in component:
            out[k] = 1.0 if large else (out[k] if out[k] > 0 else -1.0)
    return out


def _thickness_modes(walls, ctx: Context) -> tuple[float, ...]:
    """Thicknesses carrying at least MODE_SHARE of confident wall-layer run length."""
    lengths: Counter = Counter()
    for w in walls:
        role, confidence = ctx.roles.get(w.source_layer.casefold(), (Role.IGNORE, 0.0))
        if role in WALL_ROLES and confidence >= WALL_CONFIDENCE_FLOOR:
            lengths[round(w.thickness_ft * 48) / 48] += w.length_ft
    total = sum(lengths.values())
    return tuple(t for t, length in sorted(lengths.items()) if total and length / total >= MODE_SHARE)


def _thickness(wall: WallSeg, modes: tuple[float, ...]) -> float:
    if not modes:
        return 0.0
    return 1.0 if any(abs(wall.thickness_ft - m) <= MODE_TOLERANCE_FT for m in modes) else -0.5


def score_candidates(walls, ctx: Context) -> tuple[ScoredCandidate, ...]:
    walls = tuple(walls)
    touches = _touches(walls)
    connectivity, closure = _connectivity(walls, touches), _closure(walls, touches)
    modes = _thickness_modes(walls, ctx)
    out = []
    for i, w in enumerate(walls):
        signals = candidate_signals(w, ctx) + (
            ("connectivity", connectivity[i]), ("closure", closure[i]),
            ("thickness", _thickness(w, modes)))
        score = combine(signals)
        out.append(ScoredCandidate(candidate_id(w), w, signals, score, band(score)))
    return tuple(out)
