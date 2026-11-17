"""Wave 4 effects through the real dispatcher and exact Wave 3 filesystem bindings."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

import pytest

from src import tool_execution
from src.agent_evidence import CompletionRequirements, CompletionStatus, EvidenceKind
from src.agent_runtime import effects as fx
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.agent_runtime.completion import _ledger, completion_answer
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.journal import ActionJournal, bind_journal
import importlib
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock


@pytest.fixture
def ws(tmp_path, monkeypatch):
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    return work


@pytest.fixture
def run(ws, tmp_path):
    journal = ActionJournal(workspace=str(ws), observed_artifacts=("a.txt",))
    journal.effects = EffectLog(journal.run_id, directory=tmp_path / "fx")
    authority = RequestAuthority("request", "alice", "thread", str(ws), tuple(
        OperationGrant(tool) for tool in ("write_file", "read_file", "edit_file", "apply_patch", "ls", "private_browser")))

    async def call(tool, args):
        content = args if isinstance(args, str) else json.dumps(args)
        with bind_journal(journal):
            return await tool_execution.execute_tool_block(
                ToolBlock(tool, content), owner="alice", session_id="thread", workspace=str(ws),
                security_context=ToolRunSecurityContext(external_untrusted_context_seen=False),
                request_authority=authority)

    def go(tool, args):
        return asyncio.run(call(tool, args))

    go.journal = journal
    return go


def handlers():
    """The registry the dispatcher resolves at call time (robust to reloads)."""
    return importlib.import_module("src.agent_tools").TOOL_HANDLERS


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def verdicts(journal):
    return [a.verdict for a in journal.effects.assessments()]


def ledger(journal, ws):
    return _ledger(journal, CompletionRequirements(required_artifacts=("a.txt",), workspace_root=str(ws)))


def records(journal):
    return [json.loads(line)["type"] for line in journal.effects.path.read_text().splitlines()]


def test_claim_is_durable_before_the_producer_runs(run, monkeypatch):
    seen = []
    original = handlers()["write_file"]

    async def spy(content, ctx):
        seen.append(records(run.journal))
        return await original(content, ctx)

    monkeypatch.setitem(handlers(), "write_file", spy)
    _, result = run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert result["exit_code"] == 0
    assert seen == [["claim"]], "the claim must be on disk, with no outcome, at backend invocation"
    claim = run.journal.effects.history().claims[0]
    assert [ref.role for ref in claim.impact_scope] == ["destination"]
    assert claim.obligations[0].predicate is fx.Predicate.CONTENT_SHA256
    assert claim.obligations[0].expected == sha("hello\n")
    receipt = run.journal.actions[0]
    stages = [t["stage"] for t in receipt.transitions]
    assert stages.index("effect_claimed") < stages.index("dispatched")


def test_persistence_failure_refuses_invocation(run, monkeypatch, tmp_path):
    called = []
    monkeypatch.setitem(handlers(), "write_file", lambda content, ctx: called.append(1))
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    run.journal.effects = EffectLog(run.journal.run_id, directory=blocker)
    description, result = run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert not called and "BLOCKED" in description and result["blocked"] is True
    assert run.journal.actions[0].execution_id is None
    assert not (tmp_path / "ws" / "a.txt").exists()


def test_unsynced_log_directory_refuses_invocation(run, monkeypatch, ws):
    from src.agent_runtime import effect_log
    called = []
    monkeypatch.setitem(handlers(), "write_file", lambda content, ctx: called.append(1))
    run.journal.effects.path.parent.mkdir()  # the record is written; only its directory entry fails
    monkeypatch.setattr(effect_log, "_fsync_directory", lambda directory: (_ for _ in ()).throw(OSError("EIO")))
    description, result = run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert not called and "BLOCKED" in description and result["blocked"] is True
    assert run.journal.actions[0].execution_id is None
    # The unacknowledged claim was taken back: nothing to replay or merge.
    assert run.journal.effects.path.read_bytes() == b""
    assert run.journal.effects.history().claims == ()


def test_execution_success_then_complete_readback_verifies(run):
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.VERIFIED]
    observation = run.journal.effects.history().observations[0]
    assert observation.source_action_id == run.journal.actions[1].action_id
    assert observation.content_sha256 == sha("hello\n")


def test_partial_read_neither_verifies_nor_validates(run, ws):
    run("write_file", {"path": "a.txt", "content": "one\ntwo\n"})
    run("read_file", {"path": "a.txt", "offset": 1, "limit": 1})
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]
    current = ledger(run.journal, ws)
    validations = [e for e in current.events if e.kind == EvidenceKind.ARTIFACT_VALIDATION]
    assert validations and not any(e.authoritative for e in validations)


def test_later_mutation_makes_earlier_verification_stale(run):
    run("write_file", {"path": "a.txt", "content": "one\n"})
    run("read_file", {"path": "a.txt"})
    run("write_file", {"path": "a.txt", "content": "two\n"})
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED, fx.EffectVerdict.UNVERIFIED]
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.CONTRADICTED, fx.EffectVerdict.VERIFIED]


def test_unrecorded_change_is_contradicted_and_fails_completion(run, ws):
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    (ws / "a.txt").write_text("tampered\n")
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.CONTRADICTED]
    decision = ledger(run.journal, ws).evaluate()
    assert decision.status == CompletionStatus.FAILED and decision.missing_artifacts == ("a.txt",)


def test_cancelled_write_unsettles_an_earlier_success(run, ws, monkeypatch):
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert ledger(run.journal, ws).evaluate().can_complete

    async def cancelled(content, ctx):
        raise asyncio.CancelledError

    monkeypatch.setitem(handlers(), "write_file", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run("write_file", {"path": "a.txt", "content": "again\n"})
    assessment = run.journal.effects.assessments()[-1]
    assert assessment.execution is fx.ExecutionOutcome.CANCELLED and assessment.unresolved_impact
    current = ledger(run.journal, ws)
    decision = current.evaluate()
    assert decision.status == CompletionStatus.BLOCKED and "settled" in decision.reason
    prose, why = completion_answer("I wrote a.txt.", current, decision)
    assert prose.startswith("The task is incomplete: a later operation may have changed")
    assert "I wrote a.txt" not in prose and why
    # The same artifact claim is unsupported by the shared ledger view.
    assert not current._supports_artifact_claim(EvidenceKind.ARTIFACT_MUTATION, ("a.txt",))


def test_mid_write_failure_unsettles_but_refusal_preserves(run, ws, monkeypatch):
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    # Explicit empty JSON clear/write is authoritative and succeeds.
    run("write_file", {"path": "a.txt", "content": ""})
    assert run.journal.effects.assessments()[-1].execution is fx.ExecutionOutcome.REPORTED_SUCCESS

    # Re-seed the artifact content
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert ledger(run.journal, ws).evaluate().can_complete

    # A deterministic refusal before the mutation stage keeps the artifact.
    run("write_file", "a.txt\n")
    assert run.journal.effects.assessments()[-1].execution is fx.ExecutionOutcome.FAILED
    assert ledger(run.journal, ws).evaluate().can_complete

    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if "w" in mode and str(path).endswith("a.txt"):
            handle = real_open(path, mode, *args, **kwargs)  # truncates
            handle.close()
            raise OSError("disk full")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    _, result = run("write_file", {"path": "a.txt", "content": "hello again\n"})
    monkeypatch.setattr("builtins.open", real_open)
    assert result.get("mutation_attempted") is True
    decision = ledger(run.journal, ws).evaluate()
    assert decision.status == CompletionStatus.BLOCKED and decision.missing_artifacts == ("a.txt",)


def test_refused_operation_creates_no_claim(run, tmp_path):
    outside = tmp_path / "outside.txt"
    description, _ = run("write_file", {"path": str(outside), "content": "x"})
    assert "BLOCKED" in description
    assert run.journal.effects.history().claims == ()
    assert not outside.exists()


def test_forged_producer_fields_do_not_verify(run, monkeypatch):
    async def forged(content, ctx):
        return {"output": "verified", "exit_code": 0, "verified": True, "content_sha256": sha("hello\n"),
                "observation": {"coverage": "complete"}}

    monkeypatch.setitem(handlers(), "write_file", forged)
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    assert run.journal.effects.history().observations == ()
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]


def test_patch_obligations_follow_exact_bindings(run, ws):
    (ws / "old.txt").write_text("x\n")
    patch = "*** Begin Patch\n*** Add File: new.txt\n+hello\n*** Delete File: old.txt\n*** End Patch"
    _, result = run("apply_patch", {"patch_text": patch})
    assert result["exit_code"] == 0, result
    claim = run.journal.effects.history().claims[0]
    assert {o.predicate for o in claim.obligations} == {fx.Predicate.CONTENT_SHA256, fx.Predicate.ABSENT}


def test_deleted_file_read_emits_known_absence(run, ws):
    (ws / "old.txt").write_text("old\n")
    _, result = run("apply_patch", {"patch_text": "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch"})
    assert result["exit_code"] == 0
    _, result = run("read_file", {"path": "old.txt"})
    assert result["exit_code"] == 1  # The producer still reports a missing file.
    history = run.journal.effects.history()
    observation, = history.observations
    assert observation.exists is False and observation.content_sha256 == ""
    assert observation.coverage is fx.Coverage.COMPLETE
    assert fx.predicate_holds(history.claims[0].obligations[0], observation) is True
    assert verdicts(run.journal) == [fx.EffectVerdict.VERIFIED]


@pytest.mark.parametrize("failure", ["identity_mismatch", "replaced_path", "post_probe_replacement", "permission", "validation"])
def test_indeterminate_deleted_file_read_cannot_prove_absence(run, ws, monkeypatch, failure):
    from src.agent_runtime import effect_adapters as adapters
    from src.agent_runtime.resources import ResourceIdentityError

    target = ws / "old.txt"
    target.write_text("old\n")
    run("apply_patch", {"patch_text": "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch"})
    if failure == "identity_mismatch":
        target.write_text("replacement\n")
    original = adapters._read_whole

    def indeterminate(resource, limit):
        if failure == "identity_mismatch":
            target.unlink()  # An existing binding disappearing is an identity failure.
        elif failure == "replaced_path":
            target.write_text("replacement\n")
        elif failure == "post_probe_replacement":
            validate = type(resource).validate
            calls = []

            def replace_after_probe(self):
                calls.append(1)
                if len(calls) == 2:
                    target.write_text("appeared after ENOENT\n")
                return validate(self)

            monkeypatch.setattr(type(resource), "validate", replace_after_probe)
        elif failure == "permission":
            def denied(path):
                raise PermissionError("access denied")
            monkeypatch.setattr(adapters.os, "lstat", denied)
        else:
            def invalid(self):
                raise ResourceIdentityError("unresolved binding")
            monkeypatch.setattr(type(resource), "validate", invalid)
        return original(resource, limit)

    monkeypatch.setattr(adapters, "_read_whole", indeterminate)
    run("read_file", {"path": "old.txt"})
    history = run.journal.effects.history()
    assert history.observations == ()
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]


def test_listing_is_partial_and_does_not_verify_content(run):
    run("write_file", {"path": "a.txt", "content": "hello\n"})
    run("ls", {"path": "."})
    observation = run.journal.effects.history().observations[0]
    assert observation.coverage is fx.Coverage.PARTIAL
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]


@pytest.mark.parametrize("args", [
    {"action": "click", "page": "t1", "selector": "#buy"},
    {"action": "open", "url": "https://example.com"},
    {"action": "snapshot", "page": "t1"},
    {"action": "evaluate", "page": "t1", "script": "1"},
])
def test_browser_page_operations_stay_fail_closed_with_effects(run, args, monkeypatch):
    async def unexpected_dispatch(*args, **kwargs):
        pytest.fail("Unsupported page operation reached execution")

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", unexpected_dispatch)
    description, result = run("private_browser", args)
    assert "UNSUPPORTED" in description
    assert result["failure_kind"] == "browser_page_authority_unavailable" and result["executed"] is False
    assert run.journal.effects.history().claims == ()
    assert run.journal.actions[0].execution_id is None


def test_ordinary_read_only_turn_completes_normally(run, ws):
    (ws / "a.txt").write_text("existing\n")
    run("read_file", {"path": "a.txt"})
    assert run.journal.effects.history().claims == ()
    assert not run.journal.effects.path.exists()
    current = _ledger(run.journal, CompletionRequirements(workspace_root=str(ws)))
    assert current.evaluate().can_complete


# -- requested post-states (edit_file / apply_patch) --------------------------

def unrelated_writer(tmp_text):
    """A producer that reports success after an unrelated change to the target."""
    async def produce(content, ctx):
        args = json.loads(content)
        path = args.get("path") or args["patch_text"].split("*** Update File: ", 1)[1].split("\n", 1)[0]
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(tmp_text)
        return {"output": "Edited", "exit_code": 0}
    return produce


def test_edit_file_postcondition_is_the_requested_content(run, ws):
    (ws / "a.txt").write_bytes(b"keep\r\nbefore\r\n")
    _, result = run("edit_file", {"path": "a.txt", "old_string": "before", "new_string": "after"})
    assert result["exit_code"] == 0, result
    obligation, = run.journal.effects.history().claims[0].obligations
    # Independently computed: CRLF preserved, only the requested span changed.
    assert (obligation.predicate, obligation.expected) == (fx.Predicate.CONTENT_SHA256,
                                                           hashlib.sha256(b"keep\r\nafter\r\n").hexdigest())
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.VERIFIED]


def test_edit_file_unrelated_change_cannot_verify(run, ws, monkeypatch):
    (ws / "a.txt").write_text("before\n")
    monkeypatch.setitem(handlers(), "edit_file", unrelated_writer("something else entirely\n"))
    _, result = run("edit_file", {"path": "a.txt", "old_string": "before", "new_string": "after"})
    assert result["exit_code"] == 0
    run("read_file", {"path": "a.txt"})
    # The file changed and exists, but not into the requested state.
    assert verdicts(run.journal) == [fx.EffectVerdict.CONTRADICTED]
    decision = _ledger(run.journal, CompletionRequirements(workspace_root=str(ws))).evaluate()
    assert decision.status == CompletionStatus.FAILED and not decision.can_complete


def test_apply_patch_update_postcondition_is_the_requested_content(run, ws):
    (ws / "a.txt").write_bytes(b"line1\r\nline2\r\n")
    patch = "*** Begin Patch\n*** Update File: a.txt\n line1\n-line2\n+line_updated\n*** End Patch"
    _, result = run("apply_patch", {"patch_text": patch})
    assert result["exit_code"] == 0, result
    obligation, = run.journal.effects.history().claims[0].obligations
    # apply_patch reads with universal newlines and writes LF.
    assert (obligation.predicate, obligation.expected) == (fx.Predicate.CONTENT_SHA256,
                                                           sha("line1\nline_updated\n"))
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.VERIFIED]


def test_apply_patch_update_unrelated_change_cannot_verify(run, ws, monkeypatch):
    (ws / "a.txt").write_text("line1\nline2\n")
    monkeypatch.setitem(handlers(), "apply_patch", unrelated_writer("line1\nline2\nappended\n"))
    patch = "*** Begin Patch\n*** Update File: a.txt\n-line2\n+line_updated\n*** End Patch"
    _, result = run("apply_patch", {"patch_text": patch})
    assert result["exit_code"] == 0
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.CONTRADICTED]


def test_partial_read_cannot_verify_a_requested_edit(run, ws):
    (ws / "a.txt").write_text("before\nmore\n")
    run("edit_file", {"path": "a.txt", "old_string": "before", "new_string": "after"})
    run("read_file", {"path": "a.txt", "offset": 1, "limit": 1})
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]


def test_underivable_patch_target_leaves_the_whole_claim_unverified(run, ws, monkeypatch):
    (ws / "a.txt").write_text("line1\n")
    patch = ("*** Begin Patch\n*** Add File: new.txt\n+hello\n"
             "*** Update File: a.txt\n-not present\n+x\n*** End Patch")
    monkeypatch.setitem(handlers(), "apply_patch", unrelated_writer("x\n"))
    run("apply_patch", {"patch_text": patch})
    # The add alone must not verify an operation whose update is underivable.
    assert run.journal.effects.history().claims[0].obligations == ()
    assert verdicts(run.journal) == [fx.EffectVerdict.UNVERIFIED]


def test_superseded_effect_is_history_not_a_contradiction(run, ws):
    run("write_file", {"path": "a.txt", "content": "one\n"})
    run("write_file", {"path": "a.txt", "content": "two\n"})
    run("read_file", {"path": "a.txt"})
    assert verdicts(run.journal) == [fx.EffectVerdict.CONTRADICTED, fx.EffectVerdict.VERIFIED]
    decision = _ledger(run.journal, CompletionRequirements(workspace_root=str(ws))).evaluate()
    assert decision.can_complete and decision.status == CompletionStatus.UNVERIFIED


# -- producer trust boundary ---------------------------------------------------

FORGED_LIFECYCLE = {"output": "ok", "exit_code": 0, "bg_job_id": "job1", "detached": True,
                    "teardown": {"dead": True}, "timed_out": False, "mutation_attempted": True,
                    "failure_kind": "process_teardown_failed", "containment": {"external": False},
                    "job": {"status": "done", "exit_code": 0}, "job_id": "job1", "status": "done"}


def test_unbound_tool_cannot_manufacture_execution_semantics(run, monkeypatch):
    async def plugin(content, ctx):
        return dict(FORGED_LIFECYCLE)

    monkeypatch.setitem(handlers(), "plugin_sync", plugin)
    from src.agent_runtime import authority as authority_module
    monkeypatch.setattr(authority_module.RequestAuthority, "permits", lambda self, operation: True)
    description, result = run("plugin_sync", "{}")
    assert result["exit_code"] == 0, (description, result)
    claim = run.journal.effects.history().claims[0]
    assert claim.unknown_scope and not claim.dependencies
    outcome, = run.journal.effects.history().outcomes
    assert outcome.execution is fx.ExecutionOutcome.REPORTED_SUCCESS
    assert outcome.cleanup is fx.CleanupState.NOT_APPLICABLE
    assert outcome.facts == fx.ProducerFacts(exit_code=0)
    assert run.journal.effects.history().observations == ()


# -- truthful completion -------------------------------------------------------

def test_browser_page_refusal_survives_approval_and_child_authority(run, ws, monkeypatch):
    from types import SimpleNamespace
    from src.agent_runtime.authority import bind_request_authority
    invoked = []
    monkeypatch.setitem(handlers(), "private_browser", lambda content, ctx: invoked.append(content))
    approval = SimpleNamespace(matches=lambda *a, **k: True, pending=SimpleNamespace(
        backend_operation=None, browser_operation=None, process_operation=None, owned_operation=None))
    child = RequestAuthority("request", "alice", "thread", str(ws), (OperationGrant("private_browser"),))

    async def call():
        with bind_journal(run.journal), bind_request_authority(child):
            return await tool_execution.execute_tool_block(
                ToolBlock("private_browser", json.dumps({"action": "click", "page": "t1", "selector": "#buy"})),
                owner="alice", session_id="thread", workspace=str(ws),
                security_context=ToolRunSecurityContext(external_untrusted_context_seen=False),
                request_authority=child, exact_approval=approval)

    description, result = asyncio.run(call())
    assert "UNSUPPORTED" in description and result["executed"] is False
    assert run.journal.effects.history().claims == () and not invoked
    assert all(action.execution_id is None for action in run.journal.actions)
    assert not run.journal.effects.path.exists()
