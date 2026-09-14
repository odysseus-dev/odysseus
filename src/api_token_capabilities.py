"""Default-deny route capabilities for ``ody_`` API tokens.

The auth middleware consults this manifest only after it has validated a bearer
token. Browser sessions, internal-tool requests, auth exemptions, preflight
requests, and auth-disabled deployments stay on their existing auth paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Mapping, Sequence


API_TOKEN_FORBIDDEN_ERROR = "API token is not authorized for this endpoint"

CHAT_SCOPES = frozenset({"chat"})
TODO_READ_SCOPES = frozenset({"todos:read", "todos:write"})
TODO_WRITE_SCOPES = frozenset({"todos:write"})
EMAIL_READ_SCOPES = frozenset({"email:read", "email:draft", "email:send"})
EMAIL_DRAFT_SCOPES = frozenset({"email:draft", "email:send"})
EMAIL_SEND_SCOPES = frozenset({"email:send"})
MEMORY_READ_SCOPES = frozenset({"memory:read", "memory:write"})
MEMORY_WRITE_SCOPES = frozenset({"memory:write"})
CALENDAR_READ_SCOPES = frozenset({"calendar:read", "calendar:write"})
CALENDAR_WRITE_SCOPES = frozenset({"calendar:write"})
DOCS_READ_SCOPES = frozenset({"documents:read", "documents:write"})
DOCS_WRITE_SCOPES = frozenset({"documents:write"})

ALL_API_TOKEN_SCOPES = frozenset().union(
    CHAT_SCOPES,
    TODO_READ_SCOPES,
    EMAIL_READ_SCOPES,
    MEMORY_READ_SCOPES,
    CALENDAR_READ_SCOPES,
    DOCS_READ_SCOPES,
)


ScopeSet = frozenset[str]
ScopeOptions = tuple[ScopeSet, ...]


def _one_of(scopes: Iterable[str]) -> ScopeOptions:
    """Return alternatives where any one accepted scope authorizes a route."""
    return tuple(frozenset({scope}) for scope in sorted(scopes))


def _one_from_each(*groups: Iterable[str]) -> ScopeOptions:
    """Return alternatives requiring one accepted scope from every group."""
    normalized = [tuple(sorted(group)) for group in groups]
    return tuple(frozenset(option) for option in product(*normalized))


@dataclass(frozen=True)
class ApiTokenRouteCapability:
    methods: frozenset[str]
    path: str
    scope_options: ScopeOptions

    def matches(self, method: str, path: str) -> bool:
        normalized_path = _normalize_route_path(path)
        return (
            normalized_path is not None
            and method.upper() in self.methods
            and _path_template_matches(self.path, normalized_path)
        )

    def accepts(self, token_scopes: Iterable[str] | str | None) -> bool:
        scopes = normalize_api_token_scopes(token_scopes)
        return any(required.issubset(scopes) for required in self.scope_options)


@dataclass(frozen=True)
class ApiTokenRouteDecision:
    allowed: bool
    error: str | None = None


def _methods(*methods: str) -> frozenset[str]:
    return frozenset(method.upper() for method in methods)


def _capability(
    method: str,
    path: str,
    scopes: Iterable[str],
) -> ApiTokenRouteCapability:
    return ApiTokenRouteCapability(_methods(method), path, _one_of(scopes))


def _normalize_route_path(path: str) -> str | None:
    """Normalize the single trailing slash FastAPI redirects by default.

    Everything else stays strict. ASGI ``scope['path']`` is already decoded, so
    question marks and hashes can be legitimate path-segment data rather than
    query or fragment delimiters. Repeated separators, dot segments,
    backslashes, and control characters are treated as malformed instead of
    being normalized into a capability match.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None
    if "\\" in path:
        return None
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path != "/" and (path.endswith("/") or "//" in path):
        return None
    parts = path.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return path


def _path_template_matches(template: str, path: str) -> bool:
    template_parts = template.split("/")[1:]
    path_parts = path.split("/")[1:]
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


def _route_path_from_scope(scope: Mapping[str, object]) -> str:
    """Return the same application-relative path the ASGI router receives."""
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if not isinstance(path, str):
        return ""
    if not isinstance(root_path, str):
        root_path = ""
    if root_path:
        if path == root_path:
            path = "/"
        elif (
            path.startswith(root_path)
            and len(path) > len(root_path)
            and path[len(root_path)] == "/"
        ):
            path = path[len(root_path):]
    return path


def _has_encoded_path_separator(scope: Mapping[str, object]) -> bool:
    """Reject encoded delimiters whose decoding can differ across HTTP layers."""
    raw_path = scope.get("raw_path")
    if not isinstance(raw_path, (bytes, bytearray)):
        return False
    lowered = bytes(raw_path).split(b"?", 1)[0].lower()
    return any(encoded in lowered for encoded in (b"%2f", b"%5c", b"%00"))


