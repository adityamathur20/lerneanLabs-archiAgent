"""The pipeline's LLM-backed layer classifier.

The LLM owns vocabulary and nothing else: it sees LayerStats, it returns
roles. Every coordinate in this system is computed by deterministic code
that never consults a model.

`decisions_from_reply` is split out from the classifier deliberately -- all
the validation lives there, so it can be tested with a plain dict and no
client at all.
"""

from __future__ import annotations

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import Classification, LayerDecision
from archiagent.classify.prompt import (SYSTEM_PROMPT, build_user_prompt,
                                        response_schema)
from archiagent.classify.roles import Role
from archiagent.llm.client import LLMClient, LLMSchemaError

MAX_TOKENS = 2048

UNANSWERED = "the classifier returned no entry for this layer"


def decisions_from_reply(reply: dict,
                         stats: tuple[LayerStats, ...]) -> Classification:
    """Turn a validated JSON reply into one decision per inventory layer.

    Output order follows `stats`, not the reply -- the model's ordering is
    not something to depend on, and W2 wants a stable sequence.
    """
    entries = reply.get("layers") if isinstance(reply, dict) else None
    if not isinstance(entries, list):
        raise LLMSchemaError(
            "reply has no 'layers' list; got keys "
            f"{sorted(reply) if isinstance(reply, dict) else type(reply).__name__}")

    answered: dict[str, tuple[Role, float, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise LLMSchemaError(f"layer entry is not an object: {entry!r}")
        name = entry.get("name")
        if not isinstance(name, str):
            raise LLMSchemaError(f"layer entry has no string name: {entry!r}")

        raw_role = entry.get("role")
        try:
            role = Role(raw_role)
        except ValueError as e:
            raise LLMSchemaError(
                f"layer {name!r} got role {raw_role!r}, which is not in the "
                "role vocabulary") from e

        raw_conf = entry.get("confidence")
        if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
            raise LLMSchemaError(
                f"layer {name!r} got confidence {raw_conf!r}, which is not a "
                "number")
        # The schema cannot express minimum/maximum, so the range is ours
        # to enforce.
        confidence = min(1.0, max(0.0, float(raw_conf)))

        reason = entry.get("reason")
        answered[name] = (role, confidence,
                          reason if isinstance(reason, str) else "")

    out: list[LayerDecision] = []
    for s in stats:
        if s.name in answered:
            role, confidence, reason = answered[s.name]
            out.append(LayerDecision(s.name, role, confidence, reason, "llm"))
        else:
            # Asked and not answered. Marked "default" so validate() reports
            # it -- a layer silently dropped to ignore is exactly the kind of
            # invisible failure this project exists to avoid.
            out.append(LayerDecision(s.name, Role.IGNORE, 0.0,
                                     UNANSWERED, "default"))
    return tuple(out)


class LLMLayerClassifier:
    """LayerClassifier backed by an LLMClient."""

    def __init__(self, client: LLMClient, max_tokens: int = MAX_TOKENS) -> None:
        self._client = client
        self._max_tokens = max_tokens

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        reply = self._client.classify_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(stats),
            schema=response_schema(),
            max_tokens=self._max_tokens,
        )
        return decisions_from_reply(reply, stats)
