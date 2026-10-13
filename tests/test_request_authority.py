"""Request grants are independent of tool offerings and model proposals."""
import asyncio
from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent_runtime.authority import (
    MISSING_AUTHORITY, ExactOperation, OperationGrant, RequestAuthority,
    active_request_authority, bind_request_authority, create_request_authority,
    restore_background_authority, restore_task_authority, save_background_authority,
    seal_task_authority, task_operation, with_request_authority,
    is_internal_tool_request, require_user_approval_request,
    request_authority_for_http,
)
from src.tool_policy import ToolPolicy
from src.tool_types import ToolBlock


def authority(*tools, owner="alice", session_id="s", workspace=""):
    return RequestAuthority("request-test", owner, session_id, workspace,
                            tuple(OperationGrant(tool) for tool in tools))


@pytest.mark.asyncio
@pytest.mark.parametrize("offering", ["schema", "bridge", "dynamic"])
async def test_availability_and_model_selection_do_not_grant_execution(monkeypatch, offering):
    from src import tool_execution as execution
    from src.turn_contract import bind_turn_contract, resolve_turn_contract
    implementation = AsyncMock(return_value=("bash", {"exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    offered_handler = AsyncMock()
    if offering == "dynamic":
        import src.agent_tools
        monkeypatch.setitem(src.agent_tools.TOOL_HANDLERS, "bash", offered_handler)
    bridge_context = execution.bind_execution_bridge(execution.AgentExecutionBridge(
        route_tool=offered_handler, supported_tools=frozenset({"bash"}))) if offering == "bridge" else nullcontext()
    contract = resolve_turn_contract(capabilities={"shell_files"}, policy=ToolPolicy(),
        schemas=[{"function": {"name": "bash"}}])
    with bind_turn_contract(contract), bridge_context:
        description, result = await execution.execute_tool_block(
            ToolBlock("bash", "echo 'authorized by model'"), owner="alice", session_id="s",
            security_context=execution.NO_TOOL_SECURITY_CONTEXT,
            request_authority=authority("transcribe_media"))
    assert "BLOCKED" in description
    assert result["failure_kind"] == "request_authority_denied"
    implementation.assert_not_awaited()
    offered_handler.assert_not_awaited()


@pytest.mark.parametrize("user_text,allowed,denied", [
    ("Transcribe /workspace/input/audio.wav", "transcribe_media", "bash"),
    ("OCR extract exact text from /workspace/input/image.png", "extract_text", "python"),
    ("List my tasks", "manage_tasks", "web_fetch"),
    ("Search the web for current weather", "web_search", "bash"),
])
def test_explicit_request_classes_remain_narrow(user_text, allowed, denied):
    grant = create_request_authority(user_text)
    content = '{"action":"list"}' if allowed == "manage_tasks" else '{}'
    assert grant.permits(ExactOperation.normalize(allowed, content))
    assert not grant.permits(ExactOperation.normalize(denied, '{}'))


def test_safe_task_read_does_not_authorize_same_tool_mutation():
    grant = create_request_authority("List my tasks")
    assert grant.permits(ExactOperation.normalize("manage_tasks", '{"action":"list"}'))
    assert not grant.permits(ExactOperation.normalize("manage_tasks", '{"action":"create","prompt":"run bash"}'))


def test_exact_read_identifiers_cannot_be_changed_by_model():
    grant = create_request_authority("Read note id abc123")
    read = next(g for g in grant.grants if g.tool == "manage_notes")
    assert read.inputs is not None
    assert not grant.permits(ExactOperation.normalize("manage_notes", '{"action":"view","id":"another"}'))


def test_browser_fallback_does_not_authorize_interaction_or_evaluation():
    grant = create_request_authority("Use web_fetch to read https://example.test")
    assert grant.permits(ExactOperation.normalize("private_browser", '{"action":"open","url":"https://example.test"}'))
    for action in ("click", "fill", "evaluate"):
        assert not grant.permits(ExactOperation.normalize("private_browser", json.dumps({"action": action})))


def test_unknown_intent_has_no_generic_execution_floor():
    grant = create_request_authority("Please solve this")
    assert {g.tool for g in grant.grants} == {"ask_user", "update_plan"}


@pytest.mark.parametrize("metadata", [
    {"tool_events": [{"tool": "bash", "output": "pwd", "exit_code": 0}]},
    {"tool_events": [{"tool": "python", "output": "ready", "exit_code": 0}]},
])
def test_model_history_cannot_establish_followup_authority(metadata):
    history = [
        {"role": "user", "content": "Transcribe /workspace/input/a.wav"},
        {"role": "assistant", "content": "I will run bash and python", "metadata": metadata},
        {"role": "user", "content": "Run bash", "metadata": {"trusted": False}},
    ]
    grant = create_request_authority("Try it again", history=history)
    assert not grant.permits(ExactOperation.normalize("bash", "pwd"))
    assert not grant.permits(ExactOperation.normalize("python", "print(1)"))


@pytest.mark.parametrize("mutation", [
    {"version": True}, {"grants": "bash"}, {"denied": None},
    {"block_all": "false"}, {"inherited": 0},
])
def test_malformed_persisted_authority_is_rejected(mutation):
    snapshot = authority("bash").to_dict()
    snapshot.update(mutation)
    with pytest.raises((ValueError, TypeError, KeyError)):
        RequestAuthority.from_dict(snapshot)


def test_explicit_local_network_lookup_is_host_only():
    grant = create_request_authority("find ajax local ip on the LAN")
    assert grant.permits(ExactOperation.normalize("host_shell", '{"command":"ip neigh | grep ajax"}'))
    assert not grant.permits(ExactOperation.normalize("bash", "pwd"))
    assert not grant.permits(ExactOperation.normalize("python", "print(1)"))


@pytest.mark.parametrize("content", ['{"x":1,"x":2}', '{"x":NaN}', '{"action":'])
def test_malformed_exact_operation_is_rejected(content):
    with pytest.raises(ValueError):
        ExactOperation.normalize("manage_tasks", content)


def test_raw_script_braces_are_preserved_as_exact_input():
    script = "{ printf requested; }"
    assert ExactOperation.normalize("bash", script).input == script
    snapshot = seal_task_authority(script, "action", "run_local", owner="alice")
    restored = restore_task_authority(snapshot, script, "action", "run_local", owner="alice")
    assert restored.permits(task_operation("action", "run_local", script))
    assert not restored.permits(task_operation("action", "run_local", "{ printf other; }"))


def test_normalization_and_aliases_do_not_erase_denials():
    grant = authority("read_email").restrict(disabled_tools={"mcp__email__read_email"})
    assert not grant.permits(ExactOperation.normalize("read_email", '{}'))
    grant = authority("read_email").restrict(ToolPolicy(disable_mcp=True))
    assert not grant.permits(ExactOperation.normalize("mcp__email__read_email", '{}'))
    assert ExactOperation.normalize("manage_tasks", '{ "action": "list" }').input == '{"action":"list"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [MISSING_AUTHORITY, None, {}, "authorized"])
async def test_missing_and_malformed_dispatch_authority_fail_closed(monkeypatch, state):
    from src import tool_execution as execution
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    _, result = await execution.execute_tool_block(ToolBlock("bash", "pwd"),
        security_context=execution.NO_TOOL_SECURITY_CONTEXT, request_authority=state)
    assert result["blocked"] is True
    implementation.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_checks_grants_and_current_disabled_policy(monkeypatch, tmp_path):
    from src import tool_execution as execution
    implementation = AsyncMock(return_value=("bash", {"exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    for disabled in (set(), {"bash"}):
        _, result = await execution.execute_tool_block(ToolBlock("bash", "pwd"),
            owner="alice", session_id="s", workspace=str(tmp_path), disabled_tools=disabled,
            security_context=execution.NO_TOOL_SECURITY_CONTEXT,
            request_authority=authority("bash", workspace=str(tmp_path)))
        assert result["exit_code"] == (1 if disabled else 0)
    assert implementation.await_count == 1


@pytest.mark.asyncio
async def test_nested_stream_intersects_and_restores_parent_on_close():
    seen = []
    @with_request_authority
    async def child(messages, request_authority=MISSING_AUTHORITY, owner="alice", session_id="s"):
        seen.append(active_request_authority())
        yield "child"
    parent = authority("transcribe_media")
    with bind_request_authority(parent):
        stream = child([{"role": "user", "content": "Run bash"}],
                       request_authority=authority("bash", "transcribe_media"))
        assert await anext(stream) == "child"
        assert not seen[0].permits(ExactOperation.normalize("bash", "pwd"))
        assert seen[0].permits(ExactOperation.normalize("transcribe_media", '{}'))
        await stream.aclose()
        assert active_request_authority() is parent
    assert active_request_authority() is None


@pytest.mark.asyncio
async def test_retries_and_provider_changes_do_not_recreate_authority():
    seen = []
    @with_request_authority
    async def run(messages, request_authority=MISSING_AUTHORITY, owner="alice", session_id="s"):
        for proposed in ("transcribe_media", "bash", "python"):
            seen.append(active_request_authority())
            yield active_request_authority().permits(ExactOperation.normalize(proposed, '{}'))
    assert [x async for x in run([{"role": "user", "content": "Transcribe /workspace/input/a.wav"}])] == [True, False, False]
    assert all(value is seen[0] for value in seen)


def test_task_snapshot_caps_model_payload_and_rejects_changed_or_missing_state():
    parent = authority("manage_tasks")
    with bind_request_authority(parent):
        snapshot = seal_task_authority("Run bash in the workspace", "llm", None, owner="alice")
    restored = restore_task_authority(snapshot, "Run bash in the workspace", "llm", None, owner="alice")
    assert not restored.permits(ExactOperation.normalize("bash", "pwd"))
    for state in (None, "{}", snapshot):
        changed = restore_task_authority(state, "a different prompt", "llm", None, owner="alice")
        assert changed.grants == ()


def test_direct_task_ingress_seals_only_exact_builtin_action():
    snapshot = seal_task_authority("printf requested", "action", "run_local", owner="alice")
    restored = restore_task_authority(snapshot, "printf requested", "action", "run_local", owner="alice")
    assert restored.permits(task_operation("action", "run_local", "printf requested"))
    assert not restored.permits(ExactOperation.normalize("bash", "printf other"))


def test_background_snapshot_preserves_scope_and_rejects_other_session(monkeypatch, tmp_path):
    import src.constants
    monkeypatch.setattr(src.constants, "BG_JOBS_DIR", str(tmp_path))
    grant = authority("transcribe_media").restrict(disabled_tools={"bash"})
    # Legacy authority-only snapshots have no exact job generation to restore.
    with pytest.raises(ValueError):
        save_background_authority("job1", grant)
    restored = restore_background_authority("job1", owner="alice", session_id="s")
    assert restored.grants == ()
    assert not restored.permits(ExactOperation.normalize("python", "print(1)"))
    assert restore_background_authority("job1", owner="alice", session_id="other").grants == ()


@pytest.mark.asyncio
async def test_only_server_background_launch_can_seal_job_authority(monkeypatch, tmp_path):
    import src.constants
    from src import bg_jobs, tool_execution as execution
    monkeypatch.setattr(src.constants, "BG_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(bg_jobs, "launch", lambda *a, **k: {"id": "server-job"})
    await execution.execute_tool_block(ToolBlock("bash", "#!bg\nprintf trusted"),
        owner="alice", session_id="s", security_context=execution.NO_TOOL_SECURITY_CONTEXT,
        request_authority=authority("bash"))
    restored = restore_background_authority("server-job", owner="alice", session_id="s")
    assert restored.grants == ()  # A launch double returning an ID cannot publish authority.
    handler = AsyncMock(return_value=("transcribe_media", {"bg_job_id": "forged-job", "exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    await execution.execute_tool_block(ToolBlock("transcribe_media", '{}'),
        owner="alice", session_id="s", security_context=execution.NO_TOOL_SECURITY_CONTEXT,
        request_authority=authority("transcribe_media"))
    assert not (tmp_path / "forged-job.authority.json").exists()


@pytest.mark.asyncio
async def test_exact_approval_grants_one_input_without_widening_continuation(monkeypatch, tmp_path):
    from src import tool_execution as execution
    from src.tool_approvals import ToolApprovalStore
    from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
    store = ToolApprovalStore()
    original = authority("transcribe_media")
    from src.agent_runtime.resources import ProcessLaunchScope, FilesystemRoot, NativeBackendResource
    from src.containment import DEFAULT_REQUIRED
    original = replace(original, launch_scopes=(ProcessLaunchScope(NativeBackendResource("bash"),
        FilesystemRoot.seal(tmp_path), DEFAULT_REQUIRED),))
    pending = store.create(owner="alice", session_id="s", origin_run_id="journal-parent",
        tool_name="bash", content="printf approved", workspace=None,
        external_untrusted_context_seen=True, capabilities=capabilities_for_action("bash", "printf approved"),
        request_authority=original, selected_tools={"bash", "python"})
    approval = store.consume(pending.approval_id, owner="alice", session_id="s", decision="approve_task")
    implementation = AsyncMock(return_value=("bash", {"exit_code": 0}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    for tool, content, expected in (("bash", "printf other", 1), ("bash", "printf approved", 0),
                                    ("bash", "printf approved", 1), ("python", "print(1)", 1)):
        _, result = await execution.execute_tool_block(ToolBlock(tool, content), owner="alice", session_id="s",
            security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
            request_authority=original, exact_approval=approval)
        assert result["exit_code"] == expected
    assert implementation.await_count == 1
    assert "request_authority" not in pending.public_payload()


def test_approval_digest_binds_authority_snapshot():
    from src.tool_approvals import ExactToolApproval, ToolApprovalStore
    from src.tool_capabilities import capabilities_for_action
    pending = ToolApprovalStore().create(owner="alice", session_id="s", origin_run_id="journal",
        tool_name="bash", content="pwd", workspace=None, external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "pwd"), request_authority=authority("transcribe_media"))
    forged = ExactToolApproval(replace(pending, request_authority=authority("bash", "python")))
    assert not forged.matches(owner="alice", session_id="s", workspace=None, tool_name="bash", content="pwd")


@pytest.mark.asyncio
async def test_nested_approval_cannot_cross_parent_class_ceiling(monkeypatch):
    from src import tool_execution as execution
    from src.tool_approvals import ToolApprovalStore
    from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
    store = ToolApprovalStore()
    pending = store.create(owner="alice", session_id="s", origin_run_id="parent",
        tool_name="bash", content="pwd", workspace=None, external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "pwd"))
    approval = store.consume(pending.approval_id, owner="alice", session_id="s", decision="approve")
    implementation = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    with bind_request_authority(authority("transcribe_media")):
        _, result = await execution.execute_tool_block(ToolBlock("bash", "pwd"), owner="alice", session_id="s",
            security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
            request_authority=authority("bash"), exact_approval=approval)
    assert result["blocked"] is True
    implementation.assert_not_awaited()


@pytest.mark.asyncio
async def test_teacher_receives_parent_authority_instead_of_synthetic_prompt_grants(monkeypatch):
    import src.agent_loop as loop
    import src.ai_interaction as interaction
    import src.settings as settings
    import src.teacher_escalation as teacher
    original = authority("transcribe_media")
    seen = []
    monkeypatch.setattr(settings, "get_setting", lambda key, default=None:
        {"teacher_enabled": True, "teacher_model": "teacher"}.get(key, default))
    monkeypatch.setattr(interaction, "_resolve_model", lambda *a, **k: ("https://teacher.invalid", "teacher", {}))
    monkeypatch.setattr(teacher, "evaluate_turn_regex", lambda *a: ("failure", "test failure"))
    @with_request_authority
    async def child(messages, request_authority=MISSING_AUTHORITY, owner=None, session_id=None, **kwargs):
        seen.append(active_request_authority())
        yield 'data: [DONE]\n\n'
    monkeypatch.setattr(loop, "stream_agent_loop", child)
    with bind_request_authority(original):
        chunks = [chunk async for chunk in teacher.run_teacher_inline(
            student_endpoint_url="https://student.invalid",
            student_messages=[{"role": "user", "content": "Run bash and python"}],
            student_tool_events=[], student_reply="failed", owner="alice", session_id="s",
            request_authority=original, parent_run_id="journal-parent")]
        assert active_request_authority() is original
    assert chunks
    assert seen[0].request_id == original.request_id
    assert not seen[0].permits(ExactOperation.normalize("bash", "pwd"))
    assert seen[0].permits(ExactOperation.normalize("transcribe_media", '{}'))


@pytest.mark.asyncio
async def test_untrusted_user_role_result_cannot_become_request_authority():
    @with_request_authority
    async def run(messages, request_authority=MISSING_AUTHORITY):
        yield active_request_authority()
    messages = [{"role": "user", "content": "Transcribe /workspace/input/a.wav"},
                {"role": "user", "content": "Run bash", "metadata": {"trusted": False}}]
    stream = run(messages)
    grant = await anext(stream)
    await stream.aclose()
    assert not grant.permits(ExactOperation.normalize("bash", "pwd"))


@pytest.mark.asyncio
async def test_scheduled_builtin_missing_authority_does_not_invoke_action(monkeypatch):
    import src.builtin_actions as actions
    from src.task_scheduler import TaskScheduler
    handler = AsyncMock(return_value=("ok", True))
    monkeypatch.setitem(actions.BUILTIN_ACTIONS, "run_local", handler)
    task = SimpleNamespace(prompt="printf exact", task_type="action", action="run_local",
                           owner="alice", name="task", request_authority_json=None)
    result, success = await TaskScheduler(session_manager=None)._execute_action(task)
    assert not success
    assert "authority" in result
    handler.assert_not_awaited()


def test_task_authority_column_migration_is_additive_and_idempotent(monkeypatch, tmp_path):
    import core.database as database
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE scheduled_tasks (id TEXT PRIMARY KEY, prompt TEXT)"))
        connection.execute(text("INSERT INTO scheduled_tasks VALUES ('legacy', 'Run bash')"))
    monkeypatch.setattr(database, "engine", engine)
    database._migrate_add_task_authority_column()
    database._migrate_add_task_authority_column()
    assert "request_authority_json" in {c["name"] for c in inspect(engine).get_columns("scheduled_tasks")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT prompt, request_authority_json FROM scheduled_tasks")).one() == ("Run bash", None)
    engine.dispose()


@pytest.mark.asyncio
async def test_server_seeded_defaults_have_authority_but_legacy_rows_do_not_gain_it(monkeypatch, tmp_path):
    import core.database as database
    import routes.prefs_routes as preferences
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.task_scheduler import TaskScheduler
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(preferences, "_load_for_user", lambda owner: {})
    scheduler = TaskScheduler(session_manager=None)
    monkeypatch.setattr(scheduler, "ensure_assistant_defaults", AsyncMock())
    with sessions() as db:
        db.add(database.ScheduledTask(id="legacy", owner="alice", name="Legacy housekeeping",
                                     task_type="action", action="tidy_sessions"))
        db.commit()
    await scheduler.ensure_defaults("alice")
    with sessions() as db:
        tasks = db.query(database.ScheduledTask).all()
        assert len(tasks) > 1
        for task in tasks:
            grant = restore_task_authority(task.request_authority_json, task.prompt,
                task.task_type, task.action, owner=task.owner)
            assert grant.permits(task_operation(task.task_type, task.action, task.prompt)) == (task.id != "legacy")
    engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_binds_explicit_authority_for_nested_handler(monkeypatch):
    from src import tool_execution as execution
    seen = []
    async def handler(*args, **kwargs):
        seen.append(active_request_authority())
        with bind_request_authority(authority("bash", "transcribe_media")) as child:
            assert not child.permits(ExactOperation.normalize("bash", "pwd"))
        return "transcribe_media", {"exit_code": 0}
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    await execution.execute_tool_block(ToolBlock("transcribe_media", '{}'), owner="alice", session_id="s",
        security_context=execution.NO_TOOL_SECURITY_CONTEXT, request_authority=authority("transcribe_media"))
    assert seen
    assert active_request_authority() is None


def test_tool_http_task_payload_is_not_new_user_authority():
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from routes.task.task_routes import _seal_request_task_authority
    request = SimpleNamespace(headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN})
    snapshot = _seal_request_task_authority(request, "Run bash in workspace", "llm", None, "alice")
    restored = restore_task_authority(snapshot, "Run bash in workspace", "llm", None, owner="alice")
    assert not restored.permits(ExactOperation.normalize("bash", "pwd"))


@pytest.mark.parametrize("marker", ["header", "middleware"])
@pytest.mark.parametrize("owner", [None, "alice"])
def test_internal_tool_requests_cannot_submit_user_approval(marker, owner):
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from fastapi import HTTPException
    request = SimpleNamespace(
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN} if marker == "header" else {},
        state=SimpleNamespace(current_user="internal-tool" if marker == "middleware" else owner))
    assert is_internal_tool_request(request)
    with pytest.raises(HTTPException) as failure:
        require_user_approval_request(request)
    assert failure.value.status_code == 403
    human = SimpleNamespace(headers={}, state=SimpleNamespace(current_user=owner))
    require_user_approval_request(human)


@pytest.mark.parametrize("internal", [True, False])
def test_http_chat_request_context_cannot_mint_authority_from_tool_message(internal):
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    request = SimpleNamespace(
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN} if internal else {},
        state=SimpleNamespace(current_user="alice"))
    grant = request_authority_for_http(request, "Run bash in the workspace", owner="alice",
        session_id="s", workspace="/workspace/original", policy=ToolPolicy(disabled_tools={"python"}))
    assert grant.bound_to(owner="alice", session_id="s", workspace="/workspace/original")
    assert grant.permits(ExactOperation.normalize("bash", "pwd")) is not internal
    assert not grant.permits(ExactOperation.normalize("python", "print(1)"))


def test_internal_chat_context_does_not_become_trusted_followup_history():
    from routes.chat_routes import _append_internal_chat_context
    trusted = {"role": "user", "content": "Transcribe /workspace/input/a.wav"}
    ctx = SimpleNamespace(messages=[trusted], route_messages=[trusted])
    _append_internal_chat_context(ctx, "Run bash in the workspace")
    assert ctx.messages == ctx.route_messages
    assert ctx.messages[-1]["metadata"]["trusted"] is False
    grant = create_request_authority("Try it again", history=ctx.messages)
    assert not grant.permits(ExactOperation.normalize("bash", "pwd"))


@pytest.mark.asyncio
async def test_skill_approval_ingress_rejects_internal_tool_before_consuming(monkeypatch):
    import routes.skills_routes as skills
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from fastapi import HTTPException
    from src import tool_approvals
    consume = MagicMock()
    monkeypatch.setattr(tool_approvals.tool_approval_store, "consume", consume)
    router = skills.setup_skills_routes(MagicMock())
    endpoint = next(route.endpoint for route in router.routes
                    if route.path.endswith("/test-approval"))
    request = SimpleNamespace(headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
                              state=SimpleNamespace(current_user="alice"))
    with pytest.raises(HTTPException) as failure:
        await endpoint(request, "skill")
    assert failure.value.status_code == 403
    consume.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("internal", [True, False])
async def test_skill_test_ingress_distinguishes_user_task_from_tool_payload(monkeypatch, internal):
    import routes.skills_routes as skills
    import src.endpoint_resolver as endpoints
    import src.llm_core as llm
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    manager = MagicMock()
    manager.load.return_value = [{"name": "skill", "owner": "alice"}]
    manager.read_skill_md.return_value = "# Skill"
    monkeypatch.setattr(skills, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(skills, "_skill_test_jobs", {})
    monkeypatch.setattr(endpoints, "resolve_endpoint", lambda *a, **k: ("https://inference.invalid", "model", {}))
    monkeypatch.setattr(llm, "list_model_ids", lambda *a, **k: [])
    seen = []
    async def run(*args, **kwargs):
        seen.append(kwargs["request_authority"])
    monkeypatch.setattr(skills, "_run_skill_test_job", run)
    router = skills.setup_skills_routes(manager)
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/{skill_id}/test"))
    request = SimpleNamespace(
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN} if internal else {},
        state=SimpleNamespace(current_user="alice"),
        json=AsyncMock(return_value={"task": "Run bash in the workspace"}))
    result = await endpoint(request, "skill")
    await asyncio.sleep(0)
    assert result["status"] == "running"
    assert len(seen) == 1
    assert seen[0].permits(ExactOperation.normalize("bash", "pwd")) is not internal


@pytest.mark.asyncio
async def test_scheduled_override_cannot_replace_stored_authority(monkeypatch):
    from src import agent_loop
    from src.task_scheduler import TaskScheduler
    seen = []
    async def fake_stream(*args, **kwargs):
        seen.append(kwargs)
        yield 'data: {"delta":"done"}\n\n'
        yield 'data: [DONE]\n\n'
    monkeypatch.setattr(agent_loop, "stream_agent_loop", fake_stream)
    task = SimpleNamespace(prompt="Transcribe /workspace/input/a.wav", task_type="llm", action=None,
                           owner="alice", name="test", max_steps=1)
    with bind_request_authority(authority("transcribe_media", workspace="/workspace/original")):
        task.request_authority_json = seal_task_authority(task.prompt, "llm", None, owner="alice")
    await TaskScheduler(session_manager=None)._run_agent_loop("https://inference.invalid", "test", task, "s",
                                                             override_user_message="Run bash and python")
    grant = seen[0]["request_authority"]
    assert grant.permits(ExactOperation.normalize("transcribe_media", '{}'))
    assert not grant.permits(ExactOperation.normalize("bash", "pwd"))
    assert grant.workspace == "/workspace/original"
    assert seen[0]["workspace"] == grant.workspace


@pytest.mark.asyncio
async def test_loop_retains_denial_before_offered_inventory_reconciliation(monkeypatch):
    from src import agent_loop as loop, tool_execution as execution
    from src.turn_contract import resolve_turn_contract
    implementation = AsyncMock(return_value=("bash", {"exit_code": 0, "output": "unexpected"}))
    monkeypatch.setattr(execution, "_execute_tool_block_impl", implementation)
    monkeypatch.setattr(loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(loop, "estimate_tokens", lambda *a, **k: 10)
    async def fake_stream(*args, **kwargs):
        yield 'data: ' + json.dumps({"delta": '```bash\npwd\n```'}) + '\n\n'
    monkeypatch.setattr(loop, "stream_llm_with_fallback", fake_stream)
    contract = resolve_turn_contract(capabilities={"shell_files"}, selected_tools={"bash"},
        schemas=[{"function": {"name": "bash"}}], policy=ToolPolicy())
    chunks = [chunk async for chunk in loop.stream_agent_loop(
        "https://inference.invalid", "test", [{"role": "user", "content": "Run bash pwd"}],
        owner="alice", session_id="s", max_rounds=1, turn_contract=contract, disabled_tools={"bash"})]
    implementation.assert_not_awaited()
    assert chunks[-1] == 'data: [DONE]\n\n'
    assert active_request_authority() is None


@pytest.mark.asyncio
async def test_authority_denial_cannot_create_completion_receipt(monkeypatch):
    from src import tool_execution as execution
    from src.agent_runtime.journal import ActionJournal, bind_journal
    journal = ActionJournal()
    with bind_journal(journal):
        await execution.execute_tool_block(ToolBlock("bash", "pwd"), owner="alice", session_id="s",
            security_context=execution.NO_TOOL_SECURITY_CONTEXT,
            request_authority=authority("transcribe_media"))
    assert len(journal.actions) == 1
    assert journal.actions[0].execution_id is None
    assert journal.actions[0].outcome["authoritative"] is False
    assert journal.actions[0].outcome["blocked"] is True
