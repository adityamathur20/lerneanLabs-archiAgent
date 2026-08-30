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

import base64
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
        return self._request(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            schema=schema, max_tokens=max_tokens)

    def classify_json_vision(self, *, system: str, user: str, schema: dict,
                             images: list[tuple[str, bytes]],
                             max_tokens: int = 2048) -> dict:
        # A layer only means something relative to the drawing it belongs
        # to, so each image is labelled and the caller's ordering -- the
        # reference render first, then each escalated layer -- is preserved
        # exactly. The user prompt comes last.
        content: list[dict] = []
        for label, png_bytes in images:
            content.append({"type": "text", "text": label})
            b64 = base64.b64encode(png_bytes).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        content.append({"type": "text", "text": user})

        return self._request(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            schema=schema, max_tokens=max_tokens)

    def _request(self, *, messages: list[dict], schema: dict,
                max_tokens: int) -> dict:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=messages,
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
        except Exception as e:
            # Anything else out of the SDK -- a pydantic ValidationError on a
            # non-conforming response body, a transport error the SDK does
            # not wrap -- still means the model could not be reached. Exit 2
            # is the honest code; letting it escape gives the user a
            # traceback and exit 1. The APIError clause above stays first so
            # its better message wins.
            raise LLMUnavailable(
                f"OpenAI-compatible API call failed unexpectedly "
                f"({type(e).__name__}): {e}") from e

        if not resp.choices:
            # Azure's content filter, and some Groq/DeepSeek error shapes,
            # return HTTP 200 with an empty `choices` array. Indexing it
            # raises IndexError, which is neither LLMUnavailable nor
            # LLMSchemaError and escapes every handler in cli.py.
            raise LLMSchemaError(
                "the reply contained no choices; some OpenAI-compatible "
                "endpoints return an empty 'choices' array on a filtered or "
                "rejected request")

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
