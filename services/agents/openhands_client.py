"""Typed OpenHands boundary. All raw HTTP for the platform stays here."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


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
        if not conversation_id:
            settings = self.transport.request("GET", "/api/settings")
            llm = ((settings.get("agent_settings") or {}).get("llm") or {})
            if isinstance(llm, dict):
                resolved_base_url = str(llm.get("base_url") or "").strip() or None
                resolved_model = str(llm.get("model") or "").strip() or None
            body: dict[str, Any] = {
                "workspace": {
                    "working_dir": "/workspace",
                    "kind": "LocalWorkspace",
                },
                "agent_settings": settings.get("agent_settings") or {},
                **({"initial_message": user_message} if message else {}),
                "autotitle": False,
            }
            if agent_profile_id == "opencode":
                body["agent_profile_id"] = os.environ.get(
                    "OPENHANDS_OPENCODE_PROFILE_ID",
                    "opencode",
                )
            created = self.transport.request("POST", "/api/conversations", body)
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
