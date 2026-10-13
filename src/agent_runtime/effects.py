"""Wave 4 effect claims, outcomes, observations and verification.

This module consumes exact Wave 3 resource identities. It never resolves a
selector, discovers an alias, grants an operation or performs I/O. A
``ResourceRef`` can only be built from an already-admitted typed Wave 3 resource
object; names, paths, PIDs, URLs, labels and dictionaries are not accepted.

Facts are kept separate:

* a claim records intent and scope before backend invocation, not dispatch;
* an outcome records what the executor reported, not the resulting state;
* an observation records state seen through an admitted mechanism;
* verification is derived from fresh, relevant, complete observations made
  after the effect settled, and never from receipts or acknowledgements.

History is append-only. Invalidation and freshness are computed from the
ordered record history; earlier records are never rewritten. Refresh is a new
observation. Unknown scope is conservative, never "no impact".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def _text(value: Any, label: str, *, optional: bool = False) -> None:
    if (not isinstance(value, str) or (not value and not optional)
            or any(c in value for c in ("\0", "\n", "\r"))):
        raise ValueError(f"Invalid effect {label}")


def _position(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Effect history position must be a nonnegative integer")


_SHA256 = re.compile(r"[a-f0-9]{64}")


# ---------------------------------------------------------------------------
# Exact resource references (Wave 3 consumption only)
# ---------------------------------------------------------------------------

class ResourceKind(str, Enum):
    FILESYSTEM = "filesystem"
    PROCESS = "process"
    PROCESS_LAUNCH = "process_launch"
    BACKGROUND_JOB = "background_job"
    OWNED = "owned"
    EXTERNAL = "external"
    BROWSER_SESSION = "browser_session"


@dataclass(frozen=True)
class ResourceRef:
    """Historical reference to one exact admitted Wave 3 resource.

    ``location`` identifies where the resource lives (including the identity of
    its sealed root/namespace); ``incarnation`` identifies the object observed
    there when the reference was taken. Replacement keeps the location and
    changes the incarnation, so evidence never transfers to a replacement.
    ``snapshot_sha256`` digests the full Wave 3 snapshot for audit. A ref is not
    authority: it is not accepted by any dispatcher, resolver or grant.
    """

    kind: ResourceKind
    role: str
    location: tuple[str, ...]
    incarnation: str
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ResourceKind):
            raise ValueError("Unsupported effect resource kind")
        _text(self.role, "resource role")
        _text(self.incarnation, "resource incarnation", optional=True)
        if (not isinstance(self.location, tuple) or len(self.location) < 2
                or any(not isinstance(part, str) or any(c in part for c in ("\0", "\n", "\r"))
                       for part in self.location)
                or self.location[0] != self.kind.value):
            raise ValueError("Malformed effect resource location")
        if not _SHA256.fullmatch(self.snapshot_sha256 or ""):
            raise ValueError("Malformed effect resource snapshot digest")

    @property
    def location_key(self) -> str:
        return _sha(list(self.location))

    def same_location(self, other: "ResourceRef") -> bool:
        return self.kind is other.kind and self.location == other.location

    def overlaps(self, other: "ResourceRef") -> bool:
        """Conservative relevance between two exact references.

        Filesystem relevance is ancestor-or-self within one sealed root
        identity: a mutation of ``d/x`` invalidates a listing of ``d`` and a
        replacement of ``d`` invalidates observations of ``d/x``. Other kinds
        only overlap at the same exact location. No alias discovery is done.
        """
        if self.kind is not other.kind:
            return False
        if self.kind is ResourceKind.OWNED:
            # A collection binding ("*") covers every record it can create,
            # list or change; specific records only overlap themselves.
            return self.location[:-1] == other.location[:-1] and (
                self.location[-1] == other.location[-1] or "*" in (self.location[-1], other.location[-1]))
        if self.kind is not ResourceKind.FILESYSTEM:
            return self.location == other.location
        if self.location[:-1] != other.location[:-1]:
            return False
        left, right = PurePosixPath(self.location[-1]), PurePosixPath(other.location[-1])
        return left == right or left.is_relative_to(right) or right.is_relative_to(left)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "role": self.role, "location": list(self.location),
                "incarnation": self.incarnation, "snapshot_sha256": self.snapshot_sha256}

    @classmethod
    def from_dict(cls, value: Any) -> "ResourceRef":
        """Reload a persisted historical reference. This creates no authority."""
        if (not isinstance(value, dict)
                or set(value) != {"kind", "role", "location", "incarnation", "snapshot_sha256"}
                or not isinstance(value["location"], list)):
            raise ValueError("Malformed persisted effect resource reference")
        return cls(ResourceKind(value["kind"]), value["role"], tuple(value["location"]),
                   value["incarnation"], value["snapshot_sha256"])


def resource_ref(resource: Any, role: str) -> ResourceRef:
    """Reference an exact typed Wave 3 resource; anything else is refused.

    Browser page/document resources are refused: Wave 3 fails closed for page
    authority and Wave 4 must not promote page observations into identity.
    """
    from src.agent_runtime import resources as wave3
    if isinstance(resource, wave3.BrowserPageResource):
        raise TypeError("Browser page resources are not effect-bindable")
    if isinstance(resource, wave3.FilesystemResource):
        root = resource.root
        location = ("filesystem", root.scope.value, root.owner, root.path,
                    str(root.identity.device), str(root.identity.inode), resource.path)
        chain = [[a.path, a.identity.device, a.identity.inode] for a in resource.ancestors]
        identity = resource.identity
        incarnation = ("absent:" + _sha(chain) if identity is None else
                       f"{identity.kind}:{identity.device}:{identity.inode}:" + _sha(chain))
        return ResourceRef(ResourceKind.FILESYSTEM, role, location, incarnation, _sha(resource.to_dict()))
    if isinstance(resource, wave3.ProcessResource):
        ident = resource.identity
        location = ("process", resource.namespace, resource.owner, resource.request_id, resource.thread_id,
                    str(ident.pid), ident.start_token, resource.role)
        return ResourceRef(ResourceKind.PROCESS, role, location, ident.start_token, _sha(resource.to_dict()))
    if isinstance(resource, wave3.ProcessLaunchResource):
        # The reservation generation is the exact launch -> job linkage that
        # Wave 3 validates in ``job_from_record``.
        location = ("process_launch", resource.namespace, resource.owner, resource.request_id,
                    resource.thread_id, resource.generation)
        return ResourceRef(ResourceKind.PROCESS_LAUNCH, role, location, resource.generation,
                           _sha(resource.to_dict()))
    if isinstance(resource, wave3.BackgroundJobResource):
        location = ("background_job", resource.namespace, resource.owner, resource.request_id,
                    resource.thread_id, resource.job_id, resource.generation)
        return ResourceRef(ResourceKind.BACKGROUND_JOB, role, location, resource.generation,
                           _sha(resource.to_dict()))
    if isinstance(resource, wave3.OwnedResource):
        location = ("owned", resource.namespace, resource.owner, resource.thread_id,
                    resource.collection, resource.record_id)
        return ResourceRef(ResourceKind.OWNED, role, location, resource.revision, _sha(resource.to_dict()))
    if isinstance(resource, wave3.ExternalResource):
        location = ("external", resource.namespace, resource.owner, resource.endpoint_id,
                    resource.server_id, resource.tool_id)
        return ResourceRef(ResourceKind.EXTERNAL, role, location, resource.incarnation, _sha(resource.to_dict()))
    if isinstance(resource, wave3.BrowserSessionResource):
        observation = resource.observation
        location = ("browser_session", resource.owner, resource.thread_id, observation.session_key)
        return ResourceRef(ResourceKind.BROWSER_SESSION, role, location, observation.session_incarnation,
                           _sha(resource.to_dict()))
    raise TypeError("Effect scope requires an exact Wave 3 resource identity")


def bound_filesystem_refs(bound: Any) -> tuple[ResourceRef, ...]:
    """References for an admitted ``BoundFilesystemOperation``'s exact bindings."""
    from src.agent_runtime.resource_binding import BoundFilesystemOperation
    if not isinstance(bound, BoundFilesystemOperation):
        raise TypeError("Filesystem effect scope requires a server-owned bound operation")
    return tuple(resource_ref(binding.resource, binding.role) for binding in bound.bindings)


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperationRef:
    """Final normalized operation reference; not a second normalization API."""

    tool: str
    action: str
    input_sha256: str
    request_id: str = ""

    def __post_init__(self) -> None:
        _text(self.tool, "operation tool")
        _text(self.action, "operation action", optional=True)
        _text(self.request_id, "operation request", optional=True)
        if not _SHA256.fullmatch(self.input_sha256 or ""):
            raise ValueError("Malformed operation input digest")

    @classmethod
    def from_exact(cls, operation: Any, execution_input: str | None = None, request_id: str = "") -> "OperationRef":
        from src.agent_runtime.authority import ExactOperation
        if not isinstance(operation, ExactOperation):
            raise TypeError("Effect claims require the admitted exact operation")
        body = operation.input if execution_input is None else execution_input
        return cls(str(operation.tool), str(operation.action or ""), _sha(body), request_id or "")

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "action": self.action, "input_sha256": self.input_sha256,
                "request_id": self.request_id}

    @classmethod
    def from_dict(cls, value: Any) -> "OperationRef":
        if not isinstance(value, dict) or set(value) != {"tool", "action", "input_sha256", "request_id"}:
            raise ValueError("Malformed persisted operation reference")
        return cls(**value)


