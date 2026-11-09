"""Server bindings narrow operation authority and survive approved continuations."""
import asyncio
from dataclasses import FrozenInstanceError, replace
import json
import os
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from src.agent_runtime.authority import (
    ExactOperation, OperationGrant, RequestAuthority, bind_request_authority,
    create_request_authority, save_background_authority, restore_background_authority,
    seal_task_authority, restore_task_authority,
)
from src.agent_runtime.resource_binding import (
    BoundFilesystemOperation, ResourceBinding, active_resource_operation,
    bind_resource_operation, resolve_filesystem_operation,
)
from src.agent_runtime.resources import (
    ExternalResource, FileObjectIdentity,
    FilesystemResource, FilesystemRoot, FilesystemScope, OwnedResource, ProcessResource,
)
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock


def authority(root, *tools, roots=None, owner="alice", session="s"):
    return RequestAuthority("resource-test", owner, session, str(root or ""),
                            tuple(OperationGrant(t) for t in tools), resource_roots=roots)


def resolve(grant, tool, content):
    return resolve_filesystem_operation(ExactOperation.normalize(tool, content),
        roots=grant.resource_roots, workspace=grant.workspace, request_id=grant.request_id)


async def dispatch(grant, tool, content, **kwargs):
    from src import tool_execution as execution
    return await execution.execute_tool_block(ToolBlock(tool, content),
        owner=grant.owner, session_id=grant.session_id, workspace=grant.workspace or None,
        request_authority=grant, security_context=kwargs.pop("security_context", execution.NO_TOOL_SECURITY_CONTEXT),
        **kwargs)


@pytest.fixture(autouse=True)
def native_admin(monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)


@pytest.mark.parametrize("selector", ["a.txt", "/workspace/a.txt", "host", "link"])
def test_aliases_resolve_to_one_observed_resource(tmp_path, selector):
    target = tmp_path / "a.txt"
    target.write_text("same object")
    (tmp_path / "link").symlink_to(target)
    grant = authority(tmp_path, "read_file")
    value = str(target) if selector == "host" else selector
    bound = resolve(grant, "read_file", value)
    assert bound.bindings[0].resource.path == str(target)
    assert bound.bindings[0].resource.identity == FileObjectIdentity.observe(target)
    assert json.loads(bound.execution_input)["path"] == str(target)
    assert bound.operation.input == value


@pytest.mark.parametrize("path", ["../sibling/secret", "/etc/passwd", ".SSH/key", "ID_RSA", "bad\0path", "bad\npath"])
def test_escapes_sensitive_and_malformed_paths_fail_closed(tmp_path, path):
    grant = authority(tmp_path, "write_file")
    with pytest.raises(ValueError):
        resolve(grant, "write_file", json.dumps({"path": path, "content": "x"}))


