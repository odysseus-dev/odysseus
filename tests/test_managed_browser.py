"""Managed browser ownership and real Chromium form interaction regressions."""
import base64
from dataclasses import replace
import json

import pytest

from src import browser_identity, managed_browser as browser
from src.agent_runtime.authority import (
    ExactOperation, OperationGrant, RequestAuthority, create_request_authority,
)
from src.agent_runtime.resources import BrowserSessionResource, ManagedBrowserObservation, ResourceIdentityError
from src.tool_capabilities import ToolEffect, ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock


def authority(owner="alice", thread="browser-test"):
    return RequestAuthority("browser-request", owner, thread, "", (OperationGrant("private_browser"),))


def test_managed_observation_roundtrip_and_forged_snapshot_rejected():
    session = BrowserSessionResource("alice", "thread", ManagedBrowserObservation("a" * 64))
    assert BrowserSessionResource.from_dict(session.to_dict()) == session
    with pytest.raises(ResourceIdentityError):
        session.validate()
    with pytest.raises(ValueError):
        ManagedBrowserObservation("not-an-incarnation")


@pytest.mark.parametrize("args", [
    {"action": "open", "url": "file:///etc/passwd"},
    {"action": "open", "url": "javascript:alert(1)"},
    {"action": "open", "url": "https://user:password@example.com"},
    {"action": "evaluate", "script": "1+1"},
    {"action": "screenshot", "path": "/etc/output.png"},
    {"action": "wait", "timeout_ms": -1},
    {"action": "scroll", "amount": 10001},
])
def test_unsafe_or_unbounded_browser_input_rejected(args):
    with pytest.raises((ValueError, TypeError)):
        browser.validate_args(args)


def test_site_submissions_have_external_effects():
    for action in ("click", "press"):
        cap = capabilities_for_action("private_browser", json.dumps({"action": action}))
        assert ToolEffect.EXTERNAL_SIDE_EFFECT in cap.effects
    for action in ("snapshot", "read", "tabs"):
        assert ToolEffect.EXTERNAL_SIDE_EFFECT not in capabilities_for_action("private_browser", json.dumps({"action": action})).effects


async def test_inherited_authority_cannot_launch_a_browser():
    with pytest.raises(ResourceIdentityError, match="direct chat"):
        await browser.ensure_session(replace(authority(), inherited=True), {"action": "open"})


@pytest.fixture
async def live_browser(monkeypatch):
    pytest.importorskip("playwright.async_api")
    # Fulfill a public test origin in Chromium; no external website or local
    # server is needed. Exercise the actual renderer, form events and cookies.
    html = '''<!doctype html><title>Catalog</title>
    <form><label>Search catalog<input aria-label="Search catalog" name="q"></label>
    <button>Search</button></form><main id="results">Ready</main>
    <script>document.querySelector('form').onsubmit = e => {
      e.preventDefault(); document.cookie = 'visited=yes; path=/';
      document.querySelector('#results').textContent = 'Results for ' + document.querySelector('input').value;
    };</script>'''
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=html)
    monkeypatch.setattr(browser, "_route", route)
    def validate(url, **kwargs):
        if not url.startswith("https://catalog.example/"):
            raise ValueError("Test URL outside fixture")
        return url
    monkeypatch.setattr("src.url_security.validate_public_http_url", validate)
    yield
    await browser.shutdown()


async def call(auth, action, **args):
    from src.tool_execution import execute_tool_block
    # Use the common dispatcher, not the browser adapter directly. Fresh
    # contexts simulate a task-approved continuation for form interaction.
    return (await execute_tool_block(ToolBlock("private_browser", json.dumps({"action": action, **args})),
        owner=auth.owner, session_id=auth.session_id, workspace=auth.workspace,
        request_authority=auth, security_context=ToolRunSecurityContext()))[1]


def output(result):
    assert result.get("exit_code") == 0, result
    return json.loads(result["output"])


