import subprocess
import json
import time
import httpx
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cache for discovered hosts
_hosts_cache: List[str] = []
_hosts_cache_time: float = 0
_HOSTS_CACHE_TTL = 60  # seconds


def _parse_tailscale_status(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_tailscale_ipv4(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return None
    for ip in value:
        if isinstance(ip, str) and "." in ip:
            return ip
    return None


def discover_tailscale_hosts() -> List[str]:
    """Discover online Tailscale peers, returning their IPv4 addresses."""
    global _hosts_cache, _hosts_cache_time

    now = time.time()
    # Gate on the timestamp, not the list: a successful query that found no
    # eligible peers is a real answer, and testing the list's truthiness made
    # that case re-run `tailscale status` (up to a 5s timeout) on every call.
    if _hosts_cache_time and (now - _hosts_cache_time) < _HOSTS_CACHE_TTL:
        return list(_hosts_cache)

    hosts = []
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return hosts

        data = _parse_tailscale_status(result.stdout)
        if not data:
            return hosts

        # Add self
        self_data = data.get("Self") if isinstance(data.get("Self"), dict) else {}
        self_ip = _first_tailscale_ipv4(self_data.get("TailscaleIPs"))
        if self_ip:
            hosts.append(self_ip)

        # Add online peers (skip funnel-ingress-nodes and android devices)
        peers = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
        for peer in peers.values():
            if not isinstance(peer, dict):
                continue
            if not peer.get("Online"):
                continue
            hostname = peer.get("HostName", "")
            if hostname == "funnel-ingress-node":
                continue
            os_name = peer.get("OS", "")
            if os_name == "android":
                continue
            peer_ip = _first_tailscale_ipv4(peer.get("TailscaleIPs"))
            if peer_ip:
                hosts.append(peer_ip)

        _hosts_cache = hosts
        _hosts_cache_time = now
        logger.info(f"Tailscale discovery found {len(hosts)} hosts: {hosts}")
    except FileNotFoundError:
        logger.debug("tailscale command not found")
    except Exception as e:
        logger.warning(f"Tailscale discovery failed: {e}")

    return hosts



SUPPORTED_PROVIDER_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "ollama",
        "name": "Ollama",
        "category": "local",
        "endpoint_kind": "local",
        "default_base_url": "http://localhost:11434/v1",
        "default_port": 11434,
        "requires_api_key": False,
        "key_prefix_hint": "",
        "env_var": "OLLAMA_BASE_URL",
        "docs_url": "https://ollama.com",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "lmstudio",
        "name": "LM Studio",
        "category": "local",
        "endpoint_kind": "local",
        "default_base_url": "http://localhost:1234/v1",
        "default_port": 1234,
        "requires_api_key": False,
        "key_prefix_hint": "",
        "env_var": "LM_STUDIO_URL",
        "docs_url": "https://lmstudio.ai/docs/api",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "llamacpp",
        "name": "llama.cpp (llama-server)",
        "category": "local",
        "endpoint_kind": "local",
        "default_base_url": "http://localhost:8080/v1",
        "default_port": 8080,
        "requires_api_key": False,
        "key_prefix_hint": "",
        "env_var": "",
        "docs_url": "https://github.com/ggml-org/llama.cpp/tree/master/tools/server",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "vllm",
        "name": "vLLM",
        "category": "local",
        "endpoint_kind": "local",
        "default_base_url": "http://localhost:8000/v1",
        "default_port": 8000,
        "requires_api_key": False,
        "key_prefix_hint": "",
        "env_var": "",
        "docs_url": "https://docs.vllm.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "sglang",
        "name": "SGLang",
        "category": "local",
        "endpoint_kind": "local",
        "default_base_url": "http://localhost:30000/v1",
        "default_port": 30000,
        "requires_api_key": False,
        "key_prefix_hint": "",
        "env_var": "",
        "docs_url": "https://docs.sglang.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.openai.com/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "sk-",
        "env_var": "OPENAI_API_KEY",
        "docs_url": "https://platform.openai.com/docs/api-reference",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.anthropic.com/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "sk-ant-",
        "env_var": "ANTHROPIC_API_KEY",
        "docs_url": "https://docs.anthropic.com",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "google",
        "name": "Google Gemini",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "AIza",
        "env_var": "GEMINI_API_KEY",
        "docs_url": "https://ai.google.dev/gemini-api/docs/openai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "sk-or-",
        "env_var": "OPENROUTER_API_KEY",
        "docs_url": "https://openrouter.ai/docs",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "groq",
        "name": "Groq",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.groq.com/openai/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "gsk_",
        "env_var": "GROQ_API_KEY",
        "docs_url": "https://console.groq.com/docs",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "together",
        "name": "Together AI",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.together.xyz/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "",
        "env_var": "TOGETHER_API_KEY",
        "docs_url": "https://docs.together.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "sk-",
        "env_var": "DEEPSEEK_API_KEY",
        "docs_url": "https://api-docs.deepseek.com",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": False,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "xai",
        "name": "xAI (Grok)",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.x.ai/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "xai-",
        "env_var": "XAI_API_KEY",
        "docs_url": "https://docs.x.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": False,
        },
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.mistral.ai/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "",
        "env_var": "MISTRAL_API_KEY",
        "docs_url": "https://docs.mistral.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.fireworks.ai/inference/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "fw_",
        "env_var": "FIREWORKS_API_KEY",
        "docs_url": "https://docs.fireworks.ai",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": True,
            "vision": True,
            "structured_output": True,
            "embeddings": True,
        },
    },
    {
        "id": "featherless",
        "name": "Featherless AI",
        "category": "hosted",
        "endpoint_kind": "api",
        "default_base_url": "https://api.featherless.ai/v1",
        "default_port": 443,
        "requires_api_key": True,
        "key_prefix_hint": "rc_",
        "env_var": "FEATHERLESS_API_KEY",
        "docs_url": "https://featherless.ai/docs",
        "default_capabilities": {
            "chat": True,
            "streaming": True,
            "tools": False,
            "vision": False,
            "structured_output": False,
            "embeddings": False,
        },
    },
]

