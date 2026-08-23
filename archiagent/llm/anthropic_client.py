"""LLMClient over the official `anthropic` SDK. The default provider.

Verified against anthropic 1.0.0 on 2026-08-23: messages.create accepts
output_config. The SDK is imported lazily so that `--walls` and `--inspect`
keep working in an install without the `llm` extra.
"""

from __future__ import annotations

import json
import os

from archiagent.llm.client import LLMSchemaError, LLMUnavailable

DEFAULT_MODEL = "claude-haiku-4-5"

_NO_KEY = (
    "ANTHROPIC_API_KEY is not set. Export it, or pass --walls LAYER... to "
    "name the wall layers yourself and skip the LLM entirely."
)

_NO_SDK = (
    "the 'anthropic' package is not installed. Run "
    "`pip install 'archiagent[llm]'`, or pass --walls LAYER... to skip the "
    "LLM entirely."
)


class AnthropicClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise LLMUnavailable(_NO_SDK) from e

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable(_NO_KEY)

        self._sdk = anthropic
        self._model = model
        kwargs = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
        except self._sdk.APIError as e:
            raise LLMUnavailable(f"Anthropic API call failed: {e}") from e

        stop = getattr(resp, "stop_reason", None)
        if stop == "max_tokens":
            raise LLMSchemaError(
                f"the reply was truncated at max_tokens={max_tokens}, so the "
                "JSON is incomplete. Raise max_tokens.")

        text = next((b.text for b in resp.content
                     if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise LLMSchemaError(
                f"the reply contained no text block (stop_reason={stop!r})")

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMSchemaError(
                f"the reply was not valid JSON: {e}") from e
