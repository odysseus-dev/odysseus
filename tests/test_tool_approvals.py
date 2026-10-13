"""Exact one-use continuation coverage for tainted agent actions."""

import time
from collections import namedtuple

import pytest
from tests.runtime_evidence_helpers import server_authorized_executor


@pytest.fixture(autouse=True)
def standalone_dispatch_authority(monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "execute_tool_block",
                        server_authorized_executor(tool_execution.execute_tool_block))

from src.tool_approvals import ToolApprovalStore, document_content_digest
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action


ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])


def _pending(store, **overrides):
    values = {
        "owner": "Alice",
        "session_id": "session-1",
        "origin_run_id": "run-1",
        "tool_name": "bash",
        "content": "printf exact",
        "workspace": None,
        "external_untrusted_context_seen": True,
        "capabilities": capabilities_for_action("bash", "printf exact"),
    }
    values.update(overrides)
    if "request_authority" not in values:
        import tempfile
        from src.agent_runtime.authority import RequestAuthority, OperationGrant
        from src.agent_runtime.resources import ProcessLaunchScope, FilesystemRoot, NativeBackendResource
        from src.containment import DEFAULT_REQUIRED
        tool = values["tool_name"]
        scopes = (ProcessLaunchScope(NativeBackendResource(tool), FilesystemRoot.seal(tempfile.mkdtemp(prefix="w3-approval-fixture-")), DEFAULT_REQUIRED),) if tool in {"bash", "python"} else ()
        values["request_authority"] = RequestAuthority("standalone-test-request", str(values["owner"]).casefold(),
            str(values["session_id"] or ""), str(values["workspace"] or ""), (OperationGrant(tool),), launch_scopes=scopes)
    return store.create(**values)


def test_approval_is_bound_to_exact_action_and_claimed_once():
    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    assert grant is not None
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf modified",
        workspace=None,
    )
    assert grant.claim(
        owner="ALICE",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace=None,
    )
    assert not grant.claim(
        owner="alice",
        session_id="session-1",
        tool_name="bash",
        content="printf exact",
        workspace=None,
    )


def test_wrong_owner_cannot_consume_but_deny_retires_pending_action():
    store = ToolApprovalStore()
    wrong_owner = _pending(store)

    assert store.consume(
        wrong_owner.approval_id,
        decision="approve",
        owner="mallory",
        session_id="session-1",
    ) is None
    assert store.peek(wrong_owner.approval_id) == wrong_owner

    denied = _pending(store)
    assert store.consume(
        denied.approval_id,
        decision="deny",
        owner="alice",
        session_id="session-1",
    ) is None
    assert store.peek(denied.approval_id) is None


def test_expired_approval_cannot_be_consumed(monkeypatch):
    store = ToolApprovalStore(ttl_seconds=1)
    pending = _pending(store)
    monkeypatch.setattr(time, "time", lambda: pending.expires_at + 1)

    assert store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    ) is None


def test_new_session_approval_supersedes_prior_pending_action():
    store = ToolApprovalStore()
    first = _pending(store, content="printf first")
    second = _pending(store, content="printf second")

    assert store.peek(first.approval_id) is None
    assert store.peek(second.approval_id) == second


def test_ordinary_session_turn_retires_pending_action_and_preserves_taint():
    store = ToolApprovalStore()
    pending = _pending(store, owner="Alice", session_id="session-1")

    assert store.retire_for_session(owner="bob", session_id="session-1") is False
    assert store.peek(pending.approval_id) == pending
    assert store.retire_for_session(owner="alice", session_id="session-1") is True
    assert store.peek(pending.approval_id) is None
    assert store.retire_for_session(owner="alice", session_id=None) is False


def test_independent_headless_runs_do_not_supersede_each_other():
    store = ToolApprovalStore()
    first = _pending(store, session_id=None, origin_run_id="headless-1")
    second = _pending(store, session_id=None, origin_run_id="headless-2")

    assert store.peek(first.approval_id) == first
    assert store.peek(second.approval_id) == second


def test_public_payload_shows_complete_action_but_not_authority_fields():
    store = ToolApprovalStore()
    pending = _pending(
        store,
        content="printf safe\nSECOND_LINE",
        document_id="document-7",
        document_version=4,
        document_digest=document_content_digest("original"),
    )

    payload = pending.public_payload()

    assert payload["kind"] == "tool_approval"
    assert payload["action"]["content"] == "printf safe\nSECOND_LINE"
    assert payload["action"]["document_id"] == "document-7"
    assert payload["action"]["document_version"] == 4
    assert "SECOND_LINE" in str(payload)
    assert "origin_run_id" not in str(payload)


def test_approval_preserves_originating_request_only_for_server_continuation():
    store = ToolApprovalStore()
    request = "delete the note titled ODY-EVAL-SEQUENCE"
    pending = _pending(store, request_text=request)

    assert pending.request_text == request
    assert request not in str(pending.public_payload())


@pytest.mark.asyncio
async def test_dispatcher_claims_approval_immediately_before_execution(monkeypatch):
    import src.tool_execution as tool_execution

    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )
    calls = []

    async def fake_implementation(block, **kwargs):
        calls.append((block.tool_type, block.content))
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        fake_implementation,
    )
    desc, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf exact"),
        session_id="session-1",
        owner="alice",
        workspace=None,
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        exact_approval=grant,
    )

    assert desc == "bash"
    assert result["exit_code"] == 0
    assert calls == [("bash", "printf exact")]


