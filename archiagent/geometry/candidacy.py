"""Geometry-first wall candidacy: score every proposed wall run, keep the walls.

Layer role is one weighted signal, never a gate. Scoring is pure and
deterministic; only runs the score cannot settle may go to an adjudicator.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import networkx as nx
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from archiagent.classify.layers import WALL_CONFIDENCE_FLOOR, WALL_ROLES, Classification, layers_for_roles
from archiagent.classify.roles import Role
from archiagent.geometry.candidates import CandidateDecision
from archiagent.geometry.walls import WallSeg, reject_ladder_runs
from archiagent.model import Issue
from archiagent.primitives import PrimitiveSet
from archiagent.semantic import SymbolInstance

SMALL_FT = 4.0        # a closed outline or run below this is fixture/glyph scale
REPEAT_MIN = 3        # a block inserted this often is a symbol, not a one-off
SHORT_FT = 1.5
WALL_SCALE_FT = 8.0
NEGATIVE_ROLES: frozenset[Role] = frozenset({
    Role.FURNITURE, Role.STAIR, Role.ANNOTATION, Role.PLUMBING, Role.ELECTRICAL,
    Role.VEHICLE, Role.LANDSCAPE, Role.RAILING, Role.DOOR, Role.WINDOW})
_GLYPH_TYPES = frozenset({"CIRCLE", "HATCH"})
MODE_SHARE = 0.10
MODE_TOLERANCE_FT = 1 / 12


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
    # Declared by the user, in feet. Empty means candidacy infers the set from
    # the drawing, as it always has.
    declared_thickness_ft: tuple[float, ...] = ()
    thickness_tolerance_ft: float = MODE_TOLERANCE_FT
    thickness_exhaustive: bool = False


def build_context(ps: PrimitiveSet, classification: Classification,
                  units_per_foot: float, *,
                  declared_thickness_ft: tuple[float, ...] = (),
                  thickness_tolerance_ft: float = MODE_TOLERANCE_FT,
                  thickness_exhaustive: bool = False) -> Context:
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
                   STRtree(glyphs) if glyphs else None, tuple(glyphs),
                   declared_thickness_ft, thickness_tolerance_ft, thickness_exhaustive)


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
    """Declared thicknesses when the user supplied them, else the inferred set.

    Thicknesses carrying at least MODE_SHARE of confident wall-layer run length.
    """
    if ctx.declared_thickness_ft:
        return ctx.declared_thickness_ft
    lengths: Counter = Counter()
    for w in walls:
        role, confidence = ctx.roles.get(w.source_layer.casefold(), (Role.IGNORE, 0.0))
        if role in WALL_ROLES and confidence >= WALL_CONFIDENCE_FLOOR:
            lengths[round(w.thickness_ft * 48) / 48] += w.length_ft
    total = sum(lengths.values())
    return tuple(t for t, length in sorted(lengths.items()) if total and length / total >= MODE_SHARE)


def _thickness(wall: WallSeg, modes: tuple[float, ...], tolerance_ft: float) -> float:
    if not modes:
        return 0.0
    return 1.0 if any(abs(wall.thickness_ft - m) <= tolerance_ft for m in modes) else -0.5


def score_candidates(walls, ctx: Context) -> tuple[ScoredCandidate, ...]:
    walls = tuple(walls)
    touches = _touches(walls)
    connectivity, closure = _connectivity(walls, touches), _closure(walls, touches)
    modes = _thickness_modes(walls, ctx)
    out = []
    for i, w in enumerate(walls):
        signals = candidate_signals(w, ctx) + (
            ("connectivity", connectivity[i]), ("closure", closure[i]),
            ("thickness", _thickness(w, modes, ctx.thickness_tolerance_ft)))
        score = combine(signals)
        # A declared set the user calls complete is ground truth, so a run
        # matching none of it is not a wall. This overrides the band rather
        # than raising the thickness weight: combine() normalises by the sum of
        # WEIGHTS, so reweighting would re-band every candidate in every
        # drawing against floors that were tuned to the current normalisation.
        vetoed = (ctx.thickness_exhaustive and ctx.declared_thickness_ft
                  and _thickness(w, modes, ctx.thickness_tolerance_ft) < 0)
        out.append(ScoredCandidate(candidate_id(w), w, signals, score,
                                   "reject" if vetoed else band(score)))
    return tuple(out)


ADJUDICATION_CONFIDENCE_FLOOR = 0.70
# Symbols inferred ONLY from a layer's role are the same layer gate this module
# removes. Their geometry stays visible to candidacy, which decides.
YIELDING_KINDS = frozenset({"furniture", "stair", "electrical", "plumbing", "vehicle"})

Adjudicator = Callable[[PrimitiveSet, tuple[ScoredCandidate, ...], float],
                       tuple[dict[str, tuple[str, float, str]], tuple[Issue, ...]]]


def yields_to_walls(symbol: SymbolInstance) -> bool:
    return symbol.evidence == "layer-and-geometry" and symbol.kind in YIELDING_KINDS


def reconcile_symbols(symbols, walls, ps: PrimitiveSet, classification: Classification,
                      units_per_foot: float) -> tuple[tuple[SymbolInstance, ...], tuple[Issue, ...]]:
    """Split layer-inferred symbols whose geometry was partly accepted as a wall.

    The wall lines go to the wall. The remaining lines are regrouped by the very
    rules recognize_symbols used to form the symbol, so the leftover furniture
    keeps its label -- as one piece, or several if the wall line was what joined them.
    """
    from dataclasses import replace

    from archiagent.recognition import recognize_symbols

    wall_ids = {sid for w in walls for sid in w.source_ids}
    kept, regrouped, issues = [], [], []
    for s in symbols:
        if not (yields_to_walls(s) and wall_ids.intersection(s.source_ids)):
            kept.append(s)
            continue
        rest = set(s.source_ids) - wall_ids
        pieces = ()
        if rest:
            remainder = replace(ps, primitives=tuple(p for p in ps.primitives if p.source_id in rest),
                                entities=())
            pieces = tuple(p for p in recognize_symbols(remainder, units_per_foot, classification)
                           if p.kind == s.kind and p.evidence == "layer-and-geometry")
        if pieces:
            regrouped.extend(pieces)
            issues.append(Issue("info", s.id, "symbol_split_by_wall",
                                f"{s.kind} inferred from its layer's role: {len(s.source_ids) - len(rest)} "
                                f"lines became walls; the rest kept as {len(pieces)} {s.kind} symbol(s)"))
        else:
            issues.append(Issue("info", s.id, "symbol_superseded_by_wall",
                                f"{s.kind} inferred from its layer's role: its geometry was accepted "
                                "as a wall, leaving nothing of that kind"))
    return tuple(kept) + tuple(regrouped), tuple(issues)


def _explain(c: ScoredCandidate) -> str:
    ranked = sorted(c.signals, key=lambda s: -abs(WEIGHTS[s[0]] * s[1]))
    top = ", ".join(f"{name}={value:+.2f}" for name, value in ranked[:3] if value)
    return f"score {c.score:.2f} ({top or 'no decisive signal'})"


def _decide(c: ScoredCandidate, verdict, on_wall_layer: bool) -> CandidateDecision:
    if c.band != "ambiguous":
        return CandidateDecision(c.id, c.wall, c.signals, c.score, c.band, "deterministic", _explain(c))
    if verdict is not None and verdict[1] >= ADJUDICATION_CONFIDENCE_FLOOR:
        label, confidence, reason = verdict
        return CandidateDecision(c.id, c.wall, c.signals, c.score,
                                 "accept" if label == "wall" else "reject", "adjudicated",
                                 f"{label} ({confidence:.2f}): {reason}")
    # Unsettled: keep the outcome the layer gate gave before candidacy existed.
    return CandidateDecision(c.id, c.wall, c.signals, c.score,
                             "accept" if on_wall_layer else "reject", "ambiguous-default", _explain(c))


def select_walls(ps: PrimitiveSet, proposed, classification: Classification, units_per_foot: float,
                 *, adjudicator: Adjudicator | None = None,
                 declared_thickness_ft: tuple[float, ...] = (),
                 thickness_tolerance_ft: float = MODE_TOLERANCE_FT,
                 thickness_exhaustive: bool = False
                 ) -> tuple[tuple[WallSeg, ...], tuple[CandidateDecision, ...], tuple[Issue, ...]]:
    """Accepted walls (in proposed order), a decision for every run, and issues."""
    proposed = tuple(proposed)
    wall_layers = {n.casefold() for n in layers_for_roles(classification, WALL_ROLES, WALL_CONFIDENCE_FLOOR)}
    established = tuple(w for w in proposed if w.source_layer.casefold() in wall_layers)
    admitted = tuple(w for w in proposed if w.source_layer.casefold() not in wall_layers)
    # Stair treads now get paired on stair layers; the ladder rule is applied to
    # newly admitted layers only, so wall-layer behaviour is unchanged.
    kept, ladders = reject_ladder_runs(admitted) if admitted else ((), ())
    scored = score_candidates(established + tuple(kept),
                              build_context(ps, classification, units_per_foot,
                                            declared_thickness_ft=declared_thickness_ft,
                                            thickness_tolerance_ft=thickness_tolerance_ft,
                                            thickness_exhaustive=thickness_exhaustive))
    verdicts, issues = {}, []
    ambiguous = tuple(c for c in scored if c.band == "ambiguous")
    if ambiguous and adjudicator is not None:
        verdicts, adjudication_issues = adjudicator(ps, ambiguous, units_per_foot)
        issues.extend(adjudication_issues)
    decisions = [CandidateDecision(candidate_id(w), w, (), 0.0, "reject", "ladder-run",
                                   "evenly spaced parallel runs: stair treads or hatching")
                 for w in ladders]
    decisions += [_decide(c, verdicts.get(c.id), c.wall.source_layer.casefold() in wall_layers)
                  for c in scored]
    kept_by_default = 0
    rejected_by_default = []
    for d in decisions:
        if d.verdict_source != "ambiguous-default":
            continue
        if d.verdict == "reject":
            rejected_by_default.append(d.wall)
        else:
            kept_by_default += 1
    if rejected_by_default:
        top = sorted(rejected_by_default, key=lambda w: -w.length_ft)[:5]
        named = ", ".join(f"{w.source_layer!r} {w.length_ft:.1f}ft" for w in top)
        issues.append(Issue("warn", "wall-candidates", "wall_candidate_unresolved",
                            f"{len(rejected_by_default)} ambiguous runs rejected by default; "
                            f"longest: {named}"))
    if kept_by_default:
        issues.append(Issue("info", "wall-candidates", "wall_candidates_kept_by_default",
                            f"{kept_by_default} uncertain runs on wall layers kept, as before candidacy"))
    accepted_ids = {d.id for d in decisions if d.verdict == "accept"}
    accepted = tuple(w for w in proposed if candidate_id(w) in accepted_ids)
    return accepted, tuple(sorted(decisions, key=lambda d: d.id)), tuple(issues)
