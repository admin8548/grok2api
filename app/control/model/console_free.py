"""Free console.x.ai model aliases for the v2 account runtime.

These aliases are routed through console.x.ai `/v1/responses` by the OpenAI
product layer while account selection still uses the v2 `accounts.db` runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConsoleFreeModel:
    public_model: str
    upstream_model: str
    fixed_reasoning_effort: str | None
    include_reasoning: bool
    max_output_tokens: int
    enable_search_tools: bool
    display_name: str


_DEFAULT_MAX_OUTPUT_TOKENS = 1_000_000
_MULTI_AGENT_MAX_OUTPUT_TOKENS = 2_000_000
_BUILD_MAX_OUTPUT_TOKENS = 256_000


CONSOLE_FREE_MODELS: tuple[ConsoleFreeModel, ...] = (
    ConsoleFreeModel(
        "grok-4.3-console", "grok-4.3", None, True,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.3 (Console)",
    ),
    ConsoleFreeModel(
        "grok-4.3-low", "grok-4.3", "low", True,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.3 Low Thinking",
    ),
    ConsoleFreeModel(
        "grok-4.3-medium", "grok-4.3", "medium", True,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.3 Medium Thinking",
    ),
    ConsoleFreeModel(
        "grok-4.3-high", "grok-4.3", "high", True,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.3 High Thinking",
    ),
    ConsoleFreeModel(
        "grok-4.20-0309-console", "grok-4.20-0309", None, False,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 0309 (Console)",
    ),
    ConsoleFreeModel(
        "grok-4.20-0309-reasoning-console", "grok-4.20-0309-reasoning", None, False,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 0309 Reasoning (Console)",
    ),
    ConsoleFreeModel(
        "grok-4.20-0309-non-reasoning-console", "grok-4.20-0309-non-reasoning", None, False,
        _DEFAULT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 0309 Non-Reasoning (Console)",
    ),
    ConsoleFreeModel(
        "grok-4.20-multi-agent-console", "grok-4.20-multi-agent-0309", None, True,
        _MULTI_AGENT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 Multi-Agent (Console)",
    ),
    ConsoleFreeModel(
        "grok-4.20-multi-agent-low", "grok-4.20-multi-agent-0309", "low", True,
        _MULTI_AGENT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 Multi-Agent Low",
    ),
    ConsoleFreeModel(
        "grok-4.20-multi-agent-medium", "grok-4.20-multi-agent-0309", "medium", True,
        _MULTI_AGENT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 Multi-Agent Medium",
    ),
    ConsoleFreeModel(
        "grok-4.20-multi-agent-high", "grok-4.20-multi-agent-0309", "high", True,
        _MULTI_AGENT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 Multi-Agent High",
    ),
    ConsoleFreeModel(
        "grok-4.20-multi-agent-xhigh", "grok-4.20-multi-agent-0309", "xhigh", True,
        _MULTI_AGENT_MAX_OUTPUT_TOKENS, True, "Grok 4.20 Multi-Agent XHigh",
    ),
    ConsoleFreeModel(
        "grok-build-console", "grok-build-0.1", None, False,
        _BUILD_MAX_OUTPUT_TOKENS, True, "Grok Build 0.1 (Console)",
    ),
)

CONSOLE_FREE_MODEL_MAP: dict[str, ConsoleFreeModel] = {
    model.public_model: model for model in CONSOLE_FREE_MODELS
}

_EFFORT_ALIASES = {
    "none": "none",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}


def get_console_free_model(public_model: str) -> ConsoleFreeModel | None:
    return CONSOLE_FREE_MODEL_MAP.get(public_model)


def is_console_free_model(public_model: str) -> bool:
    return public_model in CONSOLE_FREE_MODEL_MAP


def list_console_free_models() -> list[ConsoleFreeModel]:
    return list(CONSOLE_FREE_MODELS)


def resolve_reasoning_effort(
    spec: ConsoleFreeModel,
    request_effort: str | None,
    default_effort: str | None = None,
) -> str:
    effort = spec.fixed_reasoning_effort or request_effort or default_effort or "medium"
    effort = str(effort).strip().lower() or "medium"
    return _EFFORT_ALIASES.get(effort, effort)


def response_defaults(spec: ConsoleFreeModel) -> dict[str, Any]:
    return {
        "reasoning_effort": spec.fixed_reasoning_effort,
        "include_reasoning": spec.include_reasoning,
        "max_output_tokens": spec.max_output_tokens,
        "enable_search_tools": spec.enable_search_tools,
    }


__all__ = [
    "ConsoleFreeModel",
    "CONSOLE_FREE_MODELS",
    "CONSOLE_FREE_MODEL_MAP",
    "get_console_free_model",
    "is_console_free_model",
    "list_console_free_models",
    "resolve_reasoning_effort",
    "response_defaults",
]
