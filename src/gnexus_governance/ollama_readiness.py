"""Local Ollama model readiness helpers for Juniperus / Gnexus Operations Console.

Local-first. Detects a local Ollama daemon, classifies model capability,
and produces a single-endpoint registry. No cloud calls. No secrets stored.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


OLLAMA_HTTP_PRIMARY = "http://127.0.0.1:11434"
OLLAMA_HTTP_FALLBACK = "http://localhost:11434"
ENDPOINT_NAME = "Local Ollama (All Models)"
ENDPOINT_BASE_URL = "http://127.0.0.1:11434/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    return repo_root() / "data" / "gnexus" / "ollama"


def registry_path() -> Path:
    return data_root() / "ollama-model-registry.json"


def smoke_path() -> Path:
    return data_root() / "ollama-smoke-test.json"


def _http_tags(base: str, timeout: float = 2.5) -> Optional[Dict[str, Any]]:
    url = base.rstrip("/") + "/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local only)
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
    except Exception:
        return None


def _cli_tags(timeout: float = 6.0) -> Optional[List[Dict[str, Any]]]:
    """Fallback: parse `ollama list` text output when HTTP fails."""
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    models: List[Dict[str, Any]] = []
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    for ln in lines[1:]:  # skip header
        parts = ln.split()
        if not parts:
            continue
        models.append({"name": parts[0]})
    return models


def detect_ollama() -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Return (running, source, raw_models). Tries 127.0.0.1, localhost, then CLI."""
    data = _http_tags(OLLAMA_HTTP_PRIMARY)
    if data is not None:
        return True, "http-127", data.get("models", []) or []
    data = _http_tags(OLLAMA_HTTP_FALLBACK)
    if data is not None:
        return True, "http-localhost", data.get("models", []) or []
    cli = _cli_tags()
    if cli is not None:
        return True, "cli", cli
    return False, "none", []


_CODING_HINTS = ("coder", "code", "deepseek-coder", "starcoder", "codellama", "qwen2.5-coder", "codegemma")
_REASONING_HINTS = ("r1", "reason", "think", "qwq", "deepseek-r1", "o1")
_FAST_HINTS = ("mini", "small", "1b", "1.5b", "2b", "3b", "phi", "gemma2:2b", "qwen2.5:0.5b", "tinyllama")
_LONG_CTX_HINTS = ("128k", "1m", "long", "yarn", "qwen2.5", "llama3.1", "llama3.2", "mistral-nemo")
_TOOL_YES_HINTS = ("llama3.1", "llama3.2", "qwen2.5", "mistral", "command-r", "firefunction", "hermes")


def classify_capabilities(name: str) -> Dict[str, Any]:
    n = (name or "").lower()
    caps: List[str] = []
    if any(h in n for h in _FAST_HINTS):
        caps.append("fast chat candidate")
    if any(h in n for h in _CODING_HINTS):
        caps.append("coding candidate")
    if any(h in n for h in _REASONING_HINTS):
        caps.append("reasoning candidate")
    if any(h in n for h in _LONG_CTX_HINTS):
        caps.append("long-context candidate")
    if not caps:
        caps.append("general chat candidate")

    if any(h in n for h in _TOOL_YES_HINTS):
        tool_capable: Optional[bool] = True
    else:
        tool_capable = None  # unknown
    return {"capabilities": caps, "tool_capable": tool_capable}


def _family_of(raw: Dict[str, Any], name: str) -> str:
    details = raw.get("details") or {}
    fam = details.get("family") or details.get("families")
    if isinstance(fam, list) and fam:
        return str(fam[0])
    if fam:
        return str(fam)
    return (name.split(":")[0] if name else "?")


def _param_size(raw: Dict[str, Any]) -> str:
    details = raw.get("details") or {}
    return str(details.get("parameter_size") or "?")


