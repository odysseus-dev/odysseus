"""Dispatcher doubles must simulate the receipt boundary as well as the result."""
from src.agent_runtime.journal import mark_dispatch, record_action


def server_authorized_executor(executor):
    """Give standalone dispatcher fixtures their explicit server grants.

    These existing suites exercise handlers, policy, confinement and approvals.
    Their fixture grants cover the declared native tool registry, independently
    of the proposed block. Request-authority denial tests use the raw dispatcher.
    """
    from functools import wraps
    from inspect import signature
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    from src.tool_policy import known_tool_names
    from src.turn_contract import canonical_tool
    from src.agent_runtime.remote_resources import seal_backends
    from src.agent_runtime.resources import FilesystemRoot, NativeBackendResource, ProcessLaunchScope
    from src.containment import DEFAULT_REQUIRED
    from src.agent_runtime.process_resources import seal_launch_scope
    from pathlib import Path
    import tempfile
    call_signature = signature(executor)
    @wraps(executor)
    async def execute(*args, **kwargs):
        bound = call_signature.bind(*args, **kwargs)
        parameters = bound.arguments
        grants = tuple(OperationGrant(name) for name in sorted(
            {canonical_tool(n) for n in known_tool_names()} | {"list_dir", "find_files"}))
        original = parameters.get("exact_approval")
        authority = original.pending.request_authority if original is not None else None
        if authority is not None:
            kwargs.setdefault("request_authority", authority)
        scratch = Path(tempfile.mkdtemp(prefix="odysseus-dispatch-fixture-"))
        launch_scopes = (None if parameters.get("workspace") else tuple(
            seal_launch_scope(NativeBackendResource(tool), FilesystemRoot.seal(scratch))
            for tool in ("bash", "python")))
        kwargs.setdefault("request_authority", RequestAuthority(
            "standalone-test-request", str(parameters.get("owner") or "").strip().casefold(),
            str(parameters.get("session_id") or ""), str(parameters.get("workspace") or ""),
            grants,
            launch_scopes=launch_scopes,
            backend_resources=seal_backends((g.tool for g in grants), context=parameters.get("client_runtime_context"),
                owner=str(parameters.get("owner") or "").strip().casefold()),
        ))
        return await executor(*args, **kwargs)
    return execute


def authoritative_executor(function):
    @record_action
    async def execute(block, *args, **kwargs):
        mark_dispatch()
        return await function(block, *args, **kwargs)
    return execute
