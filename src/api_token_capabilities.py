"""Default-deny route capabilities for ``ody_`` API tokens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ApiTokenRouteCapability:
    methods: frozenset[str]
    path: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    def matches(self, method: str, path: str) -> bool:
        return (
            method.upper() in self.methods
            and _path_template_matches(self.path, path)
        )


@dataclass(frozen=True)
class ApiTokenRouteDecision:
    allowed: bool
    error: str | None = None
    required_scopes: tuple[str, ...] = ()


def _methods(*methods: str) -> frozenset[str]:
    return frozenset(m.upper() for m in methods)


def _scopes(*scopes: str) -> frozenset[str]:
    return frozenset(scopes)


def _path_template_matches(template: str, path: str) -> bool:
    template_parts = template.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(template_parts) != len(path_parts):
        return False
    for expected, actual in zip(template_parts, path_parts):
        if expected.startswith("{") and expected.endswith("}"):
            if not actual:
                return False
            continue
        if expected != actual:
            return False
    return True


TODO_READ = _scopes("todos:read", "todos:write")
TODO_WRITE = _scopes("todos:write")
EMAIL_READ = _scopes("email:read", "email:draft", "email:send")
EMAIL_DRAFT = _scopes("email:draft", "email:send")
EMAIL_SEND = _scopes("email:send")
MEMORY_READ = _scopes("memory:read", "memory:write")
MEMORY_WRITE = _scopes("memory:write")
CALENDAR_READ = _scopes("calendar:read", "calendar:write")
CALENDAR_WRITE = _scopes("calendar:write")
DOCUMENTS_READ = _scopes("documents:read", "documents:write")
DOCUMENTS_WRITE = _scopes("documents:write")
COOKBOOK_READ = _scopes("cookbook:read", "cookbook:launch")
COOKBOOK_LAUNCH = _scopes("cookbook:launch")


API_TOKEN_ROUTE_CAPABILITIES: tuple[ApiTokenRouteCapability, ...] = (
    ApiTokenRouteCapability(_methods("POST"), "/api/v1/chat", _scopes("chat")),
    ApiTokenRouteCapability(_methods("GET"), "/api/companion/ping"),
    ApiTokenRouteCapability(_methods("GET"), "/api/companion/info"),
    ApiTokenRouteCapability(_methods("GET"), "/api/companion/models"),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/capabilities"),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/plugin.zip"),
    ApiTokenRouteCapability(_methods("GET"), "/api/claude/plugin.zip"),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/todos", TODO_READ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/todos",
        TODO_READ | TODO_WRITE,
    ),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/emails", EMAIL_READ),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/emails/{uid}", EMAIL_READ),
    ApiTokenRouteCapability(_methods("POST"), "/api/codex/emails/draft", EMAIL_DRAFT),
    ApiTokenRouteCapability(_methods("POST"), "/api/codex/emails/send", EMAIL_SEND),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/memory", MEMORY_READ),
    ApiTokenRouteCapability(_methods("POST"), "/api/codex/memory", MEMORY_WRITE),
    ApiTokenRouteCapability(
        _methods("DELETE"),
        "/api/codex/memory/{memory_id}",
        MEMORY_WRITE,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/calendar/events",
        CALENDAR_READ,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/calendar/events",
        CALENDAR_WRITE,
    ),
    ApiTokenRouteCapability(
        _methods("DELETE"),
        "/api/codex/calendar/events/{uid}",
        CALENDAR_WRITE,
    ),
    ApiTokenRouteCapability(_methods("GET"), "/api/codex/documents", DOCUMENTS_READ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/documents/{doc_id}",
        DOCUMENTS_READ,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/documents",
        DOCUMENTS_WRITE,
    ),
    ApiTokenRouteCapability(
        _methods("DELETE"),
        "/api/codex/documents/{doc_id}",
        DOCUMENTS_WRITE,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/cookbook/tasks",
        COOKBOOK_READ,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/cookbook/servers",
        COOKBOOK_READ,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/cookbook/output/{session_id}",
        COOKBOOK_READ,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/cookbook/cached",
        COOKBOOK_READ,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/cookbook/presets",
        COOKBOOK_READ,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/cookbook/serve",
        COOKBOOK_LAUNCH,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/cookbook/stop/{session_id}",
        COOKBOOK_LAUNCH,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/cookbook/preset/{name}",
        COOKBOOK_LAUNCH,
    ),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/cookbook/adopt",
        COOKBOOK_LAUNCH,
    ),
)


def find_api_token_route_capability(
    method: str,
    path: str,
) -> ApiTokenRouteCapability | None:
    for capability in API_TOKEN_ROUTE_CAPABILITIES:
        if capability.matches(method, path):
            return capability
    return None


def authorize_api_token_route(
    method: str,
    path: str,
    token_scopes: Iterable[str] | None,
) -> ApiTokenRouteDecision:
    capability = find_api_token_route_capability(method, path)
    if capability is None:
        return ApiTokenRouteDecision(
            allowed=False,
            error="API token is not allowed for this endpoint",
        )

    if not capability.scopes:
        return ApiTokenRouteDecision(allowed=True)

    scopes = {
        str(scope).strip()
        for scope in (token_scopes or [])
        if str(scope).strip()
    }
    if scopes.intersection(capability.scopes):
        return ApiTokenRouteDecision(allowed=True)

    required = tuple(sorted(capability.scopes))
    return ApiTokenRouteDecision(
        allowed=False,
        error=f"API token missing required scope: {' or '.join(required)}",
        required_scopes=required,
    )
