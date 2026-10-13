from dataclasses import replace
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import browser_identity as browser
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from src.agent_runtime.resources import BrowserSessionResource, BrowserPageResource, ResourceIdentityError, FilesystemRoot, FilesystemResource
from src.agent_tools.web_tools import PrivateBrowserTool
from src.process_lifecycle import ProcessIdentity
from tests.test_runtime_resource_integration import approval_for, dispatch


@pytest.fixture
def producer(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    binary = root / "agent-browser-linux-x64"
    binary.write_bytes(b"explicit trusted fake producer")
    binary.chmod(0o755)
    checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(browser, "PRODUCER_ROOT", root)
    monkeypatch.setattr(browser, "PRODUCER_HASHES", {"linux-x64": checksum})
    monkeypatch.setattr(browser, "STATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(browser, "_REGISTRY", {})
    monkeypatch.setattr(ProcessIdentity, "owned", lambda self: True)
    state = SimpleNamespace(pid=4321, guid="12345678-1234-1234-1234-123456789abc", loader="loader-original",
        target="A" * 32, label=None, active=True, version="0.35.0", launches=False, calls=[], cdp_calls=[], raw_calls=[])
    async def run(argv, **kwargs):
        state.raw_calls.append(argv)
        return "agent-browser " + state.version, ""
    monkeypatch.setattr(browser, "run_client", run)
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    monkeypatch.setattr(browser.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(browser, "observe", lambda pid, facts: SimpleNamespace(
        identity=ProcessIdentity(state.pid, "frozen:" + str(state.pid), state.pid), facts=binary))
    class Sidecar:
        def __init__(self, url):
            browser.browser_digest(url)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def call(self, method, params=None, session_id=None):
            assert method in browser.CDP_METHODS
            state.cdp_calls.append(method)
            if method == "Target.getTargets":
                return {"targetInfos": [{"targetId": state.target, "type": "page"}]}
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": state.target, "type": "page"}}
            if method == "Target.attachToTarget": return {"sessionId": "observation-only"}
            if method == "Page.getFrameTree": return {"frameTree": {"frame": {"id": state.target, "loaderId": state.loader}}}
            return {}
    monkeypatch.setattr(browser, "CDPSidecar", Sidecar)
    async def command(record, *args):
        state.calls.append(args)
        lifecycle = {"launched": state.launches, "relaunchedBrowser": False, "restartedBackground": False}
        if args[:2] == ("session", "info"):
            return {"active": state.active, "version": state.version, "pid": state.pid, "session": record.key,
                "socketDir": record.env["AGENT_BROWSER_SOCKET_DIR"], "namespace": None, "runtimeError": None,
                "runtime": {"backgroundPid": state.pid, "session": record.key, "engine": "chrome", "browserLaunched": True,
                    "compatibilityStatus": "current", "socketDir": record.env["AGENT_BROWSER_SOCKET_DIR"], "restoreKey": None}}
        if args == ("get", "cdp-url"):
            return {"cdpUrl": "ws://127.0.0.1:12345/devtools/browser/" + state.guid, "lifecycle": lifecycle}
        if args == ("tab", "list"):
            return {"tabs": [{"tabId": "t1", "targetId": state.target, "label": state.label, "title": "metadata",
                "url": "https://same.example", "type": "page", "active": True}]}
        pytest.fail("Page command reached the producer")
    monkeypatch.setattr(browser.RegisteredBrowser, "command", command)
    return state


async def observed(producer):
    record = await browser.register_producer("alice", "thread")
    await browser.observe_registered(record)
    return record


def authority():
    return RequestAuthority("request", "alice", "thread", "", (OperationGrant("private_browser"),))


@pytest.mark.parametrize("action", sorted(browser.PAGE_ACTIONS | {"close"}))
async def test_disabled_page_operations_never_observe_select_or_execute(producer, action):
    record = await observed(producer)
    old = record.pages[0]
    producer.target, producer.loader = "B" * 32, "replacement-document"
    record.pin_armed_for = record.session.observation.session_incarnation  # Still not a producer capability.
    producer.calls.clear(); producer.cdp_calls.clear()
    result = await PrivateBrowserTool().execute(json.dumps({"action": action, "page": "t1"}),
        {"owner": "alice", "session_id": "thread"})
    assert result["failure_kind"] == browser.PAGE_FAILURE
    assert result["executed"] is False and result["retryable"] is False
    assert producer.calls == producer.cdp_calls == []
    assert old.target_id != producer.target


@pytest.mark.parametrize("reason,expected", [
    (browser.PAGE_FAILURE, browser.PAGE_FAILURE),
    ("Unrelated resource identity changed", "resource_identity_denied"),
    (browser.PAGE_FAILURE + ": arbitrary detail", "resource_identity_denied"),
])
async def test_dispatch_boundary_preserves_only_native_browser_page_failure(producer, tmp_path, monkeypatch, reason, expected):
    from src import tool_execution
    from src.agent_runtime.effect_log import EffectLog
    from src.agent_runtime.journal import ActionJournal, bind_journal

    await observed(producer)
    producer.calls.clear()
    producer.cdp_calls.clear()
    journal = ActionJournal()
    journal.effects = EffectLog(journal.run_id, directory=tmp_path / "fx")
    attempts = []

    async def unsupported_operation(*args, **kwargs):
        attempts.append(1)
        if reason == browser.PAGE_FAILURE:
            # The legacy server page helper raises the reserved identity error.
            await PrivateBrowserTool()._capture_post_click_state()
        raise ResourceIdentityError(reason)

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", unsupported_operation)
    monkeypatch.setattr(tool_execution, "mark_dispatch", lambda: pytest.fail("Unsupported page operation dispatched"))
    with bind_journal(journal):
        _, result = await dispatch(authority(), "private_browser", '{"action":"session_info"}')
    assert result["failure_kind"] == expected
    if expected == browser.PAGE_FAILURE:
        assert result["executed"] is False and result["retryable"] is False
    assert journal.actions[0].execution_id is None
    assert journal.effects.history().claims == ()
    assert attempts == [1]


@pytest.mark.parametrize("args", [{"action": "batch", "commands": [["click", "@e1"]]},
    {"action": "tab"}, {"action": "window"}, {"action": "frame"}, {"action": "connect"},
    {"action": "click", "target": "--new-tab"}, {"action": "evaluate", "--cdp": "endpoint"},
    {"action": "click", "targetId": "A" * 32}, {"action": "open", "label": "unsafe"},
    {"action": "open", "provider": "remote"}, {"action": "open", "profile": "private"},
    {"action": "open", "state": "private"}, {"action": "open", "session-name": "other"},
    {"action": "open", "config": "other"}])
async def test_raw_model_escapes_never_spawn(producer, args):
    result = await PrivateBrowserTool().execute(json.dumps(args), {})
    assert result["executed"] is False
    assert producer.raw_calls == producer.calls == []


@pytest.mark.parametrize("page", ["t0", "t01", "t-1", "current", "title", "label", "A" * 32, 0, None])
def test_alias_validation(page):
    with pytest.raises(ValueError): browser.parse_operation(json.dumps({"action": "click", "page": page}))


async def test_observation_serializes_no_guid_or_control_url(producer):
    record = await observed(producer)
    page = record.pages[0]
    payload = json.dumps(page.to_dict())
    assert producer.guid not in payload and "devtools/browser" not in payload
    assert BrowserPageResource.from_dict(page.to_dict()) == page
    assert page.target_id == "A" * 32 and page.loader_id == producer.loader
    assert record.pin_armed_for is None
    assert not any("pin-tab" in str(c) for c in producer.calls)
    assert "Target.detachFromTarget" in producer.cdp_calls


@pytest.mark.parametrize("field,value", [("pid", 5678), ("guid", "87654321-1234-1234-1234-123456789abc")])
async def test_session_replacement_invalidates_every_old_observation(producer, field, value):
    record = await observed(producer)
    old, page = record.session, record.pages[0]
    record.pin_armed_for = old.observation.session_incarnation
    setattr(producer, field, value)
    await browser.observe_registered(record)
    assert record.session != old and record.pin_armed_for is None
    with pytest.raises(ValueError): old.validate()
    with pytest.raises(ValueError): page.validate()


@pytest.mark.parametrize("field,value", [("label", "A" * 32), ("loader", ""), ("active", False),
    ("version", "0.27.0"), ("version", "0.36.0"), ("launches", True)])
async def test_bad_producer_observation_fails_closed(producer, field, value):
    record = await observed(producer)
    setattr(producer, field, value)
    with pytest.raises(ValueError): await browser.observe_registered(record)
    assert record.session is None and record.pages == ()


async def test_replacing_same_url_page_or_loader_invalidates_document(producer):
    record = await observed(producer)
    old = record.pages[0]
    producer.loader = "new-loader"
    await browser.observe_registered(record)
    with pytest.raises(ValueError): old.validate()
    document = record.pages[0]
    producer.target = "C" * 32
    await browser.observe_registered(record)
    with pytest.raises(ValueError): document.validate()


@pytest.mark.parametrize("version", ["0.27.0", "0.36.0", "", "0.35.0-extra"])
async def test_exact_producer_version_gate(producer, version):
    producer.version = version
    with pytest.raises(ValueError): await browser.trusted_producer()


async def test_binary_hash_gate_does_not_search_path_or_npx(producer):
    (browser.PRODUCER_ROOT / "agent-browser-linux-x64").write_bytes(b"replacement")
    with pytest.raises(ValueError): await browser.trusted_producer()
    assert producer.raw_calls == []
    assert PrivateBrowserTool._local_agent_browser_binary() is None


@pytest.mark.parametrize("raw", ['{}', '{"success":true}', '{"success":1,"data":{}}',
    '{"success":true,"data":{},"extra":1}', '{"success":true,"data":{},"success":false}',
    '{"success":true,"data":{},"error":"secret"}', 'not-json'])
def test_strict_response_schema(raw):
    with pytest.raises(ValueError): browser.response(raw)


@pytest.mark.parametrize("url", ["ws://127.0.0.1:123/devtools/browser", "ws://evil:123/devtools/browser/12345678-1234-1234-1234-123456789abc",
    "http://127.0.0.1:123/devtools/browser/12345678-1234-1234-1234-123456789abc", "ws://127.0.0.1:99999/devtools/browser/12345678-1234-1234-1234-123456789abc"])
def test_endpoint_validation_does_not_leak_capability(url):
    with pytest.raises(ValueError) as failure: browser.browser_digest(url)
    assert url not in str(failure.value)


async def test_environment_config_and_cwd_are_server_owned(producer, monkeypatch):
    monkeypatch.setenv("AGENT_BROWSER_CDP", "untrusted")
    monkeypatch.setenv("AGENT_BROWSER_CONFIG", "untrusted")
    record = await observed(producer)
    assert record.env == browser.owned_environment(record.cwd, record.key)
    assert record.cwd.is_relative_to(browser.STATE_ROOT)
    assert record.config.read_text() == "{}"
    record.config.write_text('{"cdp":"remote"}')
    with pytest.raises(ValueError): record.validate_config()


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
async def test_browser_control_state_is_not_user_filesystem(producer, tmp_path, alias):
    record = await observed(producer)
    target = record.config
    if alias != "direct":
        target = tmp_path / "alias"
        (os.link(record.config, target) if alias == "hardlink" else target.symlink_to(record.config))
    with pytest.raises(ValueError): FilesystemResource.resolve(FilesystemRoot.seal(tmp_path), str(target))
    from src.agent_runtime.process_resources import guard_launch_workspace
    with pytest.raises(ValueError): guard_launch_workspace(FilesystemRoot.seal(tmp_path))


async def test_session_metadata_exact_approval_first_use_and_replay(producer):
    await observed(producer)
    original = authority()
    content = '{"action":"session_info"}'
    approval = approval_for(original, "private_browser", content)
    assert approval.pending.browser_operation.session == original.browser_sessions[0]
    restored = replace(original, grants=(), browser_sessions=(), browser_pages=(), backend_resources=())
    _, first = await dispatch(restored, "private_browser", content, approval)
    assert first["exit_code"] == 0
    assert "https://same.example" not in first["output"]
    _, replay = await dispatch(restored, "private_browser", content, approval)
    assert replay["exit_code"] == 1
    assert restored.browser_sessions == restored.browser_pages == ()


async def test_page_approval_cannot_enable_unsupported_operations(producer):
    await observed(producer)
    original = authority()
    content = '{"action":"click","page":"t1","ref":"e1"}'
    approval = approval_for(original, "private_browser", content)
    assert approval.pending.browser_operation.page.loader_id == producer.loader
    producer.calls.clear(); producer.cdp_calls.clear()
    restored = replace(original, grants=(), browser_sessions=(), browser_pages=())
    _, denied = await dispatch(restored, "private_browser", content, approval)
    assert denied["failure_kind"] == browser.PAGE_FAILURE and denied["executed"] is False
    assert not approval._claimed and producer.calls == producer.cdp_calls == []


@pytest.mark.parametrize("field,value", [("owner", "bob"), ("request_id", "other"), ("session_id", "other")])
async def test_browser_approval_application_binding_is_exact(producer, field, value):
    await observed(producer)
    original = authority()
    content = '{"action":"session_info"}'
    approval = approval_for(original, "private_browser", content)
    changed = replace(original, **{field: value}, browser_sessions=(), browser_pages=())
    _, result = await dispatch(changed, "private_browser", content, approval)
    assert result["exit_code"] == 1 and not approval._claimed


async def test_page_child_cannot_acquire_session_scope_or_new_document(producer):
    record = await observed(producer)
    original = replace(authority(), browser_sessions=())
    child = original.intersect(authority())
    assert child.browser_sessions == () and child.browser_pages == original.browser_pages
    with pytest.raises(ValueError): browser.resolve_browser_operation(child, ExactOperation.normalize("private_browser", '{"action":"session_info"}'))
    producer.loader = "replacement"
    await browser.observe_registered(record)
    with pytest.raises(ValueError): original.intersect(authority())


@pytest.mark.parametrize("phase", ["success", "exception", "cancel", "nested"])
async def test_browser_context_restoration(producer, phase):
    await observed(producer)
    bound = browser.resolve_browser_operation(authority(), ExactOperation.normalize("private_browser", '{"action":"session_info"}'))
    try:
        with browser.bind_browser_operation(bound):
            if phase == "exception": raise RuntimeError()
            if phase == "cancel": raise asyncio.CancelledError()
            if phase == "nested":
                with browser.bind_browser_operation(None): assert browser._ACTIVE.get() is None
                assert browser._ACTIVE.get() is bound
    except (RuntimeError, asyncio.CancelledError): pass
    assert browser._ACTIVE.get() is None


async def test_legacy_restoration_does_not_discover_browser_scopes(producer):
    await observed(producer)
    data = authority().to_dict()
    data["version"] = 4
    del data["browser_sessions"], data["browser_pages"]
    restored = RequestAuthority.from_dict(data)
    assert restored.browser_sessions == restored.browser_pages == ()


def test_lookup_does_not_create_legacy_or_missing_session(producer):
    assert browser.registered("alice", "thread") is None
    assert authority().browser_sessions == ()
    assert browser._REGISTRY == {} and producer.raw_calls == []
