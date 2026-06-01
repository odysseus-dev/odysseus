# src/endpoint_resolver.py
"""Unified endpoint resolution for all backend services.

Consolidates the 4+ copies of normalize_base / resolve_endpoint logic into one place.
"""

import json
import logging
import socket
import subprocess
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse, urlunparse

from src.database import SessionLocal, ModelEndpoint
from src.llm_core import _detect_provider

logger = logging.getLogger(__name__)

# Model-name substrings that are NOT chat/generation models. When an endpoint
# has no explicit model configured we pick the first CHAT model from its list —
# never an embedding/tts/etc. (an OpenAI-style endpoint often lists
# `text-embedding-ada-002` first, which silently broke email-summarize and
# other resolve_endpoint callers with "Cannot reach model").
_NON_CHAT_MODEL = (
    "text-embedding", "embedding", "tts-", "whisper", "dall-e",
    "moderation", "rerank", "reranker", "clip", "stable-diffusion",
)


def _first_chat_model(models) -> Optional[str]:
    """First model that isn't an embedding/tts/etc.; falls back to models[0]."""
    for m in (models or []):
        if not any(p in str(m).lower() for p in _NON_CHAT_MODEL):
            return m
    return (models[0] if models else None)


# Cache for Tailscale hostname → IP resolution
_tailscale_cache: Dict[str, Optional[str]] = {}


def _resolve_tailscale_host(hostname: str) -> Optional[str]:
    """Try to resolve a hostname via 'tailscale status' if DNS fails."""
    if hostname in _tailscale_cache:
        return _tailscale_cache[hostname]

    # First check if normal DNS works
    try:
        socket.getaddrinfo(hostname, None, socket.AF_INET)
        _tailscale_cache[hostname] = None  # DNS works, no override needed
        return None
    except socket.gaierror:
        pass

    # DNS failed — try tailscale
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json as _json
            data = _json.loads(result.stdout)
            peers = data.get("Peer", {})
            for _id, peer in peers.items():
                peer_name = (peer.get("HostName") or "").lower()
                dns_name = (peer.get("DNSName") or "").split(".")[0].lower()
                if peer_name == hostname.lower() or dns_name == hostname.lower():
                    addrs = peer.get("TailscaleIPs", [])
                    if addrs:
                        ip = addrs[0]
                        logger.info(f"Resolved '{hostname}' via Tailscale → {ip}")
                        _tailscale_cache[hostname] = ip
                        return ip
    except Exception as e:
        logger.debug(f"Tailscale resolution failed for '{hostname}': {e}")

    _tailscale_cache[hostname] = None
    return None


def resolve_url(url: str) -> str:
    """If a URL's hostname can't be resolved via DNS, try Tailscale."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return url
    ip = _resolve_tailscale_host(hostname)
    if ip:
        # Replace hostname with IP in the URL
        netloc = ip
        if parsed.port:
            netloc = f"{ip}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return url


def normalize_base(url: str) -> str:
    """Strip known API path suffixes from a base URL."""
    url = (url or "").strip().rstrip("/")
    for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    for suffix in ["/chat", "/tags", "/generate"]:
        if url.endswith("/api" + suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _anthropic_api_root(base: str) -> str:
    """Return Anthropic's API root, preserving /v1 for OpenAI-compatible APIs elsewhere."""
    base = (base or "").strip().rstrip("/")
    host = urlparse(base).hostname or ""
    if host.endswith("anthropic.com") and base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


def _ollama_api_root(base: str) -> str:
    """Return the native Ollama API root, adding /api for ollama.com hosts."""
    base = (base or "").strip().rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api"):
        return base
    if host.endswith("ollama.com"):
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://ollama.com"
        return root.rstrip("/") + "/api"
    return base


def build_chat_url(base: str) -> str:
    """Return the correct chat endpoint URL for a given base."""
    base = resolve_url(base)
    provider = _detect_provider(base)
    host = urlparse(base).hostname or ""
    if provider == "anthropic" or host.endswith("anthropic.com"):
        return _anthropic_api_root(base) + "/v1/messages"
    if provider == "ollama" or host.endswith("ollama.com"):
        return _ollama_api_root(base) + "/chat"
    return base + "/chat/completions"


def build_models_url(base: str) -> str:
    """Return the provider-specific model-list endpoint URL for a base."""
    base = resolve_url(base)
    provider = _detect_provider(base)
    host = urlparse(base).hostname or ""
    if provider == "anthropic" or host.endswith("anthropic.com"):
        return _anthropic_api_root(base) + "/v1/models"
    if provider == "ollama" or host.endswith("ollama.com"):
        return _ollama_api_root(base) + "/tags"
    return base + "/models"


def build_headers(api_key: Optional[str], base: str) -> Dict[str, str]:
    """Build auth headers for an endpoint."""
    provider = _detect_provider(base)
    headers: Dict[str, str] = {}
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers.setdefault("HTTP-Referer", "https://github.com/pewdiepie-archdaemon/odysseus")
        headers.setdefault("X-OpenRouter-Title", "Odysseus")
    return headers


