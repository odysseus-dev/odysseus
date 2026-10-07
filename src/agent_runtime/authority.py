"""Server-owned request admission, independent of model tool availability."""
from __future__ import annotations

from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from inspect import signature
import json
from pathlib import Path
import re
from uuid import uuid4

from src.agent_runtime.resources import (
    FilesystemRoot, ExternalResource, NativeBackendResource, OwnedScope,
    ProcessLaunchScope, ProcessResource, BackgroundJobResource,
    BrowserSessionResource, BrowserPageResource,
    backend_from_dict, intersect_roots, seal_owned_scopes,
)
from src.tool_policy import ToolPolicy, build_effective_tool_policy
from src.turn_contract import (
    FAMILY_TOOLS, canonical_tool, requested_capabilities,
    RequiredReadOperation, required_read_operation_for_request, selected_tools_for_request,
)


def _owner(value):
    return str(value or "").strip().casefold()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate operation argument")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite operation argument")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ExactOperation:
    tool: str
    input: str
    action: str | None = None
    transport_tool: str = ""

    @classmethod
    def normalize(cls, tool, content):
        if not isinstance(tool, str) or not tool.strip() or not isinstance(content, str):
            raise ValueError("Operation requires a tool name and string input")
        transport_tool = tool.strip()
        tool = canonical_tool(transport_tool)
        normalized = content
        payload = None
        raw_input = tool in {"bash", "python"} or tool.startswith("scheduled__")
        if not raw_input and content.lstrip().startswith("{"):
            payload = json.loads(content, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            if not isinstance(payload, dict):
                raise ValueError("Structured tool input must be an object")
            normalized = _json(payload)
        # Reuse the runtime's existing multiplexed-action normalization; this
        # classifies input and never grants permission or changes the input.
        from src.tool_capabilities import _action_from_content
        action = _action_from_content(tool, content)
        if tool == "private_browser" and isinstance(payload, dict):
            action = payload.get("action")
            if action is not None and not isinstance(action, str):
                raise ValueError("Browser action must be a string")
            action = action.strip().casefold() if action else None
        return cls(tool, normalized, action, transport_tool)


@dataclass(frozen=True)
class OperationGrant:
    tool: str
    actions: frozenset[str] | None = None
    inputs: frozenset[str] | None = None

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool or canonical_tool(self.tool) != self.tool:
            raise ValueError("Grant requires a canonical tool identity")
        for values in (self.actions, self.inputs):
            if values is not None and (not isinstance(values, frozenset)
                                      or any(not isinstance(v, str) for v in values)):
                raise TypeError("Grant limits must be immutable string sets")

    def permits(self, operation):
        return (self.tool == operation.tool
                and (self.actions is None or operation.action in self.actions)
                and (self.inputs is None or operation.input in self.inputs))

    def intersect(self, other):
        if self.tool != other.tool:
            raise ValueError("Cannot intersect different operation classes")
        def limits(left, right):
            return right if left is None else left if right is None else left & right
        return OperationGrant(self.tool, limits(self.actions, other.actions),
                              limits(self.inputs, other.inputs))


@dataclass(frozen=True)
class RequestAuthority:
    request_id: str
    owner: str
    session_id: str
    workspace: str
    grants: tuple[OperationGrant, ...] = ()
    denied: frozenset[str] = frozenset()
    block_all: bool = False
    disable_mcp: bool = False
    inherited: bool = False
    # None is only the trusted constructor's instruction to seal a workspace.
    # Persisted/child authorities always carry an explicit tuple, including ().
    resource_roots: tuple[FilesystemRoot, ...] | None = None
    backend_resources: tuple[ExternalResource | NativeBackendResource, ...] | None = None
    owned_scopes: tuple[OwnedScope, ...] | None = None
    launch_scopes: tuple[ProcessLaunchScope, ...] | None = None
    process_resources: tuple[ProcessResource, ...] = ()
    job_resources: tuple[BackgroundJobResource, ...] | None = None
    browser_sessions: tuple[BrowserSessionResource, ...] | None = None
    browser_pages: tuple[BrowserPageResource, ...] | None = None

    def __post_init__(self):
        if (not isinstance(self.request_id, str) or not self.request_id
                or any(not isinstance(v, str) for v in (self.owner, self.session_id, self.workspace))
                or not isinstance(self.grants, tuple)
                or any(not isinstance(g, OperationGrant) for g in self.grants)
                or len({g.tool for g in self.grants}) != len(self.grants)
                or not isinstance(self.denied, frozenset)
                or any(not isinstance(n, str) or canonical_tool(n) != n for n in self.denied)
                or any(type(v) is not bool for v in (self.block_all, self.disable_mcp, self.inherited))):
            raise ValueError("Malformed request authority")
        if self.resource_roots is None:
            roots = ()
            if self.workspace:
                try:
                    roots = (FilesystemRoot.seal(self.workspace, owner=self.owner),)
                except (OSError, ValueError, RuntimeError):
                    pass  # An unresolved workspace grants no filesystem root.
            object.__setattr__(self, "resource_roots", roots)
        if (not isinstance(self.resource_roots, tuple)
                or any(not isinstance(r, FilesystemRoot) or (r.owner and r.owner != self.owner)
                       for r in self.resource_roots)):
            raise ValueError("Malformed request resource roots")
        if self.backend_resources is None:
            from src.agent_runtime.remote_resources import seal_backends
            object.__setattr__(self, "backend_resources", seal_backends((g.tool for g in self.grants), owner=self.owner))
        if self.owned_scopes is None:
            object.__setattr__(self, "owned_scopes", seal_owned_scopes(
                self.owner, self.session_id, (g.tool for g in self.grants)))
        if (not isinstance(self.backend_resources, tuple)
                or any(not isinstance(r, (ExternalResource, NativeBackendResource))
                       or (isinstance(r, ExternalResource) and r.owner and r.owner != self.owner) for r in self.backend_resources)
                or not isinstance(self.owned_scopes, tuple)
                or any(not isinstance(s, OwnedScope) or (s.owner, s.thread_id) != (self.owner, self.session_id)
                       for s in self.owned_scopes)):
            raise ValueError("Malformed backend or owned resource scope")
        from src.agent_runtime.process_resources import seal_launch_scopes, seal_jobs
        if self.launch_scopes is None:
            object.__setattr__(self, "launch_scopes", seal_launch_scopes(self))
        if self.job_resources is None:
            object.__setattr__(self, "job_resources", seal_jobs(self))
        for field, kind in (("launch_scopes", ProcessLaunchScope), ("process_resources", ProcessResource),
                            ("job_resources", BackgroundJobResource)):
            values = getattr(self, field)
            if not isinstance(values, tuple) or any(not isinstance(r, kind) for r in values):
                raise ValueError("Malformed process resource scope")
        if any(r.owner != self.owner for r in (*self.process_resources, *self.job_resources)):
            raise ValueError("Process resource owner changed")
        if any(s.root.owner and s.root.owner != self.owner for s in self.launch_scopes):
            raise ValueError("Launch resource owner changed")
        if any(r.thread_id != self.session_id for r in self.job_resources):
            raise ValueError("Job resource thread changed")
        if any(r.thread_id != (self.session_id or "request:" + self.request_id) for r in self.process_resources):
            raise ValueError("Process resource thread changed")
        from src.browser_identity import seal_browser_resources
        sessions, pages = seal_browser_resources(self) if self.browser_sessions is None or self.browser_pages is None else ((), ())
        if self.browser_sessions is None:
            object.__setattr__(self, "browser_sessions", sessions)
        if self.browser_pages is None:
            object.__setattr__(self, "browser_pages", pages)
        for values, kind in ((self.browser_sessions, BrowserSessionResource), (self.browser_pages, BrowserPageResource)):
            if not isinstance(values, tuple) or any(not isinstance(r, kind) for r in values):
                raise ValueError("Malformed browser resource scope")
            for r in values:
                session = r.session if isinstance(r, BrowserPageResource) else r
                if (session.owner, session.thread_id) != (self.owner, self.session_id):
                    raise ValueError("Browser owner/thread binding changed")

    @classmethod
    def empty(cls, *, owner=None, session_id=None, workspace=None):
        return cls(uuid4().hex, _owner(owner), str(session_id or ""), str(workspace or ""),
                   resource_roots=(), backend_resources=(), owned_scopes=(), launch_scopes=(), job_resources=(), browser_sessions=(), browser_pages=())

    def bound_to(self, *, owner=None, session_id=None, workspace=None):
        return (self.owner == _owner(owner) and self.session_id == str(session_id or "")
                and self.workspace == str(workspace or ""))

    def restricted(self, operation):
        return (self.block_all or operation.tool in self.denied
                or (self.disable_mcp and (operation.tool.startswith("mcp__")
                                         or operation.transport_tool.startswith("mcp__"))))

    def permits(self, operation):
        return not self.restricted(operation) and any(g.permits(operation) for g in self.grants)

    def restrict(self, policy=None, disabled_tools=()):
        policy = policy or ToolPolicy()
        return replace(self, denied=self.denied | frozenset(
            canonical_tool(n) for n in set(disabled_tools or ()) | policy.all_disabled_names()),
            block_all=self.block_all or policy.block_all_tool_calls,
            disable_mcp=self.disable_mcp or policy.disable_mcp)

    def intersect(self, child):
        if not isinstance(child, RequestAuthority):
            raise TypeError("Child authority must be server-owned RequestAuthority")
        grants = []
        roots = ()
        backends = ()
        owned = ()
        launches = processes = jobs = ()
        browser_sessions = browser_pages = ()
        if (self.owner, self.session_id, self.workspace) == (child.owner, child.session_id, child.workspace):
            theirs = {g.tool: g for g in child.grants}
            grants = [g.intersect(theirs[g.tool]) for g in self.grants if g.tool in theirs]
            roots = intersect_roots(self.resource_roots, child.resource_roots)
            backends = tuple(r for r in self.backend_resources if r in child.backend_resources)
            owned = tuple(s for left in self.owned_scopes for right in child.owned_scopes
                          if (s := left.intersect(right)) is not None)
            from src.agent_runtime.process_resources import intersect_observed, intersect_launch_scopes, validate_job
            launches = intersect_launch_scopes(self.launch_scopes, child.launch_scopes)
            processes = intersect_observed(self.process_resources, child.process_resources, lambda r: r.validate())
            jobs = intersect_observed(self.job_resources, child.job_resources, validate_job)
            from src.browser_identity import intersect_browser
            browser_sessions, browser_pages = intersect_browser(self.browser_sessions, self.browser_pages,
                child.browser_sessions, child.browser_pages)
        return replace(self, grants=tuple(grants), denied=self.denied | child.denied,
                       block_all=self.block_all or child.block_all,
                       disable_mcp=self.disable_mcp or child.disable_mcp, inherited=True,
                       resource_roots=roots, backend_resources=backends, owned_scopes=owned,
                       launch_scopes=launches, process_resources=processes, job_resources=jobs,
                       browser_sessions=browser_sessions, browser_pages=browser_pages)

    def continuation(self, *, owner=None, session_id=None):
        """A server continuation may rebind a session, never change owner/grants."""
        if self.owner != _owner(owner):
            return RequestAuthority.empty(owner=owner, session_id=session_id)
        rebound = str(session_id or "")
        return replace(self, session_id=rebound, inherited=True,
                       owned_scopes=tuple(replace(s, thread_id=rebound) for s in self.owned_scopes) if rebound else (),
                       process_resources=tuple(r for r in self.process_resources if r.thread_id == rebound),
                       job_resources=tuple(r for r in self.job_resources if r.thread_id == rebound),
                       browser_sessions=tuple(r for r in self.browser_sessions if r.thread_id == rebound),
                       browser_pages=tuple(r for r in self.browser_pages if r.session.thread_id == rebound))

    def to_dict(self):
        return {"version": 5, "request_id": self.request_id, "owner": self.owner,
                "session_id": self.session_id, "workspace": self.workspace,
                "grants": [{"tool": g.tool,
                            "actions": None if g.actions is None else sorted(g.actions),
                            "inputs": None if g.inputs is None else sorted(g.inputs)} for g in self.grants],
                "denied": sorted(self.denied), "block_all": self.block_all,
                "disable_mcp": self.disable_mcp, "inherited": self.inherited,
                "resource_roots": [r.to_dict() for r in self.resource_roots],
                "backend_resources": [r.to_dict() for r in self.backend_resources],
                "owned_scopes": [s.to_dict() for s in self.owned_scopes],
                "launch_scopes": [s.to_dict() for s in self.launch_scopes],
                "process_resources": [r.to_dict() for r in self.process_resources],
                "job_resources": [r.to_dict() for r in self.job_resources],
                "browser_sessions": [r.to_dict() for r in self.browser_sessions],
                "browser_pages": [r.to_dict() for r in self.browser_pages]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] not in {1, 2, 3, 4, 5}):
            raise ValueError("Unsupported authority snapshot")
        def limits(value):
            if value is None:
                return None
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError("Malformed authority limits")
            return frozenset(value)
        roots = value["resource_roots"] if value["version"] >= 2 else []
        if not isinstance(roots, list):
            raise ValueError("Malformed request resource snapshot")
        backends = value["backend_resources"] if value["version"] >= 3 else []
        owned = value["owned_scopes"] if value["version"] >= 3 else []
        process_fields = {name: value[name] if value["version"] >= 4 else []
                          for name in ("launch_scopes", "process_resources", "job_resources")}
        if any(not isinstance(v, list) for v in process_fields.values()):
            raise ValueError("Malformed process resource snapshot")
        if not isinstance(backends, list) or not isinstance(owned, list):
            raise ValueError("Malformed request resource scope snapshot")
        if value["version"] >= 5 and any(not isinstance(value.get(name), list) for name in ("browser_sessions", "browser_pages")):
            raise ValueError("Malformed browser resource scope snapshot")
        return cls(value["request_id"], value["owner"], value["session_id"], value["workspace"],
                   tuple(OperationGrant(g["tool"], limits(g["actions"]), limits(g["inputs"]))
                         for g in value["grants"]), limits(value["denied"]),
                   value["block_all"], value["disable_mcp"], value["inherited"],
                   tuple(FilesystemRoot.from_dict(r) for r in roots),
                   tuple(backend_from_dict(r) for r in backends), tuple(OwnedScope.from_dict(s) for s in owned),
                   tuple(ProcessLaunchScope.from_dict(s) for s in process_fields["launch_scopes"]),
                   tuple(ProcessResource.from_dict(r) for r in process_fields["process_resources"]),
                   tuple(BackgroundJobResource.from_dict(r) for r in process_fields["job_resources"]),
                   tuple(BrowserSessionResource.from_dict(r) for r in value["browser_sessions"]) if value["version"] >= 5 else (),
                   tuple(BrowserPageResource.from_dict(r) for r in value["browser_pages"]) if value["version"] >= 5 else ())


