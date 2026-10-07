"""Wave 4 adapters for process/background, owned, external and browser bindings.

These drive ``begin_effect``/``settle_effect`` with real Wave 3 bound-operation
objects. The dispatcher's contextvar capture is replaced by the same objects so
that each producer family can be exercised without its live backend.
"""
from __future__ import annotations

import asyncio
import gc
import json

import pytest

from src import browser_identity
from src.agent_evidence import CompletionRequirements, CompletionStatus
from src.agent_runtime import effect_adapters as adapters
from src.agent_runtime import effects as fx
from src.agent_runtime.authority import ExactOperation
from src.agent_runtime.completion import _ledger
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.journal import ActionJournal
from src.agent_runtime.owned_resources import BoundOwnedOperation
from src.agent_runtime.process_resources import BoundProcessOperation, digest as process_digest
from src.agent_runtime.remote_resources import BoundBackendOperation
from src.agent_runtime.resources import (
    BackgroundJobResource, BrowserPageResource, BrowserSessionObservation, BrowserSessionResource, ExternalResource,
    FilesystemRoot, NativeBackendResource, OwnedResource, ProcessLaunchResource, ProcessLaunchScope, ProcessResource,
)
from src.process_lifecycle import ProcessIdentity
from src.tool_types import ToolBlock


GENERATION = "c" * 32


@pytest.fixture
def store(tmp_path):
    return tmp_path / "fx"


def journal_for(store, parent=None):
    journal = ActionJournal(parent_run_id=parent.run_id if parent else None)
    journal.effects = parent.effects if parent else EffectLog(journal.run_id, directory=store)
    return journal


def act(journal, monkeypatch, capture, tool="bash", content="{}", *, result=None, error=None):
    """One admitted action: claim at dispatch, then settle with a producer result."""
    action = journal.propose(ToolBlock(tool, content))
    monkeypatch.setattr(adapters, "capture_dispatch", lambda: capture)
    captured = adapters.begin_effect(journal, action)
    action.execution_id = action.action_id + ":execution:1"
    if result is not None:
        action.finish(result)
    adapters.settle_effect(journal, action, captured, result=result, error=error)
    return action, captured


def launch_capture(tmp_path, generation=GENERATION):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    operation = ExactOperation.normalize("bash", "#!bg\nsleep 1")
    scope = ProcessLaunchScope(NativeBackendResource("bash"), FilesystemRoot.seal(str(workspace)), frozenset({"filesystem"}))
    launch = ProcessLaunchResource("native:containment", "alice", "request", "thread", generation, "bash",
                                   process_digest(operation.input), scope, "b" * 64)
    return adapters.DispatchCapture(process=BoundProcessOperation(operation, "request", "alice", "thread", launch))


def job_capture(action="status", generation=GENERATION, job_id="job1"):
    supervisor = ProcessResource("native:bg_jobs", "alice", "request", "thread",
                                 ProcessIdentity(4242, "boot:1:100", None), "supervisor", job_id, "cont-1")
    job = BackgroundJobResource("native:bg_jobs", job_id, generation, "alice", "request", "thread", "cont-1",
                                (supervisor,))
    operation = ExactOperation.normalize("manage_bg_jobs", json.dumps({"action": action, "job_id": job_id}))
    return adapters.DispatchCapture(process=BoundProcessOperation(operation, "request", "alice", "thread", jobs=(job,)))


def job_result(status, exit_code=None, **flags):
    return {"output": "Job report says everything succeeded and was verified.", "exit_code": 0,
            "job": {"status": status, "exit_code": exit_code, "timed_out": False, "killed": False,
                    "died": False, **flags}}


# -- process / background ----------------------------------------------------

def test_process_exit_is_execution_evidence_not_a_postcondition(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "ls",
        result={"output": "ok", "exit_code": 0, "teardown": {"dead": True}})
    claim = journal.effects.history().claims[0]
    assert claim.unknown_scope, "an arbitrary command has unknown impact scope"
    assert [ref.kind for ref in claim.dependencies] == [fx.ResourceKind.PROCESS_LAUNCH]
    assessment = journal.effects.assessments()[0]
    assert (assessment.execution, assessment.verdict, assessment.cleanup) == (
        fx.ExecutionOutcome.REPORTED_SUCCESS, fx.EffectVerdict.UNVERIFIED, fx.CleanupState.VERIFIED)


