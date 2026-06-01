"""Experimental Codex CLI model-provider capability boundary.

This module does not implement chat dispatch. It reports whether a future
Codex-backed provider can be exposed safely, without treating completed CLI
output as token streaming or reading Codex credential files.
"""

from __future__ import annotations

import asyncio
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
CODEX_PROVIDER_ENDPOINT_ID = "codex-cli"
CODEX_PROVIDER_ENDPOINT_NAME = "Codex / ChatGPT"
CODEX_EXPERIMENTAL_MODEL_ID = "codex-cli/chatgpt-experimental"
CODEX_EXPERIMENTAL_MODEL_DISPLAY = "Codex CLI / ChatGPT (experimental, non-streaming)"
CODEX_CHAT_TIMEOUT_SECONDS = 120
CODEX_MODELS_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
CODEX_FALLBACK_MODELS = [
    {
        "id": CODEX_EXPERIMENTAL_MODEL_ID,
        "display": CODEX_EXPERIMENTAL_MODEL_DISPLAY,
        "experimental": True,
    }
]

_TOKEN_PATTERNS = (
    re.compile(r"(?i)(access_token|refresh_token|id_token)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)

_LIMITATIONS = [
    "Non-streaming: returns one completed assistant message.",
    "Stateless: session/resume is not implemented.",
    "In Odysseus agent mode, tools are executed by Odysseus agent rounds, not by Codex CLI itself.",
    "The adapter requires Codex CLI sandbox/approval flags before running.",
]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def codex_model_provider_enabled() -> bool:
    return _truthy(os.getenv(CODEX_MODEL_PROVIDER_FLAG, "false"))


def _sanitize_text(text: str | None, limit: int = 2000) -> str:
    safe = text or ""
    for pattern in _TOKEN_PATTERNS:
        safe = pattern.sub("<redacted-token>", safe)
    return safe.strip()[:limit]


def normalize_codex_model_id(model: str | None) -> str:
    """Return the Codex CLI model slug to pass with --model.

    Older Odysseus builds exposed a synthetic codex-cli/chatgpt-experimental id.
    Modern Codex CLI stores real selectable slugs in ~/.codex/models_cache.json.
    """
    raw = (model or "").strip()
    if not raw or raw == CODEX_EXPERIMENTAL_MODEL_ID:
        models = codex_available_models()
        first = models[0]["id"] if models else ""
        return "" if first == CODEX_EXPERIMENTAL_MODEL_ID else first
    if raw.startswith("codex-cli/"):
        slug = raw.split("/", 1)[1]
        return "" if slug == "chatgpt-experimental" else slug
    return raw


def codex_available_models(cache_path: Path | None = None) -> list[dict[str, Any]]:
    """Read the model list that Codex CLI/VS Code cache after ChatGPT OAuth.

    We intentionally only expose list-visible, API-supported entries and never
    return the bulky instruction payloads from the cache.
    """
    path = cache_path or CODEX_MODELS_CACHE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [dict(m) for m in CODEX_FALLBACK_MODELS]

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
            "streaming_supported": False,
            "session_resume_supported": False,
        })
    items.sort(key=lambda m: (m.get("priority", 9999), str(m.get("display") or m.get("id"))))
    return items or [dict(m) for m in CODEX_FALLBACK_MODELS]


