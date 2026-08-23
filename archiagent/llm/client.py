"""The application-level port for talking to a language model.

One method. That is the whole surface W1 (layer roles), W3 (glyph
identification) and W5 (symbol naming) need -- every one of them is
"classify this into a fixed vocabulary and hand me structured JSON back".

This module imports no vendor SDK. Each adapter uses its own vendor's real
SDK; we never route one provider through another's wire format.
"""

from __future__ import annotations

from typing import Protocol


class LLMUnavailable(RuntimeError):
    """The model could not be reached: missing credentials, transport
    failure, auth rejection, quota exhaustion, or a missing SDK package.

    The message must name what to set or install, and mention that --walls
    bypasses the LLM entirely.
    """


class LLMSchemaError(RuntimeError):
    """The provider answered, but not with something the schema allows.

    This is a bug worth surfacing -- in the prompt, the schema, or the
    provider -- not something to silently retry around.
    """


class LLMClient(Protocol):
    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        """Return a JSON object conforming to `schema`.

        Raises LLMUnavailable on transport, auth or quota failure.
        Raises LLMSchemaError if the provider returns something the schema
        rejects, including a reply truncated by max_tokens.
        """
        ...
