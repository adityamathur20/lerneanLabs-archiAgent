"""Where the provider, model and endpoint come from.

Environment variables, with the provider-native key names honoured so an
existing ANTHROPIC_API_KEY or OPENAI_API_KEY just works. CLI flags override
the environment. No config file: one is easy to add later and premature now.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from archiagent.llm.anthropic_client import DEFAULT_MODEL
from archiagent.llm.client import LLMClient, LLMUnavailable

ENV_PROVIDER = "ARCHIAGENT_LLM_PROVIDER"
ENV_MODEL = "ARCHIAGENT_LLM_MODEL"
ENV_BASE_URL = "ARCHIAGENT_LLM_BASE_URL"
ENV_TIMEOUT = "ARCHIAGENT_LLM_TIMEOUT"

PROVIDERS = ("anthropic", "openai")


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    # Seconds to wait for a reply. None leaves the SDK default (600s for the
    # OpenAI client). A vision call carrying 20 rendered layers to a slow
    # reasoning model can exceed that, and a timeout returns nothing at all --
    # not a partial classification -- so the wait has to be reachable.
    timeout: float | None = None


def config_from_env(env: Mapping[str, str] | None = None,
                    provider: str | None = None,
                    model: str | None = None,
                    timeout: float | None = None) -> LLMConfig:
    """Resolve configuration. Explicit arguments beat the environment."""
    e = os.environ if env is None else env
    return LLMConfig(
        provider=provider or e.get(ENV_PROVIDER) or "anthropic",
        model=model or e.get(ENV_MODEL) or DEFAULT_MODEL,
        base_url=e.get(ENV_BASE_URL) or None,
        timeout=timeout if timeout is not None else _float_or_none(e.get(ENV_TIMEOUT)),
    )


def build_client(cfg: LLMConfig) -> LLMClient:
    if cfg.provider == "anthropic":
        from archiagent.llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=cfg.model, base_url=cfg.base_url,
                               timeout=cfg.timeout)

    if cfg.provider == "openai":
        if cfg.model == DEFAULT_MODEL:
            # An Anthropic model id on an OpenAI endpoint is a 404 with a
            # message that blames the model, not the config. Say the useful
            # thing here instead.
            raise LLMUnavailable(
                f"provider is 'openai' but the model is still the Anthropic "
                f"default {DEFAULT_MODEL!r}. Set {ENV_MODEL} (or --model) to "
                "a model your endpoint serves.")
        from archiagent.llm.openai_client import OpenAICompatClient
        return OpenAICompatClient(model=cfg.model, base_url=cfg.base_url,
                                  timeout=cfg.timeout)

    raise LLMUnavailable(
        f"unknown provider {cfg.provider!r}. Supported: "
        f"{', '.join(PROVIDERS)}. Groq, Kimi and DeepSeek are reached with "
        f"provider 'openai' plus {ENV_BASE_URL}.")


def _float_or_none(raw: str | None) -> float | None:
    """A malformed ARCHIAGENT_LLM_TIMEOUT falls back to the SDK default rather
    than failing the run: the variable is a convenience, not a requirement."""
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None
