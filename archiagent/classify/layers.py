"""Layer classification: the pipeline's only LLM step in M1-M5.

The classifier is a protocol so downstream tests stay deterministic. It
sees LayerStats only — never coordinates, and it never returns any.
"""

from __future__ import annotations

from typing import Protocol

from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role

Classification = dict[str, tuple[Role, float]]

WALL_ROLES: frozenset[Role] = frozenset({Role.WALL_STRUCTURAL, Role.WALL_PARTITION})


class LayerClassifier(Protocol):
    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        """Map each layer name to (role, confidence in 0..1)."""
        ...


class StubClassifier:
    """Deterministic classifier for tests and for replaying a saved mapping."""

    def __init__(self, mapping: Classification) -> None:
        self._mapping = dict(mapping)

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        return {s.name: self._mapping.get(s.name, (Role.IGNORE, 0.0)) for s in stats}


def layers_for_roles(classification: Classification, roles: set[Role] | frozenset[Role],
                     min_confidence: float = 0.0) -> set[str]:
    """Layer names whose assigned role is in `roles` and clears the floor."""
    return {name for name, (role, conf) in classification.items()
            if role in roles and conf >= min_confidence}