class Predicate(str, Enum):
    EXISTS = "exists"
    ABSENT = "absent"
    CONTENT_SHA256 = "content_sha256"
    # The observed content digest differs from ``expected`` (the pre-state).
    CONTENT_CHANGED = "content_changed"


_PREDICATE_KINDS = {
    Predicate.EXISTS: {ResourceKind.FILESYSTEM, ResourceKind.OWNED, ResourceKind.EXTERNAL},
    Predicate.ABSENT: {ResourceKind.FILESYSTEM, ResourceKind.OWNED, ResourceKind.EXTERNAL},
    Predicate.CONTENT_SHA256: {ResourceKind.FILESYSTEM, ResourceKind.OWNED, ResourceKind.EXTERNAL},
    Predicate.CONTENT_CHANGED: {ResourceKind.FILESYSTEM, ResourceKind.OWNED, ResourceKind.EXTERNAL},
}


@dataclass(frozen=True)
class Postcondition:
    """An explicit requested post-state predicate on one exact claimed target."""

    target: ResourceRef
    predicate: Predicate
    expected: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.target, ResourceRef) or not isinstance(self.predicate, Predicate):
            raise ValueError("Malformed postcondition")
        if self.target.kind not in _PREDICATE_KINDS[self.predicate]:
            raise ValueError("Predicate is not supported for this resource kind")
        needs_digest = self.predicate in {Predicate.CONTENT_SHA256, Predicate.CONTENT_CHANGED}
        if needs_digest != bool(_SHA256.fullmatch(self.expected or "")) or (not needs_digest and self.expected):
            raise ValueError("Malformed postcondition expectation")

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target.to_dict(), "predicate": self.predicate.value, "expected": self.expected}

    @classmethod
    def from_dict(cls, value: Any) -> "Postcondition":
        if not isinstance(value, dict) or set(value) != {"target", "predicate", "expected"}:
            raise ValueError("Malformed persisted postcondition")
        return cls(ResourceRef.from_dict(value["target"]), Predicate(value["predicate"]), value["expected"])


