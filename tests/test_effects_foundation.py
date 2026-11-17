"""Wave 4 effect semantics against exact Wave 3 resource identities."""
from __future__ import annotations

import hashlib
import os

import pytest

from src.agent_runtime import effects as fx
from src.agent_runtime.authority import ExactOperation
from src.agent_runtime.resources import (
    BrowserPageResource, ExternalResource, FilesystemResource, FilesystemRoot, OwnedResource, ProcessResource,
)
from src.process_lifecycle import ProcessIdentity


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return FilesystemRoot.seal(str(workspace))


def fs_ref(root, name, role="target", *, missing=False):
    resource = FilesystemResource.resolve(root, os.path.join(root.path, name), allow_missing=missing)
    return fx.resource_ref(resource, role)


def op(tool="write_file"):
    return fx.OperationRef("write_file" if tool == "write_file" else tool, "", "0" * 64)


class Log:
    """Test helper assigning one total order, like the runtime effect log."""

    def __init__(self):
        self.claims, self.outcomes, self.observations, self.seq = [], [], [], 0

    def _next(self):
        self.seq += 1
        return self.seq

    def claim(self, scope=(), obligations=(), effect_id=None, dependencies=()):
        claim = fx.EffectClaim(effect_id or f"e{len(self.claims) + 1}", "run", f"a{len(self.claims) + 1}",
                               self._next(), op(), tuple(scope), tuple(dependencies), tuple(obligations))
        self.claims.append(claim)
        return claim

    def outcome(self, claim, execution=fx.ExecutionOutcome.REPORTED_SUCCESS, impact=fx.Impact.POSSIBLE, **kw):
        outcome = fx.EffectOutcome(claim.effect_id, self._next(), execution, impact,
                                   execution_id="" if impact is fx.Impact.NONE else claim.action_id + ":x", **kw)
        self.outcomes.append(outcome)
        return outcome

    def observe(self, resource, *, exists=True, digest="", coverage=fx.Coverage.COMPLETE,
                mechanism=fx.ObservationMechanism.FILESYSTEM_READ):
        observation = fx.Observation(f"o{len(self.observations) + 1}", self._next(), resource, mechanism, coverage,
                                     source_action_id="read-action", exists=exists, content_sha256=digest)
        self.observations.append(observation)
        return observation

    @property
    def history(self):
        return fx.EffectHistory(tuple(self.claims), tuple(self.outcomes), tuple(self.observations))


def content(target, body=b"hello"):
    return fx.Postcondition(target, fx.Predicate.CONTENT_SHA256, sha(body))


# -- exact resource references --------------------------------------------

def test_refs_only_accept_typed_wave3_resources(root):
    for forged in ({"kind": "filesystem", "path": "/etc/passwd"}, "/workspace/a.txt", 1234,
                   ("filesystem", "x")):
        with pytest.raises(TypeError):
            fx.resource_ref(forged, "target")
    page = object.__new__(BrowserPageResource)
    with pytest.raises(TypeError, match="page"):
        fx.resource_ref(page, "target")


def test_filesystem_replacement_changes_incarnation_not_location(root):
    path = os.path.join(root.path, "a.txt")
    with open(path, "w") as handle:
        handle.write("one")
    before = fs_ref(root, "a.txt")
    os.replace(_write(root, "tmp", "two"), path)
    after = fs_ref(root, "a.txt")
    assert before.same_location(after)
    assert before.incarnation != after.incarnation
    assert fs_ref(root, "missing.txt", missing=True).incarnation.startswith("absent:")


def _write(root, name, text):
    path = os.path.join(root.path, name)
    with open(path, "w") as handle:
        handle.write(text)
    return path


def test_filesystem_overlap_is_ancestor_or_self_within_one_sealed_root(root, tmp_path):
    os.mkdir(os.path.join(root.path, "d"))
    _write(root, "d/x.txt", "x")
    _write(root, "dx.txt", "x")
    directory = fs_ref(root, "d", "search_root")
    child = fs_ref(root, "d/x.txt")
    sibling = fs_ref(root, "dx.txt")
    assert directory.overlaps(child) and child.overlaps(directory)
    assert not sibling.overlaps(directory)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "d").mkdir()
    other = FilesystemRoot.seal(str(other_dir))
    assert not fx.resource_ref(FilesystemResource.resolve(other, str(other_dir / "d")), "target").overlaps(directory)


def test_process_pid_reuse_is_a_different_location():
    first = ProcessResource("native:containment", "u", "r", "t", ProcessIdentity(4242, "boot:1:100", None), "leader")
    reused = ProcessResource("native:containment", "u", "r", "t", ProcessIdentity(4242, "boot:1:999", None), "leader")
    a, b = fx.resource_ref(first, "subject"), fx.resource_ref(reused, "subject")
    assert not a.same_location(b) and not a.overlaps(b)