class CodexCliChatAdapter:
    """Admin-only, non-streaming adapter boundary for `codex exec`.

    This intentionally does not provide a normal chat-provider hook yet. It
    refuses to run unless the installed CLI advertises the sandbox and approval
    flags needed for a constrained one-shot completion probe.
    """

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
            "supports_json": help_result.get("supports_json", False),
            "limitations": list(_LIMITATIONS),
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
        allow_odysseus_tools: bool = False,
    ) -> dict[str, Any]:
        started = time.time()
        availability = await self.available()
        if not availability.get("ok"):
            return {
                **availability,
                "ok": False,
                "duration_ms": round((time.time() - started) * 1000),
                "model": model or CODEX_EXPERIMENTAL_MODEL_ID,
            }

        preflight = await self._preflight()
        prompt = self._build_prompt(messages, allow_odysseus_tools=allow_odysseus_tools)
        timeout = max(1, min(int(timeout_seconds or CODEX_CHAT_TIMEOUT_SECONDS), 300))

        with tempfile.TemporaryDirectory(prefix="odysseus-codex-chat-") as workdir:
            model_slug = normalize_codex_model_id(model)
            sandbox_mode = "workspace-write" if allow_odysseus_tools else "read-only"
            args = [
                preflight["bin_path"],
                "exec",
                "-c",
                "approval_policy=\"never\"",
                "--sandbox",
                sandbox_mode,
                "--skip-git-repo-check",
                "--ignore-rules",
                "--ephemeral",
            ]
            if model_slug:
                args.extend(["--model", model_slug])
            args.append(prompt)
            rc, out, err = await self._run(
                args,
                timeout=timeout,
                cwd=workdir,
                env=preflight["env"],
            )

        duration_ms = round((time.time() - started) * 1000)
        if rc == 124:
            return self._error("timeout", "Codex CLI timed out", duration_ms, model)
        if rc != 0:
            detail = _sanitize_text(err or out, limit=500)
            return self._error("cli_failed", detail or "Codex CLI failed", duration_ms, model)

        message = self._extract_message(out)
        if not message:
            return self._error("empty_response", "Codex CLI returned no assistant message", duration_ms, model)

        return {
            "ok": True,
            "status": "ok",
            "message": message,
            "duration_ms": duration_ms,
            "model": model or CODEX_EXPERIMENTAL_MODEL_ID,
            "limitations": list(_LIMITATIONS),
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": bool(allow_odysseus_tools),
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

        bin_path = ""
        try:
            bin_path = service._bin_path()  # Existing auth service owns CLI resolution.
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
                "status": "unsupported_unsafe_cli_mode",
                "error": "Unable to inspect codex exec safety flags",
                "detail": _sanitize_text(err or out, limit=500),
            }
        help_text = out or ""
        missing = [flag for flag in ("--sandbox",) if flag not in help_text]
        if missing:
            return {
                "ok": False,
                "status": "unsupported_unsafe_cli_mode",
                "error": "Codex CLI does not advertise required safety flags",
                "missing_flags": missing,
            }
        # Codex CLI 0.135+ no longer exposes the old --ask-for-approval flag.
        # Approval can still be pinned safely for exec runs through config:
        #   -c approval_policy="never"
        # Keep accepting older CLIs that advertise --ask-for-approval too, but
        # do not block modern CLIs just because that legacy flag disappeared.
        return {
            "ok": True,
            "status": "exec_help_ok",
            "supports_json": "--json" in help_text,
            "supports_model": "--model" in help_text or " -m" in help_text,
            "supports_approval_config": True,
            "supports_legacy_ask_for_approval": "--ask-for-approval" in help_text,
        }

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
    def _build_prompt(messages: list[dict[str, Any]], allow_odysseus_tools: bool = False) -> str:
        parts = ["You are replying through Odysseus' Codex CLI provider."]
        if allow_odysseus_tools:
            parts.extend([
                "You are inside Odysseus agent mode. Odysseus, not Codex CLI, executes tools between rounds.",
                "You are NOT a read-only agent: the local Codex CLI wrapper is constrained, but Odysseus agent tools are allowed to modify the workspace when the user asks.",
                "You may create, edit, overwrite, and delete files by requesting Odysseus tool blocks such as ```write_file, ```bash, or ```python.",
                "Use Odysseus fenced tool blocks for shell commands, file edits, browsing, and other tool work; Odysseus will execute them between rounds.",
                "When you need a tool, output the exact fenced tool block requested by the system prompt (for example ```read_file or ```write_file).",
                "After Odysseus executes that block, you will receive the result in a later round and can continue.",
                "Return only assistant text plus any required Odysseus fenced tool blocks; do not explain this wrapper.",
            ])
        else:
            parts.extend([
                "Return only the final assistant response.",
                "Do not run tools, shell commands, file edits, or web requests.",
                "If a request requires tools, say that this experimental provider does not support tools yet.",
            ])
        parts.extend(["", "Conversation:"])
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user").strip() or "user"
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
            parts.append(f"{role}: {content}")
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_message(output: str) -> str:
        text = _sanitize_text(output)
        if not text:
            return ""
        # If a future CLI emits JSONL, prefer common completed-message fields.
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
            "error": _sanitize_text(error, limit=500),
            "duration_ms": duration_ms,
            "model": model or CODEX_EXPERIMENTAL_MODEL_ID,
            "limitations": list(_LIMITATIONS),
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
        }