@pytest.mark.slow
async def test_live_browser_search_form_cookies_and_exact_page_lifetime(live_browser):
    auth = create_request_authority("Browse https://catalog.example/ and find a wooden desk",
                                    owner="alice", session_id="browser-test")
    opened = output(await call(auth, "open", url="https://catalog.example/"))
    assert opened["page"] == "t1"
    search_ref = next(e["ref"] for e in opened["elements"] if e["tag"] == "input")
    filled = output(await call(auth, "fill", page="t1", ref=search_ref, text="wooden desk"))
    button_ref = next(e["ref"] for e in filled["elements"] if e["tag"] == "button")
    clicked = output(await call(auth, "click", page="t1", ref=button_ref))
    assert "Results for wooden desk" in clicked["snapshot"]
    read = output(await call(auth, "read", page="t1"))
    assert "Results for wooden desk" in read["text"]
    screenshot = await call(auth, "screenshot", page="t1")
    assert base64.b64decode(screenshot["images"][0]["data"]).startswith(b"\x89PNG")
    record = browser.current(auth.owner, auth.session_id)
    async def cookies():
        return await record.context.cookies()
    assert any(c["name"] == "visited" for c in await browser._submit(cookies()))

    # A subsequent request seals the existing session and keeps its cookies.
    next_auth = replace(authority(), request_id="next-request", browser_sessions=None, browser_pages=None)
    assert next_auth.browser_sessions == (record.resource,)
    assert RequestAuthority.from_dict(next_auth.to_dict()) == next_auth
    assert "Results for wooden desk" in output(await call(next_auth, "read", page="t1"))["text"]

    operation = ExactOperation.normalize("private_browser", '{"action":"click","page":"t1","selector":"button"}')
    bound = browser_identity.resolve_browser_operation(next_auth, operation)
    output(await call(next_auth, "new_page", url="https://catalog.example/"))
    assert (await call(next_auth, "close_page", page="t1"))["exit_code"] == 0
    # Destroying the captured page must not route the action to t2.
    with pytest.raises(ResourceIdentityError, match="closed or replaced"):
        bound.validate()
    stale = await call(next_auth, "click", page="t1", ref=button_ref)
    assert stale["exit_code"] == 1
    remaining = output(await call(next_auth, "tabs"))
    assert [p["page"] for p in remaining["tabs"]] == ["t2"]


@pytest.mark.slow
async def test_live_browser_owner_isolation_and_no_implicit_read_launch(live_browser):
    alice = authority()
    output(await call(alice, "open", url="https://catalog.example/"))
    bob = authority(owner="bob")
    failed = await call(bob, "read", page="t1")
    assert failed["exit_code"] == 1 and browser.current("bob", bob.session_id) is None
    output(await call(bob, "open", url="https://catalog.example/"))
    assert browser.current("alice", alice.session_id).resource != browser.current("bob", bob.session_id).resource
    inherited = replace(alice, inherited=True, browser_sessions=(), browser_pages=())
    assert (await call(inherited, "read", page="t1"))["exit_code"] == 1
    assert (await call(alice, "close"))["exit_code"] == 0
    assert browser.current("alice", alice.session_id) is None
    assert browser.current("bob", bob.session_id) is not None


