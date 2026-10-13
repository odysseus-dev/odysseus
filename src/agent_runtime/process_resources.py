"""Process/job admission. Lifecycle mechanics remain in process_lifecycle.

Only trusted launch producers publish observations. Persisted legacy records
are never enrolled by looking at their PID. Receipts identify boundaries, not
application authority. Resource snapshots contain no command or environment.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4
from core.atomic_io import store_transaction

from src.agent_runtime.resources import (
    BackgroundJobResource, NativeBackendResource, ProcessLaunchResource,
    ProcessLaunchScope, ProcessResource, ResourceIdentityError,
)
from src.constants import PROCESS_RESOURCES_DIR

_LAUNCH_DIR = Path(PROCESS_RESOURCES_DIR)
LAUNCH_TOOLS = frozenset({"bash", "python"})
JOB_TOOL = "manage_bg_jobs"
_ACTIVE = ContextVar("process_resource_operation", default=None)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _thread(authority):
    return authority.session_id or "request:" + authority.request_id


def launch_path(generation):
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ResourceIdentityError("Malformed launch generation")
    return _LAUNCH_DIR / (generation + ".json")


def seal_launch_scopes(authority):
    return tuple(seal_launch_scope(backend, root)
                 for backend in authority.backend_resources
                 if isinstance(backend, NativeBackendResource) and backend.tool_id in LAUNCH_TOOLS
                 for root in authority.resource_roots)


def seal_launch_scope(backend, root, *, env=None):
    from src.agent_tools.subprocess_tools import _owned_spec
    from src.tool_execution import _agent_subprocess_env
    from src.agent_runtime.resources import PathObservation, FileObjectIdentity
    env = _agent_subprocess_env() if env is None else env
    extra = tuple(Path(p).resolve().as_posix() for p in str(env.get("ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES", "")).split(os.pathsep)
                  if p and os.path.isabs(p)) if backend.tool_id == "python" else ()
    spec = _owned_spec(root.path, env, 3600, extra)
    return ProcessLaunchScope(backend, root, spec.required,
        tuple(PathObservation(str(Path(p).resolve()), FileObjectIdentity.observe(Path(p).resolve())) for p in spec.readonly_extra),
        spec.network, spec.wall_clock_s)


def validate_launch_spec(launch, spec):
    scope = launch.scope
    scope.validate()
    if (spec.workspace != scope.root.path or spec.required != scope.required or spec.network != scope.network
            or spec.wall_clock_s > scope.max_runtime_s or spec.writable_extra
            or tuple(spec.readonly_extra) != tuple(r.path for r in scope.runtime_roots)):
        raise ResourceIdentityError("Producer launch boundary exceeds the sealed reservation")


def job_from_record(record):
    if not isinstance(record, dict):
        raise ResourceIdentityError("Missing authoritative job")
    try:
        resource = BackgroundJobResource.from_dict(record["resource_identity"])
        if (resource.namespace != "native:bg_jobs"
                or (record["id"], record["session_id"], record["containment_id"])
                != (resource.job_id, resource.thread_id, resource.containment_id)):
            raise ValueError("Job linkage changed")
        supervisor = next(p for p in resource.processes if p.role == "supervisor")
        if (record.get("pid"), record.get("start_token"), record.get("pgid")) != (
                supervisor.identity.pid, supervisor.identity.start_token, supervisor.identity.pgid):
            raise ValueError("Supervisor linkage changed")
        launch = ProcessLaunchResource.from_dict(record["launch_resource"])
        if (launch.generation, launch.owner, launch.request_id, launch.thread_id) != (
                resource.generation, resource.owner, resource.request_id, resource.thread_id):
            raise ValueError("Launch/job linkage changed")
        return resource
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError) as error:
        raise ResourceIdentityError("Malformed or unowned background job") from error


def validate_job(resource, *, mutation=False):
    try:
        return _validate_job(resource, mutation=mutation)
    except ResourceIdentityError:
        raise
    except (ValueError, TypeError, OSError, KeyError, AttributeError) as error:
        raise ResourceIdentityError("Background job linkage is missing or malformed") from error


def validate_job_receipt(resource, receipt):
    from src import containment
    supervisor = resource.processes[0]
    if (not isinstance(receipt, dict) or receipt.get("id") != resource.containment_id
            or receipt.get("launch_generation") != resource.generation
            or receipt.get("owner") != "bg:" + resource.thread_id
            or (receipt.get("supervisor_pid"), receipt.get("supervisor_token")) !=
            (supervisor.identity.pid, supervisor.identity.start_token)
            or receipt.get("mechanism") not in {m.name for m in containment.MECHANISMS}
            or receipt.get("external") is True):
        raise ResourceIdentityError("Containment receipt linkage changed")


def _validate_job(resource, *, mutation=False):
    from src import bg_jobs, containment
    if not isinstance(resource, BackgroundJobResource):
        raise ResourceIdentityError("Missing exact background job identity")
    record = bg_jobs.peek(resource.job_id)
    if job_from_record(record) != resource:
        raise ResourceIdentityError("Background job resource changed")
    if record.get("status") not in {"running", "done", "failed"}:
        raise ResourceIdentityError("Unknown job lifecycle")
    launch = ProcessLaunchResource.from_dict(record["launch_resource"])
    persisted = json.loads(launch_path(resource.generation).read_text())
    if (persisted.get("launch") != launch.to_dict()
            or persisted.get("job") != resource.to_dict()
            or persisted.get("containment_id") != resource.containment_id):
        raise ResourceIdentityError("Job/launch publication changed")
    sidecar = json.loads((bg_jobs._JOBS_DIR / (resource.job_id + ".authority.json")).read_text())
    origin = persisted.get("authority", {})
    if (sidecar.get("job") != resource.to_dict() or sidecar.get("authority") != origin
            or (origin.get("owner"), origin.get("request_id"), origin.get("session_id")) !=
            (resource.owner, resource.request_id, resource.thread_id)):
        raise ResourceIdentityError("Background authority linkage changed")
    receipt = containment._load_records().get(resource.containment_id)
    # Lifecycle receipts have a shorter retention than job results. A finished
    # exact generation needs only its durable application linkage for history;
    # it never regains signalling authority when its receipt has been pruned.
    historical = record.get("status") in {"done", "failed"}
    if receipt is None and not historical:
        raise ResourceIdentityError("Missing active containment receipt")
    if receipt is not None:
        validate_job_receipt(resource, receipt)
    if record.get("status") == "running":
        for process in resource.processes:
            try:
                process.validate()
            except ResourceIdentityError:
                # Publication can precede store reconciliation. That exact
                # completed generation is readable, but never signallable.
                if mutation or not Path(record["exit_path"]).is_file():
                    raise
                report = json.loads(Path(record["result_path"]).read_text())
                if report.get("resource_identity") != resource.to_dict() or report.get("containment", {}).get("id") != resource.containment_id:
                    raise ResourceIdentityError("Historical result linkage changed")
    # A completed record is readable history, never a new process observation.
    return record


def seal_jobs(authority):
    if not any(g.tool == JOB_TOOL for g in authority.grants) or not authority.session_id:
        return ()
    from src import bg_jobs
    admitted = []
    for record in bg_jobs._load().values():
        try:
            resource = job_from_record(record)
            if (resource.owner, resource.thread_id) == (authority.owner, authority.session_id):
                validate_job(resource)
                admitted.append(resource)
        except (ValueError, TypeError, OSError, RuntimeError):
            continue
    return tuple(admitted)


def intersect_observed(parent, child, validate):
    # Validate both sides before equality. Seeing a replacement cannot renew a
    # stale parent observation, even when the child has just sealed it.
    # Stale/dead/unverifiable resources on EITHER side are conservatively
    # excluded from the resulting authority — a normal process exit must not
    # crash child authority intersection.
    live_parent = []
    for resource in parent:
        try:
            validate(resource)
            live_parent.append(resource)
        except ResourceIdentityError:
            continue
    live_child = set()
    for resource in child:
        try:
            validate(resource)
            live_child.add(resource)
        except ResourceIdentityError:
            continue
    return tuple(resource for resource in live_parent if resource in live_child)


def intersect_launch_scopes(parent, child):
    from src.agent_runtime.resources import FilesystemResource
    for scope in (*parent, *child):
        scope.validate()
    narrowed = []
    for left in parent:
        for right in child:
            if (left.backend != right.backend or not left.required <= right.required
                    or right.max_runtime_s > left.max_runtime_s
                    or not set(right.runtime_roots) <= set(left.runtime_roots)
                    or (left.network == "none" and right.network != "none")):
                continue
            if Path(right.root.path).is_relative_to(left.root.path):
                observation = FilesystemResource.resolve(left.root, right.root.path)
                if observation.identity == right.root.identity:
                    narrowed.append(right)
    return tuple(dict.fromkeys(narrowed))


class _LaunchUse:
    """Non-persisted one-use producer reservation, shared by approval copies."""
    def __init__(self):
        self.used = False
        self.lock = threading.Lock()

    def claim(self):
        with self.lock:
            if self.used:
                raise ResourceIdentityError("Launch reservation has already been used")
            self.used = True


@dataclass(frozen=True)
class BoundProcessOperation:
    operation: object
    request_id: str
    owner: str
    thread_id: str
    launch: ProcessLaunchResource | None = None
    jobs: tuple[BackgroundJobResource, ...] = ()
    processes: tuple[ProcessResource, ...] = ()
    exact_approval: object | None = None
    _launch_use: _LaunchUse = field(default_factory=_LaunchUse, compare=False, repr=False)

    def __post_init__(self):
        from src.agent_runtime.authority import ExactOperation
        if (not isinstance(self.operation, ExactOperation) or not isinstance(self.request_id, str) or not self.request_id
                or not isinstance(self.owner, str) or not isinstance(self.thread_id, str) or not self.thread_id
                or (self.launch is not None and not isinstance(self.launch, ProcessLaunchResource))
                or not isinstance(self.jobs, tuple) or any(not isinstance(j, BackgroundJobResource) for j in self.jobs)
                or not isinstance(self.processes, tuple) or any(not isinstance(p, ProcessResource) for p in self.processes)):
            raise ValueError("Malformed process-bound operation")
        if self.launch is not None and (
                (self.launch.owner, self.launch.request_id, self.launch.thread_id, self.launch.tool, self.launch.input_digest)
                != (self.owner, self.request_id, self.thread_id, self.operation.tool, digest(self.operation.input))):
            raise ValueError("Launch operation/application binding changed")
        if any((r.owner, r.thread_id) != (self.owner, self.thread_id) for r in (*self.jobs, *self.processes)):
            raise ValueError("Observed resource application binding changed")

    def validate(self):
        if self.launch is not None:
            self.launch.validate()
            if self._launch_use.used:
                raise ResourceIdentityError("Launch reservation has already been used")
        for job in self.jobs:
            validate_job(job, mutation=self.operation.action in {"kill", "stop", "cancel", "terminate", "ack"})
        for process in self.processes:
            process.validate()

    def to_dict(self):
        return {"tool": self.operation.transport_tool, "input_digest": digest(self.operation.input),
                "request_id": self.request_id, "owner": self.owner, "thread_id": self.thread_id,
                "launch": self.launch.to_dict() if self.launch else None,
                "jobs": [r.to_dict() for r in self.jobs], "processes": [r.to_dict() for r in self.processes]}


def needs_process_binding(operation, backend):
    return isinstance(backend, NativeBackendResource) and operation.tool in LAUNCH_TOOLS | {JOB_TOOL}


def resolve_process_operation(authority, operation, backend, *, approved=None, exact_admission=False):
    if not needs_process_binding(operation, backend):
        raise ResourceIdentityError("No native process adapter for this backend")
    if approved is not None:
        if (approved.operation != operation or (approved.request_id, approved.owner, approved.thread_id)
                != (authority.request_id, authority.owner, _thread(authority))):
            raise ResourceIdentityError("Approved process operation binding changed")
        bound = approved
    elif operation.tool in LAUNCH_TOOLS:
        scopes = [s for s in authority.launch_scopes if s.backend == backend]
        if len(scopes) != 1:
            raise ResourceIdentityError("Process creation requires a sealed workspace and launch scope")
        launch = ProcessLaunchResource("native:containment", authority.owner, authority.request_id,
            _thread(authority), uuid4().hex, operation.tool, digest(operation.input), scopes[0],
            digest(json.dumps(authority.to_dict(), sort_keys=True)))
        bound = BoundProcessOperation(operation, authority.request_id, authority.owner, _thread(authority), launch)
    else:
        try:
            args = json.loads(operation.input)
            action = str(args.get("action", "list")).strip().lower()
            job_id = args.get("job_id", args.get("id", ""))
        except (ValueError, TypeError, AttributeError) as error:
            raise ResourceIdentityError("Malformed job operation") from error
        if action in {"list", "ls", "jobs"}:
            jobs = authority.job_resources
        elif action in {"output", "get", "read", "tail", "status", "show", "kill", "stop", "cancel", "terminate", "ack"}:
            if not isinstance(job_id, str) or not job_id:
                raise ResourceIdentityError("An exact job selector is required")
            jobs = tuple(r for r in authority.job_resources if r.job_id == job_id)
            if len(jobs) != 1:
                raise ResourceIdentityError("Job is outside admitted resource scope")
        else:
            raise ResourceIdentityError("Unsupported job operation")
        bound = BoundProcessOperation(operation, authority.request_id, authority.owner, _thread(authority), jobs=jobs)
    if not (approved is not None and exact_admission and not authority.inherited):
        if bound.launch is not None and bound.launch.scope not in authority.launch_scopes:
            raise ResourceIdentityError("Launch exceeds inherited creation scope")
        if any(j not in authority.job_resources for j in bound.jobs) or any(p not in authority.process_resources for p in bound.processes):
            raise ResourceIdentityError("Process/job exceeds inherited resource scope")
    if bound.launch is not None and bound.launch.scope.backend != backend:
        raise ResourceIdentityError("Launch backend changed")
    bound.validate()
    return bound


def active_process_operation():
    return _ACTIVE.get()


@contextmanager
def bind_process_operation(operation):
    if operation is not None and not isinstance(operation, BoundProcessOperation):
        raise TypeError("Process operation must be server-owned")
    if operation is not None:
        operation.validate()
        if operation.launch is not None:
            # One fresh authoritative scan for each execution binding. Resolution
            # and producer entry retain cheap exact identity checks; no scan is
            # reused across independent bindings or persisted in an approval.
            guard_launch_workspace(operation.launch.scope.root)
    token = _ACTIVE.set(operation)
    try:
        yield operation
    finally:
        _ACTIVE.reset(token)


def require_launch(tool, *, cwd, content=None):
    bound = active_process_operation()
    if bound is None or bound.launch is None or bound.operation.tool != tool:
        raise ResourceIdentityError("Native process producer has no bound launch reservation")
    require_process_admission(bound)
    bound.validate()
    if Path(cwd).resolve() != Path(bound.launch.scope.root.path):
        raise ResourceIdentityError("Launch workspace changed")
    if content is not None and content.strip() != bound.operation.input.strip():
        raise ResourceIdentityError("Launch operation changed at producer entry")
    return bound.launch


def require_process_admission(bound):
    from src.agent_runtime.authority import active_request_authority
    authority = active_request_authority()
    if authority is None or (authority.owner, authority.request_id, _thread(authority)) != (
            bound.owner, bound.request_id, bound.thread_id):
        raise ResourceIdentityError("Producer application authority changed")
    if not authority.permits(bound.operation):
        approval = bound.exact_approval
        if (authority.inherited or approval is None or not approval._claimed
                or approval.pending.process_operation is None
                or approval.pending.process_operation.to_dict() != bound.to_dict()):
            raise ResourceIdentityError("Producer operation has no request admission or exact claim")


def guard_launch_workspace(root):
    """Reject a boundary containing execution control state or its aliases.

    These are pathname/inode observations, not an atomic kernel access policy.
    They do not claim freedom from concurrent link replacement after checking.
    """
    from src import bg_jobs, containment, constants
    from src import browser_identity
    from src.agent_runtime.resources import _control_plane_path, _control_plane_snapshot
    control = (Path(bg_jobs._STORE), Path(bg_jobs._JOBS_DIR), containment._store_path(), _LAUNCH_DIR,
               Path(constants.BROWSER_RESOURCES_DIR),
               browser_identity.STATE_ROOT,
               Path(constants.APP_DB), Path(constants.AUTH_FILE), Path(constants.SETTINGS_FILE))
    base = Path(root.path)
    if any(Path(p).resolve().is_relative_to(base) for p in control):
        raise ResourceIdentityError("Launch boundary contains server control state")
    def unresolved(error):
        raise ResourceIdentityError("Launch workspace cannot be inspected") from error
    snapshot = None
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=unresolved):
        for name in (*dirs, *files):
            path = Path(directory) / name
            info = path.lstat()
            if path.is_symlink() or info.st_nlink > 1:
                if snapshot is None:
                    snapshot = _control_plane_snapshot()
                if _control_plane_path(str(path.resolve()), snapshot=snapshot):
                    raise ResourceIdentityError("Launch boundary aliases server control state")


@store_transaction(lambda: _LAUNCH_DIR / "publication")
def publish_launch(launch, authority, containment_id, *, job=None, processes=()):
    from core.atomic_io import atomic_write_json
    launch.validate()
    if authority is None or (authority.owner, authority.request_id) != (launch.owner, launch.request_id):
        raise ResourceIdentityError("Launch authority linkage changed")
    path = launch_path(launch.generation)
    if path.exists():
        raise ResourceIdentityError("Launch reservation has already been used")
    bound = active_process_operation()
    if bound is not None:
        if bound.launch != launch:
            raise ResourceIdentityError("Publication differs from the bound launch")
        bound._launch_use.claim()
    atomic_write_json(path, {"launch": launch.to_dict(), "authority": authority.to_dict(),
                          "containment_id": containment_id, "job": job.to_dict() if job else None,
                          "processes": [p.to_dict() for p in processes]})


@store_transaction(lambda: _LAUNCH_DIR / "publication")
def retire_launch(launch, containment_id, *, job=None):
    """Remove only this exact producer publication; never a replacement.

    Callers establish the lifetime end (verified foreground teardown, or exact
    background history pruning). Missing/malformed/replaced state is retained.
    One-use launch reservations live in the bound operation, not this file.
    """
    path = launch_path(launch.generation)
    try:
        published = json.loads(path.read_text())
    except FileNotFoundError:
        return False
    if (not isinstance(published, dict)
            or published.get("launch") != launch.to_dict()
            or published.get("containment_id") != containment_id
            or published.get("job") != (job.to_dict() if job else None)):
        return False
    path.unlink()
    return True


@store_transaction(lambda: _LAUNCH_DIR / "publication")
def prune_foreground_publications():
    """Startup-only recovery: retire foreground generations without a caller.

    A dead/replaced manager cannot resume attachment. A missing receipt also
    makes attachment impossible; publication cannot reconstruct that receipt.
    Its process tree still belongs to containment recovery; deleting a
    publication never signals or asserts tree death. Live/unverifiable managers
    retain publication even after child teardown: attachment may still need it.
    Background history stays intact.
    """
    from src import containment
    from src import process_ownership
    try:
        receipts = json.loads(containment._store_path().read_text())
    except FileNotFoundError:
        receipts = {}
    except (OSError, ValueError):
        return 0  # Unreadable state is not evidence that consumers are gone.
    if not isinstance(receipts, dict) or any(not isinstance(r, dict) for r in receipts.values()):
        return 0
    retired = 0
    for path in _LAUNCH_DIR.glob("*.json"):
        try:
            published = json.loads(path.read_text())
            launch = ProcessLaunchResource.from_dict(published["launch"])
            receipt = receipts.get(published["containment_id"])
            abandoned = (receipt is not None
                and type(receipt.get("manager_pid")) is int and receipt["manager_pid"] > 0
                and isinstance(receipt.get("manager_token"), str) and bool(receipt["manager_token"])
                and process_ownership.verify(receipt["manager_pid"], receipt["manager_token"]) in {
                    process_ownership.GONE, process_ownership.FOREIGN})
            if (published.get("job") is None and path == launch_path(launch.generation)
                    and (receipt is None or (
                        receipt.get("launch_generation") == launch.generation
                        and receipt.get("id") == published["containment_id"]
                        and abandoned))):
                # Already under the publication lock; no nested file lock.
                path.unlink()
                retired += 1
        except (ValueError, TypeError, KeyError, OSError):
            continue
    return retired


@store_transaction(lambda: _LAUNCH_DIR / "publication")
def attach_containment_processes(launch, containment_id):
    """Attach producer-frozen lifecycle records; never capture a current PID."""
    from src import containment
    from src.process_lifecycle import ProcessIdentity
    record = containment._load_records().get(containment_id, {})
    path = launch_path(launch.generation)
    published = json.loads(path.read_text())
    if (published.get("launch") != launch.to_dict() or published.get("containment_id") != containment_id
            or record.get("id") != containment_id or record.get("launch_generation") != launch.generation
            or record.get("workspace") != launch.scope.root.path):
        raise ResourceIdentityError("Launch/receipt changed during publication")
    processes = []
    for role, pid_key, token_key, group_key in (("leader", "pid", "start_token", "pgid"),
            ("namespace_init", "namespace_pid", "namespace_start_token", None)):
        pid = record.get(pid_key)
        token = record.get(token_key)
        if not pid or not token:
            continue
        processes.append(ProcessResource("native:containment", launch.owner, launch.request_id,
            launch.thread_id, ProcessIdentity(pid, token, record.get(group_key) if group_key else None),
            role, "", containment_id))
    from core.atomic_io import atomic_write_json
    published["processes"] = [p.to_dict() for p in processes]
    atomic_write_json(path, published)


def expected_job(job_id, *, action):
    bound = active_process_operation()
    if bound is None or bound.operation.tool != JOB_TOOL:
        raise ResourceIdentityError("Job producer has no bound operation")
    require_process_admission(bound)
    # The caller's actual action must agree with the normalized proposal.
    args = json.loads(bound.operation.input)
    proposed = str(args.get("action", "list")).strip().lower()
    if action != proposed:
        raise ResourceIdentityError("Job action changed at producer entry")
    target = next((j for j in bound.jobs if j.job_id == job_id), None)
    if target is None:
        raise ResourceIdentityError("Job selector is outside the bound operation")
    validate_job(target, mutation=action in {"kill", "stop", "cancel", "terminate", "ack"})
    return target