_BROWSER_READ_ACTIONS = frozenset({"open", "navigate", "snapshot", "text", "read", "find",
    "screenshot", "scroll", "back", "forward", "wait", "status", "close", "tabs", "session_info"})


@dataclass(frozen=True)
class SemanticIntent:
    """Routing facts, with no execution permission or provider inventory."""
    capabilities: frozenset[str]
    selected_tools: frozenset[str] | None
    required_read: RequiredReadOperation | None


def interpret_request(request_text, *, history=(), workspace=None, active_document=False,
                      image_attachment=False):
    if not isinstance(request_text, str):
        raise TypeError("Intent requires request text")
    # Routing may use model/tool history. Admission may only inherit intent
    # from trusted user requests; a model's proposal or attempted tool call
    # cannot establish a new authorized operation class.
    history = tuple(history or ())
    trusted_history = []
    for row in history:
        get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
        metadata = get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                metadata = {}
        if (get("role") == "user" and isinstance(metadata, dict)
                and metadata.get("trusted") is not False and not metadata.get("tool_gate_untrusted")):
            trusted_history.append({"role": "user", "content": get("content", "")})
    families = requested_capabilities(request_text, trusted_history,
        active_document=active_document, workspace=bool(workspace), image_attachment=image_attachment)
    selected = selected_tools_for_request(request_text)
    if (families <= {"unknown"} and selected is None
            and re.search(r"\b(?:lan|local\s+(?:network|ip)|tailscale|arp|ip\s+route|default\s+route|subnet|network\s+interface|neighbor\s+table|wifi|ethernet)\b", request_text, re.I)
            and re.search(r"\b(?:find|check|inspect|show|list|lookup|locate)\b", request_text, re.I)
            and not re.search(r"\b(?:web|internet|online)\b", request_text, re.I)):
        # An explicit local-network lookup is a host operation. The existing
        # router already chooses host_shell; neither its schema nor bridge
        # availability grants Bash/Python alongside this request.
        return SemanticIntent(frozenset({"shell_files"}), frozenset({"host_shell"}), None)
    return SemanticIntent(families, selected, required_read_operation_for_request(request_text, history))


