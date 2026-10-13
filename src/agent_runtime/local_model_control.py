"""One-use transport capabilities for admitted local Cookbook producers.

The internal HTTP token authenticates transport only. A capability bridges one
server-owned request/operation/backend to one exact resolved local launch body.
It is never persisted, returned to the model, or usable for shell/job control.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time

from src.agent_runtime.resources import NativeBackendResource, ResourceIdentityError

CAPABILITY_HEADER = "X-Odysseus-Local-Model-Capability"
_ROUTES = {"download_model": "/api/model/download", "serve_model": "/api/model/serve",
           "serve_preset": "/api/model/serve"}
_PENDING = {}
_LOCK = threading.Lock()


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class _Capability:
    authority: object
    operation: object
    backend: NativeBackendResource
    path: str
    payload_digest: str
    deadline: float


@contextmanager
def model_control_headers(tool, content, owner, payload, *, scheduled=False):
    from src.tools._common import _internal_headers
    headers = _internal_headers(owner)
    if payload.get("remote_host"):
        yield headers  # Remote workload authority/transport is unchanged.
        return
    from src.agent_runtime.authority import active_request_authority, ExactOperation
    from src.agent_runtime.remote_resources import active_backend_operation
    from src.tool_security import owner_is_admin_or_single_user
    authority = active_request_authority()
    operation = ExactOperation.normalize(tool, content)
    backend = active_backend_operation()
    if (authority is None or authority.owner != str(owner or "").strip().casefold()
            or tool not in _ROUTES or not owner_is_admin_or_single_user(owner)):
        raise ResourceIdentityError("Local model producer has no matching server authority")
    if scheduled:
        # Called only by the server-owned scheduled action, after restoration of
        # its immutable input ceiling. A task name or owner alone is not enough.
        if tool != "serve_model" or not authority.permits(operation):
            raise ResourceIdentityError("Scheduled local model input is outside authority")
        resource = NativeBackendResource(tool)
        if resource not in authority.backend_resources:
            raise ResourceIdentityError("Scheduled local model backend is outside authority")
    else:
        # This binding exists only after dispatch admission (including one-use
        # exact approval). A generic tool grant/header cannot create it over HTTP.
        if (backend is None or backend.resource != NativeBackendResource(tool)
                or (backend.request_id, backend.owner, backend.session_id,
                    backend.transport_tool, backend.exact_input) !=
                (authority.request_id, authority.owner, authority.session_id, tool, operation.input)):
            raise ResourceIdentityError("Local model producer operation or backend changed")
        resource = backend.resource
    capability = _Capability(authority, operation, resource, _ROUTES[tool], _digest(payload), time.monotonic() + 60)
    token = secrets.token_urlsafe(32)
    headers.update({CAPABILITY_HEADER: token, "X-Odysseus-Owner": authority.owner})
    with _LOCK:
        _PENDING[token] = capability
    try:
        yield headers
    finally:
        with _LOCK:
            _PENDING.pop(token, None)


def consume_model_control(request, payload):
    """Claim exactly once at the local route, before any producer effect."""
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN, INTERNAL_TOOL_USER
    from src.auth_helpers import is_direct_loopback_request
    token = request.headers.get(CAPABILITY_HEADER)
    if not token:
        return False
    if (not is_direct_loopback_request(request)
            or not secrets.compare_digest(request.headers.get(INTERNAL_TOOL_HEADER, ""), INTERNAL_TOOL_TOKEN)):
        raise ResourceIdentityError("Local model transport is untrusted")
    with _LOCK:
        capability = _PENDING.get(token)
        if (capability is None or capability.deadline < time.monotonic()
                or request.method != "POST" or request.url.path != capability.path
                or payload.get("remote_host") or _digest(payload) != capability.payload_digest
                or request.headers.get("X-Odysseus-Owner", "") != capability.authority.owner
                or getattr(request.state, "current_user", None) not in
                    (None, INTERNAL_TOOL_USER, capability.authority.owner)):
            raise ResourceIdentityError("Local model capability binding changed or expired")
        del _PENDING[token]
    request.state.local_model_authority = capability.authority
    return True
