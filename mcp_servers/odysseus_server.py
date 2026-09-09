"""Thin Streamable-HTTP MCP adapter over Odysseus domain services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from services.agents.approvals import ApprovalAuthority, ApprovalDenied, ConfirmedAction
from services.agents.delegation import DelegationAuthority, DelegationDenied, DelegationToken, WorkloadAuthenticationRequired
from services.agents.domains import (
    CalendarService,
    DocumentsService,
    GalleryService,
    MailService,
    MemoryDomainService,
    NotesService,
    NotificationsService,
    ResearchDomainService,
    SessionsService,
    SkillsService,
    TasksService,
)

REQUIRED_OPERATIONS = (
    "notes.read",
    "notes.write",
    "documents.read",
    "documents.index",
    "mail.read",
    "mail.send",
    "calendar.read",
    "calendar.write",
    "memory.read",
    "memory.write",
    "research.invoke",
    "tasks.read",
    "tasks.write",
    "sessions.read",
    "gallery.read",
    "notifications.read",
    "skills.read",
    "skills.invoke",
)


@dataclass(frozen=True)
class McpOperation:
    name: str
    target_layer: str
    mutating: bool
    required_scope: str
    requires_approval: bool


def _ops() -> dict[str, McpOperation]:
    mutating = {
        "notes.write",
        "documents.index",
        "mail.send",
        "calendar.write",
        "memory.write",
        "research.invoke",
        "tasks.write",
        "skills.invoke",
    }
    return {
        name: McpOperation(
            name=name,
            target_layer="service",
            mutating=name in mutating,
            required_scope=name,
            requires_approval=name in mutating,
        )
        for name in REQUIRED_OPERATIONS
    }


class OdysseusMcpServer:
    def __init__(
        self,
        *,
        delegation: DelegationAuthority | None = None,
        approvals: ApprovalAuthority | None = None,
    ) -> None:
        self.delegation = delegation or DelegationAuthority()
        self.approvals = approvals or ApprovalAuthority()
        self.registry = _ops()
        self.services = {
            "notes": NotesService(),
            "documents": DocumentsService(),
            "mail": MailService(),
            "calendar": CalendarService(),
            "memory": MemoryDomainService(),
            "research": ResearchDomainService(),
            "tasks": TasksService(),
            "sessions": SessionsService(),
            "gallery": GalleryService(),
            "notifications": NotificationsService(),
            "skills": SkillsService(),
        }
        self._results: dict[str, Any] = {}
        self._handlers: dict[str, Callable[..., Any]] = {
            "notes.read": lambda owner, arguments, key: self.services["notes"].read(owner, arguments),
            "notes.write": lambda owner, arguments, key: self.services["notes"].write(owner, arguments, key),
            "documents.read": lambda owner, arguments, key: self.services["documents"].read(owner, arguments),
            "documents.index": lambda owner, arguments, key: self.services["documents"].index(owner, arguments, key),
            "mail.read": lambda owner, arguments, key: self.services["mail"].read(owner, arguments),
            "mail.send": lambda owner, arguments, key: self.services["mail"].send(owner, arguments, key),
            "calendar.read": lambda owner, arguments, key: self.services["calendar"].read(owner, arguments),
            "calendar.write": lambda owner, arguments, key: self.services["calendar"].write(owner, arguments, key),
            "memory.read": lambda owner, arguments, key: self.services["memory"].read(owner, arguments),
            "memory.write": lambda owner, arguments, key: self.services["memory"].write(owner, arguments, key),
            "research.invoke": lambda owner, arguments, key: self.services["research"].invoke(owner, arguments, key),
            "tasks.read": lambda owner, arguments, key: self.services["tasks"].read(owner, arguments),
            "tasks.write": lambda owner, arguments, key: self.services["tasks"].write(owner, arguments, key),
            "sessions.read": lambda owner, arguments, key: self.services["sessions"].read(owner, arguments),
            "gallery.read": lambda owner, arguments, key: self.services["gallery"].read(owner, arguments),
            "notifications.read": lambda owner, arguments, key: self.services["notifications"].read(owner, arguments),
            "skills.read": lambda owner, arguments, key: self.services["skills"].read(owner, arguments),
            "skills.invoke": lambda owner, arguments, key: self.services["skills"].invoke(owner, arguments, key),
        }

    def call(
        self,
        operation: str,
        *,
        workload: Any,
        token: DelegationToken | None,
        grant: Any = None,
        arguments: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        if workload is None:
            raise WorkloadAuthenticationRequired("workload authentication required")
        workload.authenticate()
        if token is None:
            raise DelegationDenied("token required")
        spec = self.registry[operation]
        claims = self.delegation.verify(
            token,
            audience="odysseus-mcp",
            execution_id=token.claims.execution_id,
        )
        if spec.required_scope not in claims.scopes:
            raise DelegationDenied("scope missing")
        arguments = dict(arguments or {})
        resource = arguments.get("resource")
        if resource and claims.resources and resource not in claims.resources:
            raise DelegationDenied("resource not granted")
        if idempotency_key and idempotency_key in self._results:
            return self._results[idempotency_key]
        if spec.requires_approval:
            if grant is None:
                raise ApprovalDenied("approval required")
            action = ConfirmedAction(
                event_id=grant.event_id,
                execution_id=claims.execution_id,
                conversation_id=claims.conversation_id,
                tool=operation,
                arguments=arguments,
                owner=claims.owner,
                profile_id=claims.profile_id,
                profile_revision=claims.profile_revision,
                resources=grant.resources,
            )
            self.approvals.verify_and_consume(grant, action, idempotency_key=idempotency_key)
        result = self._handlers[operation](claims.owner, arguments, idempotency_key)
        if idempotency_key:
            self._results[idempotency_key] = result
        return result


def create_app():
    """Authenticated Streamable HTTP front door. Imported only when FastAPI is present."""
    from fastapi import FastAPI, Header, HTTPException

    server = OdysseusMcpServer()
    app = FastAPI()

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mcp")
    def mcp_call(body: dict[str, Any], x_odysseus_workload: str | None = Header(default=None)) -> Any:
        if not x_odysseus_workload:
            raise HTTPException(status_code=401, detail="workload required")
        raise HTTPException(status_code=501, detail="use in-process OdysseusMcpServer.call")

    app.state.mcp = server
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=7000)


if __name__ == "__main__":
    main()