@dataclass(frozen=True)
class EffectClaim:
    """Server-owned claim, persisted before backend invocation.

    The claim states intent and scope; it is not evidence that dispatch, the
    backend operation, or any mutation happened. ``impact_scope`` holds the
    exact admitted bindings the operation may change; empty means unknown
    scope, never no impact. ``dependencies`` are resources the predicate
    relies on without being mutation targets.
    """

    effect_id: str
    run_id: str
    action_id: str
    sequence: int
    operation: OperationRef
    impact_scope: tuple[ResourceRef, ...] = ()
    dependencies: tuple[ResourceRef, ...] = ()
    obligations: tuple[Postcondition, ...] = ()
    parent_run_id: str = ""
    external: bool = False

    def __post_init__(self) -> None:
        for name in ("effect_id", "run_id", "action_id"):
            _text(getattr(self, name), name)
        _text(self.parent_run_id, "parent run", optional=True)
        _position(self.sequence)
        if not isinstance(self.operation, OperationRef) or type(self.external) is not bool:
            raise ValueError("Malformed effect claim")
        for name in ("impact_scope", "dependencies"):
            refs = getattr(self, name)
            if not isinstance(refs, tuple) or any(not isinstance(r, ResourceRef) for r in refs):
                raise ValueError("Effect scope must be exact resource references")
        if (not isinstance(self.obligations, tuple)
                or any(not isinstance(o, Postcondition) for o in self.obligations)):
            raise ValueError("Malformed effect obligations")
        for obligation in self.obligations:
            if not any(obligation.target == ref for ref in self.impact_scope):
                raise ValueError("Postcondition target must be a claimed impact binding")

    @property
    def unknown_scope(self) -> bool:
        return not self.impact_scope

    def to_dict(self) -> dict[str, Any]:
        return {"effect_id": self.effect_id, "run_id": self.run_id, "action_id": self.action_id,
                "sequence": self.sequence, "operation": self.operation.to_dict(),
                "impact_scope": [r.to_dict() for r in self.impact_scope],
                "dependencies": [r.to_dict() for r in self.dependencies],
                "obligations": [o.to_dict() for o in self.obligations],
                "parent_run_id": self.parent_run_id, "external": self.external}

    @classmethod
    def from_dict(cls, value: Any) -> "EffectClaim":
        keys = {"effect_id", "run_id", "action_id", "sequence", "operation", "impact_scope",
                "dependencies", "obligations", "parent_run_id", "external"}
        if not isinstance(value, dict) or set(value) != keys or any(
                not isinstance(value[k], list) for k in ("impact_scope", "dependencies", "obligations")):
            raise ValueError("Malformed persisted effect claim")
        return cls(value["effect_id"], value["run_id"], value["action_id"], value["sequence"],
                   OperationRef.from_dict(value["operation"]),
                   tuple(ResourceRef.from_dict(r) for r in value["impact_scope"]),
                   tuple(ResourceRef.from_dict(r) for r in value["dependencies"]),
                   tuple(Postcondition.from_dict(o) for o in value["obligations"]),
                   value["parent_run_id"], value["external"])


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

