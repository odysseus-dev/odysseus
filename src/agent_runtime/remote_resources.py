"""Backend resolution and pinning, independent of transport and lifecycle.

Connection/configuration incarnations here are not process identities. Backend
snapshots are captured by trusted admission; discovery never supplies a grant.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import hashlib
import hmac
import secrets
import json

from src.agent_runtime.resources import ExternalResource, NativeBackendResource, ResourceIdentityError


def endpoint_identity(url):
    """Credential-free origin. Paths may themselves contain access tokens."""
    if not isinstance(url, str) or any(c in url for c in ("\0", "\n", "\r")):
        raise ValueError("Malformed resource endpoint")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Resource endpoint requires an HTTP origin")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    if port and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, "", "", ""))


_CLIENT_ENDPOINTS = {}
_CONFIG_KEY = secrets.token_bytes(32)


def configuration_incarnation(value):
    """Opaque in-process configuration identity, including secret URL changes."""
    return hmac.new(_CONFIG_KEY, str(value).encode(), hashlib.sha256).hexdigest()


def _client_resource(tool, context, *, admission):
    from src.tool_execution import _client_bridge, _tui_host_bridge_patch_url, _ROUTED_BRIDGE_TOOLS
    bridge = _client_bridge(context)
    target = _tui_host_bridge_patch_url(context) if tool == "apply_patch" else None
    if target is not None:
        url = target[0]
    elif bridge is not None and (tool in _ROUTED_BRIDGE_TOOLS or tool == "host_shell"):
        url = bridge["url"]
    else:
        return None
    endpoint = endpoint_identity(url)
    # Never cache credentials. A request cannot create a registry entry during
    # dispatch; only trusted server admission may register an endpoint.
    key = configuration_incarnation((url, bridge.get("token") if bridge else None))
    if admission:
        _CLIENT_ENDPOINTS.setdefault(key, uuid4().hex)
    incarnation = _CLIENT_ENDPOINTS.get(key)
    if incarnation is None:
        raise ResourceIdentityError("External bridge endpoint is not sealed")
    return ExternalResource("client_bridge", endpoint, "tui", tool, incarnation)


def http_bridge_resource(tool, context, *, admission=False):
    config = context.get("external_execution_bridge") if isinstance(context, dict) else None
    if not isinstance(config, dict) or tool not in (config.get("supported_tools") or ()):
        return None
    url, token = config.get("url"), config.get("token")
    if not isinstance(token, str) or not token:
        raise ResourceIdentityError("External HTTP bridge has no server configuration")
    epoch = configuration_incarnation((url, token, tuple(sorted(config["supported_tools"]))))
    if admission:
        _CLIENT_ENDPOINTS.setdefault(epoch, epoch)
    if epoch not in _CLIENT_ENDPOINTS:
        raise ResourceIdentityError("External HTTP bridge configuration is not sealed")
    return ExternalResource("execution_bridge", endpoint_identity(url), "request_local_http", tool, epoch)


def integration_resource(config):
    if not isinstance(config, dict) or not config.get("enabled", True) or not isinstance(config.get("id"), str) or not config["id"]:
        raise ResourceIdentityError("Integration identity is unresolved")
    endpoint = endpoint_identity(config.get("base_url"))
    epoch = configuration_incarnation(json.dumps(config, sort_keys=True, allow_nan=False))
    return ExternalResource("integration", endpoint, config["id"], "api_call", epoch)


def api_arguments(content):
    if content.lstrip().startswith("{"):
        args = json.loads(content)
    else:
        lines = content.strip().split("\n", 2)
        args = {"integration": lines[0].strip()}
        if len(lines) > 1:
            method, _, path = lines[1].strip().partition(" ")
            args.update(method=method, path=path or "/")
        if len(lines) > 2:
            args["body"] = json.loads(lines[2])
    selector = args.get("integration")
    if not isinstance(selector, str) or not selector.strip():
        raise ResourceIdentityError("Integration selector is unresolved")
    return args


def resolve_backend(tool, *, context=None, admission=False, content="", owner=None):
    from src.tool_execution import get_active_execution_bridge, get_mcp_manager, _MCP_TOOL_MAP
    from src.tool_security import BUILTIN_EMAIL_TOOLS
    bridge = get_active_execution_bridge()
    if bridge is not None and tool in bridge.supported_tools:
        return bridge.resource_identity(tool)
    configured_bridge = http_bridge_resource(tool, context, admission=admission)
    if configured_bridge is not None:
        return configured_bridge
    client = _client_resource(tool, context, admission=admission)
    if client is not None:
        return client
    if tool == "api_call":
        from src.integrations import load_integrations
        selector = api_arguments(content)["integration"]
        rows = [row for row in load_integrations() if row.get("id") == selector
                or str(row.get("name", "")).casefold() == selector.casefold()]
        if len(rows) != 1:
            raise ResourceIdentityError("Integration alias is missing or ambiguous")
        return integration_resource(rows[0])
    qualified = tool
    required = tool.startswith("mcp__") or tool in BUILTIN_EMAIL_TOOLS
    if tool in BUILTIN_EMAIL_TOOLS:
        qualified = "mcp__email__" + tool
    elif tool in _MCP_TOOL_MAP and tool not in {"read_file", "write_file", "generate_image"}:
        server, name = _MCP_TOOL_MAP[tool]
        qualified = f"mcp__{server}__{name}"
    if qualified.startswith("mcp__"):
        manager = get_mcp_manager()
        identity = manager.resource_identity(qualified) if manager is not None else None
        if isinstance(identity, ExternalResource):
            if identity.owner and owner != identity.owner:
                raise ResourceIdentityError("MCP backend belongs to another owner")
            return identity
        if required:
            raise ResourceIdentityError("MCP backend/tool identity is unresolved")
    if tool == "host_shell":
        raise ResourceIdentityError("Host-shell backend identity is unresolved")
    return NativeBackendResource(tool)


def seal_backends(tools, *, context=None, owner=None):
    result = []
    for tool in tools:
        try:
            if tool == "api_call":
                # A generic API operation grant does not select an integration.
                # Trusted admission must supply its explicit backend identity,
                # or a user can approve one fully sealed exact operation.
                continue
            result.append(resolve_backend(tool, context=context, admission=True, owner=owner))
        except (ValueError, TypeError, AttributeError):
            continue
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class BoundBackendOperation:
    resource: ExternalResource | NativeBackendResource
    request_id: str
    owner: str
    session_id: str
    transport_tool: str
    exact_input: str

    def __post_init__(self):
        if not isinstance(self.resource, (ExternalResource, NativeBackendResource)):
            raise ValueError("Malformed bound backend operation")
        if any(not isinstance(v, str) for v in (self.request_id, self.owner, self.session_id, self.transport_tool, self.exact_input)):
            raise ValueError("Malformed backend operation binding")

    def to_dict(self):
        # Exact arguments/selectors are already digest-bound by the approval's
        # original content. Keep credentials out of the identity serializer.
        return {"resource": self.resource.to_dict(), "request_id": self.request_id,
                "owner": self.owner, "session_id": self.session_id, "tool": self.transport_tool,
                "input_digest": configuration_incarnation(self.exact_input)}

    def validate(self, context=None):
        current = resolve_backend(self.transport_tool, context=context, content=self.exact_input, owner=self.owner)
        if current != self.resource:
            # A pinned native backend remains native when MCP availability
            # changes. It cannot be upgraded to an external backend.
            if isinstance(self.resource, NativeBackendResource) and isinstance(current, ExternalResource) and current.namespace == "mcp":
                return
            raise ResourceIdentityError("Backend resource identity changed")


def bind_backend_for_operation(authority, operation, *, context=None, approved=None, exact_admission=False):
    current = resolve_backend(operation.transport_tool, context=context, content=operation.input, owner=authority.owner)
    native = NativeBackendResource(operation.transport_tool)
    if approved is not None:
        if (not isinstance(approved, BoundBackendOperation)
                or (approved.request_id and approved.request_id != authority.request_id)
                or (approved.owner, approved.session_id) != (authority.owner, authority.session_id)
                or (approved.transport_tool, approved.exact_input) != (operation.transport_tool, operation.input)):
            raise ResourceIdentityError("Approved backend binding changed")
        selected = approved.resource
    elif current in authority.backend_resources:
        selected = current
    elif native in authority.backend_resources:
        selected = native
    elif isinstance(current, NativeBackendResource) and not authority.inherited:
        # Legacy operation authority can only retain the fixed local backend;
        # it cannot reconstruct any external backend from current availability.
        selected = current
    else:
        raise ResourceIdentityError("External backend is outside sealed request scope")
    if isinstance(selected, ExternalResource) and selected not in authority.backend_resources:
        if not (exact_admission and approved is not None and not authority.inherited):
            raise ResourceIdentityError("External backend exceeds parent/request scope")
    bound = BoundBackendOperation(selected, authority.request_id, authority.owner, authority.session_id,
                                  operation.transport_tool, operation.input)
    bound.validate(context)
    return bound


_ACTIVE = ContextVar("backend_resource_operation", default=None)


def active_backend_operation():
    return _ACTIVE.get()


@contextmanager
def bind_backend_operation(operation):
    if operation is not None and not isinstance(operation, BoundBackendOperation):
        raise TypeError("Backend operation must be server-owned")
    token = _ACTIVE.set(operation)
    try:
        yield operation
    finally:
        _ACTIVE.reset(token)