def normalize_api_token_scopes(
    token_scopes: Iterable[str] | str | None,
) -> frozenset[str]:
    if isinstance(token_scopes, str):
        values: Iterable[object] = token_scopes.split(",")
    else:
        values = token_scopes or ()
    return frozenset(
        normalized
        for scope in values
        if (normalized := str(scope).strip())
    )


_BOOTSTRAP_SCOPES = _one_of(ALL_API_TOKEN_SCOPES)
_EMAIL_DOCUMENT_DRAFT_SCOPES = _one_from_each(
    EMAIL_DRAFT_SCOPES,
    DOCS_WRITE_SCOPES,
)


API_TOKEN_ROUTE_CAPABILITIES: tuple[ApiTokenRouteCapability, ...] = (
    _capability("POST", "/api/v1/chat", CHAT_SCOPES),
    _capability("GET", "/api/models", CHAT_SCOPES),
    _capability("GET", "/api/companion/ping", CHAT_SCOPES),
    _capability("GET", "/api/companion/info", CHAT_SCOPES),
    _capability("GET", "/api/companion/models", CHAT_SCOPES),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/capabilities",
        _BOOTSTRAP_SCOPES,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/codex/plugin.zip",
        _BOOTSTRAP_SCOPES,
    ),
    ApiTokenRouteCapability(
        _methods("GET"),
        "/api/claude/plugin.zip",
        _BOOTSTRAP_SCOPES,
    ),
    _capability("GET", "/api/codex/todos", TODO_READ_SCOPES),
    _capability("POST", "/api/codex/todos", TODO_READ_SCOPES),
    _capability("GET", "/api/codex/emails", EMAIL_READ_SCOPES),
    _capability("GET", "/api/codex/emails/{uid}", EMAIL_READ_SCOPES),
    ApiTokenRouteCapability(
        _methods("POST"),
        "/api/codex/emails/draft-document",
        _EMAIL_DOCUMENT_DRAFT_SCOPES,
    ),
    _capability("POST", "/api/codex/emails/draft", EMAIL_DRAFT_SCOPES),
    _capability("POST", "/api/codex/emails/send", EMAIL_SEND_SCOPES),
    _capability("GET", "/api/codex/memory", MEMORY_READ_SCOPES),
    _capability("POST", "/api/codex/memory", MEMORY_WRITE_SCOPES),
    _capability("DELETE", "/api/codex/memory/{memory_id}", MEMORY_WRITE_SCOPES),
    _capability("GET", "/api/codex/calendar/events", CALENDAR_READ_SCOPES),
    _capability("POST", "/api/codex/calendar/events", CALENDAR_WRITE_SCOPES),
    _capability(
        "DELETE",
        "/api/codex/calendar/events/{uid}",
        CALENDAR_WRITE_SCOPES,
    ),
    _capability("GET", "/api/codex/documents", DOCS_READ_SCOPES),
    _capability("GET", "/api/codex/documents/{doc_id}", DOCS_READ_SCOPES),
    _capability("POST", "/api/codex/documents", DOCS_WRITE_SCOPES),
    _capability("DELETE", "/api/codex/documents/{doc_id}", DOCS_WRITE_SCOPES),
)


def _validate_manifest(capabilities: Sequence[ApiTokenRouteCapability]) -> None:
    seen: set[tuple[str, str]] = set()
    for capability in capabilities:
        if _normalize_route_path(capability.path) != capability.path:
            raise RuntimeError(
                f"Malformed API-token capability path: {capability.path!r}"
            )
        if not capability.methods or not capability.scope_options:
            raise RuntimeError("API-token capabilities must declare methods and scopes")
        for required in capability.scope_options:
            if not required or not required.issubset(ALL_API_TOKEN_SCOPES):
                raise RuntimeError("API-token capability contains an unknown scope")
        for method in capability.methods:
            key = (method, capability.path)
            if key in seen:
                raise RuntimeError(
                    f"Duplicate API-token capability: {method} {capability.path}"
                )
            seen.add(key)


_validate_manifest(API_TOKEN_ROUTE_CAPABILITIES)


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
    token_scopes: Iterable[str] | str | None,
) -> ApiTokenRouteDecision:
    capability = find_api_token_route_capability(method, path)
    if capability is not None and capability.accepts(token_scopes):
        return ApiTokenRouteDecision(allowed=True)
    return ApiTokenRouteDecision(allowed=False, error=API_TOKEN_FORBIDDEN_ERROR)


def authorize_api_token_request(
    method: str,
    scope: Mapping[str, object],
    token_scopes: Iterable[str] | str | None,
) -> ApiTokenRouteDecision:
    if _has_encoded_path_separator(scope):
        return ApiTokenRouteDecision(allowed=False, error=API_TOKEN_FORBIDDEN_ERROR)
    return authorize_api_token_route(
        method,
        _route_path_from_scope(scope),
        token_scopes,
    )
