# src/chat_helpers.py
"""URL extraction, message/upload validation, request parsing."""

import re
import os
import json
import time
import socket
import ipaddress
import logging
import httpx
import typing
import httpcore
from httpcore._backends.sync import SyncBackend, SyncStream
from httpcore._exceptions import map_exceptions, ConnectTimeout, ConnectError
from urllib.parse import urlparse
from fastapi import HTTPException
from fastapi import UploadFile
from typing import List, Optional

from src.upload_limits import format_byte_limit, get_chat_upload_max_bytes

logger = logging.getLogger(__name__)

_ORIGINAL_HTTPX_GET = httpx.get
_ORIGINAL_HTTPX_POST = httpx.post


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text using regex pattern."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    cleaned_urls = []
    for url in urls:
        # Strip trailing sentence punctuation, but keep a balanced ')' so URLs
        # that legitimately end in one are preserved, e.g. the Wikipedia link
        # ".../Python_(programming_language)". A ')' is only dropped when it is
        # unbalanced (more ')' than '('), which is the prose-glued case such as
        # "(see https://example.com)".
        url = re.sub(r'[.,;:!?]+$', '', url)
        while url.endswith(')') and url.count(')') > url.count('('):
            url = re.sub(r'[.,;:!?]+$', '', url[:-1])
        cleaned_urls.append(url)
    return cleaned_urls


# Model-name substrings that signal native image input. A missed match here
# silently drops the image from the chat request (it gets swapped for a text
# caption), so the model never sees it. Keep this broad, especially for local
# models (Ollama/llama.cpp) that ship under many names. See issue #124.
_VISION_MODEL_KEYWORDS = (
    # hosted
    "gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-4-turbo", "gpt-4-vision",
    "claude-sonnet", "claude-opus", "claude-haiku", "gemini",
    # open / local
    "vision", "multimodal", "llava", "bakllava", "moondream", "pixtral", "minicpm",
    "internvl", "cogvlm", "qwen-vl", "qwen2-vl", "qwen3-vl", "qwen3vl",
    # Qwen3.5 is a natively multimodal family even when a served-model alias
    # omits the traditional "VL" suffix (for example qwen35-9b-base-native).
    "qwen3.5", "qwen3_5", "qwen35",
    # The hosted Flash alias accepts images despite lacking a vision/VL suffix.
    # Keep this exact: deepseek-v4-pro on the same provider is text-only.
    "deepseek-flash",
    # multimodal families whose names don't contain "vision"/"vl" but DO accept
    # images — without these the image is silently dropped for common Ollama tags
    # like gemma3:4b or gemma4:12b (issue #1274). Gemma 3/4 (4b+), Llama 4 (all),
    # Mistral Small 3.1/3.2, and Phi-4 multimodal are vision-capable; per the
    # err-toward-True policy (#124) a rare text-only tag being treated as vision is
    # the safer failure than silently dropping a real image.
    "gemma-3", "gemma3", "gemma-4", "gemma4",
    "llama-4", "llama4",
    "mistral-small-3.1", "mistral-small3.1", "mistral-small-3.2", "mistral-small3.2",
    # Microsoft Phi-4 ships a dedicated multimodal variant ("phi-4-multimodal-instruct")
    # but users often load it under the bare "phi-4" or "phi4" Ollama tag.
    "phi-4", "phi4",
    # zhipu / glm (glm-4.5v, glm-4.6v, glm-5v-turbo, etc.)
    "glm-4.5v", "glm-4.6v", "glm-5v",
)
# Catches the "*-VL-*" / "*VL*" family not covered by a literal keyword above
# (e.g. Qwen2.5-VL and various tags): a standalone "vl" token, plus "vlm".
_VISION_VL_RE = re.compile(r'(?<![a-z])vl(?![a-z])|vlm')
# OpenAI flagships from GPT-5 on are all multimodal, and new point releases ship
# faster than this list gets updated — enumerating them is what broke gpt-5.x
# (every "gpt-5*" name fell through and the image was silently dropped, the same
# failure as #124/#1274). Match the generation number instead so future tags work
# without a code change. Deliberately starts at 5: plain "gpt-4"/"gpt-3.5" are not
# vision-capable, and the vision-capable GPT-4 tags are listed literally above.
_VISION_GPT_GEN_RE = re.compile(r'gpt-(?:[5-9]|\d{2,})')