def resolve_endpoint(
    setting_prefix: str,
    fallback_url: Optional[str] = None,
    fallback_model: Optional[str] = None,
    fallback_headers: Optional[Dict] = None,
    owner: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Resolve an endpoint/model from settings, with fallback.

    Args:
        setting_prefix: Settings key prefix, e.g. "research", "task", "utility", "default".
                       Reads ``{prefix}_endpoint_id`` and ``{prefix}_model`` from settings.
        fallback_url:    URL to use if settings are empty or endpoint missing.
        fallback_model:  Model to use if settings are empty.
        fallback_headers: Headers to use if using fallback.

    Returns:
        (endpoint_url, model, headers) — resolved or fallback values.
    """
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
    except Exception:
        return fallback_url, fallback_model, fallback_headers

    ep_id = (get_user_setting(f"{setting_prefix}_endpoint_id", owner or "", settings.get(f"{setting_prefix}_endpoint_id", "")) or "").strip()
    model = (get_user_setting(f"{setting_prefix}_model", owner or "", settings.get(f"{setting_prefix}_model", "")) or "").strip()

    # Unset Utility means "same as Default Chat Model". This keeps background
    # features usable out of the box and lets users override Utility only when
    # they explicitly want a separate cheaper/faster model.
    if setting_prefix == "utility" and not ep_id:
        ep_id = (get_user_setting("default_endpoint_id", owner or "", settings.get("default_endpoint_id", "")) or "").strip()
        model = (get_user_setting("default_model", owner or "", settings.get("default_model", "")) or "").strip()

    # Fall back to utility model for task/research/auto-naming if not specifically configured.
    # If Utility itself is unset, the block above makes that resolve to Default Chat.
    if not ep_id and setting_prefix != "utility":
        ep_id = (get_user_setting("utility_endpoint_id", owner or "", settings.get("utility_endpoint_id", "")) or "").strip()
        model = (get_user_setting("utility_model", owner or "", settings.get("utility_model", "")) or "").strip()
        if not ep_id:
            ep_id = (get_user_setting("default_endpoint_id", owner or "", settings.get("default_endpoint_id", "")) or "").strip()
            model = (get_user_setting("default_model", owner or "", settings.get("default_model", "")) or "").strip()

    if not ep_id:
        return fallback_url, fallback_model, fallback_headers

    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == ep_id,
            ModelEndpoint.is_enabled == True,
        )
        if owner:
            from src.auth_helpers import owner_filter
            ep = owner_filter(ep, ModelEndpoint, owner).first()
        else:
            ep = ep.first()
        if not ep:
            return fallback_url, fallback_model, fallback_headers

        base = normalize_base(ep.base_url)
        chat_url = build_chat_url(base)
        headers = build_headers(ep.api_key, base)

        # If no model specified, try to pick the first from endpoint's cached list
        if not model and hasattr(ep, 'models') and ep.models:
            try:
                models = json.loads(ep.models) if isinstance(ep.models, str) else ep.models
                if models:
                    model = _first_chat_model(models)
            except Exception:
                pass

        return chat_url, model or fallback_model, headers
    except Exception as e:
        logger.debug(f"Could not resolve {setting_prefix} endpoint: {e}")
        return fallback_url, fallback_model, fallback_headers
    finally:
        db.close()


def resolve_endpoint_by_id(
    ep_id: str, model: Optional[str] = None
) -> Optional[Tuple[str, str, Dict]]:
    """Resolve a specific endpoint id (+ optional model) to (chat_url, model, headers).

    Returns None if the endpoint doesn't exist or is disabled. Used to turn
    a configured fallback entry ({endpoint_id, model}) into a dispatch target.
    """
    if not ep_id:
        return None
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == ep_id,
            ModelEndpoint.is_enabled == True,
        ).first()
        if not ep:
            return None
        base = normalize_base(ep.base_url)
        chat_url = build_chat_url(base)
        headers = build_headers(ep.api_key, base)
        m = (model or "").strip()
        if not m and getattr(ep, "models", None):
            try:
                models = json.loads(ep.models) if isinstance(ep.models, str) else ep.models
                if models:
                    m = _first_chat_model(models) or ""
            except Exception:
                pass
        if not m:
            return None
        return chat_url, m, headers
    except Exception as e:
        logger.debug(f"Could not resolve endpoint {ep_id}: {e}")
        return None
    finally:
        db.close()


def resolve_chat_fallback_candidates() -> list:
    """Build the configured default-chat fallback chain as a list of
    (chat_url, model, headers) tuples, skipping any that can't resolve.

    The primary model is NOT included — callers prepend their session's
    current (url, model, headers) so per-session model overrides are honored.
    """
    return _resolve_fallback_candidates("default_model_fallbacks")


def resolve_utility_fallback_candidates(owner: Optional[str] = None) -> list:
    """Configured fallback chain for the Utility model (`utility_model_fallbacks`)."""
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
        if not (get_user_setting("utility_endpoint_id", owner or "", settings.get("utility_endpoint_id", "")) or "").strip():
            return _resolve_fallback_candidates("default_model_fallbacks", owner=owner)
    except Exception:
        pass
    return _resolve_fallback_candidates("utility_model_fallbacks", owner=owner)


def resolve_vision_fallback_candidates() -> list:
    """Configured fallback chain for the Vision model (`vision_model_fallbacks`)."""
    return _resolve_fallback_candidates("vision_model_fallbacks")


def _resolve_fallback_candidates(setting_key: str, owner: Optional[str] = None) -> list:
    out = []
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
        chain = get_user_setting(setting_key, owner or "", settings.get(setting_key) or []) or []
    except Exception:
        return out
    for entry in chain:
        if not isinstance(entry, dict):
            continue
        resolved = resolve_endpoint_by_id(entry.get("endpoint_id", ""), entry.get("model", ""))
        if resolved:
            out.append(resolved)
    return out
