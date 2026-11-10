"""Server resolution pins record aliases before approval and dispatch."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from src.agent_runtime.owned_resources import (
    active_owned_operation, admit_owned_operation, bind_owned_operation,
    bound_attachment_path, resolve_owned_operation,
    observe_vault_records,
)
from src.agent_runtime.remote_resources import active_backend_operation
from src.agent_runtime.resources import OwnedScope, ResourceIdentityError
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock


@pytest.fixture(autouse=True)
def fresh_vault_observations(monkeypatch):
    from src.agent_runtime import owned_resources
    monkeypatch.setattr(owned_resources, "_VAULT_RECORDS", {})


def grant(*tools, scopes=None, owner="alice", thread="s"):
    return RequestAuthority("owned-request", owner, thread, "",
                            tuple(OperationGrant(t) for t in tools), owned_scopes=scopes)


async def dispatch(authority, tool, content, **kwargs):
    from src import tool_execution as execution
    return await execution.execute_tool_block(ToolBlock(tool, content), owner=authority.owner,
        session_id=authority.session_id, request_authority=authority,
        security_context=kwargs.pop("security_context", execution.NO_TOOL_SECURITY_CONTEXT), **kwargs)


def approval(authority, tool, content, **kwargs):
    store = ToolApprovalStore()
    pending = store.create(owner=authority.owner, session_id=authority.session_id, origin_run_id="run",
        tool_name=tool, content=content, workspace=None, request_authority=authority,
        external_untrusted_context_seen=True, capabilities=capabilities_for_action(tool, content), **kwargs)
    return store.consume(pending.approval_id, decision="approve", owner=authority.owner, session_id=authority.session_id)


@pytest.fixture
def records(monkeypatch):
    import core.database as db
    import src.database as compatibility
    from src.agent_tools import document_tools
    engine = create_engine("sqlite:///:memory:")
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(compatibility, "SessionLocal", factory)
    from src import tool_execution as execution
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    now = datetime(2026, 1, 1)
    with factory() as connection:
        for identifier, owner in (("s", "alice"), ("other", "alice"), ("foreign", "bob")):
            connection.add(db.Session(id=identifier, owner=owner, name=identifier, endpoint_url="https://model.test", model="test"))
        for identifier, owner, thread, offset in (("d1", "alice", "s", 1), ("d2", "alice", "other", 2), ("private", "bob", "foreign", 3)):
            connection.add(db.Document(id=identifier, owner=owner, session_id=thread, title=identifier,
                current_content=identifier + " original", language="text", version_count=1,
                created_at=now, updated_at=now + timedelta(days=offset)))
        connection.add(db.Note(id="note-one", owner="alice", title="first", content="original"))
        connection.add(db.Note(id="note-two", owner="alice", title="second", content="original"))
        connection.add(db.Note(id="note-foreign", owner="bob", title="private", content="private"))
        connection.commit()
    monkeypatch.setattr(document_tools, "_active_document_id", None)
    yield factory
    engine.dispose()


@pytest.mark.parametrize("selector", ["active", "current", "latest"])
async def test_document_alias_resolves_once_and_does_not_follow_new_active_or_latest(records, monkeypatch, selector):
    from src import tool_execution as execution
    authority = grant("manage_documents")
    content = json.dumps({"action": "read", "document_id": selector})
    exact = approval(authority, "manage_documents", content, document_id="d1")
    bound = exact.pending.owned_operation
    expected = "d2" if selector == "latest" else "d1"
    assert bound.document_id == expected
    import core.database as db
    with records() as connection:
        connection.add(db.Document(id="newest", owner="alice", session_id="s", title="newest",
            current_content="newest content", version_count=1, updated_at=datetime(2030, 1, 1)))
        connection.commit()
    seen = []
    async def implementation(block, **kwargs):
        seen.append((json.loads(block.content)["document_id"], kwargs["approved_document_id"], active_owned_operation()))
        return "read", {"exit_code": 0}
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    _, result = await dispatch(authority, "manage_documents", content, active_document_id="newest",
        exact_approval=exact, security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["exit_code"] == 0
    assert seen[0][:2] == (expected, expected)
    assert seen[0][2] is bound
    assert active_owned_operation() is None


async def test_document_runtime_executes_captured_id_not_process_global_alias(records):
    from src.agent_tools import document_tools
    authority = grant("update_document")
    exact = approval(authority, "update_document", "replacement", document_id="d1")
    document_tools.set_active_document("d2")
    _, result = await dispatch(authority, "update_document", "replacement", active_document_id="d2",
        exact_approval=exact, security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result.get("exit_code", 0) == 0 and not result.get("error")
    import core.database as db
    with records() as connection:
        assert connection.get(db.Document, "d1").current_content == "replacement"
        assert connection.get(db.Document, "d2").current_content == "d2 original"


@pytest.mark.parametrize("change", ["revision", "owner", "thread", "deleted", "request", "invocation_thread"])
async def test_stale_or_rebound_document_approval_fails_before_effect(records, monkeypatch, change):
    import core.database as db
    from src import tool_execution as execution
    authority = grant("update_document")
    exact = approval(authority, "update_document", "replacement", document_id="d1")
    if change in {"request", "invocation_thread"}:
        authority = replace(authority, **({"request_id": "other"} if change == "request" else
            {"session_id": "other", "owned_scopes": (OwnedScope("documents", "alice", "other"),)}))
    else:
        with records() as connection:
            row = connection.get(db.Document, "d1")
            if change == "revision":
                row.current_content = "changed"
                row.version_count += 1
            elif change == "owner":
                row.owner = "bob"
            elif change == "thread":
                row.session_id = "other"
            else:
                connection.delete(row)
            connection.commit()
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    _, result = await dispatch(authority, "update_document", "replacement", exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["failure_kind"] == "resource_identity_denied"
    implementation.assert_not_awaited()
    assert not exact._claimed


@pytest.mark.parametrize("owner,thread", [("", "s"), ("alice", ""), ("bob", "s")])
async def test_owner_and_invocation_thread_are_mandatory(records, owner, thread):
    _, result = await dispatch(grant("manage_documents", owner=owner, thread=thread),
                              "manage_documents", '{"action":"read","id":"d1"}')
    assert result["failure_kind"] == "resource_identity_denied"


async def test_child_record_scope_is_intersection_and_exact_approval_cannot_widen(records, monkeypatch):
    parent = grant("manage_documents", scopes=(OwnedScope("documents", "alice", "s", frozenset({"d1"})),))
    child = grant("manage_documents")
    assert parent.intersect(child).owned_scopes == parent.owned_scopes
    exact = approval(child, "manage_documents", '{"action":"read","id":"d2"}')
    from src import tool_execution as execution
    handler = AsyncMock(return_value=("read", {"exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    with bind_request_authority(parent):
        _, denied = await dispatch(child, "manage_documents", exact.pending.content, exact_approval=exact,
            security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
        _, allowed = await dispatch(child, "manage_documents", '{"action":"read","id":"d1"}')
        _, collection = await dispatch(child, "manage_documents", '{"action":"list"}')
    assert denied["failure_kind"] == collection["failure_kind"] == "resource_identity_denied"
    assert allowed["exit_code"] == 0 and handler.await_count == 1


async def test_legacy_owned_approval_is_exact_one_use_not_reconstructed_scope(records, monkeypatch):
    snapshot = grant("manage_documents").to_dict()
    snapshot["version"] = 2
    authority = RequestAuthority.from_dict(snapshot)
    exact = approval(authority, "manage_documents", '{"action":"read","id":"d1"}')
    from src import tool_execution as execution
    handler = AsyncMock(return_value=("read", {"exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    _, denied = await dispatch(authority, "manage_documents", exact.pending.content)
    assert denied["failure_kind"] == "resource_identity_denied"
    security = ToolRunSecurityContext(external_untrusted_context_seen=True)
    _, allowed = await dispatch(authority, "manage_documents", exact.pending.content, exact_approval=exact, security_context=security)
    _, replay = await dispatch(authority, "manage_documents", exact.pending.content, exact_approval=exact, security_context=security)
    assert allowed["exit_code"] == 0 and replay["exit_code"] == 1
    assert authority.owned_scopes == () and handler.await_count == 1


@pytest.mark.parametrize("tool,content", [("manage_session", '{"action":"rename","session":"current","value":"new"}'),
    ("manage_session", "rename\ncurrent\nnew"), ("send_to_session", "current\nhello")])
def test_thread_current_alias_becomes_exact_owned_identity(records, tool, content):
    bound = resolve_owned_operation(ExactOperation.normalize(tool, content), owner="alice", thread_id="s")
    assert bound.resources[0].record_id == bound.resources[0].record_thread_id == "s"
    assert "current" not in bound.execution_input
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize(tool, content.replace("current", "foreign")), owner="alice", thread_id="s")


@pytest.mark.parametrize("selector", ["note-o", "first"])
def test_note_alias_resolves_once_and_prefix_ambiguity_fails_closed(records, selector):
    content = {"action": "update", "content": "replacement", "id" if selector == "note-o" else "title": selector}
    bound = resolve_owned_operation(ExactOperation.normalize("manage_notes", json.dumps(content)), owner="alice", thread_id="s")
    assert bound.resources[0].record_id == json.loads(bound.execution_input)["id"] == "note-one"
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("manage_notes", '{"action":"view","id":"note-"}'), owner="alice", thread_id="s")


@pytest.fixture
def attachment_store(tmp_path, monkeypatch):
    from src import tool_utils
    file = tmp_path / "image.png"
    file.write_bytes(b"test image")
    row = {"id": "upload.png", "owner": "alice", "path": str(file), "hash": "observed-hash"}
    handler = SimpleNamespace(upload_dir=str(tmp_path), resolve_upload=lambda identifier, *, owner, allow_admin:
        dict(row) if identifier == row.get("id") and owner == row.get("owner") and not allow_admin else None)
    monkeypatch.setattr(tool_utils, "get_upload_handler", lambda: handler)
    return row, file


@pytest.mark.parametrize("change", ["owner", "path", "file", "missing"])
def test_attachment_ownership_index_and_file_identity_are_pinned(attachment_store, change):
    row, file = attachment_store
    bound = resolve_owned_operation(ExactOperation.normalize("extract_text", '{"path":"odysseus://attachment/upload.png"}'), owner="alice", thread_id="s")
    with bind_owned_operation(bound):
        assert bound_attachment_path("alice", "odysseus://attachment/upload.png") == str(file)
        with pytest.raises(ResourceIdentityError):
            bound_attachment_path("bob", "odysseus://attachment/upload.png")
    if change == "owner":
        row["owner"] = "bob"
    elif change == "path":
        other = file.with_name("other.png")
        other.write_bytes(b"other")
        row["path"] = str(other)
    elif change == "file":
        file.rename(file.with_name("old.png"))
        file.write_bytes(b"replacement")
    else:
        row.clear()
    with pytest.raises(ResourceIdentityError):
        bound.validate()


def test_memory_prefix_is_owner_scoped_exact_and_revision_sensitive(monkeypatch):
    from src import ai_interaction
    rows = [{"id": "memory-one", "owner": "alice", "text": "secret", "timestamp": 1},
            {"id": "memory-other", "owner": "bob", "text": "private", "timestamp": 1}]
    monkeypatch.setattr(ai_interaction, "_memory_manager", SimpleNamespace(load=lambda owner: rows))
    bound = resolve_owned_operation(ExactOperation.normalize("manage_memory", "edit\nmemory-o\nreplacement"), owner="alice", thread_id="s")
    assert bound.resources[0].record_id == "memory-one"
    assert bound.execution_input == "edit\nmemory-one\nreplacement"
    assert "secret" not in json.dumps(bound.to_dict())
    rows[0]["text"] = "changed in same second"
    with pytest.raises(ResourceIdentityError):
        bound.validate()


@pytest.mark.parametrize("change", ["owner", "endpoint", "session"])
def test_private_vault_identity_binds_owner_endpoint_and_exact_uuid_without_credentials(monkeypatch, change):
    from src.tools import vault
    cfg = {"owner": "alice", "server_url": "https://vault.test", "session": "SECRET_SESSION", "unlocked_at": "observed"}
    monkeypatch.setattr(vault, "_load_vault_config", lambda: cfg)
    observe_vault_records("alice", cfg, [{"id": "12345678-1234-1234-1234-123456789abc", "name": "bank", "login": {"password": "PRIVATE_PASSWORD"}}])
    tool = ExactOperation.normalize("vault_get", '{"item_id":"12345678-1234-1234-1234-123456789abc","reason":"requested"}')
    bound = resolve_owned_operation(tool, owner="alice", thread_id="s")
    assert "SECRET_SESSION" not in json.dumps(bound.to_dict()) and "PRIVATE_PASSWORD" not in json.dumps(bound.to_dict())
    cfg.update({"owner": "bob"} if change == "owner" else {"server_url": "https://other.test"} if change == "endpoint" else {"session": "OTHER_SECRET"})
    with pytest.raises(ResourceIdentityError):
        bound.validate()


def test_legacy_vault_and_model_name_alias_do_not_create_private_identity(monkeypatch):
    from src.tools import vault
    monkeypatch.setattr(vault, "_load_vault_config", lambda: {"server_url": "https://vault.test", "session": "secret"})
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("vault_search", '{"query":"bank"}'), owner="alice", thread_id="s")
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("vault_get", '{"item_id":"latest","reason":"requested"}'), owner="alice", thread_id="s")


@pytest.mark.parametrize("error", [None, RuntimeError, asyncio.CancelledError])
async def test_owned_and_backend_context_restore_after_success_error_cancel_and_nested_call(records, monkeypatch, error):
    from src import tool_execution as execution
    authority = grant("manage_documents")
    parent = admit_owned_operation(authority, ExactOperation.normalize("manage_documents", '{"action":"read","id":"d1"}'))
    async def implementation(block, **kwargs):
        assert active_owned_operation().resources[0].record_id == "d2"
        assert active_backend_operation() is not None
        if error:
            raise error("stop")
        return "read", {"exit_code": 0}
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    with bind_owned_operation(parent):
        if error:
            with pytest.raises(error):
                await dispatch(authority, "manage_documents", '{"action":"read","id":"d2"}')
        else:
            await dispatch(authority, "manage_documents", '{"action":"read","id":"d2"}')
        assert active_owned_operation() is parent
        assert active_backend_operation() is None
    assert active_owned_operation() is None


@pytest.mark.parametrize("path", ["/api/document/d1", "/api/history/s", "/api/vault/config", "/api/memory", "/api/notes",
    "/api/upload/upload.png", "/api/%64ocument/d1", "/api/cookbook/../document/d1"])
async def test_generic_internal_bridge_cannot_bypass_owned_resource_adapter(monkeypatch, path):
    from src import tool_execution as execution
    handler = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    _, result = await dispatch(grant("app_api"), "app_api", json.dumps({"path": path}))
    assert result["failure_kind"] == "resource_identity_denied"
    handler.assert_not_awaited()


def test_owned_snapshot_roundtrip_and_malformed_scopes_fail_closed(records):
    authority = grant("manage_documents", scopes=(OwnedScope("documents", "alice", "s", frozenset({"d1"})),))
    snapshot = json.loads(json.dumps(authority.to_dict()))
    assert RequestAuthority.from_dict(snapshot) == authority
    for mutation in ({"owner": "bob"}, {"thread_id": "other"}, {"record_ids": ["*"]}, {"record_ids": [1]}):
        changed = json.loads(json.dumps(snapshot))
        changed["owned_scopes"][0].update(mutation)
        with pytest.raises((ValueError, TypeError)):
            RequestAuthority.from_dict(changed)


def test_vault_config_owner_is_produced_by_authenticated_request_and_drops_legacy_session():
    from routes.vault.vault_routes import _bind_config_owner
    from fastapi import HTTPException
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))
    cfg = {"session": "legacy-secret", "unlocked_at": "legacy"}
    _bind_config_owner(cfg, request)
    assert cfg == {"owner": "alice"}
    request.state.current_user = "bob"
    with pytest.raises(HTTPException):
        _bind_config_owner(cfg, request)


def test_missing_proposal_record_is_not_reconstructed_after_it_appears(records):
    authority = grant("manage_documents")
    exact = approval(authority, "manage_documents", '{"action":"read","id":"not-yet"}')
    assert exact.pending.owned_operation is None
    import core.database as db
    with records() as connection:
        connection.add(db.Document(id="not-yet", owner="alice", title="appeared", current_content="content", version_count=1))
        connection.commit()
    _, result = asyncio.run(dispatch(authority, "manage_documents", exact.pending.content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True)))
    assert result["failure_kind"] == "resource_identity_denied" and not exact._claimed


def test_malformed_record_identity_and_normalized_approval_tampering_fail_closed(records):
    authority = grant("manage_documents")
    exact = approval(authority, "manage_documents", '{"action":"read","id":"d1"}')
    bound = exact.pending.owned_operation
    with pytest.raises(ValueError):
        replace(bound, resources=(replace(bound.resources[0], revision=""),))
    with pytest.raises(ValueError):
        replace(bound, document_id="d2")
    exact.pending = replace(exact.pending, owned_operation=replace(bound, execution_input='{"action":"read","document_id":"d2"}'))
    assert not exact.matches(owner="alice", session_id="s", workspace=None,
                             tool_name="manage_documents", content=exact.pending.content)


def test_note_prefix_wildcards_cannot_create_selector_authority(records):
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("manage_notes", '{"action":"view","id":"note-o%"}'), owner="alice", thread_id="s")


@pytest.mark.parametrize("content", ['{"action":"read"}', '{"action":"read","id":"active"}', '{"action":"read","id":"current"}', '{"action":"read","id":42}', '{"action":"read","id":"d1","uid":"d2"}'])
def test_missing_malformed_and_conflicting_document_selectors_fail_closed(records, content):
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("manage_documents", content), owner="alice", thread_id="s")


async def test_resumed_child_approval_cannot_restore_excluded_record(records):
    parent = grant("manage_documents", scopes=(OwnedScope("documents", "alice", "s", frozenset({"d1"})),))
    child = parent.intersect(grant("manage_documents"))
    exact = approval(child, "manage_documents", '{"action":"read","id":"d2"}')
    assert exact.pending.owned_operation is None
    _, result = await dispatch(replace(child, inherited=False), "manage_documents", exact.pending.content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["failure_kind"] == "resource_identity_denied" and not exact._claimed


async def test_vault_search_producer_supplies_exact_owned_item_identity_and_alias_binding(monkeypatch):
    from src.tools import vault
    from src import tool_execution as execution
    cfg = {"owner": "alice", "server_url": "https://vault.test", "session": "SECRET_SESSION", "unlocked_at": "observed"}
    item = {"id": "12345678-1234-1234-1234-123456789abc", "name": "bank", "login": {"password": "PRIVATE_PASSWORD"}}
    monkeypatch.setattr(vault, "_load_vault_config", lambda: cfg)
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    cli = AsyncMock(side_effect=[(json.dumps([item]), "", 0), (json.dumps(item), "", 0)])
    monkeypatch.setattr(vault, "_run_bw", cli)
    authority = grant("vault_search", "vault_get")
    _, missing = await dispatch(authority, "vault_get", json.dumps({"item_id": item["id"], "reason": "requested"}))
    assert missing["failure_kind"] == "resource_identity_denied"
    cli.assert_not_awaited()
    _, search = await dispatch(authority, "vault_search", '{"query":"bank"}')
    assert search["exit_code"] == 0 and item["id"] in search["output"]
    exact = approval(authority, "vault_get", '{"item_id":"bank","reason":"requested"}')
    assert exact.pending.owned_operation.resources[0].record_id == item["id"]
    # A later producer result with the same alias cannot change the approved ID.
    other = {"id": "87654321-1234-1234-1234-123456789abc", "name": "bank", "login": {"password": "OTHER_PASSWORD"}}
    observe_vault_records("alice", cfg, [other])
    _, result = await dispatch(authority, "vault_get", exact.pending.content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["exit_code"] == 0 and "PRIVATE_PASSWORD" in result["output"] and "OTHER_PASSWORD" not in result["output"]
    assert cli.await_args.args[0] == ["get", "item", item["id"]]
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(ExactOperation.normalize("vault_get", exact.pending.content), owner="alice", thread_id="s")


def test_vault_producer_revision_change_invalidates_sealed_item_and_cannot_cross_owner(monkeypatch):
    from src.tools import vault
    cfg = {"owner": "alice", "server_url": "https://vault.test", "session": "SECRET"}
    item = {"id": "12345678-1234-1234-1234-123456789abc", "name": "bank", "revisionDate": "one"}
    monkeypatch.setattr(vault, "_load_vault_config", lambda: cfg)
    observe_vault_records("alice", cfg, [item])
    bound = resolve_owned_operation(ExactOperation.normalize("vault_get", '{"item_id":"12345678","reason":"requested"}'), owner="alice", thread_id="s")
    assert json.loads(bound.execution_input)["item_id"] == item["id"]
    with pytest.raises(ResourceIdentityError):
        observe_vault_records("bob", cfg, [item])
    observe_vault_records("alice", cfg, [{**item, "revisionDate": "two"}])
    with pytest.raises(ResourceIdentityError):
        bound.validate()
