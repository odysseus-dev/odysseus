import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import bg_jobs, containment, process_ownership, tool_execution
from src.agent_runtime import process_resources as resources
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority, create_request_authority
from src.agent_runtime.resources import NativeBackendResource, ResourceIdentityError
from src.agent_tools.subprocess_tools import BashTool
from src.process_lifecycle import ProcessIdentity
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock
from tests.process_resource_helpers import launch_authority, seed_linkage


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.setattr(resources, "_LAUNCH_DIR", tmp_path / "private" / "launches")
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "private" / "jobs.json")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "private" / "jobs")
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "private" / "receipts.json")
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)
    monkeypatch.setattr(containment, "MECHANISMS", tuple(m for m in containment.MECHANISMS if m.name == "process_group"))
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    return work


def authority(workspace, tool="bash"):
    return RequestAuthority("request", "alice", "thread", str(workspace), (OperationGrant(tool),))


def approval_for(authority, tool, content):
    store = ToolApprovalStore()
    pending = store.create(owner=authority.owner, session_id=authority.session_id, origin_run_id="run",
        tool_name=tool, content=content, workspace=authority.workspace,
        capabilities=capabilities_for_action(tool, content), external_untrusted_context_seen=True,
        request_authority=authority)
    return store.consume(pending.approval_id, owner=authority.owner, session_id=authority.session_id, decision="approve")


async def dispatch(authority, tool, content, approval=None):
    return await tool_execution.execute_tool_block(ToolBlock(tool, content), owner=authority.owner,
        session_id=authority.session_id, workspace=authority.workspace,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=bool(approval)),
        request_authority=authority, exact_approval=approval)


async def test_native_producer_without_binding_cannot_spawn(workspace, monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("unbound spawn"))
    result = await BashTool().execute("printf unsafe", {})
    assert result["failure_kind"] == "resource_identity_denied"


async def test_producer_rejects_changed_command_after_admission(workspace, monkeypatch):
    with launch_authority("printf admitted", workspace):
        monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("retargeted spawn"))
        result = await BashTool().execute("printf changed", {})
    assert result["blocked"]


@pytest.mark.parametrize("ctx", [{"owner": "bob", "session_id": "thread"},
                                 {"owner": "alice", "session_id": "replacement"}])
async def test_native_producer_rechecks_application_binding(workspace, monkeypatch, ctx):
    admitted = authority(workspace)
    operation = ExactOperation.normalize("bash", "printf admitted")
    bound = resources.resolve_process_operation(admitted, operation, NativeBackendResource("bash"))
    monkeypatch.setattr(containment, "acquire", lambda *a, **k: pytest.fail("Rebound producer acquired boundary"))
    with bind_request_authority(admitted), resources.bind_process_operation(bound):
        result = await BashTool().execute(operation.input, ctx)
    assert result["exit_code"] == 1 and "owner or session changed" in result["error"]


async def test_scheduled_local_runner_uses_exact_launch_ceiling(workspace):
    from src import builtin_actions
    output, success = await builtin_actions.action_run_local("alice", script="printf scheduled")
    assert not success and "no server authority" in output
    admitted = replace(authority(workspace), grants=(OperationGrant("bash", inputs=frozenset({"printf scheduled"})),))
    with bind_request_authority(admitted):
        output, success = await builtin_actions.action_run_local("alice", script="printf scheduled")
        assert success and output == "scheduled"
        output, success = await builtin_actions.action_run_local("alice", script="printf changed")
        assert not success and "sealed operation" in output
        output, success = await builtin_actions.action_ssh_command("alice", command="printf scheduled", host="remote.example")
        assert not success and "external backend" in output


async def test_attachment_failure_after_execution_does_not_claim_no_execution(workspace, monkeypatch):
    def failure(*args):
        raise OSError("attachment publication failed")
    monkeypatch.setattr(resources, "attach_containment_processes", failure)
    _, result = await dispatch(authority(workspace), "bash", "printf occurred > effect")
    assert (workspace / "effect").read_text() == "occurred"
    assert result["exit_code"] == 1 and result["failure_kind"] == "resource_linkage_unavailable"
    assert result["containment"]["executed"] is True and result["teardown"]["dead"] is True