def create_request_authority(request_text, *, owner=None, session_id=None, workspace=None,
                             history=(), policy=None, active_document=False,
                             image_attachment=False, capabilities=None, client_runtime_context=None):
    """Deterministic server policy over semantic facts, never schema inventory."""
    if not isinstance(request_text, str):
        raise TypeError("Authority requires trusted request text")
    intent = interpret_request(request_text, history=history, active_document=active_document,
                               workspace=workspace, image_attachment=image_attachment)
    families = intent.capabilities
    if capabilities is not None:
        families |= frozenset(capabilities)
    tools = set().union(*(FAMILY_TOOLS.get(f, ()) for f in families))
    selected = intent.selected_tools
    if selected is not None:
        tools = tools & set(selected) if families else set(selected)
        if tools & {"web_search", "web_fetch"}:
            tools.add("private_browser")
    operation = intent.required_read
    if operation is not None and canonical_tool(operation.tool) in {canonical_tool(n) for n in tools}:
        tools = {canonical_tool(operation.tool)}
    else:
        operation = None
    grants = []
    for name in sorted(tools | {"ask_user", "update_plan"}):
        name = canonical_tool(name)
        actions = inputs = None
        if name == "private_browser":
            actions = _BROWSER_READ_ACTIONS
            # Explicit interaction intent admits its operation class. A
            # browser offered only as static-Web fallback gets no such grant.
            if re.search(r"\b(?:browser|browse|private_browser)\b", request_text, re.I):
                actions |= frozenset(action for action in ("click", "fill", "type", "press", "evaluate", "select")
                                     if re.search(r"\b" + action + r"\b", request_text, re.I))
        if operation is not None and name == canonical_tool(operation.tool):
            inputs = frozenset({ExactOperation.normalize(name, _json(dict(operation.args))).input})
        grants.append(OperationGrant(name, actions, inputs))
    authority = RequestAuthority(uuid4().hex, _owner(owner), str(session_id or ""),
                                 str(workspace or ""), tuple(grants))
    if client_runtime_context is not None:
        from src.agent_runtime.remote_resources import seal_backends
        authority = replace(authority, backend_resources=seal_backends(
            (g.tool for g in authority.grants), context=client_runtime_context, owner=authority.owner))
    return authority.restrict(policy or build_effective_tool_policy(last_user_message=request_text))