@pytest.mark.asyncio
async def test_dispatcher_uses_sealed_document_target(monkeypatch):
    import src.tool_execution as tool_execution
    from datetime import datetime
    from types import SimpleNamespace
    from src.agent_runtime import owned_resources
    # This dispatcher fixture seals an observed owned row, as production does;
    # model/document text alone cannot stand in for a resource identity.
    row = SimpleNamespace(id="document-7", owner="alice", session_id="session-1",
        version_count=4, current_content="original", created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 2))
    monkeypatch.setattr(owned_resources, "_row", lambda namespace, identifier, owner: row
                        if (namespace, identifier, owner) == ("documents", "document-7", "alice") else None)

    store = ToolApprovalStore()
    content = '{"content":"replacement"}'
    pending = _pending(
        store,
        tool_name="update_document",
        content=content,
        document_id="document-7",
        document_version=4,
        document_digest=document_content_digest("original"),
        capabilities=capabilities_for_action("update_document", content),
    )
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )
    captured = []

    async def fake_implementation(block, **kwargs):
        captured.append(
            (
                kwargs.get("approved_document_id"),
                kwargs.get("approved_document_version"),
                kwargs.get("approved_document_digest"),
            )
        )
        return "update_document", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        fake_implementation,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("update_document", content),
        session_id="session-1",
        owner="alice",
        workspace=None,
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        exact_approval=grant,
    )

    assert result["exit_code"] == 0
    assert captured == [
        ("document-7", 4, document_content_digest("original"))
    ]


@pytest.mark.asyncio
async def test_dispatcher_rejects_approved_document_action_without_target(monkeypatch):
    import src.tool_execution as tool_execution

    store = ToolApprovalStore()
    content = "replacement"
    pending = _pending(
        store,
        tool_name="update_document",
        content=content,
        capabilities=capabilities_for_action("update_document", content),
    )
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("unsealed document target reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("update_document", content),
        session_id="session-1",
        owner="alice",
        workspace=None,
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"


def test_approved_document_version_guard_rejects_changed_target():
    from src.agent_tools.document_tools import _approved_document_version_error

    doc = type(
        "Document",
        (),
        {"version_count": 5, "current_content": "original"},
    )()

    assert _approved_document_version_error(
        doc,
        {"expected_document_version": 4},
    )["document_changed"] is True
    assert _approved_document_version_error(
        doc,
        {
            "expected_document_version": 5,
            "expected_document_digest": document_content_digest("original"),
        },
    ) is None
    assert _approved_document_version_error(
        doc,
        {
            "expected_document_version": 5,
            "expected_document_digest": document_content_digest("changed"),
        },
    )["document_changed"] is True
    assert _approved_document_version_error(
        None,
        {"expected_document_version": 5},
    )["document_changed"] is True


@pytest.mark.asyncio
async def test_missing_sealed_document_does_not_fall_back_to_another(monkeypatch):
    import sys
    from types import ModuleType
    import src.agent_tools.document_tools as document_tools

    class FakeDb:
        def close(self):
            pass

        def rollback(self):
            pass

    database = ModuleType("src.database")
    database.SessionLocal = lambda: FakeDb()
    database.Document = object
    database.DocumentVersion = object
    monkeypatch.setitem(sys.modules, "src.database", database)
    monkeypatch.setattr(
        document_tools,
        "_get_owned_document",
        lambda *args, **kwargs: None,
    )

    def fail_fallback(*args, **kwargs):
        raise AssertionError("sealed target fell back to a different document")

    monkeypatch.setattr(
        document_tools,
        "_most_recent_owned_document",
        fail_fallback,
    )
    result = await document_tools.UpdateDocumentTool().execute(
        "replacement",
        {
            "doc_id": "deleted-document",
            "expected_document_version": 4,
            "owner": "alice",
        },
    )

    assert result["document_changed"] is True


@pytest.mark.asyncio
async def test_dispatcher_rejects_modified_approved_action(monkeypatch):
    import src.tool_execution as tool_execution

    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("modified approved action reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf changed"),
        session_id="session-1",
        owner="alice",
        workspace=None,
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"


@pytest.mark.asyncio
async def test_dispatcher_requires_armed_security_context_for_approval(monkeypatch):
    import src.tool_execution as tool_execution

    store = ToolApprovalStore()
    pending = _pending(store)
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("approval reached an unarmed implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf exact"),
        session_id="session-1",
        owner="alice",
        workspace=None,
        security_context=ToolRunSecurityContext(),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"


@pytest.mark.asyncio
async def test_dispatcher_revalidates_sealed_workspace(monkeypatch, tmp_path):
    import src.tool_execution as tool_execution

    store = ToolApprovalStore()
    pending = _pending(store, workspace=str(tmp_path))
    grant = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="session-1",
    )

    monkeypatch.setattr(tool_execution, "vet_workspace", lambda _path: None)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("invalid approved workspace reached implementation")

    monkeypatch.setattr(
        tool_execution,
        "_execute_tool_block_impl",
        should_not_run,
    )
    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf exact"),
        session_id="session-1",
        owner="alice",
        workspace=str(tmp_path),
        security_context=ToolRunSecurityContext(
            external_untrusted_context_seen=True
        ),
        exact_approval=grant,
    )

    assert result["blocked"] is True
    assert result["policy"] == "exact_tool_approval"