async def test_exact_launch_first_use_replay_and_empty_scope_restoration(workspace):
    original = authority(workspace)
    approval = approval_for(original, "bash", "printf exact")
    assert approval.pending.process_operation.launch is not None
    restored = replace(original, grants=(), resource_roots=(), backend_resources=(), launch_scopes=(), process_resources=(), job_resources=())
    _, first = await dispatch(restored, "bash", "printf exact", approval)
    assert first["exit_code"] == 0 and first["output"] == "exact"
    assert restored.launch_scopes == restored.job_resources == restored.process_resources == ()
    _, replay = await dispatch(restored, "bash", "printf exact", approval)
    assert replay["exit_code"] == 1
    _, sibling = await dispatch(restored, "bash", "printf sibling")
    assert sibling["failure_kind"] == "request_authority_denied"


async def test_exact_job_first_use_replay_and_empty_scope_restoration(workspace, monkeypatch):
    bg_jobs._JOBS_DIR.mkdir(parents=True)
    record = {"id": "job", "session_id": "thread", "command": "printf history", "pid": 4321,
              "status": "done", "started_at": 1, "max_runtime_s": 3600,
              "log_path": str(bg_jobs._JOBS_DIR / "job.log")}
    seed_linkage(record, workspace, owner="alice")
    Path(record["log_path"]).write_text("historical result")
    bg_jobs._save({"job": record})
    original = authority(workspace, "manage_bg_jobs")
    content = '{"action":"output","job_id":"job"}'
    approval = approval_for(original, "manage_bg_jobs", content)
    restored = replace(original, grants=(), resource_roots=(), backend_resources=(),
                       launch_scopes=(), process_resources=(), job_resources=())
    _, first = await dispatch(restored, "manage_bg_jobs", content, approval)
    assert first["exit_code"] == 0 and "historical result" in first["output"]
    _, replay = await dispatch(restored, "manage_bg_jobs", content, approval)
    assert replay["exit_code"] == 1
    _, unapproved = await dispatch(restored, "manage_bg_jobs", content)
    assert unapproved["failure_kind"] == "request_authority_denied"
    assert restored.process_resources == restored.job_resources == restored.launch_scopes == ()


async def test_cancellation_at_native_spawn_restores_all_context(workspace, monkeypatch):
    entered = asyncio.Event()
    async def held_run(grant, command, **kwargs):
        assert resources.active_process_operation().launch is not None
        entered.set()
        try:
            await asyncio.Future()
        finally:
            containment.release(grant, grace_s=0)
    monkeypatch.setattr(containment, "run", held_run)
    async def invoke():
        try:
            await dispatch(authority(workspace), "bash", "sleep 60")
        finally:
            from src.agent_runtime.authority import active_request_authority
            assert resources.active_process_operation() is None
            assert active_request_authority() is None
    task = asyncio.create_task(invoke())
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert containment.active_grants() == []


@pytest.mark.parametrize("field,value", [("owner", "bob"), ("request_id", "replacement"), ("session_id", "other-thread")])
async def test_exact_launch_binding_substitution_fails(workspace, field, value):
    original = authority(workspace)
    approval = approval_for(original, "bash", "printf exact")
    changed = replace(original, **{field: value}, resource_roots=None, backend_resources=None,
                      owned_scopes=None, launch_scopes=None)
    _, denied = await dispatch(changed, "bash", "printf exact", approval)
    assert denied["exit_code"] == 1 and not approval._claimed


async def test_exact_launch_replaced_workspace_fails_before_claim(workspace):
    original = authority(workspace)
    approval = approval_for(original, "bash", "pwd")
    workspace.rename(workspace.with_name("retired"))
    workspace.mkdir()
    _, result = await dispatch(original, "bash", "pwd", approval)
    assert result["failure_kind"] == "resource_identity_denied" and not approval._claimed


@pytest.mark.parametrize("phase", ["success", "error", "cancel", "nested"])
async def test_process_context_restores(workspace, phase):
    original = authority(workspace)
    bound = resources.resolve_process_operation(original, ExactOperation.normalize("bash", "pwd"), NativeBackendResource("bash"))
    async def call():
        with resources.bind_process_operation(bound):
            assert resources.active_process_operation() is bound
            if phase == "error":
                raise RuntimeError("ordinary")
            if phase == "cancel":
                raise asyncio.CancelledError()
            if phase == "nested":
                with resources.bind_process_operation(None):
                    assert resources.active_process_operation() is None
                assert resources.active_process_operation() is bound
    try:
        await call()
    except (RuntimeError, asyncio.CancelledError):
        pass
    assert resources.active_process_operation() is None