@pytest.mark.slow
async def test_live_browser_approval_binds_exact_page_and_records_effects(live_browser, tmp_path):
    from src.agent_runtime.effect_log import EffectLog
    from src.agent_runtime.journal import ActionJournal, bind_journal
    from src.tool_approvals import ToolApprovalStore
    from src.tool_execution import execute_tool_block

    auth = authority()
    opened = output(await call(auth, "open", url="https://catalog.example/"))
    ref = next(e["ref"] for e in opened["elements"] if e["tag"] == "button")
    content = json.dumps({"action": "click", "page": "t1", "ref": ref})
    security = ToolRunSecurityContext(external_untrusted_context_seen=True)
    common = dict(owner=auth.owner, session_id=auth.session_id, workspace=auth.workspace,
                  request_authority=auth, security_context=security)
    journal = ActionJournal()
    journal.effects = EffectLog(journal.run_id, directory=tmp_path / "effects")
    with bind_journal(journal):
        _, blocked = await execute_tool_block(ToolBlock("private_browser", content), **common)
    assert blocked["exit_code"] == 1
    assert journal.effects.history().claims == ()

    store = ToolApprovalStore()
    pending = store.create(owner=auth.owner, session_id=auth.session_id, origin_run_id=security.run_id,
        tool_name="private_browser", content=content, workspace=auth.workspace,
        capabilities=capabilities_for_action("private_browser", content),
        external_untrusted_context_seen=True, request_authority=auth)
    assert pending.browser_operation.page.resolved_alias == "t1"
    approval = store.consume(pending.approval_id, owner=auth.owner, session_id=auth.session_id, decision="approve")
    with bind_journal(journal):
        _, executed = await execute_tool_block(ToolBlock("private_browser", content), exact_approval=approval, **common)
    output(executed)
    claim, = journal.effects.history().claims
    assert claim.external and claim.unknown_scope
    assert claim.dependencies[0].incarnation == browser.current(auth.owner, auth.session_id).resource.observation.session_incarnation
    # Execution alone must not certify the external form submission.
    assert journal.effects.history().observations == ()

    pending = store.create(owner=auth.owner, session_id=auth.session_id, origin_run_id=security.run_id,
        tool_name="private_browser", content=content, workspace=auth.workspace,
        capabilities=capabilities_for_action("private_browser", content),
        external_untrusted_context_seen=True, request_authority=auth)
    approval = store.consume(pending.approval_id, owner=auth.owner, session_id=auth.session_id, decision="approve")
    assert (await call(auth, "close"))["exit_code"] == 0
    output(await call(auth, "open", url="https://catalog.example/"))
    _, stale = await execute_tool_block(ToolBlock("private_browser", content), exact_approval=approval, **common)
    assert stale["exit_code"] == 1 and not approval._claimed


@pytest.mark.slow
async def test_live_compact_chat_surfaces_a_sealed_browser_approval(live_browser, monkeypatch):
    from src import clean_agent_preview as preview
    from src.tool_approvals import ToolApprovalStore
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_full_inventory_contract

    auth = authority()
    opened = output(await call(auth, "open", url="https://catalog.example/"))
    ref = next(e["ref"] for e in opened["elements"] if e["tag"] == "input")
    args = {"action": "fill", "page": "t1", "ref": ref, "text": "wooden desk"}
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            delta = {"tool_calls": [{"index": 0, "id": "browser-fill", "type": "function",
                     "function": {"name": "private_browser", "arguments": json.dumps(args)}}]}
            yield "data: " + json.dumps({"choices": [{"delta": delta}]})
            yield "data: [DONE]"
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(preview.httpx, "AsyncClient", Client)
    store = ToolApprovalStore()
    monkeypatch.setattr("src.tool_approvals.tool_approval_store", store)
    policy = ToolPolicy()
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "private_browser"]
    contract = replace(resolve_full_inventory_contract(schemas=schemas, policy=policy),
                       required=frozenset({"private_browser"}))
    chunks = [chunk async for chunk in preview.stream_preview(
        endpoint_url="http://fixture", model="Ajax", headers={}, turn_contract=contract,
        messages=[{"role": "user", "content": "Browse the catalog and find a wooden desk"}],
        session_id=auth.session_id, owner=auth.owner, disabled_tools=set(), tool_policy=policy,
        external_untrusted_context_seen=True, request_authority=auth, max_rounds=2)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith("data: ") and "[DONE]" not in c]
    card = next(e["data"] for e in events if e.get("type") == "ask_user")
    assert card["action"]["tool"] == "private_browser"
    assert json.loads(card["action"]["content"]) == args
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.slow
async def test_first_browser_open_is_bound_before_approval_not_at_consumption(live_browser):
    from src.tool_approvals import ToolApprovalStore
    from src.tool_execution import execute_tool_block

    auth = authority()
    content = json.dumps({"action": "open", "url": "https://catalog.example/"})
    store = ToolApprovalStore()
    kwargs = dict(owner=auth.owner, session_id=auth.session_id, workspace=auth.workspace,
        origin_run_id="first-open", tool_name="private_browser", content=content,
        external_untrusted_context_seen=True, request_authority=auth,
        capabilities=capabilities_for_action("private_browser", content))
    assert browser.current(auth.owner, auth.session_id) is None
    # This is the former bug: standard-loop approval preceded the dispatcher's
    # lazy launch, and synchronous create silently saved an unresolved target.
    assert store.create(**kwargs).browser_operation is None
    pending = await store.create_prepared(**kwargs)
    bound = pending.browser_operation
    assert bound is not None and bound.page is not None
    record = browser.current(auth.owner, auth.session_id)
    assert bound.session == record.resource
    assert browser.validate_page(bound.page).url == "about:blank"
    # Navigation has not occurred while preparing/showing the card.
    approval = store.consume(pending.approval_id, owner=auth.owner, session_id=auth.session_id,
                             decision="approve_task")
    _, result = await execute_tool_block(ToolBlock("private_browser", content), owner=auth.owner,
        session_id=auth.session_id, workspace=auth.workspace, request_authority=auth,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
        exact_approval=approval)
    assert output(result)["url"] == "https://catalog.example/"
    assert approval._claimed