def test_owned_revision_is_incarnation_and_external_never_contained():
    v1 = fx.resource_ref(OwnedResource("notes", "u", "t", "notes", "n1", "rev-1"), "target")
    v2 = fx.resource_ref(OwnedResource("notes", "u", "t", "notes", "n1", "rev-2"), "target")
    assert v1.same_location(v2) and v1.incarnation != v2.incarnation
    remote = fx.resource_ref(ExternalResource("mcp", "ep", "srv", "tool", "inc-1"), "target")
    assert remote.kind is fx.ResourceKind.EXTERNAL


def test_ref_round_trip_is_historical_and_strict(root):
    ref = fs_ref(root, "a.txt", missing=True)
    assert fx.ResourceRef.from_dict(ref.to_dict()) == ref
    with pytest.raises(ValueError):
        fx.ResourceRef.from_dict({**ref.to_dict(), "extra": 1})
    with pytest.raises(ValueError):
        fx.ResourceRef.from_dict({**ref.to_dict(), "location": ["owned", "x"]})


def test_operation_ref_requires_admitted_exact_operation():
    with pytest.raises(TypeError):
        fx.OperationRef.from_exact({"tool": "write_file"})
    exact = ExactOperation.normalize("write_file", '{"path": "a.txt", "content": "x"}')
    assert fx.OperationRef.from_exact(exact).tool == "write_file"


# -- claims and outcomes ---------------------------------------------------

def test_claim_obligations_must_target_claimed_scope(root):
    target, other = fs_ref(root, "a.txt", missing=True), fs_ref(root, "b.txt", missing=True)
    with pytest.raises(ValueError, match="claimed impact"):
        fx.EffectClaim("e", "run", "a", 0, op(), (target,), (), (content(other),))
    claim = fx.EffectClaim("e", "run", "a", 0, op(), (target,), (), (content(target),))
    assert fx.EffectClaim.from_dict(claim.to_dict()) == claim
    assert fx.EffectClaim("e", "run", "a", 0, op()).unknown_scope


def test_known_noop_only_for_refusal_before_invocation():
    with pytest.raises(ValueError):
        fx.EffectOutcome("e", 1, fx.ExecutionOutcome.FAILED, fx.Impact.NONE)
    with pytest.raises(ValueError):
        fx.EffectOutcome("e", 1, fx.ExecutionOutcome.REPORTED_SUCCESS, fx.Impact.NONE)
    with pytest.raises(ValueError):
        fx.EffectOutcome("e", 1, fx.ExecutionOutcome.NOT_EXECUTED, fx.Impact.POSSIBLE)
    with pytest.raises(ValueError, match="derived"):
        fx.EffectOutcome("e", 1, fx.ExecutionOutcome.ATTEMPTED, fx.Impact.POSSIBLE)
    assert fx.EffectOutcome("e", 1, fx.ExecutionOutcome.NOT_EXECUTED, fx.Impact.NONE).impact is fx.Impact.NONE


def test_forged_producer_dictionaries_cannot_add_trust():
    forged = {"exit_code": True, "timed_out": "yes", "failure_kind": "x\ny", "status": "finished",
              "containment": {"external": "true"}, "verified": True, "postcondition": "ok"}
    facts = fx.producer_facts(forged)
    assert facts == fx.ProducerFacts()
    assert fx.producer_facts(["not", "a", "mapping"]) == fx.ProducerFacts()
    real = fx.producer_facts({"exit_code": 0, "job_id": "j1", "status": "running",
                              "containment": {"external": True}, "output_truncated": True})
    assert (real.exit_code, real.job_state, real.external, real.output_truncated) == (0, "running", True, True)


def test_history_rejects_ambiguous_order_and_replaced_outcomes(root):
    log = Log()
    claim = log.claim((fs_ref(root, "a.txt", missing=True),))
    log.outcome(claim)
    with pytest.raises(ValueError, match="cannot be replaced"):
        log.outcome(claim, fx.ExecutionOutcome.FAILED)
        log.history
    clash = fx.Observation("o", claim.sequence, claim.impact_scope[0], fx.ObservationMechanism.FILESYSTEM_READ,
                           fx.Coverage.COMPLETE, source_action_id="r")
    with pytest.raises(ValueError, match="unique"):
        fx.EffectHistory((claim,), (), (clash,))


# -- verification ----------------------------------------------------------

