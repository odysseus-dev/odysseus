"""Typed OpenHands boundary. All raw HTTP for the platform stays here.

Agents: GET /api/settings and POST /api/conversations emit ``openhands.settings``
and ``openhands.create``. Attributes go through ``apply_span_attributes``.
The sidecar key is a boolean ``odysseus.sidecar_restored`` only — never the key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def _native_llm_api_key() -> str:
    """Return the overlay 9router virtual key Agent Server stores for native chat.

    GET /api/settings redacts the secret. Without this file, POST /api/conversations
    copies ********** and 9router 401s (Processing request hangs).
    """
    path = os.environ.get(
        "OPENHANDS_NATIVE_LLM_API_KEY_FILE",
        "/opt/odysseus/openhands-native-llm-api-key",
    )
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _fallback_settings() -> dict[str, Any]:
    """Settings used when GET /api/settings fails so Native chat can still create.

    The sidecar file, not this dict, supplies llm.api_key at create time.
    """
    return {
        "agent_settings": {
            "schema_version": 5,
            "agent_kind": "openhands",
            "agent": "CodeActAgent",
            "llm": {
                "model": (
                    os.environ.get("OPENHANDS_NATIVE_MODEL") or "openai/cx/gpt-5.5"
                ).strip()
                or "openai/cx/gpt-5.5",
                "base_url": os.environ.get(
                    "OPENHANDS_NATIVE_BASE_URL",
                    "http://9router:20128/v1",
                ).strip()
                or "http://9router:20128/v1",
                "auth_type": "api_key",
                "api_mode": "chat",
            },
        }
    }


def _http_status(exc: OpenHandsFailure) -> int:
    """Parse ``http <code>`` from an Agent Server failure. 0 means no HTTP status."""
    parts = (exc.message or "").split()
    if len(parts) >= 2 and parts[0] == "http":
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def _sidecar_restored(settings: dict[str, Any]) -> bool:
    """True when create will attach the sidecar key. The key itself is not returned."""
    agent = settings.get("agent_settings") or {}
    llm = agent.get("llm") if isinstance(agent, dict) else None
    if not isinstance(llm, dict) or not llm:
        return False
    return bool(_native_llm_api_key())


def _agent_settings_for_create(settings: dict[str, Any]) -> dict[str, Any]:
    """Copy Agent Server settings for create, restoring a redacted llm.api_key."""
    agent = dict(settings.get("agent_settings") or {})
    llm = dict(agent.get("llm") or {})
    if llm:
        # Never copy GET /api/settings llm.api_key (it is ********** live).
        llm.pop("api_key", None)
        restored = _native_llm_api_key()
        if restored:
            llm["api_key"] = restored
        agent["llm"] = llm
    return agent


class OpenHandsFailure(Exception):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.message = message


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


class UrllibTransport:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise OpenHandsFailure("agent-server", f"http {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OpenHandsFailure("agent-server", str(exc.reason)) from exc


@dataclass(frozen=True)
class ConversationResume:
    conversation_id: str
    execution_id: str
    # Non-secret route only. Never copy llm.api_key onto this record.
    resolved_base_url: str | None = None
    resolved_model: str | None = None
    resolved_runtime: str | None = None


class OpenHandsClient:
    def __init__(
        self,
        *,
        transport: Transport | None = None,
        agent_server_base: str = "http://127.0.0.1:8000",
    ) -> None:
        self.agent_server_base = agent_server_base
        self.transport = transport or UrllibTransport(agent_server_base)

    @classmethod
    def from_env(cls) -> OpenHandsClient:
        return cls(
            agent_server_base=os.environ.get(
                "ODYSSEUS_OPENHANDS_AGENT_SERVER_URL",
                "http://openhands-agent-server:8000",
            )
        )

    def create_or_resume(
        self,
        *,
        conversation_id: str | None,
        message: str,
        request_id: str,
        profile_revision: int,
        archetype_version: int,
        workspace_grants: tuple[str, ...] = (),
        credential_delivery_mode: str = "broker",
        agent_profile_id: str = "odysseus",
    ) -> ConversationResume:
        # Agent Server 1.45 StartConversationRequest requires workspace.
        # request_id / grants / broker mode are Odysseus-only and 422 upstream.
        _ = (request_id, profile_revision, archetype_version, workspace_grants, credential_delivery_mode)
        user_message = {
            "role": "user",
            "content": [{"type": "text", "text": message}],
        }
        resolved_base_url = None
        resolved_model = None
        resolved_runtime = None
        if conversation_id:
            try:
                info = self.transport.request(
                    "GET", f"/api/conversations/{conversation_id}"
                )
            except OpenHandsFailure:
                info = {}
            status = str(
                (info or {}).get("execution_status")
                or (info or {}).get("status")
                or ""
            ).lower()
            if status in {"error", "failed", "errored"}:
                # Dead conversation (Codex 400 / missing key) cannot be resumed.
                conversation_id = None
        if not conversation_id:
            # Span covers the settings read only. The response api_key is redacted
            # upstream and must not be copied onto the span.
            from services.observability.otel import (
                apply_span_attributes,
                get_tracer,
                record_span_error,
            )

            tracer = get_tracer("odysseus")
            with tracer.start_as_current_span("openhands.settings") as span:
                try:
                    settings = self.transport.request("GET", "/api/settings")
                    settings_status = 200
                except OpenHandsFailure as exc:
                    # Overlay: Odysseus /app/data chown can make settings.json
                    # unreadable to uid 10001. Still create with 9router + sidecar.
                    settings_status = _http_status(exc)
                    settings = _fallback_settings()
                    record_span_error(span, exc)
                apply_span_attributes(
                    span,
                    {
                        "http.status_code": settings_status,
                        "odysseus.sidecar_restored": _sidecar_restored(settings),
                    },
                )
            if agent_profile_id == "opencode":
                # OpenCode provenance is overlay config, not Agent Server LLM.
                resolved_runtime = "opencode"
                resolved_base_url = os.environ.get(
                    "OPENHANDS_OPENCODE_BASE_URL",
                    "http://9router:20128/v1",
                ).strip() or "http://9router:20128/v1"
                resolved_model = (
                    os.environ.get("OPENHANDS_OPENCODE_MODEL") or "ninerouter/auto"
                ).strip() or "ninerouter/auto"
            else:
                llm = ((settings.get("agent_settings") or {}).get("llm") or {})
                if isinstance(llm, dict):
                    resolved_base_url = str(llm.get("base_url") or "").strip() or None
                    resolved_model = str(llm.get("model") or "").strip() or None
            body: dict[str, Any] = {
                "workspace": {
                    "working_dir": "/workspace",
                    "kind": "LocalWorkspace",
                },
                "agent_settings": _agent_settings_for_create(settings),
                **({"initial_message": user_message} if message else {}),
                "autotitle": False,
            }
            if agent_profile_id == "opencode":
                body["agent_profile_id"] = os.environ.get(
                    "OPENHANDS_OPENCODE_PROFILE_ID",
                    "opencode",
                )
            # Create span records model and HTTP status. The body carries the
            # sidecar key and must not be attached as an attribute.
            with tracer.start_as_current_span("openhands.create") as span:
                try:
                    created = self.transport.request("POST", "/api/conversations", body)
                    apply_span_attributes(
                        span,
                        {
                            "http.status_code": 200,
                            "gen_ai.request.model": resolved_model or "",
                        },
                    )
                except OpenHandsFailure as exc:
                    apply_span_attributes(
                        span,
                        {
                            "http.status_code": _http_status(exc),
                            "gen_ai.request.model": resolved_model or "",
                        },
                    )
                    record_span_error(span, exc)
                    raise
            conversation_id = str(created.get("id") or created.get("conversation_id"))
        elif message:
            self.transport.request(
                "POST",
                f"/api/conversations/{conversation_id}/events",
                {**user_message, "run": True},
            )
        return ConversationResume(
            conversation_id=conversation_id,
            execution_id=f"agent-server:{conversation_id}",
            resolved_base_url=resolved_base_url,
            resolved_model=resolved_model,
            resolved_runtime=resolved_runtime,
        )

    def cancel_execution(self, execution_id: str) -> dict[str, Any]:
        conversation_id = execution_id.removeprefix("agent-server:")
        return self.transport.request(
            "POST", f"/api/conversations/{conversation_id}/interrupt", {}
        )

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        conversation_id = execution_id.removeprefix("agent-server:")
        return self.transport.request("GET", f"/api/conversations/{conversation_id}")

    def conversation_events(self, conversation_id: str) -> list[dict[str, Any]]:
        payload = self.transport.request(
            "GET", f"/api/conversations/{conversation_id}/events/search"
        )
        items = payload.get("items") or payload.get("events") or []
        return list(items)

    def respond_to_confirmation(
        self,
        conversation_id: str,
        *,
        accept: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        return self.transport.request(
            "POST",
            f"/api/conversations/{conversation_id}/events/respond_to_confirmation",
            {"accept": accept, "reason": reason},
        )
