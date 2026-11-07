"""Server-side USD cost estimation for LLM turns.

Mirrors the webui's client-side pricing (static/js/chatRenderer.js MODEL_INFO +
static/js/model/matchKey.js) so the TUI and API surfaces can show $ spend
without a browser. Reported costs from the provider (OpenRouter `usage.cost`)
are always preferred; this module is the fallback estimator.

Unknown models return None — we never guess a price. Local / self-hosted /
subscription endpoints are free by definition (see is_local_endpoint /
is_subscription_endpoint).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Per-1M-token USD prices, ported from static/js/chatRenderer.js MODEL_INFO.
# Keep in sync with the JS table (ctx window kept for parity/debugging).
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # --- Anthropic --- (platform.claude.com prompt-caching pricing table)
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5-1": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts <=100k; see _LONG_PROMPT_PRICING
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),  # Opus 4 / 4.1
    "claude-opus-4-5": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4": (0.80, 4.00),
    "claude-haiku-3-5": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    # --- OpenAI ---
    "gpt-5": (2.00, 8.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o1-pro": (150.0, 600.0),
    "o3": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # --- DeepSeek ---
    "deepseek-chat": (0.27, 1.10),
    "deepseek-coder": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-r1": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-v2": (0.14, 0.28),
    # --- Google ---
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemma-3": (0.10, 0.10),
    # --- Mistral ---
    "mistral-large": (2.00, 6.00),
    "mistral-medium": (2.00, 6.00),
    "mistral-small": (0.20, 0.60),
    "mistral-nemo": (0.15, 0.15),
    "mixtral": (0.24, 0.24),
    "codestral": (0.30, 0.90),
    "pixtral": (2.00, 6.00),
    # --- xAI ---
    "grok-4": (3.00, 15.00),
    "grok-3": (3.00, 15.00),
    "grok-2": (2.00, 10.00),
    # --- Meta ---
    "llama-4": (0.20, 0.20),
    "llama-3.3": (0.20, 0.20),
    "llama-3.2": (0.20, 0.20),
    "llama-3.1": (0.20, 0.20),
    "llama-3": (0.20, 0.20),
    # --- Qwen ---
    "qwen3": (0.30, 1.20),
    "qwen2.5": (0.30, 1.20),
    "qwq": (0.30, 1.20),
    # --- Cohere ---
    "command-a": (2.50, 10.00),
    "command-r-plus": (2.50, 10.00),
    "command-r": (0.15, 0.60),
    # --- Perplexity ---
    "sonar-pro": (3.00, 15.00),
    "sonar": (1.00, 1.00),
    # --- MiniMax ---
    "minimax": (0.70, 0.70),
    # --- Kimi / Moonshot ---
    "moonshot": (1.00, 1.00),
    "kimi": (1.00, 1.00),
    # --- Microsoft ---
    "phi-4": (0.07, 0.14),
    "phi-3": (0.07, 0.14),
    # --- Nvidia ---
    "nemotron": (0.30, 1.20),
    # --- Nous ---
    "hermes": (0.20, 0.20),
}

# Anthropic prompt-cache multipliers on the base input price. 5-minute cache
# writes cost 1.25x; cache reads cost 0.1x except where listed below.
_ANTHROPIC_CACHE_WRITE_MULT = 1.25
_ANTHROPIC_CACHE_READ_MULT_DEFAULT = 0.10
_ANTHROPIC_CACHE_READ_MULT = {
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
    "claude-opus-5-5": 0.05,
    "claude-sonnet-5-5": 0.05,
}
# Models priced by prompt length: (threshold_tokens, (input, output)) used when
# the whole prompt (cached + uncached) exceeds the threshold.
_LONG_PROMPT_PRICING = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
}

_CGNAT_RE = re.compile(r"^100\.(\d+)\.")
_PRIVATE_172_RE = re.compile(r"^172\.(1[6-9]|2\d|3[01])\.")


def match_model_key(name: str, keys) -> str | None:
    """Most specific (longest) key that is a substring of `name` (case-blind)."""
    n = (name or "").lower()
    best: str | None = None
    for key in keys:
        if key in n and (best is None or len(key) > len(best)):
            best = key
    return best


def is_local_endpoint(url: str | None) -> bool:
    """Local / self-hosted model server → free. Missing/unparseable → local."""
    if not url:
        return True
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return True
    if not host:
        return True
    if (
        host == "localhost"
        or host == "0.0.0.0"
        or host == "host.docker.internal"
        or host.endswith(".local")
    ):
        return True
    # Single-label hostname = internal Docker service / LAN shortname, never a
    # public API (which needs an FQDN).
    if "." not in host:
        return True
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return True
    if _PRIVATE_172_RE.match(host):
        return True
    m = _CGNAT_RE.match(host)  # Tailscale CGNAT 100.64-127.x
    if m and 64 <= int(m.group(1)) <= 127:
        return True
    return False


def is_subscription_endpoint(url: str | None) -> bool:
    """ChatGPT Codex subscription endpoints are paid via subscription, not per-token."""
    if not url:
        return False
    try:
        parts = urlsplit(str(url))
        path = parts.path.rstrip("/")
    except ValueError:
        return False
    return parts.hostname == "chatgpt.com" and (
        path == "/backend-api/codex" or path.startswith("/backend-api/codex/")
    )


def is_cost_tracked_endpoint(url: str | None) -> bool:
    return not is_local_endpoint(url) and not is_subscription_endpoint(url)


def estimate_cost_usd(
    model: str | None,
    input_tokens: int | float | None,
    output_tokens: int | float | None,
    endpoint_url: str | None = None,
    cache_read_tokens: int | float | None = None,
    cache_write_tokens: int | float | None = None,
) -> float | None:
    """Estimated USD cost for a turn, or None when it can't be priced.

    ``input_tokens`` is the whole prompt. For Claude models, the optional
    ``cache_read_tokens`` / ``cache_write_tokens`` (parts of that prompt) are
    billed at Anthropic's prompt-cache rates instead of the base input price.

    None cases: unknown model (never guess), local/subscription/unknown
    endpoint, or no token counts.
    """
    if not model:
        return None
    if not is_cost_tracked_endpoint(endpoint_url):
        return None
    try:
        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)
        read_tok = max(int(cache_read_tokens or 0), 0)
        write_tok = max(int(cache_write_tokens or 0), 0)
    except (TypeError, ValueError):
        return None
    key = match_model_key(model, MODEL_PRICING.keys())
    if not key:
        return None
    price_in, price_out = MODEL_PRICING[key]
    long_tier = _LONG_PROMPT_PRICING.get(key)
    if long_tier and in_tok > long_tier[0]:
        price_in, price_out = long_tier[1]
    if not key.startswith("claude-") or not (read_tok or write_tok):
        return (in_tok * price_in + out_tok * price_out) / 1_000_000
    # Never let a malformed split bill more cache tokens than the prompt held.
    read_tok = min(read_tok, in_tok)
    write_tok = min(write_tok, in_tok - read_tok)
    uncached = in_tok - read_tok - write_tok
    read_mult = _ANTHROPIC_CACHE_READ_MULT.get(key, _ANTHROPIC_CACHE_READ_MULT_DEFAULT)
    input_cost = price_in * (
        uncached + read_tok * read_mult + write_tok * _ANTHROPIC_CACHE_WRITE_MULT
    )
    return (input_cost + out_tok * price_out) / 1_000_000