_ACTIVE: ContextVar[RequestAuthority | None] = ContextVar("request_authority", default=None)
MISSING_AUTHORITY = object()


def active_request_authority():
    return _ACTIVE.get()


def is_internal_tool_request(request):
    """HTTP authentication/owner attribution does not make a tool payload user intent."""
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    return (request.headers.get(INTERNAL_TOOL_HEADER) == INTERNAL_TOOL_TOKEN
            or getattr(request.state, "current_user", None) == "internal-tool")


def require_user_approval_request(request):
    if is_internal_tool_request(request):
        from fastapi import HTTPException
        raise HTTPException(403, "Tool requests cannot submit user approval decisions.")


def request_authority_for_http(request, request_text, **context):
    """Known tool loopback is a continuation, never a fresh user grant source."""
    if is_internal_tool_request(request):
        return RequestAuthority.empty(owner=context.get("owner"),
            session_id=context.get("session_id"), workspace=context.get("workspace")).restrict(context.get("policy"))
    return create_request_authority(request_text, **context)


@contextmanager
def bind_request_authority(authority):
    if not isinstance(authority, RequestAuthority):
        raise TypeError("Authority must be server-owned RequestAuthority")
    parent = _ACTIVE.get()
    authority = parent.intersect(authority) if parent is not None else authority
    token = _ACTIVE.set(authority)
    try:
        yield authority
    finally:
        _ACTIVE.reset(token)