async def test_browser_start_failure_never_publishes_an_unusable_approval(monkeypatch):
    from src.tool_approvals import ToolApprovalStore
    async def unavailable(*args):
        raise ResourceIdentityError("Browser runtime is unavailable")
    monkeypatch.setattr(browser, "ensure_session", unavailable)
    auth = authority()
    content = '{"action":"open","url":"https://catalog.example/"}'
    store = ToolApprovalStore()
    with pytest.raises(ResourceIdentityError, match="runtime is unavailable"):
        await store.create_prepared(owner=auth.owner, session_id=auth.session_id, workspace=auth.workspace,
            origin_run_id="first-open", tool_name="private_browser", content=content,
            external_untrusted_context_seen=True, request_authority=auth,
            capabilities=capabilities_for_action("private_browser", content))
    assert store._pending == {}


@pytest.mark.slow
async def test_standard_chat_first_browser_card_seals_the_cold_page(live_browser, monkeypatch):
    from src import agent_loop as loop
    from src.tool_approvals import ToolApprovalStore
    monkeypatch.setattr(loop, 'get_setting', lambda key, default=None: default)
    monkeypatch.setattr(loop, 'get_mcp_manager', lambda: None)
    monkeypatch.setattr(loop, 'estimate_tokens', lambda *args, **kwargs: 10)
    monkeypatch.setattr(loop, '_configured_model_tool_surface_raw', lambda *args, **kwargs: 'full')
    store = ToolApprovalStore()
    monkeypatch.setattr(loop, 'tool_approval_store', store)
    async def model(*args, **kwargs):
        yield 'data: ' + json.dumps({'delta': '```private_browser\n{"action":"open","url":"https://catalog.example/"}\n```'}) + '\n\n'
        yield 'data: [DONE]\n\n'
    monkeypatch.setattr(loop, 'stream_llm_with_fallback', model)
    auth = authority()
    chunks = [chunk async for chunk in loop.stream_agent_loop(
        'http://fixture', 'fixture', [{'role':'user', 'content':'Browse the catalog'}],
        headers={}, owner=auth.owner, session_id=auth.session_id,
        request_authority=auth, external_untrusted_context_seen=True,
        forced_tools={'private_browser'}, relevant_tools={'private_browser'},
        max_rounds=1, context_length=8192, force_textual_tool_transport=True)]
    events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ') and '[DONE]' not in c]
    card = next(e['data'] for e in events if e.get('type') == 'ask_user')
    pending = store.peek(card['approval_id'])
    assert pending.browser_operation is not None
    assert browser.validate_page(pending.browser_operation.page).url == 'about:blank'