def build_registry() -> Dict[str, Any]:
    running, source, raw_models = detect_ollama()
    models: List[Dict[str, Any]] = []
    names: List[str] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name") or raw.get("model") or ""
        if not name:
            continue
        names.append(name)
        cap = classify_capabilities(name)
        models.append(
            {
                "name": name,
                "family": _family_of(raw, name),
                "parameter_size": _param_size(raw),
                "size": raw.get("size"),
                "capabilities": cap["capabilities"],
                "tool_capable": cap["tool_capable"],
                "fallback": False,
            }
        )

    # Pick a fallback model: smallest / fast candidate first, else first model.
    fallback_name = None
    fast = [m for m in models if "fast chat candidate" in m["capabilities"]]
    if fast:
        fallback_name = fast[0]["name"]
    elif models:
        fallback_name = models[0]["name"]
    for m in models:
        if m["name"] == fallback_name:
            m["fallback"] = True

    return {
        "schema": "gnexus.ollama.registry.v1",
        "system": "Juniperus",
        "title": "Gnexus Operations Console - Local Ollama Model Readiness",
        "generatedAt": _utc_now(),
        "ollama": {
            "running": running,
            "source": source,
            "primaryUrl": OLLAMA_HTTP_PRIMARY,
            "fallbackUrl": OLLAMA_HTTP_FALLBACK,
        },
        "endpoint": {
            "name": ENDPOINT_NAME,
            "base_url": ENDPOINT_BASE_URL,
            "is_enabled": True,
            "model_type": "llm",
            "cached_models": names,
            "registered_in_picker": None,  # filled by importer / state route
        },
        "modelCount": len(models),
        "fallbackModel": fallback_name,
        "models": models,
    }


def load_registry() -> Optional[Dict[str, Any]]:
    p = registry_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def save_registry(reg: Dict[str, Any]) -> Path:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_smoke() -> Optional[Dict[str, Any]]:
    p = smoke_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def run_smoke_test(model: Optional[str] = None, timeout: float = 60.0) -> Dict[str, Any]:
    """Send a tiny 'Say OK' request to the LOCAL Ollama API only."""
    import time

    reg = build_registry()
    if not reg["ollama"]["running"]:
        result = {
            "ok": False,
            "ranAt": _utc_now(),
            "model": model,
            "error": "ollama_not_running",
            "detail": "Local Ollama daemon not reachable on 127.0.0.1:11434 or localhost.",
        }
        _save_smoke(result)
        return result

    chosen = model or reg.get("fallbackModel") or (reg["models"][0]["name"] if reg["models"] else None)
    if not chosen:
        result = {
            "ok": False,
            "ranAt": _utc_now(),
            "model": None,
            "error": "no_models",
            "detail": "Ollama is running but no models are available to test.",
        }
        _save_smoke(result)
        return result

    payload = json.dumps(
        {
            "model": chosen,
            "prompt": "Say OK",
            "stream": False,
            "options": {"num_predict": 8},
        }
    ).encode("utf-8")
    url = OLLAMA_HTTP_PRIMARY + "/api/generate"
    start = time.time()
    try:
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local only)
            body = json.loads(resp.read().decode("utf-8", "replace"))
        latency_ms = int((time.time() - start) * 1000)
        text = (body.get("response") or "").strip()
        result = {
            "ok": True,
            "ranAt": _utc_now(),
            "model": chosen,
            "latencyMs": latency_ms,
            "responsePreview": text[:200],
            "endpoint": OLLAMA_HTTP_PRIMARY + "/api/generate",
        }
    except Exception as exc:  # pragma: no cover - depends on local daemon
        latency_ms = int((time.time() - start) * 1000)
        result = {
            "ok": False,
            "ranAt": _utc_now(),
            "model": chosen,
            "latencyMs": latency_ms,
            "error": "request_failed",
            "detail": str(exc)[:300],
            "endpoint": OLLAMA_HTTP_PRIMARY + "/api/generate",
        }
    _save_smoke(result)
    return result


def _save_smoke(result: Dict[str, Any]) -> None:
    p = smoke_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
