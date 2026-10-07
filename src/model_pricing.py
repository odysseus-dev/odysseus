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
    # --- Anthropic ---
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
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
) -> float | None:
    """Estimated USD cost for a turn, or None when it can't be priced.

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
    except (TypeError, ValueError):
        return None
    key = match_model_key(model, MODEL_PRICING.keys())
    if not key:
        return None
    price_in, price_out = MODEL_PRICING[key]
    return (in_tok * price_in + out_tok * price_out) / 1_000_000