def _request_text(messages):
    for message in reversed(messages or ()):
        metadata = message.get("metadata") or {}
        if (message.get("role") != "user" or metadata.get("trusted") is False
                or metadata.get("tool_gate_untrusted")):
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content
                             if isinstance(p, dict) and p.get("type") == "text")
    return ""


def with_request_authority(func):
    """Bind once per invocation; model rounds/fallbacks never recreate grants."""
    call_signature = signature(func)
    @wraps(func)
    async def wrapped(*args, **kwargs):
        bound = call_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        parameters = bound.arguments
        parent = active_request_authority()
        authority = parameters.get("request_authority", MISSING_AUTHORITY)
        if authority is MISSING_AUTHORITY:
            approval = parameters.get("exact_approval")
            if parent is not None:
                authority = parent
            elif approval is not None:
                authority = approval.pending.request_authority or RequestAuthority.empty(
                    owner=parameters.get("owner"), session_id=parameters.get("session_id"),
                    workspace=parameters.get("workspace"))
            elif (parameters.get("_parent_run_id") or parameters.get("_is_teacher_run")
                  or parameters.get("workload") == "background"):
                authority = RequestAuthority.empty(owner=parameters.get("owner"),
                    session_id=parameters.get("session_id"), workspace=parameters.get("workspace"))
            else:
                authority = create_request_authority(_request_text(parameters.get("messages")),
                    owner=parameters.get("owner"), session_id=parameters.get("session_id"),
                    workspace=parameters.get("workspace"),
                    history=getattr(parameters.get("history_session"), "history", ()) or (),
                    active_document=bool(parameters.get("active_document")),
                    client_runtime_context=parameters.get("client_runtime_context"))
        if not isinstance(authority, RequestAuthority):
            raise TypeError("Missing or malformed server request authority")
        if parent is None and parameters.get("exact_approval") is not None:
            authority = replace(authority, inherited=False)
        authority = authority.restrict(parameters.get("tool_policy"), parameters.get("disabled_tools"))
        with bind_request_authority(authority) as effective:
            if "request_authority" in parameters:
                parameters["request_authority"] = effective
            async with aclosing(func(*bound.args, **bound.kwargs)) as stream:
                async for chunk in stream:
                    yield chunk
    return wrapped