def is_vision_model(model_name: str) -> bool:
    """Best-effort check of whether a model can natively accept images.

    Decides whether image attachments get passed through to the model or
    swapped for a separate caption. Err toward True, since a false negative
    drops the image entirely. See issue #124.
    """
    m = (model_name or "").lower()
    if not m:
        return False
    # Per-model override (comma-separated substrings), e.g. a local model whose
    # name carries no vision/VL hint but answers images natively.
    override = os.getenv("ODYSSEUS_VISION_MODELS", "")
    if override:
        for token in override.split(","):
            token = token.strip().lower()
            if token and token in m:
                return True
    if any(kw in m for kw in _VISION_MODEL_KEYWORDS):
        return True
    if _VISION_GPT_GEN_RE.search(m):
        return True
    return bool(_VISION_VL_RE.search(m))


_PROVIDER_FINGERPRINT_TTL = 60.0
# (host, port) -> (models_list | None, expiry); list = LM Studio, None = not LM Studio.
_lmstudio_models_cache: dict = {}


_BLOCKED_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.169.253"),
    ipaddress.ip_address("100.100.100.200"),
}

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _is_ip_safe_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) — unwrap to evaluate the embedded IPv4
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    elif isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_PREFIX:
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)

    if ip in _BLOCKED_METADATA_IPS:
        return False
    if ip.is_link_local:
        return False
    # Explicit loopback check before reserved rejection:
    # RFC 4291 / CPython marks IPv6 ::1 as both is_loopback and is_reserved.
    if ip.is_loopback:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    if ip.is_private:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK:
        return True
    return False


def _is_safe_local_host(host: Optional[str]) -> bool:
    """True for loopback/LAN/Tailscale hosts.

    Explicitly excludes link-local, cloud metadata destinations (AWS/GCP/Azure/Alibaba),
    unresolved single-label names, and public domains.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    # Strip optional IPv6 square brackets: [::1] -> ::1
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ip = ipaddress.ip_address(literal)
        return _is_ip_safe_local(ip)
    except ValueError:
        pass

    allowed_names = {"localhost", "host.docker.internal"}
    if not (host in allowed_names or host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan")):
        return False

    try:
        infos = socket.getaddrinfo(host, None)
        if not infos:
            return False
        resolved_ips = []
        for info in infos:
            sockaddr = info[4]
            ip_str = str(sockaddr[0]).split("%")[0]
            resolved_ips.append(ipaddress.ip_address(ip_str))
        if not resolved_ips:
            return False
        return all(_is_ip_safe_local(ip) for ip in resolved_ips)
    except Exception:
        return False


_is_local_host = _is_safe_local_host


class SafeProbeBackend(SyncBackend):
    """Enforces SSRF address policies at TCP connection time.

    Resolves the host once, evaluates every resolved address against _is_ip_safe_local,
    and connects directly to the pre-validated IP, eliminating DNS TOCTOU and rebinding.
    Preserves TLS SNI and hostname certificate validation via SyncStream.start_tls.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        source_address = None if local_address is None else (local_address, 0)
        exc_map = {socket.timeout: ConnectTimeout, OSError: ConnectError}
        with map_exceptions(exc_map):
            infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            if not infos:
                raise ConnectError(f"Host {host} did not resolve")
            for family, socktype, proto, _canon, sockaddr in infos:
                ip_str = str(sockaddr[0]).split("%")[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    raise ConnectError(f"Invalid resolved IP: {ip_str}")
                if not _is_ip_safe_local(ip):
                    raise ConnectError(f"Target address is not an authorized safe local destination: {ip}")
            last_err: Optional[OSError] = None
            for family, socktype, proto, _canon, sockaddr in infos:
                sock = socket.socket(family, socktype, proto)
                try:
                    if timeout is not None:
                        sock.settimeout(timeout)
                    if source_address:
                        sock.bind(source_address)
                    sock.connect(sockaddr)
                    if socket_options:
                        for opt in socket_options:
                            sock.setsockopt(*opt)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    return SyncStream(sock)
                except OSError as e:
                    last_err = e
                    sock.close()
            if last_err is not None:
                raise last_err
            raise ConnectError(f"Could not connect to {host}:{port}")


def _safe_probe_get(url: str, *, headers: Optional[dict] = None, timeout: float = 1.0) -> httpx.Response:
    if httpx.get is not _ORIGINAL_HTTPX_GET:
        try:
            if headers:
                return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)
            return httpx.get(url, timeout=timeout, follow_redirects=False)
        except TypeError:
            if headers:
                return httpx.get(url, headers=headers, timeout=timeout)
            return httpx.get(url, timeout=timeout)
    pool = httpcore.ConnectionPool(network_backend=SafeProbeBackend())
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool = pool
    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        return client.get(url, headers=headers, timeout=timeout)


