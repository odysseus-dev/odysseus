"""Server-boundary adapters from admitted Wave 3 bindings to effect records.

Runs only inside the dispatcher's existing admission scope: the bindings read
here are the contextvars the dispatcher bound after authority, resource and
approval checks. Nothing here admits, resolves, broadens or re-derives a
resource. Observations are recorded only for operations that were themselves
admitted reads of the exact bound resource; evidence bookkeeping never performs
a read that the operation was not already admitted to perform.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import io
import json
import logging
import os
import stat
from typing import Any

from src.agent_runtime.effects import (
    CleanupState, Coverage, EffectClaim, ExecutionOutcome, Impact, ObservationMechanism, OperationRef,
    Postcondition, Predicate, ProducerFacts, ResourceKind, ResourceRef, producer_facts, resource_ref,
)


_FILESYSTEM_READS = frozenset({"read_file", "ls", "glob", "grep"})
_JOB_READS = frozenset({"list", "ls", "jobs", "output", "get", "read", "tail", "status", "show"})
_OWNED_READS = frozenset({"vault_get", "vault_search", "list_sessions", "search_chats"})
_JOB_SETTLED = {"done", "failed"}
# Largest pre-state an edit/patch postcondition is derived from.
_PRE_STATE_LIMIT = 10 * 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass
class DispatchCapture:
    """The admitted bindings that were live when the backend was invoked."""

    filesystem: Any = None
    owned: Any = None
    process: Any = None
    backend: Any = None
    browser: Any = None
    claim: EffectClaim | None = None
    read_only: bool = False
    paths: tuple[str, ...] = field(default_factory=tuple)


def capture_dispatch() -> DispatchCapture:
    from src.agent_runtime.owned_resources import active_owned_operation
    from src.agent_runtime.process_resources import active_process_operation
    from src.agent_runtime.remote_resources import active_backend_operation
    from src.agent_runtime.resource_binding import active_resource_operation
    import sys
    browser_module = sys.modules.get("src.browser_identity")
    browser = browser_module._ACTIVE.get() if browser_module is not None else None
    return DispatchCapture(active_resource_operation(), active_owned_operation(), active_process_operation(),
                           active_backend_operation(), browser)


def _exact_operation(capture: DispatchCapture):
    for bound in (capture.filesystem, capture.owned, capture.process, capture.browser):
        if bound is not None:
            return bound.operation, getattr(bound, "execution_input", None), getattr(bound, "request_id", "")
    return None, None, ""


def _operation(capture: DispatchCapture, action: Any) -> OperationRef:
    operation, execution_input, request_id = _exact_operation(capture)
    if operation is not None:
        return OperationRef.from_exact(operation, execution_input, request_id)
    backend = capture.backend
    # Unbound tools still name their final normalized dispatcher input.
    digest = hashlib.sha256(str(action.arguments).encode("utf-8", errors="replace")).hexdigest()
    return OperationRef(str(action.tool) or "unknown", "", digest,
                        getattr(backend, "request_id", "") if backend is not None else "")


def _write_file_digest(execution_input: str, resource: Any) -> str:
    """The exact bytes WriteFileTool commits for this admitted input, or ''."""
    from src.agent_tools.filesystem_tools import _unwrap_fenced_source_body, _write_file_text
    try:
        args = json.loads(execution_input)
    except (TypeError, ValueError):
        return ""
    body = args.get("content") if isinstance(args, dict) else None
    if not isinstance(body, str):
        return ""
    original = "" if resource.identity is None else _pre_state_text(resource, newline="")
    if original is None:
        return ""
    body = _unwrap_fenced_source_body(body, resource.path)
    return hashlib.sha256(_write_file_text(original, body).encode("utf-8")).hexdigest()


def _pre_state_text(resource: Any, *, newline: str | None) -> str | None:
    """The exact bound file decoded as its producer decodes it, or None.

    Reads only the admitted target binding (identity-checked). An oversized,
    replaced or undecodable file yields None: a truncated read must never
    stand in for the whole pre-state.
    """
    data = _read_whole(resource, _PRE_STATE_LIMIT).data
    if data is None or len(data) > _PRE_STATE_LIMIT:
        return None
    try:
        return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", newline=newline).read()
    except (UnicodeDecodeError, ValueError):
        return None


def _edit_file_digest(execution_input: str, resource: Any) -> str:
    """SHA-256 of the exact bytes edit_file writes for this admitted input, or ''."""
    from src.agent_tools.filesystem_tools import _edit_file_text
    try:
        args = json.loads(execution_input)
    except (TypeError, ValueError):
        return ""
    if not isinstance(args, dict):
        return ""
    old, new, replace_all = args.get("old_string"), args.get("new_string"), args.get("replace_all", False)
    if not isinstance(old, str) or not old or not isinstance(new, str) or type(replace_all) is not bool or old == new:
        return ""
    # edit_file reads with newline="" and writes with newline="": no translation.
    original = _pre_state_text(resource, newline="")
    if original is None:
        return ""
    updated, _ = _edit_file_text(original, old, new, replace_all)
    return "" if updated is None else hashlib.sha256(updated.encode("utf-8")).hexdigest()


def _patch_update_digest(op: dict, resource: Any) -> str:
    """SHA-256 of the exact bytes apply_patch writes for one update, or ''."""
    from src.agent_tools.filesystem_tools import _patch_file_text
    original = _pre_state_text(resource, newline="")
    if original is None:
        return ""
    try:
        updated = _patch_file_text(original, op["hunks"], op["path"])
    except ValueError:
        return ""
    return hashlib.sha256(updated.encode("utf-8")).hexdigest()


def _filesystem_scope(bound: Any) -> tuple[tuple[ResourceRef, ...], tuple[Postcondition, ...]]:
    """Exact bindings and the requested post-state of each mutation target.

    Each postcondition is the exact content (or absence) the producer's own
    transformation yields from the admitted pre-state, so an unrelated change
    can never satisfy it. When any target's requested state cannot be derived
    the claim carries no postcondition at all and stays UNVERIFIED: a partial
    set would let the derivable targets verify the whole operation.
    """
    from src.agent_tools.filesystem_tools import _parse_agent_patch
    tool = bound.operation.tool
    refs = tuple(resource_ref(b.resource, b.role) for b in bound.bindings)
    obligations: list[Postcondition] = []
    if tool == "write_file":
        expected = _write_file_digest(bound.execution_input, bound.bindings[0].resource)
        intent = bound.write_intent
        if intent is not None:
            from src.agent_tools.filesystem_tools import _unwrap_fenced_source_body
            resource = bound.bindings[0].resource
            body = _unwrap_fenced_source_body(intent[1], resource.path)
            if not body.strip() and not intent[3] and resource.identity is not None:
                pre_state = _pre_state_text(resource, newline=None)
                expected = hashlib.sha256(b"").hexdigest() if pre_state == "" else ""

        if not expected:
            return refs, ()
        obligations.append(Postcondition(refs[0], Predicate.CONTENT_SHA256, expected))
    elif tool == "edit_file":
        expected = _edit_file_digest(bound.execution_input, bound.bindings[0].resource)
        if not expected:
            return refs, ()
        obligations.append(Postcondition(refs[0], Predicate.CONTENT_SHA256, expected))
    elif tool == "apply_patch":
        ops = _parse_agent_patch(json.loads(bound.execution_input)["patch_text"])
        if len(ops) != len(bound.bindings):
            return refs, ()
        for op, binding, ref in zip(ops, bound.bindings, refs):
            if op["kind"] == "add":
                obligations.append(Postcondition(ref, Predicate.CONTENT_SHA256,
                                                 hashlib.sha256(op["content"].encode("utf-8")).hexdigest()))
            elif op["kind"] == "delete":
                obligations.append(Postcondition(ref, Predicate.ABSENT))
            else:
                expected = _patch_update_digest(op, binding.resource)
                if not expected:
                    return refs, ()
                obligations.append(Postcondition(ref, Predicate.CONTENT_SHA256, expected))
    return refs, tuple(obligations)


def classify(capture: DispatchCapture) -> dict[str, Any] | None:
    """Claim scope for the captured bindings, or None for an admitted read.

    Unbound operations get an unknown-scope claim: they may change anything.
    """
    impact: tuple[ResourceRef, ...] = ()
    dependencies: tuple[ResourceRef, ...] = ()
    obligations: tuple[Postcondition, ...] = ()
    external = False
    if capture.browser is not None:
        # Wave 3 admits only session metadata. A page binding is never
        # effect-bindable; leave its scope unknown rather than infer it.
        if capture.browser.page is None:
            return None
    elif capture.filesystem is not None:
        if capture.filesystem.operation.tool in _FILESYSTEM_READS:
            return None
        impact, obligations = _filesystem_scope(capture.filesystem)
    elif capture.process is not None:
        bound = capture.process
        if bound.launch is not None:
            # An arbitrary command has unknown impact scope; the exact launch
            # reservation is kept only as lineage for background settlement.
            dependencies = (resource_ref(bound.launch, "launch"),)
        else:
            action = str(json.loads(bound.operation.input or "{}").get("action", "list")).strip().lower()
            if action in _JOB_READS:
                return None
            impact = tuple(resource_ref(job, "job") for job in bound.jobs) + tuple(
                resource_ref(process, "process") for job in bound.jobs for process in job.processes) + tuple(
                resource_ref(process, "process") for process in bound.processes)
    elif capture.owned is not None:
        if capture.owned.operation.tool in _OWNED_READS:
            return None
        impact = tuple(resource_ref(r, "record") for r in capture.owned.resources)
        dependencies = tuple(resource_ref(a.file, "attachment") for a in capture.owned.attachments)
    if capture.backend is not None:
        from src.agent_runtime.resources import ExternalResource
        if isinstance(capture.backend.resource, ExternalResource):
            external = True
            impact = (*impact, resource_ref(capture.backend.resource, "backend"))
    return {"impact_scope": impact, "dependencies": dependencies, "obligations": obligations, "external": external}


def begin_effect(journal: Any, action: Any) -> DispatchCapture:
    """Capture bindings and durably claim a possible effect before invocation."""
    capture = capture_dispatch()
    log = journal.effects
    try:
        scope = classify(capture)
    except Exception:  # noqa: BLE001 - classification never blocks dispatch
        # An unclassifiable admitted operation may change anything.
        logger.warning("Effect scope classification failed; claiming unknown scope", exc_info=True)
        scope = {"impact_scope": (), "dependencies": (), "obligations": (), "external": False}
    if scope is None:
        capture.read_only = True
    else:
        capture.claim = log.claim(effect_id=action.action_id + ":effect", run_id=journal.run_id,
                                  action_id=action.action_id, operation=_operation(capture, action),
                                  parent_run_id=journal.parent_run_id or "", **scope)
        capture.paths = tuple(ref.location[-1] for ref in capture.claim.impact_scope
                              if ref.kind is ResourceKind.FILESYSTEM)
        for ref in capture.claim.dependencies:
            if ref.kind is ResourceKind.PROCESS_LAUNCH:
                try:
                    log.index_launch(ref.incarnation, capture.claim.effect_id)
                except (OSError, ValueError):
                    # Without the index a later turn cannot settle this
                    # launch: it stays running/unknown, never successful.
                    logger.warning("Background launch lineage was not indexed", exc_info=True)
    return capture


def _server_producer(capture: DispatchCapture) -> bool:
    """The backend was a server-owned producer bound by Wave 3 admission.

    Only such producers build their result dictionaries from server state. An
    unbound dynamic/registry tool returns whatever it likes, so its keys carry
    no lifecycle meaning. The MCP bridge builds only stdout/stderr/exit_code.
    """
    return any(bound is not None for bound in (capture.filesystem, capture.owned, capture.process, capture.browser))


def _facts(result: Any, capture: DispatchCapture) -> ProducerFacts:
    """Typed producer facts, scoped to what the captured producer can attest."""
    facts = producer_facts(result)
    if not _server_producer(capture):
        # Reported success or failure is all an untrusted result can say.
        facts = ProducerFacts(exit_code=facts.exit_code)
    if capture.backend is not None and capture.claim is not None and capture.claim.external:
        facts = ProducerFacts(**{**facts.to_dict(), "external": True, "remote_acknowledged": facts.exit_code == 0})
    return facts


def _execution(result: Any, facts: ProducerFacts, capture: DispatchCapture) -> ExecutionOutcome:
    if not isinstance(result, dict):
        return ExecutionOutcome.INTERRUPTED
    if facts.timed_out:
        return ExecutionOutcome.TIMED_OUT
    # Only a server process producer can say this operation's own work
    # continues: the native detached launch of an exact Wave 3 launch
    # reservation, or the host bridge's server-set detachment. Lifecycle keys
    # from any other producer (or a listing reporting something else as
    # running) do not.
    process = capture.process
    if process is not None:
        if process.launch is not None and isinstance(result.get("bg_job_id"), str) and facts.exit_code == 0:
            return ExecutionOutcome.RUNNING
        if result.get("detached") is True:
            return ExecutionOutcome.RUNNING
    denied = bool(result.get("blocked") or result.get("approval_required")
                  or facts.failure_kind.endswith("_denied"))
    if facts.exit_code == 0 and not result.get("error") and not denied:
        return ExecutionOutcome.REPORTED_SUCCESS
    return ExecutionOutcome.FAILED


def _cleanup(result: Any, facts: ProducerFacts, capture: DispatchCapture) -> CleanupState:
    if not isinstance(result, dict):
        return CleanupState.UNKNOWN
    if facts.external:
        # External execution reports no locally observed teardown.
        return CleanupState.UNKNOWN
    if capture.process is None:
        return CleanupState.NOT_APPLICABLE
    # Teardown is attested only by the native process/containment producer.
    if facts.failure_kind == "process_teardown_failed":
        return CleanupState.FAILED
    teardown = result.get("teardown")
    if isinstance(teardown, dict) and type(teardown.get("dead")) is bool:
        return CleanupState.VERIFIED if teardown["dead"] else CleanupState.FAILED
    return CleanupState.NOT_APPLICABLE


def settle_effect(journal: Any, action: Any, capture: DispatchCapture | None, *,
                  result: Any = None, error: BaseException | None = None) -> None:
    """Append the outcome and any admitted-read observations for one action."""
    if capture is None:
        return
    log = journal.effects
    if capture.claim is not None:
        if error is not None:
            execution = (ExecutionOutcome.CANCELLED if isinstance(error, asyncio.CancelledError)
                         else ExecutionOutcome.INTERRUPTED)
            facts, cleanup = ProducerFacts(), CleanupState.UNKNOWN
        else:
            facts = _facts(result, capture)
            execution, cleanup = _execution(result, facts, capture), _cleanup(result, facts, capture)
        log.outcome(effect_id=capture.claim.effect_id, execution=execution, impact=Impact.POSSIBLE,
                    facts=facts, cleanup=cleanup, execution_id=action.execution_id or "")
        if (execution is ExecutionOutcome.REPORTED_SUCCESS and capture.process is not None
                and capture.process.launch is None):
            _settle_background(log, capture, result)  # e.g. an exact kill
        return
    if error is not None or not isinstance(result, dict):
        return
    successful = result.get("exit_code") == 0 and not result.get("error")
    # A missing-file read reports failure, but can independently establish
    # absence. No other failed read is eligible for an observation.
    absent_read = (capture.filesystem is not None and capture.filesystem.operation.tool == "read_file"
                   and capture.filesystem.bindings[0].resource.identity is None)
    if not successful and not absent_read:
        return
    for fields in _observations(capture, action, result):
        if successful or fields.get("exists") is False:
            log.observe(**fields)
    if capture.process is not None and capture.process.launch is None:
        _settle_background(log, capture, result)


# -- observations ------------------------------------------------------------

@dataclass(frozen=True)
class _WholeFileRead:
    data: bytes | None = None
    known_absent: bool = False


def _read_whole(resource: Any, limit: int) -> _WholeFileRead:
    """Read a stable binding, distinguish validated ENOENT from uncertainty.

    Only a binding admitted as absent can prove absence. Disappearance of an
    existing identity, replacement, or any validation/access failure is unknown.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        resource.validate()
        if resource.identity is None:
            try:
                os.lstat(resource.path)
            except FileNotFoundError:
                resource.validate()
                return _WholeFileRead(known_absent=True)
            return _WholeFileRead()
        descriptor = os.open(resource.path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            identity = resource.identity
            if (not stat.S_ISREG(info.st_mode) or identity is None
                    or (info.st_dev, info.st_ino) != (identity.device, identity.inode)):
                return _WholeFileRead()
            data = stream.read(limit + 1)
        resource.validate()
    except (OSError, ValueError, RuntimeError):
        return _WholeFileRead()
    return _WholeFileRead(data=data)


def _file_observation(capture: DispatchCapture, action: Any) -> dict[str, Any] | None:
    from src.agent_tools import filesystem_tools as producer
    bound = capture.filesystem
    binding = bound.bindings[0]
    resource = binding.resource
    args = json.loads(bound.execution_input)
    partial = bool(args.get("offset") or args.get("limit")) or (
        os.path.splitext(resource.path)[1].lower() in producer._STRUCTURED_DOCUMENT_SUFFIXES)
    read = _read_whole(resource, producer.MAX_READ_CHARS * 4)
    data = read.data
    if data is None and not read.known_absent:
        return None
    if read.known_absent:
        partial = False  # ENOENT establishes absence of the whole bound path.
    elif len(data) > producer.MAX_READ_CHARS * 4 or len(data.decode("utf-8", errors="replace")) > producer.MAX_READ_CHARS:
        partial = True  # the producer truncated what it read
    complete = not partial
    return dict(observation_id=action.action_id + ":observation", resource=resource_ref(resource, binding.role),
                mechanism=ObservationMechanism.FILESYSTEM_READ,
                coverage=Coverage.COMPLETE if complete else Coverage.PARTIAL,
                source_action_id=action.action_id, source_execution_id=action.execution_id or "",
                exists=not read.known_absent,
                content_sha256=hashlib.sha256(data).hexdigest() if complete and data is not None else "")


def _observations(capture: DispatchCapture, action: Any, result: dict) -> list[dict[str, Any]]:
    base = dict(source_action_id=action.action_id, source_execution_id=action.execution_id or "")
    if capture.browser is not None and capture.browser.page is None:
        # Session lifecycle metadata only; never page/document state.
        return [dict(observation_id=action.action_id + ":observation",
                     resource=resource_ref(capture.browser.session, "session"),
                     mechanism=ObservationMechanism.BROWSER_SESSION, coverage=Coverage.PARTIAL,
                     exists=True, **base)]
    if capture.filesystem is not None:
        tool = capture.filesystem.operation.tool
        if tool == "read_file":
            observation = _file_observation(capture, action)
            return [observation] if observation else []
        if tool in _FILESYSTEM_READS:
            # Listings/searches are partial: they cannot decide content.
            return [dict(observation_id=f"{action.action_id}:observation:{i}", resource=resource_ref(b.resource, b.role),
                         mechanism=ObservationMechanism.FILESYSTEM_READ, coverage=Coverage.PARTIAL, exists=True, **base)
                    for i, b in enumerate(capture.filesystem.bindings)]
    if capture.owned is not None and capture.owned.operation.tool in _OWNED_READS:
        return [dict(observation_id=f"{action.action_id}:observation:{i}", resource=resource_ref(r, "record"),
                     mechanism=ObservationMechanism.OWNED_RECORD_READ, coverage=Coverage.PARTIAL, exists=True, **base)
                for i, r in enumerate(capture.owned.resources) if r.record_id != "*"]
    if capture.process is not None and capture.process.launch is None:
        from src.agent_runtime.process_resources import JOB_TOOL
        job = result.get("job")
        if isinstance(job, dict) and len(capture.process.jobs) == 1 and capture.process.operation.tool == JOB_TOOL:
            return [dict(observation_id=action.action_id + ":observation",
                         resource=resource_ref(capture.process.jobs[0], "job"),
                         mechanism=ObservationMechanism.JOB_STATE, coverage=Coverage.PARTIAL, exists=True, **base)]
    return []


def _settle_background(log: Any, capture: DispatchCapture, result: dict) -> None:
    """Settle a RUNNING launch claim from an admitted read of its exact job.

    Linkage is the Wave 3 launch generation plus owner/request/thread, already
    validated by ``job_from_record`` at admission. Job completion is execution
    evidence for that claim; it verifies no postcondition.
    """
    from src.agent_runtime.process_resources import JOB_TOOL
    job_facts = result.get("job")
    if (not isinstance(job_facts, dict) or len(capture.process.jobs) != 1
            or capture.process.operation.tool != JOB_TOOL):
        return
    settle_background_job(capture.process.jobs[0], job_facts, log=log)


def settle_background_job(job: Any, job_facts: Any, *, log: Any = None) -> None:
    """Settle the RUNNING launch claim of one exact, Wave 3-validated job.

    ``job`` must be a ``BackgroundJobResource`` the caller obtained through
    Wave 3 validation (an admitted job read, or the monitor's
    ``job_from_record``/``validate_job``). ``job_facts`` are typed lifecycle
    facts from that server-owned record; delivered output is never consulted.
    """
    from src.agent_runtime.effect_log import EffectLog, EffectPersistenceError, effects_dir
    from src.agent_runtime.resources import BackgroundJobResource
    if not isinstance(job, BackgroundJobResource) or not isinstance(job_facts, dict):
        return
    status = job_facts.get("status")
    if status not in _JOB_SETTLED:
        return
    lineage = ("process_launch", "native:containment", job.owner, job.request_id, job.thread_id, job.generation)
    owner = log if log is not None and any(any(ref.kind is ResourceKind.PROCESS_LAUNCH and ref.location == lineage
                                               for ref in c.dependencies) for c in log.history().claims) else None
    if owner is None:
        # Background continuation: the launch was claimed by an earlier run.
        directory = log.path.parent if log is not None and log.path is not None else effects_dir()
        indexed = EffectLog.launch_owner(job.generation, directory=directory)
        if indexed is not None:
            try:
                owner = EffectLog.open(indexed[0], directory=directory)
            except (EffectPersistenceError, ValueError):
                owner = None
    if owner is None:
        return
    history = owner.history()
    for claim in history.claims:
        if not any(ref.kind is ResourceKind.PROCESS_LAUNCH and ref.location == lineage for ref in claim.dependencies):
            continue
        latest = history.latest_outcome(claim.effect_id)
        if latest is None or latest.execution is not ExecutionOutcome.RUNNING:
            continue
        code = job_facts.get("exit_code")
        code = code if type(code) is int else None
        if job_facts.get("timed_out") is True:
            execution = ExecutionOutcome.TIMED_OUT
        elif job_facts.get("killed") is True:
            execution = ExecutionOutcome.CANCELLED
        elif status == "done" and code == 0 and job_facts.get("died") is not True:
            execution = ExecutionOutcome.REPORTED_SUCCESS
        else:
            execution = ExecutionOutcome.FAILED
        facts = ProducerFacts(exit_code=code, timed_out=job_facts.get("timed_out") is True, job_state=status)
        owner.outcome(effect_id=claim.effect_id, execution=execution, impact=Impact.POSSIBLE, facts=facts,
                      cleanup=CleanupState.UNKNOWN, execution_id=latest.execution_id)
