"""Layer classification: the pipeline's only LLM step in M1-M5.

The classifier is a protocol so downstream tests stay deterministic. It
sees LayerStats only — never coordinates, and it never returns any.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, get_args

from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role

Source = Literal["llm", "manual", "default"]

SOURCES: frozenset[str] = frozenset(get_args(Source))


@dataclass(frozen=True)
class LayerDecision:
    """One layer's assigned role, with everything W2 needs to review it.

    `source` is exact:
      "llm"     the model answered for this layer
      "manual"  a human supplied it -- including the layers a partial
                --walls map leaves unmentioned, since naming some layers as
                walls is a deliberate statement that the rest are not
      "default" the model was ASKED about this layer and did not answer, so
                IGNORE was applied. Only this value warrants a warning.

    The type is closed deliberately: an unrecognised value would be read by
    validate() as "not default", i.e. as a silent claim of provenance that
    never warns. An omission should be conspicuous, not absorbed.
    """

    layer: str
    role: Role
    confidence: float
    reason: str = ""
    source: Source = "llm"


Classification = tuple[LayerDecision, ...]

WALL_ROLES: frozenset[Role] = frozenset({Role.WALL_STRUCTURAL, Role.WALL_PARTITION})

# A layer the model was 30% sure about used to contribute geometry exactly as
# heavily as one it was 95% sure about: layers_for_roles defaults min_confidence
# to 0.0 and the pipeline passed nothing. Below this floor a layer is reported
# rather than silently built into the model. The value is escalate.py's
# CONFIDENCE_FLOOR -- the same doubt that sends a layer to the vision stage is
# the doubt that keeps it out of the geometry.
WALL_CONFIDENCE_FLOOR = 0.70


class LayerClassifier(Protocol):
    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        """One LayerDecision per input layer, in the inventory's order."""
        ...


class StubClassifier:
    """Deterministic classifier for tests and for replaying a saved mapping."""

    def __init__(self, mapping: dict[str, tuple[Role, float]]) -> None:
        self._mapping = dict(mapping)

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        out: list[LayerDecision] = []
        for s in stats:
            role, conf = self._mapping.get(s.name, (Role.IGNORE, 0.0))
            out.append(LayerDecision(layer=s.name, role=role, confidence=conf,
                                     reason="", source="manual"))
        return tuple(out)


def layers_for_roles(classification: Classification,
                     roles: set[Role] | frozenset[Role],
                     min_confidence: float = 0.0) -> set[str]:
    """Layer names whose assigned role is in `roles` and clears the floor."""
    return {d.layer for d in classification
            if d.role in roles and d.confidence >= min_confidence}
