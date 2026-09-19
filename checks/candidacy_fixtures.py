"""Authored-from-scratch wall-candidacy geometry. No client data; feet, units_per_foot=1."""
import math

from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
from archiagent.evidence import SourceEntity
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Primitive, PrimitiveSet

CLASSIFICATION = (LayerDecision("WALL", Role.WALL_PARTITION, .9, "", "llm"),
                  LayerDecision("furni", Role.FURNITURE, .9, "", "llm"),
                  LayerDecision("PROJECTION", Role.ANNOTATION, .45, "", "llm"))


def wall(a, b, t=.75, layer="WALL", ids=()):
    return WallSeg(a, b, t, layer, "paired-line", "measured", tuple(ids))


def square(x, y, side):
    return ((x, y), (x + side, y), (x + side, y + side), (x, y + side))


def source(primitives=(), entities=()):
    return PrimitiveSet(tuple(primitives), (), 100., 100., "fixture.dxf", "c" * 64, tuple(entities))


def house(extra=()):
    """30x20ft rectangle of 9in walls: right side on WALL, the other three on furni."""
    return (wall((0, 20), (30, 20), layer="furni", ids=("t0", "t1")),
            wall((30, 0), (30, 20), layer="WALL", ids=("r0", "r1")),
            wall((0, 0), (30, 0), layer="furni", ids=("b0", "b1")),
            wall((0, 0), (0, 20), layer="furni", ids=("l0", "l1"))) + tuple(extra)


def projection():
    """A 6ft, 4.5in run on an annotation layer touching the house's left wall at one end."""
    return wall((-6, 10), (0, 10), .375, layer="PROJECTION", ids=("p0", "p1"))


def shower(k, x, y, side=1.5):
    """One shower glyph in a nested block: two concentric closed squares 0.5ft apart
    plus a centre circle, all on layer WALL. Returns (entities, primitives, walls)."""
    top, block = f"top-{k}", f"top-{k}/0/1:INSERT"
    outer, inner, circle = (f"{block}/0/{n}:{kind}" for n, kind in
                            ((0, "LWPOLYLINE"), (1, "LWPOLYLINE"), (2, "CIRCLE")))
    cx, cy = x + side / 2, y + side / 2
    entities = (SourceEntity(top, "INSERT", "S-WALL", block_name="BATHROOM"),
                SourceEntity(block, "INSERT", "WALL", block_name="GLYPH", parent_id=top),
                SourceEntity(outer, "LWPOLYLINE", "WALL", parent_id=block, closed=True),
                SourceEntity(inner, "LWPOLYLINE", "WALL", parent_id=block, closed=True),
                SourceEntity(circle, "CIRCLE", "WALL", parent_id=block, center=(cx, cy), radius=.1))
    ring = tuple((cx + .1 * math.cos(n / 8 * math.tau), cy + .1 * math.sin(n / 8 * math.tau))
                 for n in range(8))
    primitives = (Primitive("line", square(x, y, side), "WALL", None, None, outer, "LWPOLYLINE", True),
                  Primitive("line", square(x + .5, y + .5, side - 1), "WALL", None, None, inner,
                            "LWPOLYLINE", True),
                  Primitive("curve", ring, "WALL", None, None, circle, "CIRCLE", True))
    ids, lo, hi = (outer, inner), .25, side - .25
    walls = (wall((x + lo, y + lo), (x + hi, y + lo), .5, ids=ids),
             wall((x + hi, y + lo), (x + hi, y + hi), .5, ids=ids),
             wall((x + lo, y + hi), (x + hi, y + hi), .5, ids=ids),
             wall((x + lo, y + lo), (x + lo, y + hi), .5, ids=ids))
    return entities, primitives, walls


def showers(count=5, x=40, y=5, side=1.5, pitch=6):
    """`count` instances of the same glyph block. Returns (entities, primitives, walls)."""
    parts = [shower(k, x + pitch * k, y, side) for k in range(count)]
    return (tuple(e for p in parts for e in p[0]), tuple(q for p in parts for q in p[1]),
            tuple(w for p in parts for w in p[2]))


def faces(a, b, t, layer, prefix):
    """Two parallel LINE faces t apart around the axis-aligned centerline a->b."""
    (x0, y0), (x1, y1) = a, b
    shifts = ((0, -t / 2), (0, t / 2)) if y0 == y1 else ((-t / 2, 0), (t / 2, 0))
    return tuple(Primitive("line", ((x0 + dx, y0 + dy), (x1 + dx, y1 + dy)), layer, None, None,
                           f"{prefix}{n}", "LINE") for n, (dx, dy) in enumerate(shifts))


def house_faces():
    """The `house()` walls as drawn faces, for runs through the real detector."""
    return (faces((0, 20), (30, 20), .75, "furni", "t") + faces((30, 0), (30, 20), .75, "WALL", "r")
            + faces((0, 0), (30, 0), .75, "furni", "b") + faces((0, 0), (0, 20), .75, "furni", "l"))
