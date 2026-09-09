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
    ) -> ConversationResume:
        if not conversation_id:
            created = self.transport.request(
                "POST",
                "/api/conversations",
                {
                    "request_id": request_id,
                    "profile_revision": profile_revision,
                    "archetype_version": archetype_version,
                    "workspace_grants": list(workspace_grants),
                    "credential_delivery_mode": credential_delivery_mode,
                },
            )
            conversation_id = str(created.get("id") or created.get("conversation_id"))
        self.transport.request(
            "POST",
            f"/api/conversations/{conversation_id}/events",
            {
                "message": message,
                "request_id": request_id,
                "profile_revision": profile_revision,
                "archetype_version": archetype_version,
                "workspace_grants": list(workspace_grants),
                "credential_delivery_mode": credential_delivery_mode,
            },
        )
        return ConversationResume(
            conversation_id=conversation_id,
            execution_id=f"agent-server:{conversation_id}",
        )

    def cancel_execution(self, execution_id: str) -> dict[str, Any]:
        conversation_id = execution_id.removeprefix("agent-server:")
        return self.transport.request("POST", f"/api/conversations/{conversation_id}/stop", {})

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        conversation_id = execution_id.removeprefix("agent-server:")
        return self.transport.request("GET", f"/api/conversations/{conversation_id}")

    def conversation_events(self, conversation_id: str) -> list[dict[str, Any]]:
        payload = self.transport.request("GET", f"/api/conversations/{conversation_id}/events")
        items = payload.get("items") or payload.get("events") or []
        return list(items)
