"""Experimental Codex CLI chat provider.

This provider deliberately uses the official Codex CLI as the credential
authority. Odysseus does not read or store Codex tokens; it only asks the CLI to
run a constrained one-shot chat completion after the admin has signed in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from src.codex_auth import get_codex_auth_service


CODEX_MODEL_PROVIDER_FLAG = "ODYSSEUS_CODEX_MODEL_PROVIDER_ENABLED"
CODEX_PROVIDER_ENDPOINT_URL = "codex-cli://chat"
CODEX_PROVIDER_ENDPOINT_NAME = "ChatGPT subscription (Codex)"
CODEX_DEFAULT_MODEL_ID = "gpt-5.5"
CODEX_CHAT_TIMEOUT_SECONDS = 180


def codex_models_cache_path() -> Path:
    configured = os.getenv("CODEX_HOME", "").strip()
    home = Path(os.path.expanduser(configured)) if configured else Path.home() / ".codex"
    return home / "models_cache.json"


CODEX_MODELS_CACHE_PATH = codex_models_cache_path()

_TOKEN_PATTERNS = (
    re.compile(r"(?i)(access_token|refresh_token|id_token)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)

_LIMITATIONS = [
    "Experimental: chat-only provider backed by codex exec.",
    "Non-streaming: Odysseus receives one completed assistant message.",
    "Stateless: each message starts a fresh Codex CLI execution.",
    "Agent tool/event mapping is intentionally not enabled in this slice.",
]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def codex_model_provider_enabled() -> bool:
    return _truthy(os.getenv(CODEX_MODEL_PROVIDER_FLAG, "false"))


def is_codex_provider_url(endpoint_url: str | None) -> bool:
    url = (endpoint_url or "").strip().rstrip("/")
    return url == CODEX_PROVIDER_ENDPOINT_URL or url.startswith(CODEX_PROVIDER_ENDPOINT_URL + "/")


def is_codex_provider_selection(endpoint_url: str | None, model: str | None = None) -> bool:
    return is_codex_provider_url(endpoint_url) or (model or "").strip().startswith("codex-cli/")


def codex_endpoint_id_for_owner(owner: str | None) -> str:
    raw = (owner or "local").strip().lower() or "local"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"codex-cli-{digest}"


def _sanitize_text(text: str | None, limit: int = 4000) -> str:
    safe = text or ""
    for pattern in _TOKEN_PATTERNS:
        safe = pattern.sub("<redacted-token>", safe)
    return safe.strip()[:limit]


def codex_available_models(cache_path: Path | None = None) -> list[dict[str, Any]]:
    """Return list-visible Codex CLI model slugs, falling back to gpt-5.5."""
    path = cache_path or codex_models_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [{"id": CODEX_DEFAULT_MODEL_ID, "display": "GPT-5.5", "experimental": True}]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("visibility") != "list" or entry.get("supported_in_api") is False:
            continue
        slug = str(entry.get("slug") or entry.get("id") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        items.append({
            "id": slug,
            "display": str(entry.get("display_name") or slug),
            "description": str(entry.get("description") or ""),
            "priority": entry.get("priority") if isinstance(entry.get("priority"), int) else 9999,
            "experimental": False,
        })
    items.sort(key=lambda m: (m.get("priority", 9999), str(m.get("display") or m.get("id"))))
    return items or [{"id": CODEX_DEFAULT_MODEL_ID, "display": "GPT-5.5", "experimental": True}]


def normalize_codex_model_id(model: str | None) -> str:
    raw = (model or "").strip()
    if raw.startswith("codex-cli/"):
        raw = raw.split("/", 1)[1]
    return raw or CODEX_DEFAULT_MODEL_ID


class CodexCliChatAdapter:
    """Small async adapter around `codex exec` for chat-only completions."""

    def __init__(
        self,
        auth_service_getter: Callable[[], Any] | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self._auth_service_getter = auth_service_getter or get_codex_auth_service
        self._runner = runner

    async def available(self) -> dict[str, Any]:
        preflight = await self._preflight()
        if not preflight.get("ok"):
            return preflight
        help_result = await self._exec_help(preflight["bin_path"], preflight["env"])
        if not help_result.get("ok"):
            return help_result
        return {
            "ok": True,
            "status": "available",
            "chat_supported": True,
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
            "limitations": list(_LIMITATIONS),
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        started = time.time()
        availability = await self.available()
        if not availability.get("ok"):
            return {
                **availability,
                "ok": False,
                "duration_ms": round((time.time() - started) * 1000),
                "model": normalize_codex_model_id(model),
            }

        preflight = await self._preflight()
        prompt = self._build_prompt(messages)
        timeout = max(1, min(int(timeout_seconds or CODEX_CHAT_TIMEOUT_SECONDS), 300))

        with tempfile.TemporaryDirectory(prefix="odysseus-codex-chat-") as workdir:
            args = [
                preflight["bin_path"],
                "exec",
                "-c",
                "approval_policy=\"never\"",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--ephemeral",
                "--model",
                normalize_codex_model_id(model),
                prompt,
            ]
            rc, out, err = await self._run(args, timeout=timeout, cwd=workdir, env=preflight["env"])

        duration_ms = round((time.time() - started) * 1000)
        if rc == 124:
            return self._error("timeout", "Codex CLI timed out", duration_ms, model)
        if rc != 0:
            detail = _sanitize_text(err or out, limit=700)
            return self._error("cli_failed", detail or "Codex CLI failed", duration_ms, model)

        message = self._extract_message(out)
        if not message:
            return self._error("empty_response", "Codex CLI returned no assistant message", duration_ms, model)

        return {
            "ok": True,
            "status": "ok",
            "message": message,
            "duration_ms": duration_ms,
            "model": normalize_codex_model_id(model),
            "limitations": list(_LIMITATIONS),
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
        }

    async def _preflight(self) -> dict[str, Any]:
        if not codex_model_provider_enabled():
            return {"ok": False, "status": "disabled", "error": "Codex model provider is disabled"}

        service = self._auth_service_getter()
        try:
            auth = await service.status()
        except Exception as exc:
            return {"ok": False, "status": "auth_status_failed", "error": exc.__class__.__name__}

        cli_available = bool(
            auth.get("codex_cli_available")
            or (auth.get("cli_found") and auth.get("cli_executable"))
        )
        if not cli_available:
            return {"ok": False, "status": "cli_unavailable", "error": "Codex CLI is unavailable"}

        authenticated = bool(auth.get("codex_authenticated") or auth.get("authenticated"))
        if not authenticated:
            return {"ok": False, "status": "sign_in_required", "error": "Sign in with Codex / ChatGPT first"}

        try:
            bin_path = service._bin_path()
        except Exception:
            bin_path = auth.get("resolved_binary_path") or ""
        if not bin_path:
            bin_path = auth.get("resolved_binary_path") or os.getenv("CODEX_BIN", "codex")

        try:
            env = service._env()
        except Exception:
            env = os.environ.copy()

        return {"ok": True, "status": "preflight_ok", "bin_path": bin_path, "env": env}

    async def _exec_help(self, bin_path: str, env: dict[str, str]) -> dict[str, Any]:
        rc, out, err = await self._run([bin_path, "exec", "--help"], timeout=20, env=env)
        if rc != 0:
            return {
                "ok": False,
                "status": "unsupported_cli",
                "error": "Unable to inspect codex exec safety flags",
                "detail": _sanitize_text(err or out, limit=700),
            }
        help_text = out or ""
        required = ("--sandbox", "--skip-git-repo-check", "--ephemeral", "--model")
        missing = [flag for flag in required if flag not in help_text]
        if missing:
            return {
                "ok": False,
                "status": "unsupported_cli",
                "error": "Codex CLI does not advertise required isolation flags",
                "missing_flags": missing,
            }
        return {"ok": True, "status": "exec_help_ok"}

    async def _run(
        self,
        args: list[str],
        *,
        timeout: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if self._runner:
            return await self._runner(args=args, timeout=timeout, cwd=cwd, env=env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return 124, "", "Command timed out"
            return (
                proc.returncode or 0,
                (out or b"").decode("utf-8", errors="replace"),
                (err or b"").decode("utf-8", errors="replace"),
            )
        except Exception as exc:
            return 1, "", f"Failed to start Codex CLI: {exc.__class__.__name__}"

    @staticmethod
    def _build_prompt(messages: list[dict[str, Any]]) -> str:
        parts = [
            "You are replying through Odysseus using a ChatGPT subscription via Codex CLI.",
            "Return only the final assistant response.",
            "Do not run tools, shell commands, file edits, or web requests.",
            "",
            "Conversation:",
        ]
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user").strip() or "user"
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(part.get("text", part)) if isinstance(part, dict) else str(part)
                    for part in content
                )
            parts.append(f"{role}: {content}")
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_message(output: str) -> str:
        text = _sanitize_text(output)
        if not text:
            return ""
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            for key in ("message", "content", "text", "output"):
                value = data.get(key) if isinstance(data, dict) else None
                if isinstance(value, str) and value.strip():
                    return _sanitize_text(value)
        return text

    @staticmethod
    def _error(status: str, error: str, duration_ms: int, model: str | None) -> dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "error": _sanitize_text(error, limit=700),
            "duration_ms": duration_ms,
            "model": normalize_codex_model_id(model),
            "limitations": list(_LIMITATIONS),
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
        }


class CodexModelProvider:
    def __init__(self, chat_adapter: CodexCliChatAdapter | None = None) -> None:
        self._chat_adapter = chat_adapter or CodexCliChatAdapter()

    async def status(self) -> dict[str, Any]:
        enabled = codex_model_provider_enabled()
        base = {
            "feature_enabled": enabled,
            "feature_flag": CODEX_MODEL_PROVIDER_FLAG,
            "provider": "codex_cli",
            "experimental": True,
            "models": [],
            "chat_supported": False,
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
            "limitations": list(_LIMITATIONS),
            "endpoint_name": CODEX_PROVIDER_ENDPOINT_NAME,
            "endpoint_url": CODEX_PROVIDER_ENDPOINT_URL,
            "default_model": CODEX_DEFAULT_MODEL_ID,
        }
        if not enabled:
            return {**base, "status": "disabled", "cli_available": False, "authenticated": False}

        availability = await self._chat_adapter.available()
        if not availability.get("ok"):
            status = availability.get("status") or "unavailable"
            return {
                **base,
                "status": status,
                "cli_available": status != "cli_unavailable",
                "authenticated": status not in {"sign_in_required", "cli_unavailable", "auth_status_failed"},
                "error": availability.get("error"),
            }

        return {
            **base,
            "status": "available",
            "cli_available": True,
            "authenticated": True,
            "models": codex_available_models(),
            "chat_supported": True,
        }

    async def test_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        return await self._chat_adapter.complete(messages, model=model, timeout_seconds=timeout_seconds)


async def codex_complete_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    provider: CodexModelProvider | None = None,
    timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    provider = provider or CodexModelProvider()
    return await provider.test_chat(messages, model=model, timeout_seconds=timeout_seconds)


async def stream_codex_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    provider: CodexModelProvider | None = None,
    timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
):
    result = await codex_complete_chat(messages, model=model, provider=provider, timeout_seconds=timeout_seconds)
    if not result.get("ok"):
        payload = {
            "type": "error",
            "error": result.get("error") or result.get("status") or "Codex CLI provider failed",
            "status": result.get("status"),
            "model": result.get("model") or normalize_codex_model_id(model),
        }
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
        return
    message = result.get("message") or ""
    if message:
        yield f"data: {json.dumps({'delta': message})}\n\n"
    metrics = {
        "model": result.get("model") or normalize_codex_model_id(model),
        "response_time": round((result.get("duration_ms") or 0) / 1000, 2),
        "usage_source": "codex_cli_subscription",
        "streaming_supported": False,
    }
    yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
    yield "data: [DONE]\n\n"