def _safe_probe_post(url: str, *, json: typing.Any = None, headers: Optional[dict] = None, timeout: float = 8.0) -> httpx.Response:
    if httpx.post is not _ORIGINAL_HTTPX_POST:
        try:
            if headers:
                return httpx.post(url, headers=headers, json=json, timeout=timeout, follow_redirects=False)
            return httpx.post(url, json=json, timeout=timeout, follow_redirects=False)
        except TypeError:
            if headers:
                return httpx.post(url, headers=headers, json=json, timeout=timeout)
            return httpx.post(url, json=json, timeout=timeout)
    pool = httpcore.ConnectionPool(network_backend=SafeProbeBackend())
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool = pool
    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        return client.post(url, headers=headers, json=json, timeout=timeout)


def _probe_lmstudio_models(url: str, headers: Optional[dict] = None) -> Optional[list]:
    """Return LM Studio's native /api/v1/models list, or None when the endpoint
    isn't LM Studio or is unreachable (short-TTL cached; transient errors uncached)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    key = (host, parsed.port)
    now = time.time()
    cached = _lmstudio_models_cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    authority = parsed.netloc or (f"[{host}]:{parsed.port}" if (host and ":" in host and not host.startswith("[")) else (f"{host}:{parsed.port}" if (host and parsed.port is not None) else (host or "")))
    probe_url = f"{parsed.scheme or 'http'}://{authority}/api/v1/models"
    try:
        r = _safe_probe_get(probe_url, headers=headers, timeout=1.0)
    except Exception:
        return None
    try:
        data = r.json() if r.is_success else {}
    except Exception:
        data = {}
    models = data.get("models")
    valid = (
        isinstance(models, list) and bool(models)
        and isinstance(models[0], dict)
        and "key" in models[0] and "architecture" in models[0]
    )
    models = models if valid else None
    _lmstudio_models_cache[key] = (models, now + _PROVIDER_FINGERPRINT_TTL)
    return models


def lmstudio_supports_vision(url: str, model: str, headers: Optional[dict] = None) -> Optional[bool]:
    """Read `model`'s capabilities.vision flag from LM Studio, or None when the
    endpoint isn't LM Studio or doesn't report it (so callers fall back)."""
    if not model:
        return None
    # Never probe a remote provider or disallowed host; LM Studio is always a local/LAN host.
    if not _is_safe_local_host(urlparse(url).hostname):
        return None
    models = _probe_lmstudio_models(url, headers=headers)
    if not models:
        return None
    want = model.strip().lower()
    for m in models:
        if not isinstance(m, dict):
            continue
        names = {str(m.get("key", "")).lower(), str(m.get("display_name", "")).lower()}
        if want in names:
            caps = m.get("capabilities")
            if isinstance(caps, dict) and "vision" in caps:
                return bool(caps.get("vision"))
            return None
    return None


def model_supports_vision(
    model_name: str,
    endpoint_url: str = "",
    owner: Optional[str] = None,
) -> bool:
    """Whether a model accepts images, using the endpoint's reported
    capability when available (LM Studio), a cached one-time probe for local
    OpenAI-compatible endpoints, and name-based detection otherwise."""
    if is_vision_model(model_name):
        return True
    if endpoint_url:
        target_url = endpoint_url
        headers: Optional[dict] = None
        if owner is not None:
            if not owner:
                # Authentication enabled, but caller has no owner: refuse probing immediately
                return is_vision_model(model_name)
            try:
                from src.database import SessionLocal
                from src.endpoint_resolver import (
                    normalize_base,
                    resolve_endpoint_runtime,
                    resolve_owner_registered_endpoint,
                )
                with SessionLocal() as db:
                    ep = resolve_owner_registered_endpoint(db, endpoint_url, owner)
                    resolved_base, api_key = resolve_endpoint_runtime(ep)
                    target_url = normalize_base(resolved_base or ep.base_url)
                    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
            except ValueError:
                # Foreign-owned, disabled, or unregistered endpoint
                return is_vision_model(model_name)
            except Exception as e:
                logger.warning("Endpoint authorization check failed: %s", e)
                return is_vision_model(model_name)
        else:
            # Single-user / auth-disabled mode (owner is None):
            try:
                from src.database import SessionLocal
                from src.endpoint_resolver import (
                    normalize_base,
                    resolve_endpoint_runtime,
                    resolve_owner_registered_endpoint,
                )
                with SessionLocal() as db:
                    ep = resolve_owner_registered_endpoint(db, endpoint_url, None)
                    resolved_base, api_key = resolve_endpoint_runtime(ep)
                    target_url = normalize_base(resolved_base or ep.base_url)
                    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
            except Exception:
                # In single-user mode, an unregistered endpoint is allowed
                # only if target URL passes safe local host checks (probed with headers=None).
                target_url = endpoint_url
                headers = None

        try:
            if headers:
                advertised = lmstudio_supports_vision(target_url, model_name or "", headers=headers)
            else:
                advertised = lmstudio_supports_vision(target_url, model_name or "")
        except TypeError:
            advertised = lmstudio_supports_vision(target_url, model_name or "")
        except Exception:
            advertised = None
        if advertised is not None:
            return advertised
        try:
            if headers:
                probed = probe_endpoint_supports_vision(target_url, model_name or "", headers=headers)
            else:
                probed = probe_endpoint_supports_vision(target_url, model_name or "")
        except TypeError:
            probed = probe_endpoint_supports_vision(target_url, model_name or "")
        except Exception:
            probed = None
        if probed is not None:
            return probed
    return is_vision_model(model_name)


# 1x1 red PNG — the cheapest possible image probe payload.
_PROBE_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_VISION_PROBE_TTL = 3600.0
# (host, model) -> (bool | None, expiry); None = undecidable (auth/network).
_vision_probe_cache: dict = {}


def _vision_probe_enabled() -> bool:
    return os.getenv("ODYSSEUS_VISION_PROBE", "1").strip().lower() not in ("0", "false", "off", "no")


def _chat_completions_url(endpoint_url: str) -> str:
    base = (endpoint_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def probe_endpoint_supports_vision(
    endpoint_url: str,
    model_name: str,
    headers: Optional[dict] = None,
) -> Optional[bool]:
    """One-time (cached) probe: does a local OpenAI-compatible endpoint accept
    an image_url content part for `model_name`?

    Returns True on a 200, False when the endpoint rejects the image (400/413/
    415/422), and None when we cannot decide (remote host, auth required,
    unreachable, or probing disabled) so the caller falls back to name rules.
    """
    if not endpoint_url or not model_name or not _vision_probe_enabled():
        return None
    parsed = urlparse(endpoint_url)
    if not _is_safe_local_host(parsed.hostname):
        return None  # never probe a public provider or disallowed host
    key = ((parsed.hostname or "").lower(), (parsed.port or 0), model_name.strip().lower())
    now = time.time()
    cached = _vision_probe_cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    payload = {
        "model": model_name,
        "max_tokens": 1,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Reply with one word."},
                {"type": "image_url", "image_url": {"url": _PROBE_IMAGE_DATA_URL}},
            ],
        }],
    }
    verdict: Optional[bool] = None
    try:
        r = _safe_probe_post(
            _chat_completions_url(endpoint_url),
            headers=headers,
            json=payload,
            timeout=8.0,
        )
        if r.status_code == 200:
            verdict = True
        elif r.status_code in (400, 413, 415, 422):
            body = (r.text or "").lower()
            if any(tok in body for tok in ("image", "vision", "multimodal", "content type")):
                verdict = False
            else:
                verdict = False
    except Exception:
        verdict = None
    _vision_probe_cache[key] = (verdict, now + _VISION_PROBE_TTL)
    if verdict is not None:
        logger.info("vision probe %s@%s -> %s", model_name, parsed.hostname, verdict)
    return verdict


def validate_message(message: str) -> str:
    """Validate message input."""
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    message = message.strip()
    if len(message) == 0:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if len(message) > 50000:
        raise HTTPException(status_code=400, detail="Message exceeds maximum length")

    return message


def validate_file_upload(file: UploadFile) -> UploadFile:
    """Validate uploaded file meets requirements."""
    if not file or not file.filename:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_FILE",
                "message": "No file uploaded or invalid filename"
            }
        )

    try:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        if file_size == 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "EMPTY_FILE",
                    "message": "File is empty"
                }
            )

        upload_limit = get_chat_upload_max_bytes()
        if file_size > upload_limit:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "FILE_TOO_LARGE",
                    "message": f"File size exceeds {format_byte_limit(upload_limit)} limit"
                }
            )
    except IOError as e:
        logger.error(f"Error reading file size for {file.filename}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "FILE_READ_ERROR",
                "message": "Error reading uploaded file"
            }
        )

    allowed_extensions = {'.txt', '.py', '.html', '.md', '.json', '.csv', '.js',
                         '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.pdf',
                         '.webm', '.wav', '.mp3', '.m4a', '.ogg'}

    _, ext = os.path.splitext(file.filename.lower())

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UNSUPPORTED_FILE_TYPE",
                "message": f"File type '{ext}' not allowed",
                "allowed_types": sorted(allowed_extensions)
            }
        )

    return file


def coerce_message_and_session(req_json: dict | None, message: str | None,
                               session: str | None, session_manager,
                               allow_empty: bool = False):
    """Extract message and session from request, with validation.

    If allow_empty=True (e.g. attachment-only sends), the message-required
    check is skipped and an empty/whitespace message is normalized to "".
    """
    try:
        if message is None or session is None:
            if req_json is None:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "MISSING_PARAMETERS",
                        "message": "Missing 'message' and/or 'session' in request"
                    }
                )
            message = message or req_json.get("message")
            session = session or req_json.get("session")

        if allow_empty and (message is None or not str(message).strip()):
            message = ""
        else:
            message = validate_message(message)

        if not session:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "VALIDATION_ERROR",
                    "message": "Session ID is required"
                }
            )
        try:
            session_manager.get_session(session)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "SESSION_NOT_FOUND",
                    "message": f"Session '{session}' not found"
                }
            )

        return message, session
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_JSON",
                "message": "Invalid JSON in request body"
            }
        )
    except Exception as e:
        logger.error(f"Unexpected error in coerce_message_and_session: {e}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "REQUEST_PROCESSING_ERROR",
                "message": "Error processing request"
            }
        )
