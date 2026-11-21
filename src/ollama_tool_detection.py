"""Local Ollama capability discovery for the automatic tool mode."""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

_CACHE: dict[str, tuple[float, bool | None]] = {}
_LOCK = threading.Lock()
_TTL = 300.0
_ERROR_TTL = 10.0


def native_tool_capability(endpoint_url: str, model: str) -> bool | None:
    """Return true/false only for verified loopback metadata; otherwise unknown."""
    try:
        parsed = urlparse(endpoint_url or '')
        path = parsed.path.rstrip('/')
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or parsed.port != 11434 or not (path == '/v1' or path.startswith('/v1/'))
                or parsed.username is not None or parsed.password is not None):
            return None
    except ValueError:
        return None
    model = str(model or '').strip()
    if not model or len(model) > 512 or any(ord(c) < 32 for c in model):
        return None
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(model)
        if cached and cached[0] > now:
            return cached[1]
    request = Request('http://127.0.0.1:11434/api/show',
                      data=json.dumps({'model': model}).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=1.0) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError('Ollama metadata exceeds limit')
        payload = json.loads(raw)
        caps = payload.get('capabilities') if isinstance(payload, dict) else None
        if not isinstance(caps, list):
            raise ValueError('Ollama metadata has no capability list')
        supported = any(isinstance(c, str) and c.casefold() in {'tool', 'tools'} for c in caps)
        ttl = _TTL
    except (OSError, ValueError, TypeError):
        supported = None
        ttl = _ERROR_TTL
    with _LOCK:
        if len(_CACHE) >= 256:
            _CACHE.clear()
        _CACHE[model] = (now + ttl, supported)
    return supported


def advertises_tools(endpoint_url: str, model: str) -> bool:
    """Use only verified native tool support; unknown stays conservative."""
    return native_tool_capability(endpoint_url, model) is True
