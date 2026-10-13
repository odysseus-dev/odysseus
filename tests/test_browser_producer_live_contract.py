"""Release-only probes, isolated owned sessions; no model page authorization.

Run in the actual release image with ODYSSEUS_BROWSER_LIVE_CONTRACT=1. Without
that explicit gate these are reported as skips, not producer-contract passes.
The pin test asserts the known 0.35.0 defect, never enables page operations.
"""
import json
import os
import tempfile
import urllib.request
from urllib.parse import urlsplit

import pytest

from src import browser_identity as browser
from src.agent_tools.web_tools import PrivateBrowserTool
from src import browser_lifecycle

pytestmark = pytest.mark.skipif(os.environ.get("ODYSSEUS_BROWSER_LIVE_CONTRACT") != "1",
    reason="requires explicit live contract gate in the allowlisted 0.35.0 release Docker image")


@pytest.fixture
async def live(tmp_path, monkeypatch):
    from pathlib import Path
    # Unix-domain sockets have a strict path-length limit. Match the release's
    # short owned runtime instead of pytest's long per-test directory name.
    directory = tempfile.TemporaryDirectory(prefix="w3-live-")
    monkeypatch.setattr(browser, "STATE_ROOT", Path(directory.name))
    monkeypatch.setattr(browser, "_REGISTRY", {})
    record = await browser.register_producer("live-contract", "thread")
    # Test setup only. Exercise the source-audited first-pin local launch case.
    await record.command("get", "cdp-url", "--pin-tab")
    try:
        yield record
    finally:
        try:
            await record.command("close")
        finally:
            browser_lifecycle.force_cleanup(record.cwd / "runtime", record.key)
            directory.cleanup()


async def test_live_exact_schema_target_loader_and_observation_stability(live):
    first = await browser.observe_registered(live, "t1")
    second = await browser.observe_registered(live, "t1")
    assert first.authority_key() == second.authority_key()
    assert first.loader_id and first.target_id
    assert live.pin_armed_for is None
    assert "devtools/browser" not in json.dumps(first.to_dict())


async def test_live_document_navigation_reload_hash_and_identical_tabs(live):
    await live.command("open", "data:text/html,<title>fixture</title><p>content</p>", "--pin-tab")
    first = await browser.observe_registered(live, "t1")
    await live.command("eval", "history.replaceState(null,'','#same')", "--pin-tab")
    same = await browser.observe_registered(live, "t1")
    assert same.loader_id == first.loader_id
    await live.command("reload", "--pin-tab")
    reloaded = await browser.observe_registered(live, "t1")
    assert reloaded.loader_id != first.loader_id
    await live.command("open", "data:text/html,<title>replacement</title>", "--pin-tab")
    navigated = await browser.observe_registered(live, "t1")
    assert navigated.loader_id != reloaded.loader_id
    await live.command("tab", "new", "data:text/html,<title>replacement</title>", "--pin-tab")
    await browser.observe_registered(live)
    assert len({p.target_id for p in live.pages}) == 2
    assert len({p.loader_id for p in live.pages}) == 2


async def test_live_local_launch_rearm_drops_flags_and_retargets_destroyed_page(live):
    await live.command("tab", "new", "about:blank", "--pin-tab")
    await browser.observe_registered(live)
    # Digit-leading target avoids the distinct producer label-parser hazard.
    captured = next((p for p in live.pages if p.target_id[0].isdigit()), None)
    for _ in range(8):
        if captured is not None:
            break
        await live.command("tab", "new", "about:blank", "--pin-tab")
        await browser.observe_registered(live)
        captured = next((p for p in live.pages if p.target_id[0].isdigit()), None)
    assert captured is not None, "could not obtain a digit-leading target for the pin probe"
    switched = await live.command("tab", captured.target_id, "--pin-tab")
    assert switched["targetId"] == captured.target_id
    await live.command("session", "info", "--no-pin-tab")
    await live.command("session", "info", "--pin-tab")
    endpoint = urlsplit(live._endpoint)
    # External destruction is TEST FIXTURE ONLY, outside the identity sidecar.
    with urllib.request.urlopen(f"http://127.0.0.1:{endpoint.port}/json/close/{captured.target_id}", timeout=3) as response:
        assert response.status == 200
    result = await live.command("snapshot", "--pin-tab")
    active = [t for t in browser.tabs_schema(await live.command("tab", "list")) if t["active"]]
    assert active and active[0]["targetId"] != captured.target_id
    assert "tab_gone" not in json.dumps(result)
    assert result["lifecycle"]["relaunchedBrowser"] is False
    assert live.pin_armed_for is None
    # Actual Odysseus refuses before any page command, even with this observation.
    denied = await PrivateBrowserTool().execute('{"action":"snapshot","page":"t1"}',
        {"owner": "live-contract", "session_id": "thread"})
    assert denied["failure_kind"] == browser.PAGE_FAILURE and denied["executed"] is False


async def test_live_af_target_switch_is_exact_but_never_grants_page_execution(live):
    await browser.observe_registered(live)
    captured = live.pages[0]
    switched = await live.command("tab", captured.target_id, "--pin-tab")
    assert switched["targetId"] == captured.target_id
    denied = await PrivateBrowserTool().execute('{"action":"click","page":"t1","ref":"e1"}', {})
    assert denied["executed"] is False