@pytest.mark.parametrize("publication", ["launch", "sidecar", "job"])
def test_detached_publication_failure_cannot_release_workload(workspace, monkeypatch, publication):
    effect = workspace / "effect"
    if publication == "launch":
        monkeypatch.setattr(resources, "publish_launch", lambda *a, **k: (_ for _ in ()).throw(OSError("publication failed")))
    elif publication == "sidecar":
        monkeypatch.setattr("src.agent_runtime.authority.save_background_authority", lambda *a, **k: (_ for _ in ()).throw(OSError("sidecar failed")))
    else:
        monkeypatch.setattr(bg_jobs, "_save", lambda *a: (_ for _ in ()).throw(OSError("job failed")))
    with launch_authority("printf unsafe > effect", workspace):
        with pytest.raises(OSError):
            bg_jobs.launch("printf unsafe > effect", "chat", cwd=str(workspace))
    assert not effect.exists()
    assert containment.active_grants() == []


def test_detached_release_observes_complete_durable_linkage(workspace, monkeypatch):
    real_popen = bg_jobs.subprocess.Popen
    observations = []
    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        original = proc.stdin
        class Gate:
            @property
            def closed(self):
                return original.closed
            def close(self):
                return original.close()
            def write(self, content):
                payload = json.loads(content)
                published = json.loads(Path(payload["launch_path"]).read_text())
                sidecar = json.loads(Path(payload["authority_path"]).read_text())
                rec = bg_jobs.peek(payload["job_id"])
                assert rec["resource_identity"] == published["job"] == sidecar["job"]
                assert sidecar["authority"] == published["authority"]
                observations.append(True)
                return original.write(content)
        proc.stdin = Gate()
        return proc
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", popen)
    with launch_authority("printf released", workspace):
        rec = bg_jobs.launch("printf released", "chat", cwd=str(workspace))
    assert observations == [True]
    proc = bg_jobs._LIVE_PROCS.pop(rec["pid"])
    proc.wait(timeout=10)
    bg_jobs.refresh(rec["id"])
    assert bg_jobs.peek(rec["id"])["status"] == "done"


@pytest.mark.parametrize("replacement", ["pid", "job", "receipt", "role"])
async def test_job_approval_revalidates_exact_resource_before_claim(workspace, monkeypatch, replacement):
    monkeypatch.setattr(process_ownership, "verify", lambda *a: process_ownership.OWNED)
    monkeypatch.setattr(ProcessIdentity, "exited", lambda self: False)
    bg_jobs._JOBS_DIR.mkdir(parents=True)
    record = {"id": "job", "session_id": "thread", "command": "sleep 60", "pid": 4321,
              "status": "running", "started_at": 1, "max_runtime_s": 3600,
              "exit_path": str(bg_jobs._JOBS_DIR / "job.exit"), "log_path": str(bg_jobs._JOBS_DIR / "job.log")}
    seed_linkage(record, workspace, owner="alice")
    bg_jobs._save({"job": record})
    admitted = authority(workspace, "manage_bg_jobs")
    content = '{"action":"kill","job_id":"job"}'
    approval = approval_for(admitted, "manage_bg_jobs", content)
    assert approval.pending.process_operation.jobs
    if replacement == "pid":
        monkeypatch.setattr(process_ownership, "verify", lambda *a: process_ownership.FOREIGN)
    else:
        jobs = bg_jobs._load()
        if replacement == "job":
            jobs["job"]["resource_identity"]["generation"] = "f" * 32
        elif replacement == "role":
            jobs["job"]["resource_identity"]["processes"][0]["role"] = "leader"
        else:
            jobs["job"]["containment_id"] = "replacement"
        bg_jobs._save(jobs)
    _, result = await dispatch(admitted, "manage_bg_jobs", content, approval)
    assert result["failure_kind"] == "resource_identity_denied" and not approval._claimed


@pytest.mark.parametrize("request_text", ["Transcribe /workspace/audio.wav", "OCR this image", "List my tasks"])
async def test_new_resources_do_not_expand_turn_contract_classes(workspace, request_text):
    admitted = create_request_authority(request_text, owner="alice", session_id="thread", workspace=str(workspace))
    _, denied = await dispatch(admitted, "bash", "pwd")
    assert denied["failure_kind"] == "request_authority_denied"


def test_internal_shell_control_has_no_admin_floor_even_without_auth(monkeypatch):
    from routes import shell_routes
    from core.middleware import INTERNAL_TOOL_USER
    from fastapi import HTTPException
    request = SimpleNamespace(headers={}, state=SimpleNamespace(current_user=INTERNAL_TOOL_USER))
    monkeypatch.setattr(shell_routes, "_auth_disabled", lambda: True)
    with pytest.raises(HTTPException) as error:
        shell_routes._require_admin(request)
    assert error.value.status_code == 403


