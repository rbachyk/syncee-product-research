"""Optional cached LLM classification fallback (spec §25.4, §31).

Deterministic-first: this is a stub, disabled by default via ``classification.llm.enabled``
(plan decision). It defines the caching contract so a real implementation can be dropped in
without touching callers. AI is only ever used *after* deterministic filtering, is cached by
(input fingerprint, prompt version, model id), and must never override hard gates or
auto-approve (spec §31.1, §31.2).

Transport constraint: LLM access is ALWAYS via OpenRouter (``OPENROUTER_API_KEY``,
``classification.llm.base_url``) or a subscription CLI (``classification.llm.cli_command``)
— NEVER a direct provider API. A real implementation must honour ``classification.llm.provider``
and must not read a direct-provider API key.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..models import Collection


@dataclass
class LLMClassification:
    collection: Collection
    confidence: float
    cache_key: str


def is_enabled(config: AppConfig) -> bool:
    return config.classification.llm.enabled


def cache_key(input_fingerprint: str, config: AppConfig) -> str:
    """Cache key components required by spec §31.1."""
    llm = config.classification.llm
    return f"{llm.model}:{llm.prompt_version}:{input_fingerprint}"


def classify_batch(products: list[dict], config: AppConfig) -> list[LLMClassification]:
    """Batch-classify uncertain products. Not implemented while LLM is disabled.

    A real implementation would call the configured model once per *batch* of uncertain
    products via OpenRouter or a subscription CLI (never a direct provider API), cache by
    :func:`cache_key`, and return refined collections. It must never be invoked per raw
    product (spec §25.4) nor override deterministic hard gates.
    """
    raise NotImplementedError(
        "LLM classification fallback is disabled (classification.llm.enabled=false). "
        "Enable it and implement a cached batch call via OpenRouter or a subscription CLI."
    )
