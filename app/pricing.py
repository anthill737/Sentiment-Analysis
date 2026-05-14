"""
Per-million-token pricing for the API providers used by SA Runner.

Updated 2026-05. These are list prices in USD. Cached input tokens (Anthropic
prompt caching) are billed differently than fresh input; for simplicity we
treat them all as fresh-input here, slightly overestimating cost (safer).

Adjust these values when providers change their pricing — they're the single
source of truth for cost computation.
"""
from __future__ import annotations
from typing import Optional

# Per-million-token rates: (input_per_M, output_per_M) in USD.
# When a model is missing here we fall back to a per-provider default below.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-7":        (15.00, 75.00),
    "claude-opus-4-6":        (15.00, 75.00),
    "claude-opus-4-5":        (15.00, 75.00),
    "claude-sonnet-4-6":       (3.00, 15.00),
    "claude-sonnet-4-5":       (3.00, 15.00),
    "claude-haiku-4-5":        (1.00,  5.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022":  (1.00,  5.00),
    # xAI
    "grok-4":                  (5.00, 15.00),
    "grok-4-fast":             (0.20,  0.50),
    "grok-3":                  (3.00, 15.00),
    "grok-3-mini":             (0.30,  0.50),
    # Perplexity (their pricing is closer to per-request flat, but
    # they also publish per-token rates for sonar-pro)
    "sonar-pro":               (3.00, 15.00),
    "sonar":                   (1.00,  1.00),
}

# Fallback per provider for unknown models — conservative (higher) defaults so
# we don't under-report cost.
PROVIDER_FALLBACK: dict[str, tuple[float, float]] = {
    "anthropic":  (15.00, 75.00),  # assume Opus
    "xai":         (5.00, 15.00),
    "perplexity":  (3.00, 15.00),
}

# Free / non-token-priced providers
FREE_PROVIDERS = {"google_trends", "trends", "fmp", "github", "steam"}


def price_call(provider: str, model: str,
               input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a single call given provider/model/token counts."""
    if provider in FREE_PROVIDERS:
        return 0.0
    rates = MODEL_PRICES.get((model or "").lower()) or \
            MODEL_PRICES.get(model or "") or \
            PROVIDER_FALLBACK.get(provider, (0.0, 0.0))
    input_per_m, output_per_m = rates
    return (input_tokens / 1_000_000.0) * input_per_m + \
           (output_tokens / 1_000_000.0) * output_per_m


def aggregate_usage(calls: list[dict]) -> dict:
    """Walk a list of usage call records and return totals + breakdown.

    Returns:
      {
        "total_usd": float,
        "total_input_tokens": int,
        "total_output_tokens": int,
        "total_calls": int,
        "by_provider": {provider: {usd, input_tokens, output_tokens, calls}, ...}
      }
    """
    totals = {
        "total_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_calls": 0,
        "by_provider": {},
    }
    for call in calls or []:
        provider = call.get("provider", "unknown")
        model = call.get("model") or ""
        in_t = int(call.get("input_tokens", 0) or 0)
        out_t = int(call.get("output_tokens", 0) or 0)
        cost = price_call(provider, model, in_t, out_t)

        bucket = totals["by_provider"].setdefault(provider, {
            "usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0,
        })
        bucket["usd"] += cost
        bucket["input_tokens"] += in_t
        bucket["output_tokens"] += out_t
        bucket["calls"] += 1

        totals["total_usd"] += cost
        totals["total_input_tokens"] += in_t
        totals["total_output_tokens"] += out_t
        totals["total_calls"] += 1

    # Round usd to 4 decimals (avoid float noise)
    totals["total_usd"] = round(totals["total_usd"], 4)
    for b in totals["by_provider"].values():
        b["usd"] = round(b["usd"], 4)
    return totals