@pytest.mark.parametrize("mode", ["auth_disabled", "missing_manager"])
def test_unlabelled_loopback_cannot_gain_native_control(monkeypatch, mode):
    from routes import shell_routes
    from fastapi import HTTPException
    request = SimpleNamespace(headers={}, state=SimpleNamespace(current_user=None),
                              app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    monkeypatch.setattr(shell_routes, "_auth_disabled", lambda: mode == "auth_disabled")
    with pytest.raises(HTTPException) as error:
        shell_routes._require_admin(request)
    assert error.value.status_code == 403


def test_authenticated_human_administration_is_not_an_internal_tool_floor(monkeypatch):
    from routes import shell_routes
    request = SimpleNamespace(headers={}, state=SimpleNamespace(current_user="admin"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_admin=lambda u: u == "admin"))))
    monkeypatch.setattr(shell_routes, "_auth_disabled", lambda: False)
    shell_routes._require_admin(request)


@pytest.mark.parametrize("path,payload", [("/api/cookbook/kill-pid", {"pid": 4321}),
    ("/api/cookbook/state", {"tasks": []}), ("/api/model/serve", {}), ("/api/model/download", {})])
async def test_anonymous_native_cookbook_control_rejected_before_producer(monkeypatch, path, payload):
    from routes import cookbook_routes, shell_routes
    from fastapi import FastAPI
    import httpx
    monkeypatch.setattr(shell_routes, "_auth_disabled", lambda: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("Anonymous producer reached"))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", lambda *a, **k: pytest.fail("Anonymous producer reached"))
    app = FastAPI()
    app.include_router(cookbook_routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 123)), base_url="http://local") as client:
        result = await client.post(path, json=payload)
    assert result.status_code == 403


@pytest.mark.parametrize("path", ["/api/shell/exec", "/api/model/serve", "/api/cookbook/kill-pid", "/api/cookbook/state", "/api/shell/../cookbook/kill-pid"])
def test_generic_loopback_cannot_bypass_process_resources(path):
    from src.agent_runtime.owned_resources import needs_owned_binding
    with pytest.raises(ResourceIdentityError):
        needs_owned_binding(ExactOperation.normalize("app_api", json.dumps({"path": path})))


