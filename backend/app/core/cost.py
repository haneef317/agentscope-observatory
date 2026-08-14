"""
LLM cost model.

Costs are published per 1,000,000 tokens (USD). This module computes the cost
of a single LLM request from its model name and token usage. Prices are
snapshotted from public provider pricing pages (OpenAI, Anthropic, Google) and
can be overridden at runtime through the environment variable
`CUSTOM_PRICING_JSON` (a JSON object mapping model substrings to
{"input": x, "output": y} per 1M tokens).

Cost formula per request:

    cost_usd = (input_tokens / 1_000_000) * input_price
             + (output_tokens / 1_000_000) * output_price
"""

import json
import os

# --------------------------------------------------------------------
# Pricing table: USD per 1,000,000 tokens.
# --------------------------------------------------------------------
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # ---- OpenAI --------------------------------------------------------
    "gpt-5":        {"input": 1.25, "output": 10.0},
    "gpt-5-mini":   {"input": 0.25, "output": 2.0},
    "gpt-5-nano":   {"input": 0.05, "output": 0.40},
    "gpt-4.1":      {"input": 2.00, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.6},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4o":       {"input": 2.50, "output": 10.0},
    "gpt-4o-mini":  {"input": 0.15, "output": 0.60},
    "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo":  {"input": 10.0, "output": 30.0},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    # ---- Anthropic -----------------------------------------------------
    "claude-sonnet-4-5":  {"input": 3.0, "output": 15.0},
    "claude-sonnet-4":    {"input": 3.0, "output": 15.0},
    "claude-4-sonnet":    {"input": 3.0, "output": 15.0},
    "claude-haiku-3-5":   {"input": 0.80, "output": 4.0},
    "claude-3-5-haiku":   {"input": 0.80, "output": 4.0},
    "claude-3-haiku":     {"input": 0.25, "output": 1.25},
    # ---- Google Gemini -------------------------------------------------
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    # ---- OpenRouter / generic ------------------------------------------
    "openrouter":       {"input": 1.0, "output": 5.0},   # unknown model fallback
    "unknown":          {"input": 1.0, "output": 5.0},
}

# --------------------------------------------------------------------
# Runtime pricing overrides.
# --------------------------------------------------------------------
_custom: dict[str, dict[str, float]] | None = None

_custom_json = os.environ.get("CUSTOM_PRICING_JSON")
if _custom_json:
    try:
        _custom = json.loads(_custom_json)
    except json.JSONDecodeError:
        _custom = None


def _find_pricing(model: str) -> dict[str, float] | None:
    """Best-effort match: exact id first, then longest prefix match."""
    model = model.strip().lower()
    if not model:
        return None
    if model in DEFAULT_PRICING:
        return DEFAULT_PRICING[model]
    if _custom:
        for substring, price in _custom.items():
            if substring.lower() in model:
                return price
    best, best_len = None, 0
    for key, price in DEFAULT_PRICING.items():
        if key in model and len(key) > best_len:
            best, best_len = price, len(key)
    return best


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD for one LLM request."""
    pricing = _find_pricing(model) or DEFAULT_PRICING["unknown"]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


def model_provider(model: str) -> str:
    model = model.lower()
    if any(k in model for k in ("gpt", "o1", "o3", "o4", "davinci")):
        return "openai"
    if "claude" in model:
        return "anthropic"
    if "gemini" in model:
        return "google"
    if "llama" in model or "deepseek" in model:
        return "meta/deepseek"
    return "unknown"