_PROVIDER_DISPLAY_NAMES: Dict[str, str] = {
    p["id"]: p["name"] for p in SUPPORTED_PROVIDER_PRESETS
}
_PROVIDER_DISPLAY_NAMES["apfel"] = "APFEL"


def get_provider_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return supported local and hosted provider setup presets."""
    cat = (category or "").strip().lower()
    if cat in {"local", "hosted"}:
        return [dict(p) for p in SUPPORTED_PROVIDER_PRESETS if p["category"] == cat]
    return [dict(p) for p in SUPPORTED_PROVIDER_PRESETS]


def match_provider_preset(
    base_url: str,
    detected_provider: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Match a base URL or fingerprinted provider ID to a preset definition."""
    slug = (detected_provider or "").strip().lower()
    if slug:
        for preset in SUPPORTED_PROVIDER_PRESETS:
            if preset["id"] == slug:
                return dict(preset)
    raw = (base_url or "").strip().lower()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    port = parsed.port
    for preset in SUPPORTED_PROVIDER_PRESETS:
        p_parsed = urlparse(preset["default_base_url"])
        p_host = (p_parsed.hostname or "").lower()
        if p_host not in {"localhost", "127.0.0.1"} and (host == p_host or host.endswith("." + p_host)):
            return dict(preset)
    if port:
        for preset in SUPPORTED_PROVIDER_PRESETS:
            if preset["category"] == "local" and preset.get("default_port") == port:
                return dict(preset)
    return None