async def test_direct_local_cookbook_control_does_not_enroll_discovered_processes(monkeypatch):
    from src.tools import cookbook
    async def state():
        return {}
    monkeypatch.setattr(cookbook, "_capture_session_processes", lambda *a: pytest.fail("discovery enrolled as ownership"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("unbound Cookbook control"))
    # No server session registry exists for this selector; observation cannot
    # mint a process resource even when the UI supplies a matching name.
    result = await cookbook._cookbook_kill_session("serve-unowned")
    assert result["failure_kind"] == "resource_identity_denied"


def test_direct_containment_attachment_skips_unobservable_token(workspace, monkeypatch):
    import uuid
    from src.agent_runtime.resources import ProcessResource

    # 1. Unobservable child token: pid exists, start_token is None
    op = ExactOperation.normalize("bash", "printf test")
    bound = resources.resolve_process_operation(authority(workspace), op, NativeBackendResource("bash"))
    launch = bound.launch
    containment_id = uuid.uuid4().hex

    resources.publish_launch(launch, authority(workspace), containment_id)
    containment._save_records({
        containment_id: {
            "id": containment_id,
            "launch_generation": launch.generation,
            "workspace": launch.scope.root.path,
            "pid": 54321,
            "start_token": None,
            "pgid": 54321,
            "mechanism": "process_group",
        }
    })

    # Guard: ensure no attempt is made to rediscover/rebind from process table
    monkeypatch.setattr(process_ownership, "process_table", lambda *a, **k: pytest.fail("re-read process table"))
    monkeypatch.setattr(process_ownership, "start_token", lambda *a, **k: pytest.fail("re-read current PID start_token"))

    # Must NOT raise
    resources.attach_containment_processes(launch, containment_id)

    # Publication remains valid
    pub_path = resources.launch_path(launch.generation)
    published = json.loads(pub_path.read_text())
    assert published["launch"] == launch.to_dict()
    assert published["containment_id"] == containment_id
    assert published["processes"] == []

    # 2. Record with pid + valid token still publishes exact ProcessResource
    op_valid = ExactOperation.normalize("bash", "printf valid")
    bound_valid = resources.resolve_process_operation(authority(workspace), op_valid, NativeBackendResource("bash"))
    launch_valid = bound_valid.launch
    cid_valid = uuid.uuid4().hex

    resources.publish_launch(launch_valid, authority(workspace), cid_valid)
    containment._save_records({
        cid_valid: {
            "id": cid_valid,
            "launch_generation": launch_valid.generation,
            "workspace": launch_valid.scope.root.path,
            "pid": 65432,
            "start_token": "procfs:boot:token65432",
            "pgid": 65432,
            "mechanism": "process_group",
        }
    })

    resources.attach_containment_processes(launch_valid, cid_valid)
    published_valid = json.loads(resources.launch_path(launch_valid.generation).read_text())
    assert len(published_valid["processes"]) == 1
    leader_res = ProcessResource.from_dict(published_valid["processes"][0])
    assert leader_res.role == "leader"
    assert leader_res.identity.pid == 65432
    assert leader_res.identity.start_token == "procfs:boot:token65432"
    assert leader_res.identity.pgid == 65432


@pytest.mark.parametrize("tool,command,expected_out", [
    ("bash", "printf hi", "hi"),
    ("python", "print('hi', end='')", "hi"),
])
async def test_end_to_end_fast_exit_preserves_command_result(workspace, monkeypatch, tool, command, expected_out):
    import os
    original_capture = process_ownership.capture
    def mocked_capture(pid):
        if pid == os.getpid():
            return original_capture(pid)
        return {"pid": pid, "start_token": None}
    monkeypatch.setattr(process_ownership, "capture", mocked_capture)

    # Observe the real attachment before foreground lifecycle retirement.
    published = []
    attach = resources.attach_containment_processes
    def observe_attachment(launch, containment_id):
        attach(launch, containment_id)
        published.append(json.loads(resources.launch_path(launch.generation).read_text()))
    monkeypatch.setattr(resources, "attach_containment_processes", observe_attachment)

    auth = authority(workspace, tool=tool)
    approval = approval_for(auth, tool, command)
    _, result = await dispatch(auth, tool, command, approval)

    assert result["exit_code"] == 0
    assert result.get("output") == expected_out
    assert "failure_kind" not in result or result["failure_kind"] != "resource_linkage_unavailable"

    launches_dir = resources._LAUNCH_DIR
    launch_files = list(launches_dir.glob("*.json"))
    assert not launch_files
    cid = result.get("containment", {}).get("id")
    assert cid
    matching = [record for record in published if record.get("containment_id") == cid]
    assert len(matching) == 1
    assert matching[0]["processes"] == []


def test_missing_start_token_security_negative(workspace, monkeypatch):
    import uuid
    import src.process_lifecycle as pl
    from src.process_lifecycle import ProcessIdentity

    op = ExactOperation.normalize("bash", "printf test")
    bound = resources.resolve_process_operation(authority(workspace), op, NativeBackendResource("bash"))
    launch = bound.launch
    cid = uuid.uuid4().hex

    resources.publish_launch(launch, authority(workspace), cid)
    containment._save_records({
        cid: {
            "id": cid,
            "launch_generation": launch.generation,
            "workspace": launch.scope.root.path,
            "pid": 77777,
            "start_token": None,
            "pgid": 77777,
            "mechanism": "process_group",
        }
    })

    created_identities = []
    orig_identity_init = ProcessIdentity.__init__
    def spy_identity_init(self, pid, start_token, pgid=None):
        created_identities.append((pid, start_token, pgid))
        return orig_identity_init(self, pid, start_token, pgid=pgid)

    monkeypatch.setattr(ProcessIdentity, "__init__", spy_identity_init)
    monkeypatch.setattr(process_ownership, "process_table", lambda *a, **k: pytest.fail("PID rediscovery attempted via process_table"))
    monkeypatch.setattr(process_ownership, "start_token", lambda *a, **k: pytest.fail("PID rediscovery attempted via start_token"))

    resources.attach_containment_processes(launch, cid)

    # 1. No ProcessIdentity created for this unobservable process
    assert not any(pid == 77777 for pid, token, pgid in created_identities)

    # 2. No process authority published
    published = json.loads(resources.launch_path(launch.generation).read_text())
    assert published["processes"] == []

    # 3. No signal authority
    fake_ident = ProcessIdentity(77777, None, 77777)
    assert fake_ident.verdict() == process_ownership.UNVERIFIABLE
    assert pl.signal_identity(fake_ident, 15) is False