def test_symlink_escape_is_not_a_resource(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("private")
    (workspace / "alias").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve(authority(workspace, "read_file"), "read_file", "alias")


@pytest.mark.parametrize("alias", ["direct", "relative", "symlink", "hardlink"])
@pytest.mark.parametrize("must_exist", [True, False])
def test_media_workspace_paths_cannot_address_control_state(tmp_path, monkeypatch, alias, must_exist):
    from src import constants, tool_execution
    from src.agent_tools.media_tools import _resolve_workspace_path
    control = tmp_path / "receipts.json"
    control.write_text("private execution state")
    monkeypatch.setattr(constants, "CONTAINMENT_STATE_FILE", str(control))
    monkeypatch.setattr(tool_execution, "get_active_workspace", lambda: str(tmp_path))
    if alias == "direct":
        selector = str(control)
    elif alias == "relative":
        selector = "./receipts.json"
    else:
        target = tmp_path / "image.png"
        if alias == "symlink":
            target.symlink_to(control)
        else:
            os.link(control, target)
        selector = "/workspace/image.png"
    with pytest.raises(ValueError, match="execution-control"):
        _resolve_workspace_path(selector, must_exist=must_exist)
    assert control.read_text() == "private execution state"


def test_destination_binds_absence_and_existing_ancestors(tmp_path):
    parent = tmp_path / "existing"
    parent.mkdir()
    bound = resolve(authority(tmp_path, "write_file"), "write_file", "existing/new/tree/result.txt\nx")
    resource = bound.bindings[0].resource
    assert bound.bindings[0].role == "destination"
    assert resource.identity is None
    assert [a.path for a in resource.ancestors] == [str(tmp_path), str(parent)]
    bound.validate()
    parent.rename(tmp_path / "old-parent")
    parent.mkdir()
    with pytest.raises(ValueError):
        bound.validate()


@pytest.mark.parametrize("replacement", ["root", "file", "parent", "new-target"])
def test_replacement_invalidates_observed_identity(tmp_path, replacement):
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "sub"
    parent.mkdir()
    target = parent / "a.txt"
    target.write_text("old")
    content = "sub/new.txt\nx" if replacement == "new-target" else "sub/a.txt"
    tool = "write_file" if replacement == "new-target" else "read_file"
    bound = resolve(authority(root, tool), tool, content)
    if replacement == "file":
        target.rename(parent / "old.txt")
        target.write_text("new")
    elif replacement == "parent":
        parent.rename(root / "old-sub")
        parent.mkdir()
        target.write_text("new")
    elif replacement == "root":
        root.rename(tmp_path / "old-root")
        root.mkdir()
    else:
        (parent / "new.txt").write_text("unapproved target")
    with pytest.raises((ValueError, OSError)):
        bound.validate()


def test_content_is_not_an_object_incarnation_or_effect_claim(tmp_path):
    target = tmp_path / "a"
    target.write_text("old")
    bound = resolve(authority(tmp_path, "read_file"), "read_file", "a")
    target.write_text("changed content in the same object")
    bound.validate()


@pytest.mark.parametrize("state", ["authority", "jobs", "containment"])
async def test_user_filesystem_scope_cannot_write_server_execution_state(tmp_path, monkeypatch, state):
    import src.constants
    monkeypatch.setattr(src.constants, "BG_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(src.constants, "BG_JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(src.constants, "CONTAINMENT_STATE_FILE", str(tmp_path / "receipts.json"))
    target = {"authority": "jobs/job.authority.json", "jobs": "jobs.json", "containment": "receipts.json"}[state]
    _, result = await dispatch(authority(tmp_path, "write_file"), "write_file", target + "\nforged")
    assert result["failure_kind"] == "resource_identity_denied"
    assert not (tmp_path / target).exists()


@pytest.mark.parametrize("state", ["authority", "jobs", "containment", "result", "exit", "database", "vault", "uploads"])
@pytest.mark.parametrize("alias", ["direct", "relative", "symlink", "hardlink"])
async def test_control_files_cannot_be_read_or_written_through_aliases(tmp_path, monkeypatch, state, alias):
    import src.constants as constants
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(constants, "BG_JOBS_DIR", str(jobs))
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(tmp_path / "uploads"))
    for name, filename in (("BG_JOBS_FILE", "jobs.json"), ("CONTAINMENT_STATE_FILE", "receipts.json"),
                           ("APP_DB", "private.db"), ("VAULT_FILE", "vault.json")):
        monkeypatch.setattr(constants, name, str(tmp_path / filename))
    filename = {"authority": "jobs/job.authority.json", "jobs": "jobs.json", "containment": "receipts.json",
                "result": "jobs/job.result.json", "exit": "jobs/job.exit", "database": "private.db",
                "vault": "vault.json", "uploads": "uploads/uploads.json"}[state]
    target = tmp_path / filename
    target.parent.mkdir(exist_ok=True)
    target.write_text("control-secret")
    selector = str(target)
    if alias == "relative":
        selector = "./" + filename
    elif alias in {"symlink", "hardlink"}:
        link = tmp_path / "ordinary.txt"
        try:
            link.symlink_to(target) if alias == "symlink" else os.link(target, link)
        except OSError as error:
            pytest.skip(f"Platform cannot create {alias}: {error}")
        selector = str(link)
    grant = authority(tmp_path, "read_file", "write_file")
    for tool, content in (("read_file", selector), ("write_file", selector + "\nforged")):
        _, result = await dispatch(grant, tool, content)
        assert result["failure_kind"] == "resource_identity_denied"
    assert target.read_text() == "control-secret"


async def test_directory_grep_does_not_scan_control_state_or_hardlinks(tmp_path, monkeypatch):
    import src.constants as constants
    control = tmp_path / "jobs.json"
    control.write_text("UNIQUE_CONTROL_SECRET")
    (tmp_path / "ordinary").write_text("visible text")
    os.link(control, tmp_path / "innocent.txt")
    monkeypatch.setattr(constants, "BG_JOBS_FILE", str(control))
    _, result = await dispatch(authority(tmp_path, "grep"), "grep", '{"pattern":"UNIQUE_CONTROL_SECRET","path":"."}')
    assert result["exit_code"] == 0
    assert "No matches" in result["output"]


@pytest.mark.parametrize("tool,content", [("glob", '{"pattern":"*.json","path":"."}'), ("ls", ".")])
async def test_directory_enumeration_does_not_address_control_files(tmp_path, monkeypatch, tool, content):
    import src.constants as constants
    control = tmp_path / "jobs.json"
    control.write_text("control")
    monkeypatch.setattr(constants, "BG_JOBS_FILE", str(control))
    _, result = await dispatch(authority(tmp_path, tool), tool, content)
    assert result["exit_code"] == 0
    assert "jobs.json" not in result["output"]


@pytest.mark.parametrize("producer", ["database", "containment", "jobs", "uploads"])
@pytest.mark.parametrize("alias", ["direct", "hardlink"])
async def test_configured_control_producer_paths_are_protected(tmp_path, monkeypatch, producer, alias):
    target = tmp_path / "custom" / "state"
    target.parent.mkdir()
    if producer == "database":
        import core.database as database
        monkeypatch.setattr(database, "engine", SimpleNamespace(url=SimpleNamespace(
            get_backend_name=lambda: "sqlite", database=str(target))))
    elif producer == "containment":
        from src import containment
        monkeypatch.setattr(containment, "_store_path", lambda: target)
    elif producer == "jobs":
        from src import bg_jobs
        monkeypatch.setattr(bg_jobs, "_STORE", target)
    else:
        from src import tool_utils
        target = target.parent / "uploads.json"
        monkeypatch.setattr(tool_utils, "get_upload_handler", lambda: SimpleNamespace(upload_dir=str(target.parent)))
    target.write_text("server state")
    selector = str(target)
    if alias == "hardlink":
        link = tmp_path / "ordinary"
        os.link(target, link)
        selector = str(link)
    _, result = await dispatch(authority(tmp_path, "read_file"), "read_file", selector)
    assert result["failure_kind"] == "resource_identity_denied"


@pytest.mark.parametrize("roots", [(), None])
async def test_nonworkspace_allowlist_and_operation_do_not_grant_resources(tmp_path, monkeypatch, roots):
    from src import tool_execution as execution
    target = tmp_path / "a"
    target.write_text("private")
    monkeypatch.setattr(execution, "_tool_path_roots", lambda: [str(tmp_path)])
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    grant = authority(None, "read_file", roots=roots)
    _, result = await dispatch(grant, "read_file", str(target))
    assert result["failure_kind"] == "resource_identity_denied"
    implementation.assert_not_awaited()


async def test_explicit_private_root_requires_owner_and_operation(tmp_path):
    (tmp_path / "a").write_text("owned")
    root = FilesystemRoot.seal(tmp_path, scope=FilesystemScope.PRIVATE, owner="alice")
    with pytest.raises(ValueError):
        authority(None, "read_file", roots=(root,), owner="bob")
    grant = authority(None, "read_file", roots=(root,))
    _, result = await dispatch(grant, "read_file", "a")
    assert result["output"] == "owned"
    _, result = await dispatch(grant, "write_file", "b\nx")
    assert result["failure_kind"] == "request_authority_denied"
    assert not (tmp_path / "b").exists()


@pytest.mark.parametrize("content", ['{"path":null}', '{"path":42}', '{"path":[]}', '{"path":{}}', '{"path":"a","path":"b"}'])
async def test_model_cannot_supply_or_reconstruct_a_resource(tmp_path, monkeypatch, content):
    from src import tool_execution as execution
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    _, result = await dispatch(authority(tmp_path, "read_file"), "read_file", content)
    assert result["blocked"] is True
    implementation.assert_not_awaited()


async def test_model_root_field_is_not_authority(tmp_path, monkeypatch):
    from src import tool_execution as execution
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    _, result = await dispatch(authority(None, "write_file"), "write_file",
        json.dumps({"path": str(tmp_path / "a"), "content": "x", "resource_roots": [str(tmp_path)]}))
    assert result["failure_kind"] == "resource_identity_denied"
    implementation.assert_not_awaited()


def test_child_intersects_root_and_preserves_workspace_alias_base(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a").write_text("child")
    (tmp_path / "outside").write_text("parent")
    parent = authority(tmp_path, "read_file")
    narrow = FilesystemRoot.seal(sub, owner="alice")
    child = authority(tmp_path, "read_file", roots=(narrow,))
    for effective in (parent.intersect(child), child.intersect(parent)):
        assert effective.resource_roots == (narrow,)
        assert resolve(effective, "read_file", "/workspace/sub/a").bindings[0].resource.path == str(sub / "a")
        with pytest.raises(ValueError):
            resolve(effective, "read_file", "/workspace/outside")
    assert parent.intersect(authority(tmp_path, "read_file", roots=())).resource_roots == ()
    assert parent.intersect(authority(tmp_path, "read_file", owner="bob")).resource_roots == ()


def test_child_cannot_renew_replaced_parent_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    parent = authority(root, "read_file")
    root.rename(tmp_path / "old")
    root.mkdir()
    child = authority(root, "read_file")
    assert parent.intersect(child).resource_roots == ()


@pytest.mark.parametrize("caller", ["intersection", "context", "task"])
@pytest.mark.parametrize("child_location", ["root", "subtree"])
def test_replaced_parent_cannot_be_renewed_by_new_child_observation(tmp_path, caller, child_location):
    root = tmp_path / "root"
    root.mkdir()
    parent = authority(root, "read_file")
    root.rename(tmp_path / "old")
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    (sub / "a").write_text("replacement")
    child_root = FilesystemRoot.seal(root if child_location == "root" else sub, owner="alice")
    child = authority(root, "read_file", roots=(child_root,))
    if caller == "intersection":
        effective = parent.intersect(child)
    elif caller == "context":
        with bind_request_authority(parent), bind_request_authority(child) as effective:
            assert effective.resource_roots == ()
    else:
        with bind_request_authority(parent):
            sealed = seal_task_authority("Read files in the workspace", "llm", None, owner="alice")
        effective = restore_task_authority(sealed, "Read files in the workspace", "llm", None,
                                          owner="alice", session_id="continuation")
    assert effective.resource_roots == ()
    with pytest.raises(ValueError):
        resolve(effective, "read_file", "sub/a")


def test_equal_stale_roots_are_revalidated(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    parent = authority(root, "read_file")
    root.rename(tmp_path / "old")
    root.mkdir()
    assert parent.intersect(parent).resource_roots == ()


@pytest.mark.parametrize("legacy", [False, True])
async def test_snapshot_preserves_incarnation_and_never_reconstructs_legacy(tmp_path, legacy):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_text("original")
    grant = authority(root, "read_file")
    snapshot = grant.to_dict()
    if legacy:
        snapshot["version"] = 1
        snapshot.pop("resource_roots")
    restored = RequestAuthority.from_dict(json.loads(json.dumps(snapshot)))
    assert restored.resource_roots == (() if legacy else grant.resource_roots)
    root.rename(tmp_path / "old")
    root.mkdir()
    (root / "a").write_text("replacement")
    _, result = await dispatch(restored, "read_file", "a")
    assert result["failure_kind"] == "resource_identity_denied"


@pytest.mark.parametrize("mutation", [None, "root", [{}], [{"path": "/", "scope": "workspace", "identity": {"device": 1, "inode": 2, "kind": "directory"}, "owner": "alice"}]])
def test_malformed_resource_snapshots_are_rejected(tmp_path, mutation):
    snapshot = authority(tmp_path, "read_file").to_dict()
    snapshot["resource_roots"] = mutation
    with pytest.raises((TypeError, ValueError, KeyError)):
        RequestAuthority.from_dict(snapshot)


def test_task_and_background_continuations_keep_original_roots(tmp_path, monkeypatch):
    import src.constants
    monkeypatch.setattr(src.constants, "BG_JOBS_DIR", str(tmp_path))
    grant = authority(tmp_path, "read_file")
    # A roots-only sidecar is legacy state and cannot invent a job generation.
    with pytest.raises(ValueError):
        save_background_authority("job", grant)
    assert restore_background_authority("job", owner="alice", session_id="s").resource_roots == ()
    assert restore_background_authority("job", owner="bob", session_id="s").resource_roots == ()
    with bind_request_authority(grant):
        sealed = seal_task_authority("Read files in the workspace", "llm", None, owner="alice")
    assert restore_task_authority(sealed, "Read files in the workspace", "llm", None,
        owner="alice", session_id="continuation").resource_roots == grant.resource_roots


async def test_dispatch_consumes_canonical_binding_and_pins_native_backend(tmp_path, monkeypatch):
    from src import tool_execution as execution
    import src.agent_tools
    (tmp_path / "a").write_text("bound")
    (tmp_path / "alias").symlink_to(tmp_path / "a")
    handler = AsyncMock(return_value={"output": "handled", "exit_code": 0})
    mcp = AsyncMock()
    monkeypatch.setitem(src.agent_tools.TOOL_HANDLERS, "read_file", handler)
    monkeypatch.setattr(execution, "get_mcp_manager", lambda: mcp)
    _, result = await dispatch(authority(tmp_path, "read_file"), "read_file", "alias")
    assert result["output"] == "handled"
    content, ctx = handler.call_args.args
    assert json.loads(content)["path"] == str(tmp_path / "a")
    assert ctx["resource_operation"].bindings[0].resource.path == str(tmp_path / "a")
    assert ctx["resource_operation"].request_id == "resource-test"
    mcp.call_tool.assert_not_awaited()
    assert active_resource_operation() is None


def test_bound_resolver_rejects_undeclared_paths_and_scopes_search(tmp_path):
    from src.tool_execution import _resolve_tool_path, _resolve_search_root
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a").write_text("a")
    (tmp_path / "outside").write_text("outside")
    grant = authority(tmp_path, "read_file", "grep")
    bound = resolve(grant, "read_file", "sub/a")
    with bind_resource_operation(bound):
        assert _resolve_tool_path(str(sub / "a")) == str(sub / "a")
        with pytest.raises(ValueError):
            _resolve_tool_path(str(tmp_path / "outside"))
    search = resolve(grant, "grep", '{"pattern":"a","path":"sub"}')
    with bind_resource_operation(search):
        assert _resolve_search_root("") == str(sub)
        assert _resolve_tool_path(str(sub / "a")) == str(sub / "a")
        with pytest.raises(ValueError):
            _resolve_tool_path(str(tmp_path / "outside"))


async def test_concurrent_resource_contexts_do_not_leak(tmp_path, monkeypatch):
    from src import tool_execution as execution
    arrived = asyncio.Event()
    seen = []
    async def implementation(block, **kwargs):
        bound = active_resource_operation()
        seen.append(bound.bindings[0].resource.path)
        if len(seen) == 2:
            arrived.set()
        await arrived.wait()
        assert active_resource_operation() is bound
        return "read", {"exit_code": 0}
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    for name in ("a", "b"):
        (tmp_path / name).write_text(name)
    grant = authority(tmp_path, "read_file")
    await asyncio.gather(dispatch(grant, "read_file", "a"), dispatch(grant, "read_file", "b"))
    assert set(seen) == {str(tmp_path / "a"), str(tmp_path / "b")}
    assert active_resource_operation() is None


async def test_last_dispatch_validation_refuses_replacement_and_resets_context(tmp_path, monkeypatch):
    from src import tool_execution as execution
    target = tmp_path / "a"
    target.write_text("old")
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    security = ToolRunSecurityContext()
    def decision(*args):
        target.rename(tmp_path / "old-a")
        target.write_text("new")
        return SimpleNamespace(allowed=True)
    monkeypatch.setattr(security, "decision_for", decision)
    _, result = await dispatch(authority(tmp_path, "read_file"), "read_file", "a", security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"
    implementation.assert_not_awaited()
    assert active_resource_operation() is None
    assert execution.get_active_workspace() is None


@pytest.mark.parametrize("error_type", [None, RuntimeError, asyncio.CancelledError])
async def test_nested_resource_context_restores_on_failure_or_cancellation(tmp_path, monkeypatch, error_type):
    from src import tool_execution as execution
    for name in ("parent", "child"):
        (tmp_path / name).write_text(name)
    grant = authority(tmp_path, "read_file")
    parent = resolve(grant, "read_file", "parent")
    async def implementation(block, **kwargs):
        assert active_resource_operation().bindings[0].resource.path == str(tmp_path / "child")
        if error_type:
            raise error_type("stop")
        return "read", {"exit_code": 0}
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    with bind_resource_operation(parent):
        if error_type:
            with pytest.raises(error_type):
                await dispatch(grant, "read_file", "child")
        else:
            await dispatch(grant, "read_file", "child")
        assert active_resource_operation() is parent
    assert active_resource_operation() is None
    assert execution.get_active_workspace() is None


@pytest.mark.parametrize("kind", ["collision", "hardlink", "escape", "move"])
async def test_patch_validates_all_targets_before_any_write(tmp_path, kind):
    (tmp_path / "a").write_text("old\n")
    (tmp_path / "alias").symlink_to(tmp_path / "a")
    os.link(tmp_path / "a", tmp_path / "hardlink")
    suffix = {
        "collision": "*** Update File: alias\n@@\n-old\n+second",
        "hardlink": "*** Update File: hardlink\n@@\n-old\n+second",
        "escape": "*** Add File: ../escape.txt\n+escaped",
        "move": "*** Update File: alias\n*** Move to: moved\n@@\n-old\n+moved",
    }[kind]
    patch = f"*** Begin Patch\n*** Update File: a\n@@\n-old\n+new\n{suffix}\n*** End Patch"
    _, result = await dispatch(authority(tmp_path, "apply_patch"), "apply_patch", patch)
    assert result["failure_kind"] == "resource_identity_denied"
    assert (tmp_path / "a").read_text() == "old\n"
    assert not (tmp_path / "moved").exists()


def test_move_contract_binds_both_distinct_resources(tmp_path):
    (tmp_path / "a").write_text("source")
    root = FilesystemRoot.seal(tmp_path)
    source = ResourceBinding("source", FilesystemResource.resolve(root, "a"))
    destination = ResourceBinding("destination", FilesystemResource.resolve(root, "b", allow_missing=True))
    operation = ExactOperation("move_file", "a -> b", "move", "move_file")
    with pytest.raises(ValueError):
        BoundFilesystemOperation(operation, "", (source,))
    BoundFilesystemOperation(operation, "", (source, destination)).validate()


def approval(grant, tool, content):
    store = ToolApprovalStore()
    pending = store.create(owner=grant.owner, session_id=grant.session_id,
        origin_run_id="run", tool_name=tool, content=content, workspace=grant.workspace,
        external_untrusted_context_seen=True, capabilities=capabilities_for_action(tool, content),
        request_authority=grant)
    exact = store.consume(pending.approval_id, decision="approve", owner=grant.owner, session_id=grant.session_id)
    security = ToolRunSecurityContext()
    security.external_untrusted_context_seen = True
    return exact, security


@pytest.mark.parametrize("change", ["alias", "file", "parent"])
async def test_approval_resource_retargeting_refuses_without_claiming(tmp_path, change):
    parent = tmp_path / "sub"
    parent.mkdir()
    (parent / "a").write_text("a")
    (parent / "b").write_text("b")
    alias = parent / "alias"
    alias.symlink_to(parent / "a")
    grant = authority(tmp_path, "read_file")
    exact, security = approval(grant, "read_file", "sub/alias")
    assert exact.pending.resource_operation is not None
    if change == "alias":
        alias.unlink()
        alias.symlink_to(parent / "b")
    elif change == "file":
        (parent / "a").rename(parent / "old-a")
        (parent / "a").write_text("replacement")
    else:
        parent.rename(tmp_path / "old-sub")
        parent.mkdir()
        (parent / "a").write_text("replacement")
        alias.symlink_to(parent / "a")
    _, result = await dispatch(grant, "read_file", "sub/alias", exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"
    assert exact.matches(owner="alice", session_id="s", workspace=str(tmp_path), tool_name="read_file", content="sub/alias")


async def test_approval_is_exact_and_one_use_with_immutable_resource_snapshot(tmp_path):
    (tmp_path / "a").write_text("a")
    grant = authority(tmp_path, "read_file")
    exact, security = approval(grant, "read_file", "a")
    with pytest.raises(FrozenInstanceError):
        exact.pending.resource_operation.execution_input = "other"
    assert "resource_operation" not in exact.pending.public_payload()
    _, modified = await dispatch(grant, "read_file", "/workspace/a", exact_approval=exact, security_context=security)
    assert modified["exit_code"] == 1
    _, result = await dispatch(grant, "read_file", "a", exact_approval=exact, security_context=security)
    assert result["output"] == "a"
    _, replay = await dispatch(grant, "read_file", "a", exact_approval=exact, security_context=security)
    assert replay["exit_code"] == 1


async def test_exact_approval_cannot_widen_a_child_resource_scope(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "outside").write_text("parent")
    parent = authority(tmp_path, "read_file")
    exact, security = approval(parent, "read_file", "outside")
    child = replace(parent, resource_roots=(FilesystemRoot.seal(sub, owner="alice"),))
    with bind_request_authority(parent), bind_request_authority(child) as effective:
        _, result = await dispatch(effective, "read_file", "outside", exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"


async def test_approved_resource_cannot_migrate_to_another_request(tmp_path):
    (tmp_path / "a").write_text("original request")
    grant = authority(tmp_path, "read_file")
    exact, security = approval(grant, "read_file", "a")
    _, result = await dispatch(replace(grant, request_id="new-request"), "read_file", "a",
        exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"


async def test_missing_approval_resource_snapshot_cannot_be_reconstructed(tmp_path, monkeypatch):
    grant = authority(tmp_path, "read_file")
    def unavailable(*args, **kwargs):
        raise PermissionError("Cannot establish the proposal's resource identity")

    with monkeypatch.context() as patch:
        patch.setattr("src.agent_runtime.resource_binding.resolve_filesystem_operation", unavailable)
        exact, security = approval(grant, "read_file", "missing")
    assert exact.pending.resource_operation is None
    (tmp_path / "missing").write_text("appeared after proposal")
    _, result = await dispatch(grant, "read_file", "missing", exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"


async def test_approved_absent_read_cannot_bind_a_file_that_appeared(tmp_path):
    grant = authority(tmp_path, "read_file")
    exact, security = approval(grant, "read_file", "missing")
    assert exact.pending.resource_operation.bindings[0].resource.identity is None
    (tmp_path / "missing").write_text("appeared after proposal")
    _, result = await dispatch(grant, "read_file", "missing", exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied"


async def test_exact_user_approval_binds_only_one_missing_destination(tmp_path):
    grant = RequestAuthority.empty(owner="alice", session_id="s", workspace=str(tmp_path))
    exact, security = approval(grant, "write_file", "new/file.txt\napproved")
    _, result = await dispatch(grant, "write_file", "new/file.txt\napproved", exact_approval=exact, security_context=security)
    assert result["exit_code"] == 0
    assert (tmp_path / "new/file.txt").read_text() == "approved"
    assert grant.grants == () and grant.resource_roots == ()
    _, next_action = await dispatch(grant, "write_file", "other.txt\nunapproved")
    assert next_action["failure_kind"] == "request_authority_denied"
    assert not (tmp_path / "other.txt").exists()


@pytest.mark.parametrize("version", [1, 2])
async def test_restored_empty_roots_approval_is_exact_and_never_restores_generic_scope(tmp_path, version):
    (tmp_path / "approved").write_text("approved content")
    (tmp_path / "sibling").write_text("private sibling")
    snapshot = authority(tmp_path, "read_file", "write_file", "ls").to_dict()
    snapshot["version"] = version
    snapshot["resource_roots"] = []
    restored = RequestAuthority.from_dict(snapshot)
    exact, security = approval(restored, "read_file", "approved")
    assert exact.pending.resource_operation is not None
    for tool, content in (("read_file", "sibling"), ("ls", "."), ("write_file", "sibling\nx")):
        _, blocked = await dispatch(restored, tool, content, exact_approval=exact, security_context=security)
        assert blocked["exit_code"] == 1
        _, unapproved = await dispatch(restored, tool, content)
        assert unapproved["failure_kind"] == "resource_identity_denied"
    _, allowed = await dispatch(restored, "read_file", "approved", exact_approval=exact, security_context=security)
    assert allowed["output"] == "approved content"
    _, replay = await dispatch(restored, "read_file", "approved", exact_approval=exact, security_context=security)
    assert replay["exit_code"] == 1
    assert restored.resource_roots == () and restored.backend_resources == ()
    assert (tmp_path / "sibling").read_text() == "private sibling"


@pytest.mark.parametrize("change", ["alias", "request", "session", "owner"])
async def test_restored_exact_filesystem_binding_rejects_retarget_and_rebinding(tmp_path, change):
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    (tmp_path / "alias").symlink_to(tmp_path / "a")
    snapshot = authority(tmp_path, "read_file").to_dict()
    snapshot["version"] = 1
    restored = RequestAuthority.from_dict(snapshot)
    exact, security = approval(restored, "read_file", "alias")
    if change == "alias":
        (tmp_path / "alias").unlink()
        (tmp_path / "alias").symlink_to(tmp_path / "b")
    else:
        restored = replace(restored, **{"request": {"request_id": "other"},
            "session": {"session_id": "other"}, "owner": {"owner": "bob"}}[change])
    _, result = await dispatch(restored, "read_file", "alias", exact_approval=exact, security_context=security)
    assert result["exit_code"] == 1
    assert exact.matches(owner="alice", session_id="s", workspace=str(tmp_path), tool_name="read_file", content="alias")


async def test_resumed_child_approval_cannot_renew_replaced_parent_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    parent = authority(root, "read_file")
    root.rename(tmp_path / "old")
    root.mkdir()
    (root / "new").write_text("replacement")
    child = parent.intersect(authority(root, "read_file"))
    exact, security = approval(child, "read_file", "new")
    assert child.resource_roots == () and exact.pending.resource_operation is None
    _, result = await dispatch(replace(child, inherited=False), "read_file", "new", exact_approval=exact, security_context=security)
    assert result["failure_kind"] == "resource_identity_denied" and not exact._claimed


@pytest.mark.parametrize("request_text,denied", [
    ("Transcribe /workspace/audio.wav", "read_file"),
    ("OCR extract exact text from /workspace/image.png", "write_file"),
    ("List my tasks", "read_file"),
])
async def test_resource_identity_never_expands_narrow_request_classes(tmp_path, request_text, denied):
    (tmp_path / "a").write_text("a")
    grant = create_request_authority(request_text, owner="alice", session_id="s", workspace=str(tmp_path))
    _, result = await dispatch(grant, denied, "a" if denied == "read_file" else "a\nx")
    assert result["failure_kind"] == "request_authority_denied"


def test_nonfilesystem_identities_are_inert_and_distinguish_producers_from_pages():
    from src.process_lifecycle import ProcessIdentity
    ProcessResource("native:containment", "alice", "request", "thread", ProcessIdentity(123, "boot:start"), "leader", "job", "receipt")
    OwnedResource("documents", "alice", "thread", "documents", "document", "revision")
    assert ExternalResource("mcp", "endpoint", "server", "tool", "connection").external is True
    with pytest.raises(ValueError):
        ExternalResource("mcp", "endpoint", "server", "tool", "connection", external=False)
    with pytest.raises(ValueError):
        ProcessResource("native:containment", "alice", "request", "thread", ProcessIdentity(123, ""), "leader", containment_id="receipt")