class CodexModelProvider:
    """Status/capability adapter for a future Codex CLI provider."""

    def __init__(
        self,
        auth_service_getter: Callable[[], Any] | None = None,
        chat_adapter: CodexCliChatAdapter | None = None,
    ) -> None:
        self._auth_service_getter = auth_service_getter or get_codex_auth_service
        self._chat_adapter = chat_adapter or CodexCliChatAdapter(self._auth_service_getter)

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
        }
        if not enabled:
            return {
                **base,
                "status": "disabled",
                "cli_available": False,
                "authenticated": False,
                "requires_sign_in": False,
            }

        try:
            auth = await self._auth_service_getter().status()
        except Exception as exc:
            return {
                **base,
                "status": "auth_status_failed",
                "cli_available": False,
                "authenticated": False,
                "requires_sign_in": False,
                "error": exc.__class__.__name__,
            }

        cli_available = bool(
            auth.get("codex_cli_available")
            or (auth.get("cli_found") and auth.get("cli_executable"))
        )
        authenticated = bool(auth.get("codex_authenticated") or auth.get("authenticated"))
        auth_status = str(auth.get("status") or "")
        auth_mode = str(auth.get("auth_mode") or "")

        if not cli_available:
            status = "cli_unavailable"
            requires_sign_in = False
        elif not authenticated:
            status = "sign_in_required"
            requires_sign_in = True
        else:
            status = "available"
            requires_sign_in = False

        models = []
        chat_available = {"ok": False}
        if status == "available":
            chat_available = await self._chat_adapter.available()
            if not chat_available.get("ok"):
                status = chat_available.get("status") or "unsupported_unsafe_cli_mode"
            else:
                base["chat_supported"] = True
                models.extend(codex_available_models())

        return {
            **base,
            "status": status,
            "cli_available": cli_available,
            "authenticated": authenticated,
            "requires_sign_in": requires_sign_in,
            "sign_in_route": "/api/codex-auth/start",
            "auth": {
                "status": auth_status,
                "auth_mode": auth_mode,
                "codex_home": auth.get("codex_home", ""),
            },
            "models": models,
        }

    async def test_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
        allow_odysseus_tools: bool = False,
    ) -> dict[str, Any]:
        return await self._chat_adapter.complete(
            messages,
            model=model,
            timeout_seconds=timeout_seconds,
            allow_odysseus_tools=allow_odysseus_tools,
        )


def is_codex_provider_selection(endpoint_url: str | None, model: str | None = None) -> bool:
    """Return True when a chat/session selection targets the Codex CLI provider."""
    endpoint = (endpoint_url or "").strip().rstrip("/")
    model_id = (model or "").strip()
    return (
        endpoint == CODEX_PROVIDER_ENDPOINT_URL
        or endpoint.startswith(CODEX_PROVIDER_ENDPOINT_URL + "/")
        or model_id == CODEX_EXPERIMENTAL_MODEL_ID
    )


async def codex_model_picker_item(provider: CodexModelProvider | None = None) -> dict[str, Any] | None:
    """Build an /api/models item when Codex OAuth is ready.

    The Codex provider is virtual: it is backed by the local Codex CLI/OAuth
    state, not a ModelEndpoint DB row and not an OpenAI-compatible HTTP URL.
    """
    provider = provider or CodexModelProvider()
    status = await provider.status()
    models = [m.get("id") for m in (status.get("models") or []) if m.get("id")]
    if status.get("status") != "available" or not models or not status.get("chat_supported"):
        return None
    display_by_id = {m.get("id"): m.get("display") for m in status.get("models") or []}
    return {
        "host": "codex",
        "port": 0,
        "url": CODEX_PROVIDER_ENDPOINT_URL,
        "models": models,
        "models_display": [display_by_id.get(m) or m.split("/")[-1] for m in models],
        "models_extra": [],
        "models_extra_display": [],
        "endpoint_id": CODEX_PROVIDER_ENDPOINT_ID,
        "endpoint_name": CODEX_PROVIDER_ENDPOINT_NAME,
        "category": "cloud",
        "model_type": "llm",
        "experimental": True,
        "streaming_supported": False,
        "session_resume_supported": False,
        "tool_execution_allowed": False,
    }


async def codex_complete_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    provider: CodexModelProvider | None = None,
    timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
    allow_odysseus_tools: bool = False,
) -> dict[str, Any]:
    provider = provider or CodexModelProvider()
    return await provider.test_chat(
        messages,
        model=model or CODEX_EXPERIMENTAL_MODEL_ID,
        timeout_seconds=timeout_seconds,
        allow_odysseus_tools=allow_odysseus_tools,
    )


async def stream_codex_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    provider: CodexModelProvider | None = None,
    timeout_seconds: int = CODEX_CHAT_TIMEOUT_SECONDS,
    allow_odysseus_tools: bool = False,
):
    """SSE wrapper for the non-streaming Codex CLI adapter."""
    result = await codex_complete_chat(
        messages,
        model=model,
        provider=provider,
        timeout_seconds=timeout_seconds,
        allow_odysseus_tools=allow_odysseus_tools,
    )
    if not result.get("ok"):
        payload = {
            "type": "error",
            "error": result.get("error") or result.get("status") or "Codex CLI provider failed",
            "status": result.get("status"),
            "model": result.get("model") or model or CODEX_EXPERIMENTAL_MODEL_ID,
        }
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
        return
    message = result.get("message") or ""
    if message:
        yield f"data: {json.dumps({'delta': message})}\n\n"
    metrics = {
        "model": result.get("model") or model or CODEX_EXPERIMENTAL_MODEL_ID,
        "response_time": round((result.get("duration_ms") or 0) / 1000, 2),
        "usage_source": "codex-cli",
        "streaming_supported": False,
    }
    yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"
    yield "data: [DONE]\n\n"