def task_operation(task_type, action, prompt):
    if task_type == "action":
        tool = ("bash" if action in {"run_local", "run_script", "ssh_command"}
                else "serve_model" if action == "cookbook_serve" else "scheduled__" + str(action))
        return ExactOperation.normalize(tool, str(prompt or ""))
    if task_type == "research":
        return ExactOperation.normalize("trigger_research", str(prompt or ""))
    return None


def seal_task_authority(prompt, task_type, action, *, owner=None, parent_authority=MISSING_AUTHORITY):
    """Only direct ingress grants; a model-created task is capped by its parent."""
    operation = task_operation(task_type, action, prompt)
    authority = create_request_authority(str(prompt or ""), owner=owner)
    if operation is not None:
        authority = replace(authority, grants=(OperationGrant(operation.tool,
            inputs=frozenset({operation.input})),))
        if task_type == "action" and action == "cookbook_serve":
            # The direct admin scheduling ingress selects the native Cookbook
            # producer. Restore never infers this from task names/availability.
            # Any model-created task still intersects with its parent's ceiling.
            backend = NativeBackendResource("serve_model")
            authority = replace(authority, backend_resources=tuple(dict.fromkeys(
                (*authority.backend_resources, backend))))
    parent = active_request_authority() if parent_authority is MISSING_AUTHORITY else parent_authority
    if parent_authority is None:
        parent = RequestAuthority.empty(owner=owner)
    if parent is not None:
        authority = parent.intersect(replace(authority, session_id=parent.session_id,
                                            workspace=parent.workspace,
                                            resource_roots=parent.resource_roots,
                                            backend_resources=parent.backend_resources,
                                            owned_scopes=parent.owned_scopes,
                                            launch_scopes=parent.launch_scopes,
                                            process_resources=parent.process_resources,
                                            job_resources=parent.job_resources,
                                            browser_sessions=parent.browser_sessions,
                                            browser_pages=parent.browser_pages))
    return _json({"task_input": [prompt, task_type, action], "authority": authority.to_dict()})


def restore_task_authority(snapshot, prompt, task_type, action, *, owner=None, session_id=None):
    try:
        value = json.loads(snapshot)
        if value["task_input"] != [prompt, task_type, action]:
            raise ValueError("Scheduled request changed")
        return RequestAuthority.from_dict(value["authority"]).continuation(owner=owner, session_id=session_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        return RequestAuthority.empty(owner=owner, session_id=session_id)


def _background_path(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise ValueError("Invalid background authority identity")
    from src.bg_jobs import _JOBS_DIR
    return Path(_JOBS_DIR) / (job_id + ".authority.json")


def save_background_authority(job_id, authority, *, resource=None):
    from core.atomic_io import atomic_write_json
    if resource is None or resource.job_id != job_id:
        raise ValueError("Background authority requires exact job linkage")
    atomic_write_json(_background_path(job_id), {"authority": authority.to_dict(), "job": resource.to_dict()})


def restore_background_authority(job_id, *, owner=None, session_id=None):
    try:
        value = json.loads(_background_path(job_id).read_text())
        resource = BackgroundJobResource.from_dict(value["job"])
        from src.agent_runtime.process_resources import validate_job
        validate_job(resource)
        authority = RequestAuthority.from_dict(value["authority"])
        if (resource.job_id, resource.owner, resource.thread_id, resource.request_id) != (
                job_id, authority.owner, authority.session_id, authority.request_id):
            raise ValueError("Background authority linkage changed")
        if authority.session_id != str(session_id or ""):
            raise ValueError("Background session changed")
        return authority.continuation(owner=owner, session_id=session_id)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return RequestAuthority.empty(owner=owner, session_id=session_id)