class ExecutionOutcome(str, Enum):
    NOT_EXECUTED = "not_executed"          # refused before backend invocation
    ATTEMPTED = "attempted"                # claimed; no settled outcome yet
    REPORTED_SUCCESS = "reported_success"  # executor reported success; not post-state
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    RUNNING = "running"                    # admitted/background; not completed work
    INTERRUPTED = "interrupted"            # unknown: lost, crashed or replayed


class Impact(str, Enum):
    NONE = "none"          # known no-op: the backend was never invoked
    POSSIBLE = "possible"  # may have changed state, including partially
    CHANGED = "changed"    # a trusted before/after capture differs


class CleanupState(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


_SETTLED = {ExecutionOutcome.NOT_EXECUTED, ExecutionOutcome.REPORTED_SUCCESS, ExecutionOutcome.FAILED,
            ExecutionOutcome.TIMED_OUT, ExecutionOutcome.CANCELLED, ExecutionOutcome.INTERRUPTED}


@dataclass(frozen=True)
class ProducerFacts:
    """Bounded typed producer facts; arbitrary returned data is never kept.

    These are execution/lifecycle facts reported by a server producer. None of
    them is a post-state observation.
    """

    exit_code: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    failure_kind: str = ""
    job_state: str = ""
    remote_acknowledged: bool = False
    external: bool = False
    # The producer reached its mutation stage before reporting failure.
    mutation_attempted: bool = False

    def __post_init__(self) -> None:
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("Malformed producer exit code")
        for name in ("timed_out", "output_truncated", "remote_acknowledged", "external", "mutation_attempted"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Malformed producer flag")
        for name in ("failure_kind", "job_state"):
            value = getattr(self, name)
            _text(value, name, optional=True)
            if len(value) > 64 or (value and not re.fullmatch(r"[a-z0-9_.:-]+", value)):
                raise ValueError("Malformed producer label")

    def to_dict(self) -> dict[str, Any]:
        return {"exit_code": self.exit_code, "timed_out": self.timed_out,
                "output_truncated": self.output_truncated, "failure_kind": self.failure_kind,
                "job_state": self.job_state, "remote_acknowledged": self.remote_acknowledged,
                "external": self.external, "mutation_attempted": self.mutation_attempted}

    @classmethod
    def from_dict(cls, value: Any) -> "ProducerFacts":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Malformed persisted producer facts")
        return cls(**value)


def _label(value: Any) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if len(text) <= 64 and re.fullmatch(r"[a-z0-9_.:-]+", text) else ""


def producer_facts(result: Any) -> ProducerFacts:
    """Project a dispatcher result into typed facts without trusting its shape.

    Only exact scalar types are copied. Anything else becomes the default, so a
    forged or malformed dictionary can only lose information, not add trust.
    """
    if not isinstance(result, Mapping):
        return ProducerFacts()
    code = result.get("exit_code")
    containment = result.get("containment")
    external = isinstance(containment, Mapping) and containment.get("external") is True
    job = result.get("status") if isinstance(result.get("job_id"), str) else ""
    return ProducerFacts(
        exit_code=code if type(code) is int else None,
        timed_out=result.get("timed_out") is True or _label(result.get("failure_kind")) == "timeout",
        output_truncated=result.get("output_truncated") is True or result.get("truncated") is True,
        failure_kind=_label(result.get("failure_kind")),
        job_state=_label(job),
        external=external,
        mutation_attempted=result.get("mutation_attempted") is True,
    )


@dataclass(frozen=True)
class EffectOutcome:
    """Append-only execution outcome for one claim.

    ``impact`` must not claim no change for anything that reached a backend.
    ``cleanup`` is recorded separately: cleanup success is not business-effect
    success and cleanup failure does not erase an achieved effect.
    """

    effect_id: str
    sequence: int
    execution: ExecutionOutcome
    impact: Impact
    facts: ProducerFacts = ProducerFacts()
    cleanup: CleanupState = CleanupState.NOT_APPLICABLE
    execution_id: str = ""
    replayed: bool = False

    def __post_init__(self) -> None:
        _text(self.effect_id, "effect identifier")
        _text(self.execution_id, "execution identifier", optional=True)
        _position(self.sequence)
        if (not isinstance(self.execution, ExecutionOutcome) or not isinstance(self.impact, Impact)
                or not isinstance(self.facts, ProducerFacts) or not isinstance(self.cleanup, CleanupState)
                or type(self.replayed) is not bool):
            raise ValueError("Malformed effect outcome")
        if self.execution is ExecutionOutcome.ATTEMPTED:
            raise ValueError("ATTEMPTED is derived from a claim without an outcome")
        if (self.impact is Impact.NONE) != (self.execution is ExecutionOutcome.NOT_EXECUTED):
            raise ValueError("Only a refused, never-invoked operation is a known no-op")
        if self.execution is ExecutionOutcome.NOT_EXECUTED and self.execution_id:
            raise ValueError("A refused operation has no execution identity")

    def to_dict(self) -> dict[str, Any]:
        return {"effect_id": self.effect_id, "sequence": self.sequence, "execution": self.execution.value,
                "impact": self.impact.value, "facts": self.facts.to_dict(), "cleanup": self.cleanup.value,
                "execution_id": self.execution_id, "replayed": self.replayed}

    @classmethod
    def from_dict(cls, value: Any) -> "EffectOutcome":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Malformed persisted effect outcome")
        return cls(value["effect_id"], value["sequence"], ExecutionOutcome(value["execution"]),
                   Impact(value["impact"]), ProducerFacts.from_dict(value["facts"]),
                   CleanupState(value["cleanup"]), value["execution_id"], value["replayed"])


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

class ObservationMechanism(str, Enum):
    FILESYSTEM_READ = "filesystem_read"        # admitted read of the exact binding
    OWNED_RECORD_READ = "owned_record_read"    # admitted owner-scoped readback
    REMOTE_READBACK = "remote_readback"        # admitted independent remote query
    PROCESS_OWNERSHIP = "process_ownership"    # lifecycle owner's verdict
    JOB_STATE = "job_state"                    # background job record transition
    BROWSER_SESSION = "browser_session"        # session lifecycle metadata only
    # The following are never post-state verification.
    EXECUTION_RECEIPT = "execution_receipt"
    REMOTE_ACKNOWLEDGEMENT = "remote_acknowledgement"


class Coverage(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


# Mechanisms able to decide a postcondition for each resource kind. Process,
# job and browser-session observations are lifecycle facts: they can make
# earlier evidence stale but cannot verify a file/record/remote predicate.
_VERIFYING = {
    ResourceKind.FILESYSTEM: {ObservationMechanism.FILESYSTEM_READ},
    ResourceKind.OWNED: {ObservationMechanism.OWNED_RECORD_READ},
    ResourceKind.EXTERNAL: {ObservationMechanism.REMOTE_READBACK},
}
_ADMITTED_READS = {ObservationMechanism.FILESYSTEM_READ, ObservationMechanism.OWNED_RECORD_READ,
                   ObservationMechanism.REMOTE_READBACK}


@dataclass(frozen=True)
class Observation:
    """State seen through one mechanism for one exact resource.

    ``exists``/``content_sha256`` are what the mechanism saw; ``None``/empty
    means not observed. A PARTIAL observation (offset/limit/truncated read,
    listing, existence-only probe) never decides a whole-content predicate.
    Admitted reads must name the journal action that performed them.
    """

    observation_id: str
    sequence: int
    resource: ResourceRef
    mechanism: ObservationMechanism
    coverage: Coverage
    source_action_id: str = ""
    source_execution_id: str = ""
    exists: bool | None = None
    content_sha256: str = ""
    evidence_event_id: str = ""

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation identifier")
        for name in ("source_action_id", "source_execution_id", "evidence_event_id"):
            _text(getattr(self, name), name, optional=True)
        _position(self.sequence)
        if (not isinstance(self.resource, ResourceRef) or not isinstance(self.mechanism, ObservationMechanism)
                or not isinstance(self.coverage, Coverage)
                or (self.exists is not None and type(self.exists) is not bool)):
            raise ValueError("Malformed observation")
        if self.content_sha256 and (not _SHA256.fullmatch(self.content_sha256) or self.exists is not True):
            raise ValueError("Malformed observed content digest")
        if self.mechanism in _ADMITTED_READS and not self.source_action_id:
            raise ValueError("Readback observations require the admitted action that performed them")

    def to_dict(self) -> dict[str, Any]:
        return {"observation_id": self.observation_id, "sequence": self.sequence,
                "resource": self.resource.to_dict(), "mechanism": self.mechanism.value,
                "coverage": self.coverage.value, "source_action_id": self.source_action_id,
                "source_execution_id": self.source_execution_id, "exists": self.exists,
                "content_sha256": self.content_sha256, "evidence_event_id": self.evidence_event_id}

    @classmethod
    def from_dict(cls, value: Any) -> "Observation":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Malformed persisted observation")
        return cls(**{**value, "resource": ResourceRef.from_dict(value["resource"]),
                      "mechanism": ObservationMechanism(value["mechanism"]),
                      "coverage": Coverage(value["coverage"])})


def predicate_holds(postcondition: Postcondition, observation: Observation) -> bool | None:
    """Decide one predicate from one observation; ``None`` means undecidable.

    The check is performed here from the observed state, so no adapter can
    attest verification by labelling an unrelated read.
    """
    target = postcondition.target
    if (not observation.resource.same_location(target)
            or observation.mechanism not in _VERIFYING.get(target.kind, set())):
        return None
    predicate = postcondition.predicate
    if predicate is Predicate.ABSENT:
        return None if observation.exists is None else not observation.exists
    if predicate is Predicate.EXISTS:
        return observation.exists
    if observation.exists is False:
        return False
    if observation.coverage is not Coverage.COMPLETE or not observation.content_sha256:
        return None
    if predicate is Predicate.CONTENT_SHA256:
        return observation.content_sha256 == postcondition.expected
    return observation.content_sha256 != postcondition.expected


# ---------------------------------------------------------------------------
# History, invalidation and freshness
# ---------------------------------------------------------------------------

class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"          # a later possible mutation or replacement overlaps
    UNSETTLED = "unsettled"  # an overlapping effect was still in flight


@dataclass(frozen=True)
class EffectHistory:
    """An immutable, totally ordered view of one effect log.

    Sequences are unique positions in one log. Duplicate positions are rejected
    rather than ordered arbitrarily.
    """

    claims: tuple[EffectClaim, ...] = ()
    outcomes: tuple[EffectOutcome, ...] = ()
    observations: tuple[Observation, ...] = ()

    def __post_init__(self) -> None:
        positions = [r.sequence for r in (*self.claims, *self.outcomes, *self.observations)]
        if len(positions) != len(set(positions)):
            raise ValueError("Effect history positions must be unique")
        ids = [c.effect_id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("Effect claims must have unique identifiers")
        claim_at = {c.effect_id: c.sequence for c in self.claims}
        settled: set[str] = set()
        for outcome in sorted(self.outcomes, key=lambda o: o.sequence):
            if outcome.effect_id not in claim_at or outcome.sequence <= claim_at[outcome.effect_id]:
                raise ValueError("Outcome must follow its claim in one history")
            # A RUNNING effect may later settle (background continuation or
            # replay interruption); a settled outcome is never replaced.
            if outcome.effect_id in settled:
                raise ValueError("A settled effect outcome cannot be replaced")
            if outcome.execution is not ExecutionOutcome.RUNNING:
                settled.add(outcome.effect_id)
        # Derived indexes (not fields): outcomes per effect in sequence order.
        by_effect: dict[str, list[EffectOutcome]] = {}
        for outcome in sorted(self.outcomes, key=lambda o: o.sequence):
            by_effect.setdefault(outcome.effect_id, []).append(outcome)
        object.__setattr__(self, "_outcomes_by_effect", by_effect)
        object.__setattr__(self, "_claims_by_id", {c.effect_id: c for c in self.claims})

    def claim(self, effect_id: str) -> EffectClaim | None:
        return self._claims_by_id.get(effect_id)

    def latest_outcome(self, effect_id: str, before: int | None = None) -> EffectOutcome | None:
        for outcome in reversed(self._outcomes_by_effect.get(effect_id, ())):
            if before is None or outcome.sequence < before:
                return outcome
        return None

    def execution(self, effect_id: str, before: int | None = None) -> ExecutionOutcome:
        outcome = self.latest_outcome(effect_id, before)
        return ExecutionOutcome.ATTEMPTED if outcome is None else outcome.execution


def _claim_touches(claim: EffectClaim, resource: ResourceRef) -> bool:
    return claim.unknown_scope or any(ref.overlaps(resource) for ref in claim.impact_scope)


def invalidated_by(observation: Observation, history: EffectHistory) -> tuple[str, ...]:
    """Identifiers of later records that make ``observation`` stale.

    Any later claim that may touch the resource invalidates it once the claim
    exists (it may already be executing), unless it settled as a known no-op.
    A later observation of the same location with a different incarnation
    reveals replacement. Execution receipts are never invalidated: they remain
    historical execution facts.
    """
    if observation.mechanism in {ObservationMechanism.EXECUTION_RECEIPT,
                                 ObservationMechanism.REMOTE_ACKNOWLEDGEMENT}:
        return ()
    reasons: list[str] = []
    for claim in history.claims:
        if claim.sequence <= observation.sequence or not _claim_touches(claim, observation.resource):
            continue
        outcome = history.latest_outcome(claim.effect_id)
        if outcome is not None and outcome.impact is Impact.NONE:
            continue
        reasons.append(claim.effect_id)
    for later in history.observations:
        if (later.sequence > observation.sequence and later.resource.same_location(observation.resource)
                and later.resource.incarnation != observation.resource.incarnation):
            reasons.append(later.observation_id)
    return tuple(dict.fromkeys(reasons))


def freshness(observation: Observation, history: EffectHistory) -> Freshness:
    if invalidated_by(observation, history):
        return Freshness.STALE
    for claim in history.claims:
        if claim.sequence < observation.sequence and _claim_touches(claim, observation.resource):
            state = history.execution(claim.effect_id, before=observation.sequence)
            if state in {ExecutionOutcome.ATTEMPTED, ExecutionOutcome.RUNNING}:
                return Freshness.UNSETTLED
    return Freshness.FRESH


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class EffectVerdict(str, Enum):
    NOT_EXECUTED = "not_executed"
    PENDING = "pending"                # attempted/running; not settled
    VERIFIED = "verified"              # reported success + fresh matching post-state
    STATE_OBSERVED = "state_observed"  # matching post-state; causality unknown
    UNVERIFIED = "unverified"          # no adequate fresh evidence
    CONTRADICTED = "contradicted"      # latest fresh check shows the predicate false
    FAILED = "failed"                  # execution failed; never effect success


_VERDICT_RANK = {EffectVerdict.FAILED: 0, EffectVerdict.CONTRADICTED: 1, EffectVerdict.PENDING: 2,
                 EffectVerdict.UNVERIFIED: 3, EffectVerdict.NOT_EXECUTED: 4,
                 EffectVerdict.STATE_OBSERVED: 5, EffectVerdict.VERIFIED: 6}


@dataclass(frozen=True)
class EffectAssessment:
    effect_id: str
    action_id: str
    execution: ExecutionOutcome
    impact: Impact | None
    verdict: EffectVerdict
    reason: str
    cleanup: CleanupState = CleanupState.NOT_APPLICABLE
    observation_ids: tuple[str, ...] = ()
    targets: tuple[ResourceRef, ...] = ()

    @property
    def unresolved_impact(self) -> bool:
        """Resources may have changed in a way no fresh evidence has settled."""
        return (self.impact is not Impact.NONE
                and self.execution is not ExecutionOutcome.REPORTED_SUCCESS
                and self.verdict not in {EffectVerdict.STATE_OBSERVED, EffectVerdict.CONTRADICTED})

    def to_dict(self) -> dict[str, Any]:
        return {"effect_id": self.effect_id, "action_id": self.action_id, "execution": self.execution.value,
                "impact": None if self.impact is None else self.impact.value, "verdict": self.verdict.value,
                "reason": self.reason, "cleanup": self.cleanup.value,
                "observation_ids": list(self.observation_ids),
                "targets": [t.to_dict() for t in self.targets]}


def _assess_obligation(claim: EffectClaim, settled: EffectOutcome, obligation: Postcondition,
                       history: EffectHistory) -> tuple[EffectVerdict, str, str]:
    candidates = [o for o in history.observations
                  if o.sequence > settled.sequence and o.resource.same_location(obligation.target)
                  and o.mechanism in _VERIFYING.get(obligation.target.kind, set())]
    if not candidates:
        return EffectVerdict.UNVERIFIED, "no authorized post-settlement observation of the target", ""
    # The newest check wins. A newer partial or failed check never falls back
    # to an earlier complete one.
    latest = max(candidates, key=lambda o: o.sequence)
    state = freshness(latest, history)
    if state is not Freshness.FRESH:
        return EffectVerdict.UNVERIFIED, f"the latest target observation is {state.value}", latest.observation_id
    holds = predicate_holds(obligation, latest)
    if holds is None:
        return EffectVerdict.UNVERIFIED, "the latest observation does not decide the postcondition", latest.observation_id
    if not holds:
        return EffectVerdict.CONTRADICTED, "the latest fresh observation contradicts the postcondition", latest.observation_id
    execution = settled.execution
    if execution is ExecutionOutcome.FAILED:
        return EffectVerdict.FAILED, "execution failed; matching state is not attributed to it", latest.observation_id
    if execution is ExecutionOutcome.REPORTED_SUCCESS:
        return EffectVerdict.VERIFIED, "fresh authorized observation matches the postcondition", latest.observation_id
    return (EffectVerdict.STATE_OBSERVED,
            "state matches, but this execution's outcome is unknown; causality is not established",
            latest.observation_id)


def assess(claim: EffectClaim, history: EffectHistory) -> EffectAssessment:
    """Derive a claim's verdict from the append-only history."""
    settled = history.latest_outcome(claim.effect_id)
    targets = tuple(o.target for o in claim.obligations)
    if settled is None:
        return EffectAssessment(claim.effect_id, claim.action_id, ExecutionOutcome.ATTEMPTED, None,
                                EffectVerdict.PENDING, "no settled execution outcome", targets=targets)
    base = dict(effect_id=claim.effect_id, action_id=claim.action_id, execution=settled.execution,
                impact=settled.impact, cleanup=settled.cleanup, targets=targets)
    if settled.execution is ExecutionOutcome.NOT_EXECUTED:
        return EffectAssessment(**base, verdict=EffectVerdict.NOT_EXECUTED, reason="refused before invocation")
    if settled.execution is ExecutionOutcome.RUNNING:
        return EffectAssessment(**base, verdict=EffectVerdict.PENDING,
                                reason="background execution has not settled")
    if not claim.obligations:
        verdict = EffectVerdict.FAILED if settled.execution is ExecutionOutcome.FAILED else EffectVerdict.UNVERIFIED
        return EffectAssessment(**base, verdict=verdict, reason="no explicit postcondition obligation")
    results = [_assess_obligation(claim, settled, o, history) for o in claim.obligations]
    worst = min(results, key=lambda r: _VERDICT_RANK[r[0]])
    if settled.execution is ExecutionOutcome.FAILED and worst[0] is not EffectVerdict.CONTRADICTED:
        worst = (EffectVerdict.FAILED, worst[1] if worst[0] is EffectVerdict.FAILED else
                 "execution failed and may have partially changed the target", worst[2])
    return EffectAssessment(**base, verdict=worst[0], reason=worst[1],
                            observation_ids=tuple(dict.fromkeys(r[2] for r in results if r[2])))


def assess_all(history: EffectHistory) -> tuple[EffectAssessment, ...]:
    return tuple(assess(claim, history) for claim in sorted(history.claims, key=lambda c: c.sequence))


def replay_interrupted(history: EffectHistory, next_sequence: int) -> tuple[EffectOutcome, ...]:
    """Outcomes to append for claims that never settled before a reload.

    Unknown remains unknown: the backend may or may not have been invoked, so
    impact is POSSIBLE. Running background effects are left to their own
    lifecycle owner and are not converted here.
    """
    _position(next_sequence)
    pending = [c for c in sorted(history.claims, key=lambda c: c.sequence)
               if history.latest_outcome(c.effect_id) is None]
    return tuple(EffectOutcome(c.effect_id, next_sequence + i, ExecutionOutcome.INTERRUPTED,
                               Impact.POSSIBLE, replayed=True) for i, c in enumerate(pending))