@pytest.mark.parametrize("result,execution,cleanup", [
    ({"error": "timed out", "exit_code": 124, "timed_out": True, "teardown": {"dead": True}},
     fx.ExecutionOutcome.TIMED_OUT, fx.CleanupState.VERIFIED),
    ({"error": "teardown", "exit_code": 1, "failure_kind": "process_teardown_failed", "teardown": {"dead": False}},
     fx.ExecutionOutcome.FAILED, fx.CleanupState.FAILED),
    ({"output": "", "exit_code": 0, "status": "running", "detached": True, "containment": {"external": True}},
     fx.ExecutionOutcome.RUNNING, fx.CleanupState.UNKNOWN),
])
def test_process_outcomes_are_preserved_separately(tmp_path, store, monkeypatch, result, execution, cleanup):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "x", result=result)
    assessment = journal.effects.assessments()[0]
    assert (assessment.execution, assessment.cleanup) == (execution, cleanup)
    assert assessment.unresolved_impact


def test_cleanup_failure_after_command_unsettles_required_artifact(tmp_path, store, monkeypatch):
    workspace = tmp_path / "ws"
    journal = journal_for(store)
    journal.workspace, journal.observed_artifacts = str(workspace), ("out.txt",)
    write = journal.propose(ToolBlock("write_file", json.dumps({"path": "out.txt", "content": "x"})))
    write.execution_id = write.action_id + ":execution:1"
    write.finish({"output": "Wrote", "exit_code": 0})
    (workspace).mkdir(exist_ok=True)
    (workspace / "out.txt").write_text("x")
    requirements = CompletionRequirements(required_artifacts=("out.txt",), workspace_root=str(workspace))
    assert _ledger(journal, requirements).evaluate().can_complete
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "x",
        result={"error": "teardown", "exit_code": 1, "failure_kind": "process_teardown_failed",
                "teardown": {"dead": False}})
    decision = _ledger(journal, requirements).evaluate()
    assert decision.status == CompletionStatus.BLOCKED and decision.missing_artifacts == ("out.txt",)


