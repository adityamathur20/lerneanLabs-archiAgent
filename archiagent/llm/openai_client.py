"""LLMClient over the official `openai` SDK.

One adapter serves OpenAI, Groq, Kimi (Moonshot) and DeepSeek: all four
publish an OpenAI-compatible endpoint, so a configurable base_url reaches
each of them through its own documented interface. We are not shimming one
vendor's wire format onto another's.

Schema enforcement is best-effort here. Not every OpenAI-compatible
endpoint honours `strict`, which is exactly why decisions_from_reply
validates the reply again on our side regardless of provider.
"""

from __future__ import annotations

import json
import os

from archiagent.llm.client import LLMSchemaError, LLMUnavailable

_NO_KEY = (
    "OPENAI_API_KEY is not set. Export it (it is also the key for Groq, "
    "Kimi and DeepSeek when used through ARCHIAGENT_LLM_BASE_URL), or pass "
    "--walls LAYER... to name the wall layers yourself and skip the LLM."
)

_NO_SDK = (
    "the 'openai' package is not installed. Run "
    "`pip install 'archiagent[llm]'`, or pass --walls LAYER... to skip the "
    "LLM entirely."
)


class OpenAICompatClient:
    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        try:
            import openai
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise LLMUnavailable(_NO_SDK) from e

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMUnavailable(_NO_KEY)

        self._sdk = openai
        self._model = model
        kwargs = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "layer_classification",
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
        except self._sdk.APIError as e:
            raise LLMUnavailable(
                f"OpenAI-compatible API call failed: {e}") from e

        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMSchemaError(
                f"the reply was truncated at max_tokens={max_tokens}, so the "
                "JSON is incomplete. Raise max_tokens.")

        content = choice.message.content
        if content is None:
            raise LLMSchemaError(
                f"the reply had no content (finish_reason="
                f"{choice.finish_reason!r})")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMSchemaError(f"the reply was not valid JSON: {e}") from e