def validate_provider_setup(
    base_url: str,
    api_key: str = "",
    endpoint_kind: str = "auto",
    detected_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate provider setup parameters and return actionable setup guidance."""
    warnings: List[str] = []
    raw_url = (base_url or "").strip()
    parsed = urlparse(raw_url if "://" in raw_url else f"http://{raw_url}")
    host = (parsed.hostname or "").strip()
    port = parsed.port
    preset = match_provider_preset(raw_url, detected_provider=detected_provider)
    provider_id = (
        (detected_provider or "").strip().lower()
        or (preset["id"] if preset else "")
    )
    display_name = _PROVIDER_DISPLAY_NAMES.get(provider_id) or (
        preset["name"] if preset else (host or "Custom Endpoint")
    )
    if preset and preset["category"] == "local" and host:
        suggested_name = f"{display_name} ({host}:{port})" if port else f"{display_name} ({host})"
    else:
        suggested_name = display_name

    clean_key = (api_key or "").strip()
    if preset:
        if preset.get("requires_api_key") and not clean_key:
            env_hint = f" (or set {preset['env_var']})" if preset.get("env_var") else ""
            warnings.append(
                f"{preset['name']} requires an API key{env_hint}."
            )
        prefix = (preset.get("key_prefix_hint") or "").strip()
        if clean_key and prefix and not clean_key.startswith(prefix):
            warnings.append(
                f"{preset['name']} API keys typically start with '{prefix}'."
            )
        expected_base = preset.get("default_base_url", "")
        if (
            preset["category"] == "hosted"
            and "/v1" in expected_base
            and (parsed.path or "") in {"", "/"}
        ):
            warnings.append(
                f"Base URL has no path suffix; {preset['name']} expects '{expected_base}'."
            )

    inferred_kind = (endpoint_kind or "auto").strip().lower()
    if inferred_kind == "auto":
        inferred_kind = preset["endpoint_kind"] if preset else (
            "local" if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"} else "api"
        )

    return {
        "valid": len(warnings) == 0,
        "provider": provider_id or None,
        "provider_name": display_name,
        "suggested_name": suggested_name,
        "endpoint_kind": inferred_kind,
        "preset": preset,
        "warnings": warnings,
    }


class ModelDiscovery:
    def __init__(self, default_host: str, openai_api_key: Optional[str] = None):
        self.default_host = default_host
        self.openai_api_key = openai_api_key
        self.openai_compat_path = "/v1/chat/completions"
        # Custom ports from env vars, merged into the scan list by discover_models.
        self._extra_ports: set = set()

    def _get_hosts(self) -> List[str]:
        """Get all hosts to scan, using env override, Tailscale, or default."""
        self._extra_ports = set()

        def _append_host(out: List[str], host: str) -> None:
            host = (host or "").strip()
            if not host or host in out:
                return
            out.append(host)

        def _append_env_hosts(out: List[str]) -> None:
            """Add hosts (and any custom ports) from provider-specific env vars."""
            for env_name in ("OLLAMA_BASE_URL", "OLLAMA_URL", "LM_STUDIO_URL"):
                raw = os.getenv(env_name, "").strip()
                if not raw:
                    continue
                try:
                    parsed = urlparse(raw if "://" in raw else "http://" + raw)
                    _append_host(out, parsed.hostname or "")
                    if parsed.port:
                        self._extra_ports.add(parsed.port)
                except Exception:
                    pass

        # Manual override takes priority
        extra = os.getenv("LLM_HOSTS", "").strip()
        if extra:
            hosts = [h.strip() for h in extra.split(",") if h.strip()]
            # Always include the default host too
            if self.default_host not in hosts:
                hosts.insert(0, self.default_host)
            _append_host(hosts, "host.docker.internal")
            _append_env_hosts(hosts)
            return hosts

        # Try Tailscale discovery
        ts_hosts = discover_tailscale_hosts()
        if ts_hosts:
            # Ensure default_host is included
            if self.default_host not in ts_hosts:
                ts_hosts.insert(0, self.default_host)
            _append_host(ts_hosts, "host.docker.internal")
            _append_env_hosts(ts_hosts)
            return ts_hosts

        hosts = [self.default_host]
        # Docker desktop/Linux compose maps this to the host machine. That is
        # the common "I started Ollama normally on this computer" case.
        _append_host(hosts, "host.docker.internal")
        _append_env_hosts(hosts)
        return hosts

    def _fingerprint_provider(self, host: str, port: int) -> Optional[str]:
        """Identify the server software via its native API, independent of port."""
        try:
            r = httpx.get(f"http://{host}:{port}/api/v1/models", timeout=1.5)
            if r.is_success:
                models = (r.json() or {}).get("models")
                if (
                    isinstance(models, list)
                    and models
                    and isinstance(models[0], dict)
                    and "key" in models[0]
                    and "architecture" in models[0]
                ):
                    return "lmstudio"
        except Exception:
            pass
        # llama.cpp's llama-server exposes a native /props endpoint (no /v1 prefix)
        # describing the loaded model, slots, and chat template — distinct from
        # LM Studio (/api/v1/models) and vLLM (/version, /metrics).
        try:
            r = httpx.get(f"http://{host}:{port}/props", timeout=1.5)
            if r.is_success:
                props = r.json() or {}
                if isinstance(props, dict) and (
                    "default_generation_settings" in props
                    or "total_slots" in props
                    or "chat_template" in props
                ):
                    return "llamacpp"
        except Exception:
            pass
        # SGLang native /get_model_info endpoint
        try:
            r = httpx.get(f"http://{host}:{port}/get_model_info", timeout=1.5)
            if r.is_success:
                info = r.json() or {}
                if isinstance(info, dict) and (
                    "model_path" in info or "tokenizer_path" in info
                ):
                    return "sglang"
        except Exception:
            pass
        # Ollama native /api/version endpoint (must carry a version string and
        # not look like /api/tags or OpenAI /models)
        try:
            r = httpx.get(f"http://{host}:{port}/api/version", timeout=1.5)
            if r.is_success:
                ver = r.json() or {}
                if (
                    isinstance(ver, dict)
                    and isinstance(ver.get("version"), str)
                    and "models" not in ver
                    and "data" not in ver
                ):
                    return "ollama"
        except Exception:
            pass
        # vLLM native /version endpoint (distinct from Ollama /api/version)
        try:
            r = httpx.get(f"http://{host}:{port}/version", timeout=1.5)
            if not getattr(r, "url", None) or str(r.url).endswith(f":{port}/version"):
                pass
            if r.is_success:
                ver = r.json() or {}
                if (
                    isinstance(ver, dict)
                    and isinstance(ver.get("version"), str)
                    and "models" not in ver
                    and "data" not in ver
                ):
                    return "vllm"
        except Exception:
            pass
        if port == 11435:
            return "apfel"
        return None

    def _check_port(self, host: str, port: int) -> Optional[Dict[str, Any]]:
        """Check a single host:port for models."""
        base = f"http://{host}:{port}/v1"
        try:
            r = httpx.get(f"{base}/models", timeout=3)
            if not r.is_success:
                return None
            data = r.json()
            # Some OpenAI-compatible servers return a bare list, not {"data": [...]}.
            items = data if isinstance(data, list) else ((data or {}).get("data") or [])
            ids = [
                m["id"] for m in items
                if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]
            ]
            if ids:
                provider = self._fingerprint_provider(host, port)
                display_provider = _PROVIDER_DISPLAY_NAMES.get(provider or "", provider or "OpenAI-Compatible")
                return {
                    "host": host,
                    "port": port,
                    "base_url": base,
                    "url": f"http://{host}:{port}{self.openai_compat_path}",
                    "endpoint_kind": "local",
                    "suggested_name": f"{display_provider} ({host}:{port})",
                    "models": ids,
                    "models_display": [i.lstrip("/") for i in ids],
                    "provider": provider,
                }
        except Exception:
            pass
        # Fallback for Ollama builds where /v1/models is empty/disabled but /api/tags works
        try:
            r_tags = httpx.get(f"http://{host}:{port}/api/tags", timeout=2)
            if r_tags.is_success:
                tags_data = r_tags.json() or {}
                tag_models = tags_data.get("models") if isinstance(tags_data, dict) else None
                if isinstance(tag_models, list):
                    ids = [
                        m["name"] for m in tag_models
                        if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"]
                    ]
                    if ids:
                        return {
                            "host": host,
                            "port": port,
                            "base_url": base,
                            "url": f"http://{host}:{port}{self.openai_compat_path}",
                            "endpoint_kind": "local",
                            "suggested_name": f"Ollama ({host}:{port})",
                            "models": ids,
                            "models_display": [i.lstrip("/") for i in ids],
                            "provider": "ollama",
                        }
        except Exception:
            pass
        return None

    def discover_models(self) -> Dict[str, List[Dict[str, Any]]]:
        """Discover available models from all reachable hosts."""
        hosts = self._get_hosts()
        items = []

        logger.info(f"Scanning {len(hosts)} hosts for models: {hosts}")

        # Well-known ports: 8000-8020 (vLLM, SGLang, Cookbook), 8080 (llama.cpp /
        # llama-server default), 1234 (LM Studio), 11434 (Ollama), 11435 for APFEL
        # as its default port is occupied by Ollama. The env vars can add more
        # ports which will be merged in.
        ports = list(range(8000, 8021)) + [8080, 1234, 11434, 11435, 30000]
        ports += [p for p in sorted(self._extra_ports) if p not in ports]
        targets = [(h, p) for h in hosts for p in ports]

        seen_models = (
            set()
        )  # dedupe by (port, model_ids) to avoid same machine via different IPs

        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = {pool.submit(self._check_port, h, p): (h, p) for h, p in targets}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    key = (result["port"], tuple(sorted(result["models"])))
                    if key not in seen_models:
                        seen_models.add(key)
                        items.append(result)

        # Sort by host then port for consistent ordering
        items.sort(key=lambda x: (x["host"], x["port"]))

        logger.info(
            f"Discovered {len(items)} model endpoints across {len(hosts)} hosts"
        )
        return {"hosts": hosts, "items": items}

    def warmup_ping_urls(self, limit: int = 5) -> List[str]:
        """The ``/models`` URLs of up to ``limit`` discovered endpoints.

        Used by the startup warmup / keepalive loop to prime connections. Each
        discovered item already carries a ``/v1/chat/completions`` url; swap the
        suffix for the cheap ``/models`` probe. Failures degrade to an empty list
        so warmup never crashes the caller.
        """
        try:
            items = (self.discover_models() or {}).get("items", [])
        except Exception:
            return []
        urls: List[str] = []
        for ep in items[:limit]:
            url = (ep.get("url") or "").replace("/chat/completions", "/models")
            if url:
                urls.append(url)
        return urls

    def get_providers(self) -> Dict[str, Any]:
        """Get all available providers"""
        discovery = self.discover_models()
        items = discovery["items"]
        providers = [{"provider": "vllm", "hosts": discovery["hosts"], "items": items}]

        if self.openai_api_key:
            openai_models = [
                "gpt-5.2-codex",
                "gpt-4o-mini",
                "gpt-image-1.5",
                "gpt-4o",
                "gpt-5.2",
                "gpt-5.2-pro",
            ]
            providers.append(
                {
                    "provider": "openai",
                    "items": [
                        {
                            "url": "https://api.openai.com/v1/chat/completions",
                            "models": openai_models,
                        }
                    ],
                }
            )

        return {"providers": providers, "presets": get_provider_presets()}