@pytest.mark.slow
async def test_noisy_page_keeps_search_refs_and_honors_focused_observations(live_browser, monkeypatch):
    from src.tool_execution import format_tool_result
    header = ''.join(f'<button>Navigation {i}</button>' for i in range(80))
    html = f'''<!doctype html><meta charset="utf-8"><title>Large county-style form</title>
      <header>{header}<p>{'繁體 Unicode navigation ' * 1000}</p></header>
      <main><form><label for="tax-key">Enter Tax Key</label>
      <input id="tax-key" type="text"><input id="tax-search" type="submit" value="Search"></form>
      <div id="results">Ready for search</div></main><footer>Not part of main</footer>
      <script>document.querySelector('form').onsubmit = e => {{
        e.preventDefault(); document.querySelector('#results').textContent =
        'Results for ' + document.querySelector('#tax-key').value;
      }};</script>'''
    async def route(request):
        await request.fulfill(status=200, content_type='text/html', body=html)
    monkeypatch.setattr(browser, '_route', route)
    auth = authority()
    result = await call(auth, 'open', url='https://catalog.example/')
    opened = output(result)
    assert len(result['output']) <= browser._OBSERVATION_LIMIT
    assert '... (truncated' not in format_tool_result('registry: private_browser open', result)
    assert any(e['name'] == 'Search' for e in opened['elements'])
    assert opened['next_offset'] > 0
    focused = output(await call(auth, 'snapshot', page='t1', selector='main'))
    assert focused['selector'] == 'main'
    assert {e['id'] for e in focused['elements']} == {'tax-key', 'tax-search'}
    assert 'Navigation' not in focused['snapshot']
    assert 'Not part of main' not in focused['snapshot']
    textbox = next(e['ref'] for e in focused['elements'] if e.get('id') == 'tax-key')
    filled = output(await call(auth, 'fill', page='t1', ref=textbox, text='BRC1115095'))
    assert any(e['name'] == 'Search' for e in filled['elements'])
    found = output(await call(auth, 'find', page='t1', selector='main', find='Search button'))
    assert found['elements'][0]['name'] == 'Search'
    assert found['elements'][0]['type'] == 'submit'
    clicked = output(await call(auth, 'click', page='t1', ref=found['elements'][0]['ref']))
    assert 'Results for BRC1115095' in clicked['snapshot']
    read = output(await call(auth, 'read', page='t1', selector='#results'))
    assert read['text'] == 'Results for BRC1115095'
    bad = await call(auth, 'snapshot', page='t1', selector='button')
    assert bad['exit_code'] == 1 and 'exactly one' in bad['error']
    root = output(await call(auth, 'snapshot', page='t1', selector='#tax-search'))
    assert len(root['elements']) == 1 and root['elements'][0]['name'] == 'Search'
    first = output(await call(auth, 'snapshot', page='t1', selector='header', limit=2))
    second = output(await call(auth, 'snapshot', page='t1', selector='header', offset=first['next_offset'], limit=2))
    assert [e['name'] for e in first['elements']] == ['Navigation 0', 'Navigation 1']
    assert [e['name'] for e in second['elements']] == ['Navigation 2', 'Navigation 3']


def test_observation_budget_preserves_valid_json_and_element_pagination():
    source = {'elements': [{'ref':f'e{i}', 'name':'x' * 120, 'id':'y' * 120} for i in range(60)],
              'snapshot':'\u0001' * 24000, 'offset':3, 'total_elements':100,
              'tabs':[{'page':f't{i}', 'url':'https://example.test/' + 'x' * 300} for i in range(16)]}
    raw = browser._encode_observation(source)
    assert len(raw) <= browser._OBSERVATION_LIMIT
    parsed = json.loads(raw)
    assert parsed['elements'][0]['ref'] == 'e0'
    assert parsed['next_offset'] == 3 + len(parsed['elements'])


@pytest.mark.slow
async def test_post_click_snapshot_waits_for_replacement_body(live_browser, monkeypatch):
    html = '''<!doctype html><title>Transition</title><body><main>
      <button onclick="document.body.remove(); setTimeout(() => {
        const body = document.createElement('body');
        body.innerHTML = '<main>Search results loaded</main>';
        document.documentElement.appendChild(body);
      }, 150)">Search</button></main></body>'''
    async def route(request):
        await request.fulfill(status=200, content_type='text/html', body=html)
    monkeypatch.setattr(browser, '_route', route)
    auth = authority()
    opened = output(await call(auth, 'open', url='https://catalog.example/'))
    ref = next(e['ref'] for e in opened['elements'] if e['name'] == 'Search')
    clicked = output(await call(auth, 'click', page='t1', ref=ref))
    assert 'Search results loaded' in clicked['snapshot']