def test_background_launch_is_running_not_completed_work(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started background job `job1`.", "exit_code": 0, "bg_job_id": "job1"})
    assessment = journal.effects.assessments()[0]
    assert (assessment.execution, assessment.verdict) == (fx.ExecutionOutcome.RUNNING, fx.EffectVerdict.PENDING)


def test_exact_job_read_settles_launch_across_a_continuation_run(tmp_path, store, monkeypatch):
    first = journal_for(store)
    act(first, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    launch_effect = first.effects.history().claims[0]
    # A still-running job does not settle anything.
    second = journal_for(store)
    act(second, monkeypatch, job_capture(), "manage_bg_jobs", "{}", result=job_result("running"))
    assert fx.assess(launch_effect, first.effects.history()).verdict is fx.EffectVerdict.PENDING
    assert second.effects.history().observations[0].mechanism is fx.ObservationMechanism.JOB_STATE

    del first
    gc.collect()  # the launching run is gone: settle through its durable log
    third = journal_for(store)
    act(third, monkeypatch, job_capture(), "manage_bg_jobs", "{}", result=job_result("done", 0))
    reloaded = EffectLog.load(launch_effect.run_id, directory=store)
    assessment = fx.assess(launch_effect, reloaded.history())
    # Delivered completion is execution evidence; the job's report prose is
    # attributed content and verifies nothing.
    assert (assessment.execution, assessment.verdict) == (fx.ExecutionOutcome.REPORTED_SUCCESS,
                                                          fx.EffectVerdict.UNVERIFIED)


@pytest.mark.parametrize("record,execution", [
    ({"status": "done", "exit_code": 0}, fx.ExecutionOutcome.REPORTED_SUCCESS),
    ({"status": "failed", "exit_code": 2}, fx.ExecutionOutcome.FAILED),
    ({"status": "failed", "exit_code": 124, "timed_out": True}, fx.ExecutionOutcome.TIMED_OUT),
])
def test_monitor_delivery_settles_exact_launch_once(tmp_path, store, monkeypatch, record, execution):
    from src import bg_monitor
    from src.agent_runtime import effect_log
    monkeypatch.setattr(effect_log, "EFFECTS_DIR", str(store))
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    job = job_capture().process.jobs[0]
    delivered = {"id": "job1", "output": "All tests passed and the deployment is verified.", **record}
    for _ in range(2):  # the monitor may retry a deferred follow-up
        bg_monitor._settle_launch_effect(job, delivered)
    history = journal.effects.history()
    assert [o.execution for o in history.outcomes] == [fx.ExecutionOutcome.RUNNING, execution]
    assert history.observations == ()  # delivery is not an observation
    assert fx.assess(history.claims[0], history).verdict in {fx.EffectVerdict.UNVERIFIED, fx.EffectVerdict.FAILED}


def test_job_linkage_requires_the_exact_generation(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    # Same display job id, different launch generation: a replacement job.
    act(journal, monkeypatch, job_capture(generation="d" * 32), "manage_bg_jobs", "{}",
        result=job_result("done", 0))
    assert journal.effects.assessments()[0].execution is fx.ExecutionOutcome.RUNNING


def test_killed_job_settles_as_cancelled(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    act(journal, monkeypatch, job_capture("kill"), "manage_bg_jobs", "{}",
        result={**job_result("failed", -9, killed=True), "output": "Killed"})
    kill_claim = journal.effects.history().claims[1]
    assert [ref.kind for ref in kill_claim.impact_scope] == [fx.ResourceKind.BACKGROUND_JOB, fx.ResourceKind.PROCESS]
    assert journal.effects.assessments()[0].execution is fx.ExecutionOutcome.CANCELLED


def test_running_background_work_unsettles_later_required_artifact(tmp_path, store, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "out.txt").write_text("x")
    journal = journal_for(store)
    journal.workspace = str(workspace)
    write = journal.propose(ToolBlock("write_file", json.dumps({"path": "out.txt", "content": "x"})))
    write.execution_id = write.action_id + ":execution:1"
    write.finish({"output": "Wrote", "exit_code": 0})
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    requirements = CompletionRequirements(required_artifacts=("out.txt",), workspace_root=str(workspace))
    assert _ledger(journal, requirements).evaluate().status == CompletionStatus.BLOCKED


# -- owned records ------------------------------------------------------------

def owned_capture(tool, payload, record):
    operation = ExactOperation.normalize(tool, json.dumps(payload))
    return adapters.DispatchCapture(owned=BoundOwnedOperation(operation, operation.input, "request", "alice",
                                                              "thread", (record,)))


def test_owned_mutation_claims_exact_record_and_stays_unverified(store, monkeypatch):
    record = OwnedResource("notes", "alice", "thread", "notes", "n1", "rev-1")
    journal = journal_for(store)
    act(journal, monkeypatch, owned_capture("manage_notes", {"action": "update", "id": "n1"}, record),
        "manage_notes", result={"output": "Note updated and verified.", "exit_code": 0})
    claim = journal.effects.history().claims[0]
    assert claim.impact_scope == (fx.resource_ref(record, "record"),)
    assert journal.effects.assessments()[0].verdict is fx.EffectVerdict.UNVERIFIED


def test_same_display_id_new_revision_does_not_inherit_freshness(store, monkeypatch):
    journal = journal_for(store)
    old = OwnedResource("vault", "alice", "thread", "vault", "rec", "rev-1")
    new = OwnedResource("vault", "alice", "thread", "vault", "rec", "rev-2")
    act(journal, monkeypatch, owned_capture("vault_get", {"id": "rec"}, old), "vault_get",
        result={"output": "secret", "exit_code": 0})
    act(journal, monkeypatch, owned_capture("vault_get", {"id": "rec"}, new), "vault_get",
        result={"output": "secret", "exit_code": 0})
    history = journal.effects.history()
    first, second = history.observations
    assert history.claims == ()
    assert fx.freshness(first, history) is fx.Freshness.STALE
    assert fx.freshness(second, history) is fx.Freshness.FRESH


# -- external / MCP -------------------------------------------------------------

def test_remote_success_is_acknowledgement_not_state(store, monkeypatch):
    remote = ExternalResource("mcp", "endpoint", "server", "tool", "inc-1")
    bound = BoundBackendOperation(remote, "request", "alice", "thread", "mcp__server__tool", "{}")
    journal = journal_for(store)
    act(journal, monkeypatch, adapters.DispatchCapture(backend=bound), "mcp__server__tool",
        result={"output": "Successfully created and verified the record.", "exit_code": 0})
    claim = journal.effects.history().claims[0]
    assert claim.external and claim.impact_scope == (fx.resource_ref(remote, "backend"),)
    outcome = journal.effects.history().outcomes[0]
    assert outcome.facts.remote_acknowledged and outcome.facts.external
    assert outcome.cleanup is fx.CleanupState.UNKNOWN
    assert journal.effects.history().observations == ()
    assert journal.effects.assessments()[0].verdict is fx.EffectVerdict.UNVERIFIED


def test_remote_failure_after_send_may_have_changed_state(store, monkeypatch):
    remote = ExternalResource("mcp", "endpoint", "server", "tool", "inc-1")
    bound = BoundBackendOperation(remote, "request", "alice", "thread", "mcp__server__tool", "{}")
    journal = journal_for(store)
    act(journal, monkeypatch, adapters.DispatchCapture(backend=bound), "mcp__server__tool",
        error=TimeoutError("transport closed after send"))
    assessment = journal.effects.assessments()[0]
    assert assessment.execution is fx.ExecutionOutcome.INTERRUPTED and assessment.unresolved_impact


# -- browser session metadata only --------------------------------------------

def session(monkeypatch, incarnation_seed="1"):
    monkeypatch.setattr(browser_identity, "PRODUCER_HASHES", {"linux-x64": "e" * 64})
    values = {"producer_namespace": "native:agent-browser", "producer_version": "0.35.0", "platform": "linux-x64",
              "binary_sha256": "e" * 64, "configuration_digest": "1" * 64, "session_key": "ody-" + "a" * 24,
              "daemon": {"pid": 4321, "start_token": "boot:" + incarnation_seed, "pgid": 4321},
              "browser_instance_digest": incarnation_seed * 64}
    observation = BrowserSessionObservation(**{**values, "daemon": ProcessIdentity(4321, "boot:" + incarnation_seed, 4321),
                                               "session_incarnation": browser_identity.incarnation(values)})
    return BrowserSessionResource("alice", "thread", observation)


def test_browser_session_info_is_lifecycle_observation_only(store, monkeypatch):
    journal = journal_for(store)
    operation = ExactOperation.normalize("private_browser", json.dumps({"action": "session_info"}))
    first = browser_identity.BoundBrowserOperation(operation, "request", "alice", "thread", session(monkeypatch, "1"))
    replaced = browser_identity.BoundBrowserOperation(operation, "request", "alice", "thread", session(monkeypatch, "2"))
    for bound in (first, replaced):
        act(journal, monkeypatch, adapters.DispatchCapture(browser=bound), "private_browser",
            result={"output": "{}", "exit_code": 0, "executed": True, "browser_page_operations_supported": False})
    history = journal.effects.history()
    assert history.claims == ()
    assert {o.mechanism for o in history.observations} == {fx.ObservationMechanism.BROWSER_SESSION}
    # Session replacement never transfers freshness to the new session.
    assert fx.freshness(history.observations[0], history) is fx.Freshness.STALE
    # A session observation decides no file/record/remote postcondition.
    assert all(o.coverage is fx.Coverage.PARTIAL for o in history.observations)


def test_browser_page_binding_never_becomes_effect_scope(store, monkeypatch):
    journal = journal_for(store)
    owner_session = session(monkeypatch)
    page = BrowserPageResource(owner_session, "A" * 32, "loader")
    operation = ExactOperation.normalize("private_browser", json.dumps({"action": "session_info"}))
    bound = browser_identity.BoundBrowserOperation(operation, "request", "alice", "thread", owner_session, page)
    act(journal, monkeypatch, adapters.DispatchCapture(browser=bound), "private_browser",
        result={"output": "{}", "exit_code": 0})
    claim = journal.effects.history().claims[0]
    assert claim.unknown_scope and journal.effects.history().observations == ()
    with pytest.raises(TypeError):
        fx.resource_ref(page, "target")


# -- lineage --------------------------------------------------------------------

def test_child_effects_share_lineage_order_and_invalidate_parent_evidence(tmp_path, store, monkeypatch):
    parent = journal_for(store)
    old = OwnedResource("vault", "alice", "thread", "vault", "rec", "rev-1")
    act(parent, monkeypatch, owned_capture("vault_get", {"id": "rec"}, old), "vault_get",
        result={"output": "x", "exit_code": 0})
    child = journal_for(store, parent=parent)
    act(child, monkeypatch, launch_capture(tmp_path), "bash", "x", result={"output": "", "exit_code": 0})
    history = parent.effects.history()
    claim = history.claims[0]
    assert (claim.run_id, claim.parent_run_id) == (child.run_id, parent.run_id)
    # The child's unknown-scope command may have changed the parent's record.
    assert fx.freshness(history.observations[0], history) is fx.Freshness.STALE


def test_listing_that_reports_other_work_running_is_not_a_running_effect(store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, adapters.DispatchCapture(), "list_downloads",
        result={"output": "1 download", "exit_code": 0, "status": "running", "running": True})
    assert journal.effects.assessments()[0].execution is fx.ExecutionOutcome.REPORTED_SUCCESS


def test_scheduler_trigger_is_admission_not_completed_work(store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, adapters.DispatchCapture(), "manage_tasks",
        content=json.dumps({"action": "run", "task_id": "t1"}),
        result={"output": "Task t1 triggered; it completed successfully.", "exit_code": 0})
    claim = journal.effects.history().claims[0]
    assessment = journal.effects.assessments()[0]
    # Unbound task control may change anything; its reply verifies nothing.
    assert claim.unknown_scope and assessment.verdict is fx.EffectVerdict.UNVERIFIED


def test_assessment_scales_to_long_lineages(store):
    from time import perf_counter
    log = EffectLog("f" * 32, directory=store, durable=False)
    owned = [fx.resource_ref(OwnedResource("notes", "u", "t", "notes", f"n{i}", "r"), "record") for i in range(600)]
    for i, ref in enumerate(owned):
        log.claim(effect_id=f"e{i}", action_id=f"a{i}", operation=fx.OperationRef("manage_notes", "", "0" * 64),
                  impact_scope=(ref,), obligations=(fx.Postcondition(ref, fx.Predicate.EXISTS),))
        log.outcome(effect_id=f"e{i}", execution=fx.ExecutionOutcome.REPORTED_SUCCESS, impact=fx.Impact.POSSIBLE)
        log.observe(observation_id=f"o{i}", resource=ref, mechanism=fx.ObservationMechanism.OWNED_RECORD_READ,
                    coverage=fx.Coverage.PARTIAL, source_action_id=f"r{i}", exists=True)
    started = perf_counter()
    assessments = log.assessments()
    assert perf_counter() - started < 10
    assert {a.verdict for a in assessments} == {fx.EffectVerdict.VERIFIED}


def test_classification_failure_claims_unknown_scope(store, monkeypatch):
    journal = journal_for(store)
    monkeypatch.setattr(adapters, "classify", lambda capture: (_ for _ in ()).throw(KeyError("bug")))
    act(journal, monkeypatch, adapters.DispatchCapture(), "anything", result={"output": "", "exit_code": 0})
    assert journal.effects.history().claims[0].unknown_scope


def test_cancellation_is_recorded_without_inventing_a_result(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "x", error=asyncio.CancelledError())
    assessment = journal.effects.assessments()[0]
    assert (assessment.execution, assessment.cleanup) == (fx.ExecutionOutcome.CANCELLED, fx.CleanupState.UNKNOWN)


# -- producer trust boundary ---------------------------------------------------

def test_running_requires_a_server_launch_reservation(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    # A process producer without a launch reservation cannot start work.
    act(journal, monkeypatch, job_capture("kill"), "manage_bg_jobs", json.dumps({"action": "kill"}),
        result={"output": "Killed", "exit_code": 0, "bg_job_id": "job9"})
    # An unbound producer cannot detach anything.
    act(journal, monkeypatch, adapters.DispatchCapture(), "plugin_sync",
        result={"output": "", "exit_code": 0, "detached": True, "teardown": {"dead": True}})
    first, second = journal.effects.history().outcomes
    assert first.execution is fx.ExecutionOutcome.REPORTED_SUCCESS
    assert (second.execution, second.cleanup) == (fx.ExecutionOutcome.REPORTED_SUCCESS,
                                                  fx.CleanupState.NOT_APPLICABLE)
    assert second.facts == fx.ProducerFacts(exit_code=0)


def test_unbound_job_lifecycle_cannot_settle_a_launch(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nsleep 1",
        result={"output": "Started", "exit_code": 0, "bg_job_id": "job1"})
    act(journal, monkeypatch, adapters.DispatchCapture(), "plugin_status", result=job_result("done", 0))
    assert journal.effects.assessments()[0].execution is fx.ExecutionOutcome.RUNNING


# -- truthful completion ---------------------------------------------------------

def remote_act(journal, monkeypatch, **outcome):
    remote = ExternalResource("mcp", "endpoint", "server", "send_email", "inc-1")
    bound = BoundBackendOperation(remote, "request", "alice", "thread", "mcp__server__send_email", "{}")
    return act(journal, monkeypatch, adapters.DispatchCapture(backend=bound), "mcp__server__send_email", **outcome)


def written_artifact(tmp_path, store):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    (workspace / "out.txt").write_text("x")
    journal = journal_for(store)
    journal.workspace = str(workspace)
    write = journal.propose(ToolBlock("write_file", json.dumps({"path": "out.txt", "content": "x"})))
    write.execution_id = write.action_id + ":execution:1"
    write.finish({"output": "Wrote", "exit_code": 0})
    return journal, CompletionRequirements(required_artifacts=("out.txt",), workspace_root=str(workspace))


DISCLOSURE = ("External operation mcp__server__send_email reported success; any external change it made was "
              "not independently verified.")


@pytest.mark.parametrize("answer", ["Here is the draft.   \n\n", " \n\n"])
@pytest.mark.parametrize("final", [False, True])
async def test_streaming_external_disclosure_survives_trailing_whitespace(store, monkeypatch, answer, final):
    from src.agent_runtime.completion import completion_answer, with_completion_gate
    from src.agent_runtime.journal import current_journal

    expected = []

    @with_completion_gate
    async def stream(messages):
        journal = current_journal()
        journal.effects = EffectLog(journal.run_id, directory=store)
        remote_act(journal, monkeypatch, result={"stdout": "ok", "stderr": "", "exit_code": 0})
        ledger = _ledger(journal, CompletionRequirements())
        expected.append(completion_answer(answer, ledger, ledger.evaluate())[0])
        yield "data: " + json.dumps({"type": "final_response", "content": answer} if final else {"delta": answer}) + "\n\n"
        yield "data: " + json.dumps({"type": "metrics", "data": {"round_texts": [answer]}}) + "\n\n"

    events = [json.loads(chunk[6:]) async for chunk in stream([])]
    disclosure = next(event["delta"] for event in events if event.get("delta") != answer and "delta" in event)
    assert disclosure == ("\n\n" if answer.strip() else "") + DISCLOSURE
    visible = "".join(event.get("delta", event.get("content", "")) for event in events)
    assert visible.count(DISCLOSURE) == 1
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert metrics["round_texts"] == expected
    assert expected[0].count(DISCLOSURE) == 1
    assert metrics["completion_gate"]["answer_replaced"] is False


def test_reported_external_mutation_cannot_complete_as_satisfied(tmp_path, store, monkeypatch):
    from src.agent_evidence import EXTERNAL_EFFECT_UNVERIFIED
    from src.agent_runtime.completion import completion_answer
    journal, requirements = written_artifact(tmp_path, store)
    assert _ledger(journal, requirements).evaluate().status == CompletionStatus.SATISFIED
    remote_act(journal, monkeypatch, result={"stdout": "Message sent", "stderr": "", "exit_code": 0})
    ledger = _ledger(journal, requirements)
    decision = ledger.evaluate()
    assert (decision.status, decision.can_complete, decision.reason) == (
        CompletionStatus.UNVERIFIED, True, EXTERNAL_EFFECT_UNVERIFIED)
    text = "I wrote out.txt. I sent the summary to Bob. I updated it. Bob has been notified. Done."
    answer, _ = completion_answer(text, ledger, decision)
    assert "I wrote out.txt." in answer
    for unsupported in ("I sent", "I updated it", "Done."):
        assert unsupported not in answer
    # Whatever phrasing survives, the server states the unverified effect.
    assert answer.rstrip().endswith(DISCLOSURE)


def test_reported_external_mutation_without_artifacts_is_disclosed(store, monkeypatch):
    from src.agent_runtime.completion import completion_answer
    journal = journal_for(store)
    remote_act(journal, monkeypatch, result={"stdout": "ok", "stderr": "", "exit_code": 0})
    ledger = _ledger(journal, CompletionRequirements())
    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.UNVERIFIED
    answer, _ = completion_answer("I sent the email to the user. The remote operation succeeded.", ledger, decision)
    assert "I sent the email" not in answer and "remote operation succeeded" not in answer
    assert answer.startswith("Unsupported execution claims were omitted") and answer.endswith(DISCLOSURE)


def test_unknown_external_outcome_is_disclosed_as_unknown(store, monkeypatch):
    from src.agent_runtime.completion import completion_answer
    journal = journal_for(store)
    remote_act(journal, monkeypatch, error=TimeoutError("transport closed after send"))
    ledger = _ledger(journal, CompletionRequirements())
    answer, _ = completion_answer("Here is the draft.", ledger, ledger.evaluate())
    assert answer.endswith("External operation mcp__server__send_email has an unknown outcome; it may or may "
                           "not have taken effect.")


def test_passing_tests_stay_a_test_fact_beside_an_external_effect(tmp_path, store, monkeypatch):
    from src.agent_runtime.completion import completion_answer
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "pytest -q",
        result={"output": "1 passed", "exit_code": 0})
    remote_act(journal, monkeypatch, result={"stdout": "ok", "stderr": "", "exit_code": 0})
    ledger = _ledger(journal, CompletionRequirements())
    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.UNVERIFIED and decision.can_complete
    answer, _ = completion_answer("All tests passed.", ledger, decision)
    assert answer.startswith("All tests passed.") and answer.endswith(DISCLOSURE)


# -- effect obligations without declared artifacts -------------------------------

def test_unsettled_effect_after_verifier_blocks_verified_completion(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "pytest -q",
        result={"output": "1 passed", "exit_code": 0})
    assert _ledger(journal, CompletionRequirements()).evaluate().status == CompletionStatus.VERIFIED
    act(journal, monkeypatch, launch_capture(tmp_path, "d" * 32), "bash", "x", error=RuntimeError("lost"))
    decision = _ledger(journal, CompletionRequirements()).evaluate()
    assert decision.status == CompletionStatus.BLOCKED and not decision.can_complete


def test_settled_effect_after_verifier_keeps_verified(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "pytest -q",
        result={"output": "1 passed", "exit_code": 0})
    act(journal, monkeypatch, launch_capture(tmp_path, "d" * 32), "bash", "echo hi",
        result={"output": "hi", "exit_code": 0, "teardown": {"dead": True}})
    assert _ledger(journal, CompletionRequirements()).evaluate().status == CompletionStatus.VERIFIED


def test_background_launch_without_obligations_can_complete_unverified(tmp_path, store, monkeypatch):
    journal = journal_for(store)
    act(journal, monkeypatch, launch_capture(tmp_path), "bash", "#!bg\nnpm run dev",
        result={"output": "Started background job `job1`.", "exit_code": 0, "bg_job_id": "job1"})
    decision = _ledger(journal, CompletionRequirements()).evaluate()
    assert (decision.status, decision.can_complete) == (CompletionStatus.UNVERIFIED, True)


def test_child_known_scope_mutation_leaves_unrelated_parent_evidence_fresh(store, monkeypatch):
    parent = journal_for(store)
    read = OwnedResource("vault", "alice", "thread", "vault", "rec", "rev-1")
    act(parent, monkeypatch, owned_capture("vault_get", {"id": "rec"}, read), "vault_get",
        result={"output": "x", "exit_code": 0})
    child = journal_for(store, parent=parent)
    other = OwnedResource("notes", "alice", "thread", "notes", "n1", "rev-1")
    act(child, monkeypatch, owned_capture("manage_notes", {"action": "update", "id": "n1"}, other),
        "manage_notes", result={"output": "updated", "exit_code": 0})
    history = parent.effects.history()
    # Exact child scope invalidates only what it overlaps.
    assert fx.freshness(history.observations[0], history) is fx.Freshness.FRESH
