"""Which layers does stage 1 not deserve the last word on?

Deterministic and image-free by design: the rule is the expensive part of
the classifier to get wrong, so it is testable without an LLM or a renderer.
"""

from __future__ import annotations

from dataclasses import replace

from archiagent.classify.inventory import LayerStats, ThicknessEvidence
from archiagent.classify.layers import (Classification, WALL_CONFIDENCE_FLOOR, WALL_ROLES)
from archiagent.classify.roles import Role

# Each escalated layer costs one rendered image in the vision call. This
# configurable cap controls cost and coverage; it is not a correctness gate.
# Candidates beyond the cap remain unresolved by vision, rather than verified.
ESCALATION_CAP = 20
CONFIDENCE_FLOOR = 0.70
SHARE_FLOOR = 0.10

# A column is structural: a large COLUMN layer is not suspicious the way a
# large FURNITURE layer is.
BODY_ROLES: frozenset[Role] = WALL_ROLES | {Role.COLUMN}


# Declared-thickness evidence. A layer is wall-like when most of its pairs sit at
# the declared thicknesses AND they run wall-length (a column's sides match too,
# but are short). It must also be big enough to matter: a few feet is not a wall layer.
WALL_LIKE_MIN_FT = 40.0
WALL_LIKE_SHARE = 0.60
WALL_LIKE_LONG_SHARE = 0.50
NOT_WALL_LIKE_SHARE = 0.30
NOT_WALL_LIKE_MAX_FT = 15.0
SUGGESTS_WALLS_MIN_FT = 100.0
# A wall role the declared thickness corroborates is raised just over the wall floor.
CORROBORATED_CONFIDENCE = WALL_CONFIDENCE_FLOOR + 0.05


def wall_like(ev: ThicknessEvidence | None) -> bool:
    return (ev is not None and ev.matched_ft >= WALL_LIKE_MIN_FT and ev.share >= WALL_LIKE_SHARE
            and ev.long_share >= WALL_LIKE_LONG_SHARE)


def not_wall_like(ev: ThicknessEvidence | None) -> bool:
    return ev is not None and (ev.share < NOT_WALL_LIKE_SHARE or ev.matched_ft < NOT_WALL_LIKE_MAX_FT)


def corroborate(decisions: Classification, stats: tuple[LayerStats, ...]):
    """Raise wall roles the user's declared thicknesses corroborate to the wall floor.

    Returns (decisions, notes). Only ever raises, only wall roles, only on
    evidence that is wall-like in length as well as thickness.
    """
    evidence = {s.name: s.thickness for s in stats}
    out, notes = [], []
    for d in decisions:
        ev = evidence.get(d.layer)
        if d.role in WALL_ROLES and d.confidence < CORROBORATED_CONFIDENCE and wall_like(ev):
            why = (f"declared thickness corroborates: {ev.matched_ft:.0f} of {ev.paired_ft:.0f} ft of "
                   f"parallel pairs at {'/'.join(f'{t:g}' for t in ev.declared_in)} in")
            out.append(replace(d, confidence=CORROBORATED_CONFIDENCE, reason=f"{d.reason}; {why}"))
            notes.append((d.layer, f"wall role confidence {d.confidence:.2f} -> {CORROBORATED_CONFIDENCE:.2f}: {why}"))
        else:
            out.append(d)
    return tuple(out), tuple(notes)


def is_uninformative(name: str) -> bool:
    n = name.strip()
    return (n == "" or n.lower() == "defpoints" or n.isdigit() or len(n) == 1)


def escalation_candidates(decisions: Classification,
                          stats: tuple[LayerStats, ...]
                          ) -> tuple[tuple[str, str], ...]:
    share = {s.name: s.entity_share for s in stats}
    evidence = {s.name: s.thickness for s in stats}
    wall_layers = [d.layer for d in decisions if d.role in WALL_ROLES]

    picked: dict[str, str] = {}
    for d in decisions:
        ev = evidence.get(d.layer)
        is_wall = d.role in WALL_ROLES
        # The declared thickness contradicts the role: look at it, whatever else is true.
        if is_wall and ev is not None and ev.paired_ft >= WALL_LIKE_MIN_FT and not_wall_like(ev):
            picked.setdefault(d.layer, "thickness_disagrees")
            continue
        if (not is_wall and d.role is not Role.COLUMN and wall_like(ev)
                and ev.matched_ft >= SUGGESTS_WALLS_MIN_FT):
            picked.setdefault(d.layer, "thickness_suggests_walls")
            continue
        corroborated = is_wall and wall_like(ev)
        if d.confidence < CONFIDENCE_FLOOR and not corroborated:
            picked.setdefault(d.layer, "low_confidence")
        elif (share.get(d.layer, 0.0) >= SHARE_FLOOR
              and d.role not in BODY_ROLES and not not_wall_like(ev)):
            picked.setdefault(d.layer, "large_non_wall_layer")
        elif is_uninformative(d.layer) and not corroborated:
            picked.setdefault(d.layer, "uninformative_name")
        elif len(wall_layers) >= 2 and d.layer in wall_layers:
            picked.setdefault(d.layer, "competing_wall_layers")

    return tuple(sorted(picked.items(),
                        key=lambda kv: (-share.get(kv[0], 0.0), kv[0])))


def select_for_escalation(decisions: Classification,
                          stats: tuple[LayerStats, ...],
                          cap: int = ESCALATION_CAP
                          ) -> tuple[tuple[str, str], ...]:
    return escalation_candidates(decisions, stats)[:cap]
