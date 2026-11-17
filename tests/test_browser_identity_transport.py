import asyncio
import json
from types import SimpleNamespace

import pytest

from src import browser_identity as browser
from src.agent_runtime.resources import ResourceIdentityError
from tests.test_browser_resource_identity import producer, observed, authority
from tests.test_runtime_resource_integration import approval_for, dispatch


@pytest.mark.parametrize("phase", ["timeout", "cancel", "spawn_cancel"])
async def test_client_is_killed_before_resend_deadline_without_retry(monkeypatch, phase):
    calls = []
    class Child:
        returncode = None
        killed = False
        async def wait(self):
            if self.killed:
                self.returncode = -9
                return -9
            await asyncio.Future()
        def kill(self): self.killed = True
    child = Child()
    started, release = asyncio.Event(), asyncio.Event()
    async def spawn(*args, **kwargs):
        calls.append(args); started.set()
        if phase == "spawn_cancel": await release.wait()
        return child
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", spawn)
    original = asyncio.wait_for
    async def bounded(awaitable, timeout):
        assert timeout == browser.CLIENT_DEADLINE_S and timeout < 30
        return await original(awaitable, .01 if phase == "timeout" else timeout)
    monkeypatch.setattr(browser.asyncio, "wait_for", bounded)
    task = asyncio.create_task(browser.run_client(["trusted-producer", "session", "info"], env={}, cwd="/"))
    await started.wait()
    if phase != "timeout": task.cancel()
    release.set()
    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)): await task
    assert child.killed and len(calls) == 1


async def test_sidecar_allowlist_has_no_enable_mutation_or_arbitrary_cdp():
    client = browser.CDPSidecar("ws://127.0.0.1:1234/devtools/browser/12345678-1234-1234-1234-123456789abc")
    for method in ("Page.enable", "Runtime.evaluate", "Page.navigate", "Target.closeTarget", "Browser.close"):
        with pytest.raises(ValueError): await client.call(method)


@pytest.mark.parametrize("envelope", [[], None, {"id": True, "result": {}}, {"id": "1", "result": {}}, {"id": 1, "error": {}, "result": {}}])
async def test_sidecar_rejects_malformed_identity_envelopes(monkeypatch, envelope):
    client = browser.CDPSidecar("ws://127.0.0.1:1234/devtools/browser/12345678-1234-1234-1234-123456789abc")
    async def send(*args): pass
    async def receive(): return envelope
    monkeypatch.setattr(client, "_send", send)
    monkeypatch.setattr(client, "_message", receive)
    with pytest.raises(ResourceIdentityError):
        await client.call("Target.getTargets")


@pytest.mark.parametrize("field", ["namespace", "runtimeError", "restoreKey"])
async def test_missing_nullable_lifecycle_fields_are_not_valid_observations(producer, field):
    record = await observed(producer)
    info = await record.command("session", "info")
    del (info["runtime"] if field == "restoreKey" else info)[field]
    with pytest.raises(ResourceIdentityError): browser.daemon_observation(record, info)


async def test_observation_cancellation_while_waiting_for_lock_invalidates_session(producer):
    record = await observed(producer)
    await record.lock.acquire()
    task = asyncio.create_task(browser.observe_registered(record))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    record.lock.release()
    assert record.session is None and record.pages == ()


@pytest.mark.parametrize("args", [{"action": "click", "page": "t1"}, {"action": "batch", "commands": [["click", "e1"]]}])
async def test_central_dispatch_cannot_bypass_page_denial(producer, args):
    await observed(producer)
    current = authority()
    producer.calls.clear(); producer.cdp_calls.clear()
    _, result = await dispatch(current, "private_browser", json.dumps(args))
    assert result["failure_kind"] == browser.PAGE_FAILURE and result["executed"] is False
    assert not producer.calls and not producer.cdp_calls


@pytest.mark.parametrize("replacement", ["browser", "daemon"])
async def test_exact_approval_revalidates_before_claim(producer, replacement):
    record = await observed(producer)
    current = authority()
    content = '{"action":"session_info"}'
    approval = approval_for(current, "private_browser", content)
    if replacement == "daemon":
        producer.pid += 1
    else:
        record._endpoint = "ws://127.0.0.1:1234/devtools/browser/87654321-1234-1234-1234-123456789abc"
    _, result = await dispatch(current, "private_browser", content, approval)
    assert result["exit_code"] == 1 and not approval._claimed
    assert record.session is None and record.pages == ()


async def test_metadata_revalidation_never_auto_launches_or_calls_get_cdp_url(producer):
    await observed(producer)
    current = authority()
    producer.calls.clear()
    _, result = await dispatch(current, "private_browser", '{"action":"session_info"}')
    assert result["exit_code"] == 0
    assert producer.calls and all(command == ("session", "info") for command in producer.calls)


@pytest.mark.parametrize("status", ["EOF", "connection reset", "EAGAIN", "read timeout"])
async def test_page_failures_never_enter_producer_internal_retry_path(producer, status, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Producer retry hazard reached: " + status)
    monkeypatch.setattr(browser, "run_client", forbidden)
    producer.calls.clear()
    from src.agent_tools.web_tools import PrivateBrowserTool
    result = await PrivateBrowserTool().execute('{"action":"wait","page":"t1","timeout_ms":120000}', {})
    assert result["executed"] is False and result["retryable"] is False
    assert producer.calls == []


async def test_page_scoped_child_still_cannot_execute_even_matching_observation(producer):
    await observed(producer)
    from dataclasses import replace
    parent = replace(authority(), browser_sessions=())
    child = parent.intersect(authority())
    _, result = await dispatch(child, "private_browser", '{"action":"click","page":"t1","ref":"e1"}')
    assert result["failure_kind"] == browser.PAGE_FAILURE and result["executed"] is False


async def test_observed_url_or_alias_change_is_not_resource_authority(producer):
    record = await observed(producer)
    from dataclasses import replace
    original = record.pages[0]
    metadata = replace(original, resolved_alias="t99", observed_url="https://different.example")
    assert original.authority_key() == metadata.authority_key()
    metadata.validate()


def test_raw_global_playwright_and_native_backend_are_not_substitutable():
    from src.agent_runtime.resources import ExternalResource
    from src.agent_runtime.authority import ExactOperation
    assert not browser.native_browser(ExactOperation.normalize("private_browser", '{"action":"session_info"}'),
        ExternalResource("mcp", "endpoint", "server", "tool", "epoch"))


@pytest.mark.parametrize("tool", ["browser_click", "browser_snapshot", "browser_evaluate", "browser_navigate", "browser_run_code"])
async def test_raw_mcp_browser_execution_cannot_evade_disabled_page_contract(tool):
    from src.agent_runtime.authority import RequestAuthority, OperationGrant
    name = "mcp__builtin_browser__" + tool
    current = RequestAuthority("request", "alice", "thread", "", (OperationGrant(name),))
    _, result = await dispatch(current, name, '{}')
    assert result["failure_kind"] == browser.PAGE_FAILURE and result["executed"] is False