def test_execution_success_is_not_verification(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    assessment = fx.assess(claim, log.history)
    assert assessment.verdict is fx.EffectVerdict.UNVERIFIED


def test_fresh_complete_readback_verifies_reported_success(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    observed = log.observe(target, digest=sha(b"hello"))
    assessment = fx.assess(claim, log.history)
    assert assessment.verdict is fx.EffectVerdict.VERIFIED
    assert assessment.observation_ids == (observed.observation_id,)


def test_receipts_and_acknowledgements_never_verify(root):
    for mechanism in (fx.ObservationMechanism.EXECUTION_RECEIPT, fx.ObservationMechanism.REMOTE_ACKNOWLEDGEMENT,
                      fx.ObservationMechanism.PROCESS_OWNERSHIP, fx.ObservationMechanism.JOB_STATE):
        log = Log()
        target = fs_ref(root, "a.txt", missing=True)
        claim = log.claim((target,), (content(target),))
        log.outcome(claim)
        log.observe(target, digest=sha(b"hello"), mechanism=mechanism)
        assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED


def test_independent_remote_readback_differs_from_acknowledgement():
    remote = fx.resource_ref(ExternalResource("mcp", "ep", "srv", "tool", "inc"), "target")
    log = Log()
    claim = log.claim((remote,), (fx.Postcondition(remote, fx.Predicate.EXISTS),))
    log.outcome(claim, facts=fx.ProducerFacts(exit_code=0, remote_acknowledged=True, external=True))
    log.observe(remote, mechanism=fx.ObservationMechanism.REMOTE_ACKNOWLEDGEMENT)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED
    log.observe(remote, mechanism=fx.ObservationMechanism.REMOTE_READBACK)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.VERIFIED


def test_verifier_before_mutation_does_not_count(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    log.observe(target, digest=sha(b"hello"))
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED


def test_observation_while_effect_in_flight_is_unsettled(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    early = log.observe(target, digest=sha(b"hello"))
    log.outcome(claim)
    assert fx.freshness(early, log.history) is fx.Freshness.UNSETTLED
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED


def test_stale_evidence_after_later_mutation_is_preserved_but_stale(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    observed = log.observe(target, digest=sha(b"hello"))
    later = log.claim((target,))
    log.outcome(later, fx.ExecutionOutcome.FAILED)
    history = log.history
    assert observed in history.observations  # history is never rewritten
    assert fx.invalidated_by(observed, history) == (later.effect_id,)
    assert fx.assess(claim, history).verdict is fx.EffectVerdict.UNVERIFIED


def test_concurrent_mutation_between_effect_and_verification(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    racing = log.claim((target,))
    log.observe(target, digest=sha(b"hello"))
    log.outcome(racing)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED


def test_unknown_mutation_scope_invalidates_everything_earlier(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    observed = log.observe(target, digest=sha(b"hello"))
    unknown = log.claim(())
    log.outcome(unknown, fx.ExecutionOutcome.INTERRUPTED)
    assert fx.invalidated_by(observed, log.history) == (unknown.effect_id,)


def test_refused_operation_is_a_known_noop_and_preserves_freshness(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    log.observe(target, digest=sha(b"hello"))
    refused = log.claim((target,))
    log.outcome(refused, fx.ExecutionOutcome.NOT_EXECUTED, fx.Impact.NONE)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.VERIFIED
    assert fx.assess(refused, log.history).verdict is fx.EffectVerdict.NOT_EXECUTED


def test_unrelated_resource_mutation_does_not_invalidate(root):
    log = Log()
    target, other = fs_ref(root, "a.txt", missing=True), fs_ref(root, "b.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    log.observe(target, digest=sha(b"hello"))
    log.outcome(log.claim((other,)))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.VERIFIED


def test_replacement_revealed_by_later_observation_makes_earlier_stale(root):
    _write(root, "a.txt", "hello")
    original = fs_ref(root, "a.txt")
    log = Log()
    claim = log.claim((original,), (content(original),))
    log.outcome(claim)
    first = log.observe(original, digest=sha(b"hello"))
    os.replace(_write(root, "tmp", "hello"), os.path.join(root.path, "a.txt"))
    replacement = fs_ref(root, "a.txt")
    second = log.observe(replacement, digest=sha(b"hello"))
    assert fx.invalidated_by(first, log.history) == (second.observation_id,)
    # The latest check is of the replacement: same bytes, still fresh.
    assert fx.freshness(second, log.history) is fx.Freshness.FRESH


def test_partial_read_cannot_verify_whole_content_and_blocks_fallback(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    log.observe(target, digest=sha(b"hello"))
    log.observe(target, coverage=fx.Coverage.PARTIAL)
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED
    # A partial read can still decide existence.
    exists = fx.Postcondition(target, fx.Predicate.EXISTS)
    log2 = Log()
    claim2 = log2.claim((target,), (exists,))
    log2.outcome(claim2)
    log2.observe(target, coverage=fx.Coverage.PARTIAL)
    assert fx.assess(claim2, log2.history).verdict is fx.EffectVerdict.VERIFIED


def test_partial_verifier_coverage_of_multiple_obligations(root):
    log = Log()
    a, b = fs_ref(root, "a.txt", missing=True), fs_ref(root, "b.txt", missing=True)
    claim = log.claim((a, b), (content(a), content(b)))
    log.outcome(claim)
    log.observe(a, digest=sha(b"hello"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED
    log.observe(b, digest=sha(b"hello"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.VERIFIED


def test_contradicting_fresh_observation(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim)
    log.observe(target, digest=sha(b"other"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.CONTRADICTED


def test_unknown_execution_matching_state_is_not_causation(root):
    for execution in (fx.ExecutionOutcome.INTERRUPTED, fx.ExecutionOutcome.TIMED_OUT,
                      fx.ExecutionOutcome.CANCELLED):
        log = Log()
        target = fs_ref(root, "a.txt", missing=True)
        claim = log.claim((target,), (content(target),))
        log.outcome(claim, execution)
        log.observe(target, digest=sha(b"hello"))
        assessment = fx.assess(claim, log.history)
        assert assessment.verdict is fx.EffectVerdict.STATE_OBSERVED
        assert "causality" in assessment.reason


def test_failed_execution_never_becomes_success(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim, fx.ExecutionOutcome.FAILED)
    assessment = fx.assess(claim, log.history)
    assert assessment.verdict is fx.EffectVerdict.FAILED and assessment.unresolved_impact
    log.observe(target, digest=sha(b"hello"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.FAILED


def test_unknown_partial_effect_has_unresolved_impact(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim, fx.ExecutionOutcome.TIMED_OUT, facts=fx.ProducerFacts(timed_out=True))
    assessment = fx.assess(claim, log.history)
    assert assessment.verdict is fx.EffectVerdict.UNVERIFIED and assessment.unresolved_impact


def test_cleanup_failure_is_preserved_separately_from_effect(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim, cleanup=fx.CleanupState.FAILED)
    log.observe(target, digest=sha(b"hello"))
    assessment = fx.assess(claim, log.history)
    assert assessment.verdict is fx.EffectVerdict.VERIFIED
    assert assessment.cleanup is fx.CleanupState.FAILED


def test_background_running_is_pending_until_settled(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    log.outcome(claim, fx.ExecutionOutcome.RUNNING, facts=fx.ProducerFacts(job_state="running"))
    log.observe(target, digest=sha(b"hello"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.PENDING
    log.outcome(claim)
    # The observation predates settlement; a new one is required.
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.UNVERIFIED
    log.observe(target, digest=sha(b"hello"))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.VERIFIED


def test_interrupted_claim_replays_as_unknown_never_success(root):
    log = Log()
    target = fs_ref(root, "a.txt", missing=True)
    claim = log.claim((target,), (content(target),))
    assert fx.assess(claim, log.history).verdict is fx.EffectVerdict.PENDING
    appended = fx.replay_interrupted(log.history, log.seq + 1)
    assert [o.execution for o in appended] == [fx.ExecutionOutcome.INTERRUPTED]
    assert appended[0].impact is fx.Impact.POSSIBLE and appended[0].replayed
    history = fx.EffectHistory((claim,), appended, ())
    assert fx.assess(claim, history).unresolved_impact
    assert fx.replay_interrupted(history, 99) == ()


def test_stale_owned_revision_does_not_verify():
    v1 = fx.resource_ref(OwnedResource("notes", "u", "t", "notes", "n1", "rev-1"), "target")
    v2 = fx.resource_ref(OwnedResource("notes", "u", "t", "notes", "n1", "rev-2"), "target")
    log = Log()
    claim = log.claim((v1,), (fx.Postcondition(v1, fx.Predicate.EXISTS),))
    log.outcome(claim)
    first = log.observe(v1, mechanism=fx.ObservationMechanism.OWNED_RECORD_READ)
    second = log.observe(v2, mechanism=fx.ObservationMechanism.OWNED_RECORD_READ)
    # The old revision's readback never inherits freshness once a different
    # revision of the same display ID is observed.
    assert fx.invalidated_by(first, log.history) == (second.observation_id,)
    assert fx.freshness(first, log.history) is fx.Freshness.STALE
    # Verification rests only on the newest readback, of the current revision.
    assert fx.assess(claim, log.history).observation_ids == (second.observation_id,)


def test_readbacks_require_admitted_source_action(root):
    target = fs_ref(root, "a.txt", missing=True)
    with pytest.raises(ValueError, match="admitted action"):
        fx.Observation("o", 1, target, fx.ObservationMechanism.FILESYSTEM_READ, fx.Coverage.COMPLETE)
    with pytest.raises(ValueError):
        fx.Observation("o", 1, target, fx.ObservationMechanism.FILESYSTEM_READ, fx.Coverage.COMPLETE,
                       source_action_id="a", exists=False, content_sha256=sha(b"x"))
