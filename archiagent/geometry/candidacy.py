"""Geometry-first wall candidacy: score every proposed wall run, keep the walls.

Layer role is one weighted signal, never a gate. Scoring is pure and
deterministic; only runs the score cannot settle may go to an adjudicator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from archiagent.classify.layers import WALL_ROLES, Classification
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
