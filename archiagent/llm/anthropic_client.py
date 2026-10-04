"""LLMClient over the official `anthropic` SDK. The default provider.

Verified against anthropic 1.0.0 on 2026-08-23: messages.create accepts
output_config. The SDK is imported lazily so that `--walls` and `--inspect`
keep working in an install without the `llm` extra.
"""

from __future__ import annotations

import base64

from archiagent.llm.usage import current as _usage
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
                 base_url: str | None = None,
                 timeout: float | None = None) -> None:
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
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = anthropic.Anthropic(**kwargs)

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        return self._request(
            messages=[{"role": "user", "content": user}],
            system=system, schema=schema, max_tokens=max_tokens)

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
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(png_bytes).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": user})

        return self._request(
            messages=[{"role": "user", "content": content}],
            system=system, schema=schema, max_tokens=max_tokens, vision=True)

    def _request(self, *, messages: list[dict], system: str, schema: dict,
                max_tokens: int, vision: bool = False) -> dict:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
        except self._sdk.APIError as e:
            raise LLMUnavailable(f"Anthropic API call failed: {e}") from e
        except Exception as e:
            # Anything else out of the SDK -- a pydantic ValidationError on a
            # non-conforming response body, a transport error the SDK does
            # not wrap -- still means the model could not be reached. Exit 2
            # is the honest code; letting it escape gives the user a
            # traceback and exit 1. The APIError clause above stays first so
            # its better message wins.
            raise LLMUnavailable(
                f"Anthropic API call failed unexpectedly "
                f"({type(e).__name__}): {e}") from e

        # Telemetry, recorded before any parsing can raise: a truncated or
        # malformed reply still cost tokens, and a report that silently omits
        # the failed calls understates what a run spent.
        _record_usage(resp, provider="anthropic", model=self._model, vision=vision)

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


def _record_usage(resp, *, provider: str, model: str, vision: bool) -> None:
    """Records what the provider said this call cost.

    Usage is telemetry: a provider that omits it, or renames a field, must cost
    the conversion nothing. Hence the getattr defaults rather than indexing.
    """
    u = getattr(resp, "usage", None)
    _usage().record(
        provider=provider,
        model=model,
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        # Anthropic bills a cache read at a different rate from fresh input, so
        # the two are never folded together.
        cached_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        vision=vision,
    )
