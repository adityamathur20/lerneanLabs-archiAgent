"""Which layers does stage 1 not deserve the last word on?

Deterministic and image-free by design: the rule is the expensive part of
the classifier to get wrong, so it is testable without an LLM or a renderer.
"""

from __future__ import annotations

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import Classification, WALL_ROLES
from archiagent.classify.roles import Role

# Measured on Floor Plan.dxf (39 layers): a cap of 6 dropped 17 candidates,
# including WALLS and RCC WALL -- real wall layers left unexamined. 20 covers
# that drawing. Each escalated layer costs one rendered image in the vision
# call, so this is a cost/coverage dial, not a correctness one.
ESCALATION_CAP = 20
CONFIDENCE_FLOOR = 0.70
SHARE_FLOOR = 0.10

# A column is structural: a large COLUMN layer is not suspicious the way a
# large FURNITURE layer is.
BODY_ROLES: frozenset[Role] = WALL_ROLES | {Role.COLUMN}


def is_uninformative(name: str) -> bool:
    n = name.strip()
    return (n == "" or n.lower() == "defpoints" or n.isdigit() or len(n) == 1)


def escalation_candidates(decisions: Classification,
                          stats: tuple[LayerStats, ...]
                          ) -> tuple[tuple[str, str], ...]:
    share = {s.name: s.entity_share for s in stats}
    wall_layers = [d.layer for d in decisions if d.role in WALL_ROLES]

    picked: dict[str, str] = {}
    for d in decisions:
        if d.confidence < CONFIDENCE_FLOOR:
            picked.setdefault(d.layer, "low_confidence")
        elif (share.get(d.layer, 0.0) >= SHARE_FLOOR
              and d.role not in BODY_ROLES):
            picked.setdefault(d.layer, "large_non_wall_layer")
        elif is_uninformative(d.layer):
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
