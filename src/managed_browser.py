"""Chat-owned Playwright sessions with exact page handles, never active-tab routing.

All Playwright objects live on one service event loop so Flask/request loops may
come and go. Cookies survive tool calls in the same chat, but never cross chats.
Only immutable ownership snapshots cross the service boundary.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from urllib.parse import urlsplit
from src.browser_control import Control

from src.agent_runtime.resources import (
    BrowserSessionResource, BrowserPageResource, ManagedBrowserObservation,
    ResourceIdentityError,
)

_SESSIONS = {}
_LOCK = threading.RLock()
_LOOP = None
_THREAD = None
_LAUNCH_LOCK = None
_IDLE_SECONDS = 900
_MAX_SESSIONS = 8
SESSION_ACTIONS = frozenset({"session_info", "tabs", "new_page", "close"})
ACTIONS = SESSION_ACTIONS | frozenset({"open", "navigate", "read", "snapshot", "find",
    "click", "fill", "select", "hover", "press", "scroll", "wait", "screenshot", "reload", "back", "forward",
    "select_page", "close_page"})


@dataclass
class Session:
    resource: BrowserSessionResource
    launch_request: str
    playwright: object
    browser: object
    context: object
    pages: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    next_page: int = 0
    next_ref: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    control: Control = field(default_factory=Control, repr=False)

    def add_page(self, page):
        with _LOCK:
            for resource, held in self.pages.values():
                if held is page:
                    return resource
            if len(self.pages) >= 16:
                asyncio.create_task(page.close())
                return None
            self.next_page += 1
            resource = BrowserPageResource(self.resource, secrets.token_hex(16).upper(), "",
                f"t{self.next_page}", page.url, scope="page")
            self.pages[resource.resolved_alias] = (resource, page)
        # Navigation invalidates element refs, but never substitutes a page.
        def navigated(frame):
            if frame == page.main_frame:
                self.invalidate_refs(resource)
                self.control.documents[resource.resolved_alias] = self.control.documents.get(resource.resolved_alias,0) + 1
        page.on("framenavigated", navigated)
        def closed():
            self.invalidate_refs(resource)
            with _LOCK:
                self.pages.pop(resource.resolved_alias, None)
                self.control.documents.pop(resource.resolved_alias, None)
        page.on("close", closed)
        return resource

    def invalidate_refs(self, resource):
        for old_resource, handle in tuple(self.refs.values()):
            if old_resource == resource:
                asyncio.create_task(handle.dispose())
        self.refs = {key: value for key, value in self.refs.items() if value[0] != resource}


def is_managed(resource):
    return isinstance(resource.observation, ManagedBrowserObservation)


def current(owner, thread):
    with _LOCK:
        return _SESSIONS.get((owner, thread))


def validate_session(resource):
    record = current(resource.owner, resource.thread_id)
    if record is None or record.resource != resource or not record.browser.is_connected():
        raise ResourceIdentityError("Browser session closed or replaced; open a new session")
    return record


def validate_page(resource):
    record = validate_session(resource.session)
    with _LOCK:
        pair = record.pages.get(resource.resolved_alias)
        if pair is None or pair[0] != resource or pair[1].is_closed():
            raise ResourceIdentityError("Browser page closed or replaced; inspect tabs before continuing")
        return pair[1]


def seal(authority):
    record = current(authority.owner, authority.session_id)
    if record is None or not any(g.tool == "private_browser" for g in authority.grants):
        return (), ()
    try:
        validate_session(record.resource)
    except ResourceIdentityError:
        return (), ()
    with _LOCK:
        return (record.resource,), tuple(r for r, p in record.pages.values() if not p.is_closed())


def _loop():
    global _LOOP, _THREAD
    with _LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            def run():
                asyncio.set_event_loop(_LOOP)
                _LOOP.create_task(_reap())
                _LOOP.run_forever()
            _THREAD = threading.Thread(target=run, name="odysseus-browser", daemon=True)
            _THREAD.start()
        return _LOOP


async def _submit(coro):
    future = asyncio.run_coroutine_threadsafe(coro, _loop())
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        future.cancel()
        raise


async def _dispose(record):
    record.control.mode = 'closed'
    record.control.revision += 1
    record.control.ready.set()
    with _LOCK:
        if _SESSIONS.get((record.resource.owner, record.resource.thread_id)) is record:
            del _SESSIONS[(record.resource.owner, record.resource.thread_id)]
    try:
        await record.browser.close()
    finally:
        await record.playwright.stop()


async def _reap():
    while True:
        await asyncio.sleep(60)
        for record in tuple(_SESSIONS.values()):
            if not record.lock.locked() and time.monotonic() - record.touched > _IDLE_SECONDS:
                async with record.lock:
                    try:
                        await _dispose(record)
                    except Exception:
                        pass


async def ensure_session(authority, args):
    """Launch only for an admitted direct open/new_page, never a guessed read."""
    existing = current(authority.owner, authority.session_id)
    if existing is not None and existing.browser.is_connected():
        return
    if args["action"] not in {"open", "new_page"}:
        raise ResourceIdentityError("No browser session. Use open with an HTTP(S) URL first.")
    if authority.inherited or not authority.session_id:
        raise ResourceIdentityError("A direct chat request is required to create a browser session")
    try:
        await _submit(asyncio.wait_for(_launch(authority), 30))
    except ResourceIdentityError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ResourceIdentityError("Browser could not start. Install requirements.txt and Chromium with python -m playwright install chromium, then retry.") from None


async def _launch(authority):
    global _LAUNCH_LOCK
    if _LAUNCH_LOCK is None:
        _LAUNCH_LOCK = asyncio.Lock()
    async with _LAUNCH_LOCK:
        await _launch_locked(authority)


async def _launch_locked(authority):
    key = (authority.owner, authority.session_id)
    if key in _SESSIONS:
        existing = _SESSIONS[key]
        if existing.browser.is_connected():
            return
        async with existing.lock:
            await _dispose(existing)
    if len(_SESSIONS) >= _MAX_SESSIONS:
        raise ResourceIdentityError("Browser session limit reached; close an unused chat browser")
    # Imports are lazy so a missing runtime yields install guidance, not startup failure.
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ResourceIdentityError("Browser runtime missing. Install requirements.txt and run python -m playwright install chromium.") from None
    playwright = browser = None
    try:
        playwright = await async_playwright().start()
        binary = shutil.which("chromium") or shutil.which("chromium-browser")
        # The standard Docker image uses container isolation: Docker's default
        # seccomp profile does not admit Chromium's nested namespace sandbox.
        container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
        options = {"headless": True, "timeout": 20000,
                   "chromium_sandbox": not container and (not hasattr(os, "geteuid") or os.geteuid() != 0)}
        if binary:
            options["executable_path"] = binary
        browser = await playwright.chromium.launch(**options)
        context = await browser.new_context(accept_downloads=False, service_workers="block",
            viewport={"width": 1280, "height": 900})
        await context.route("**/*", _route)
        await context.route_web_socket("**/*", _websocket_route)
        resource = BrowserSessionResource(authority.owner, authority.session_id,
            ManagedBrowserObservation(secrets.token_hex(32)))
        record = Session(resource, authority.request_id, playwright, browser, context)
        context.on("page", record.add_page)
        record.add_page(await context.new_page())
        with _LOCK:
            _SESSIONS[key] = record
    except BaseException:
        with _LOCK:
            _SESSIONS.pop(key, None)
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
        raise


async def _route(route):
    # Check redirected requests and subresources too, not just model-provided URLs.
    # Like web_fetch, DNS validation reduces obvious SSRF, not DNS-rebinding races.
    from src.url_security import validate_public_http_url
    try:
        await asyncio.to_thread(validate_public_http_url, route.request.url)
    except ValueError:
        await route.abort("blockedbyclient")
    else:
        await route.continue_()


async def _websocket_route(route):
    from src.url_security import validate_public_http_url
    parsed = urlsplit(route.url)
    try:
        if parsed.scheme not in {"ws", "wss"}:
            raise ValueError("Unsupported WebSocket scheme")
        url = parsed._replace(scheme="https" if parsed.scheme == "wss" else "http").geturl()
        await asyncio.to_thread(validate_public_http_url, url)
    except ValueError:
        await route.close(code=1008, reason="Public endpoints only")
    else:
        route.connect_to_server()


def resolve(authority, operation, args, approved=None, exact_admission=False):
    from src.browser_identity import BoundBrowserOperation
    record = current(authority.owner, authority.session_id)
    if approved is not None:
        bound = approved
        if bound.operation != operation or (bound.owner, bound.thread_id, bound.request_id) != (authority.owner, authority.session_id, authority.request_id):
            raise ResourceIdentityError("Approved browser action changed")
    else:
        if record is None:
            raise ResourceIdentityError("No owned browser session")
        page = None
        if args["action"] not in SESSION_ACTIONS:
            with _LOCK:
                pairs = [pair for alias, pair in record.pages.items()
                         if (not args.get("page") or alias == args["page"]) and not pair[1].is_closed()]
                if len(pairs) != 1:
                    raise ResourceIdentityError("Use an observed page alias from tabs")
                page = pairs[0][0]
        bound = BoundBrowserOperation(operation, authority.request_id, authority.owner,
            authority.session_id, record.resource, page, control_revision=record.control.revision)
    bound.validate()
    if not (approved is not None and exact_admission and not authority.inherited):
        admitted = bound.session in authority.browser_sessions
        launched_here = record is not None and record.launch_request == authority.request_id and not authority.inherited
        if not admitted and not launched_here:
            raise ResourceIdentityError("Browser session is outside admitted scope")
        if authority.inherited and bound.page is not None and bound.page not in authority.browser_pages:
            raise ResourceIdentityError("Browser page is outside inherited scope")
    return bound


def validate_args(args):
    action = args["action"]
    if action not in ACTIONS:
        raise ResourceIdentityError("Unsupported browser action; use snapshot and element refs to interact")
    if "script" in args or "path" in args:
        raise ResourceIdentityError("Browser scripts and host file paths are unsupported")
    if "url" in args and action not in {"open", "navigate", "new_page"}:
        raise ResourceIdentityError("Only navigation actions accept a URL; use page to select an existing tab")
    if action in {"open", "navigate"} or (action == "new_page" and args.get("url")):
        url = args.get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ResourceIdentityError("Browser navigation requires an HTTP(S) URL without credentials")
    if "timeout_ms" in args and (type(args["timeout_ms"]) is not int or not 0 <= args["timeout_ms"] <= 20000):
        raise ResourceIdentityError("Browser timeout must be between 0 and 20000 milliseconds")
    if "amount" in args and (type(args["amount"]) is not int or not 0 < args["amount"] <= 10000):
        raise ResourceIdentityError("Browser scroll amount must be between 1 and 10000")
    for key, minimum, maximum in (("offset", 0, 100000), ("limit", 1, 60)):
        if key in args and (type(args[key]) is not int or not minimum <= args[key] <= maximum):
            raise ResourceIdentityError(f"Browser {key} must be between {minimum} and {maximum}")


async def execute(bound, args):
    validate_args(args)
    try:
        return await _submit(asyncio.wait_for(_execute(bound, args), 45))
    except asyncio.TimeoutError:
        return {"error": "Browser action exceeded its deadline; the session was retired. Open a new session.",
                "exit_code": 1, "executed": True, "retryable": False, "failure_kind": "browser_action_timeout"}


_INTERACTIVE = "a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]"
# Leave room for the tool description and formatter metadata under the shared
# 10,000-character model/UI cap. Budget serialized JSON, including escaping.
_OBSERVATION_LIMIT = 8000


def _encode_observation(output):
    def encode():
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    raw = encode()
    if len(raw) > _OBSERVATION_LIMIT:
        output["observation_note"] = "For more detail use snapshot/read with selector, or snapshot with next_offset as offset."
        raw = encode()
    while len(raw) > _OBSERVATION_LIMIT:
        field = next((key for key in ("snapshot", "text") if len(output.get(key, "")) > 128), None)
        if field:
            output[field] = output[field][:max(128, len(output[field]) // 2)]
            output[field + "_truncated"] = True
        elif output.get("elements"):
            output["elements"].pop()
            output["next_offset"] = output.get("offset", 0) + len(output["elements"])
        elif len(output.get("tabs", [])) > 1:
            output["tabs"].pop()
            output["tabs_truncated"] = True
        else:
            # Only bounded metadata remains; long redirect URLs/titles must not
            # turn a successful observation into a cut-off JSON object either.
            for key in ("url", "title", "snapshot", "text", "selector"):
                if len(output.get(key, "")) > 128:
                    output[key] = output[key][:128]
                    output[key + "_truncated"] = True
        updated = encode()
        if len(updated) >= len(raw):
            raise ResourceIdentityError("Browser observation metadata exceeds its response budget")
        raw = updated
    return raw


async def _observation_root(page, selector=None):
    root = page.locator(selector or "body")
    if await root.count() == 0:
        # A form submission can commit navigation before its body is attached.
        # Wait on this held page, never substitute a different page/active tab.
        await root.wait_for(state="attached", timeout=10000)
    if await root.count() != 1:
        raise ResourceIdentityError("Observation selector must match exactly one element")
    return root


async def _snapshot(record, resource, page, *, selector=None, query=None, offset=0, limit=24):
    # Scope both accessibility text and controls to the requested root. Keep
    # actual handles for refs, never resolve an active tab or re-target a ref.
    root = await _observation_root(page, selector)
    snapshot_scope = selector or "body"
    main = page.locator('main,[role=main]')
    if selector is None and await main.count() == 1:
        # A default overview keeps page controls discoverable, but excludes
        # repetitive header/footer text from the main reading budget.
        sections = []
        dialogs = page.locator('dialog,[role=dialog]')
        for index in range(min(await dialogs.count(), 3)):
            dialog = dialogs.nth(index)
            if await dialog.is_visible():
                sections.append((await dialog.aria_snapshot(timeout=10000))[:2000])
        sections.append(await main.aria_snapshot(timeout=10000))
        snapshot = '\n'.join(sections)
        snapshot_scope = "main and visible dialogs"
    else:
        snapshot = await root.aria_snapshot(timeout=10000)
    handles = await root.locator(_INTERACTIVE).element_handles()
    root_handle = await root.element_handle()
    if root_handle is not None:
        if await root_handle.evaluate("(el, selector) => el.matches(selector)", _INTERACTIVE):
            handles.insert(0, root_handle)
        else:
            await root_handle.dispose()
    candidates = []
    retained = set()
    try:
        for handle in handles[:500]:
            if not await handle.is_visible():
                continue
            description = await handle.evaluate("""el => {
                const tag = el.tagName.toLowerCase(), type = el.getAttribute('type');
                const name = el.getAttribute('aria-label') ||
                    [...(el.labels || [])].map(label => label.innerText).join(' ') ||
                    el.getAttribute('placeholder') ||
                    (tag === 'input' && ['submit','button','reset'].includes(type) ? el.value : '') ||
                    el.innerText || el.getAttribute('name') || '';
                return {tag, name: name.replace(/\\s+/g, ' ').trim().slice(0,120),
                    ...(type ? {type:type.slice(0,32)} : {}),
                    ...(el.id ? {id:el.id.slice(0,120)} : {}),
                    ...(el.getAttribute('role') ? {role:el.getAttribute('role').slice(0,32)} : {}),
                    priority: el.closest('[role=dialog],dialog') ? 0 :
                        el.closest('main,[role=main]') ? 1 :
                        tag !== 'a' ? 2 : 3};
            }""")
            priority = description.pop('priority')
            if query:
                words = [w for w in query.casefold().split() if w not in {'button','textbox','input','the','click'}]
                label = (description.get('name', '') + ' ' + description.get('id', '')).casefold()
                matched = bool(words) and all(word in label for word in words)
                priority = (0 if matched else 1, priority)
            candidates.append((priority, description, handle))
        candidates.sort(key=lambda row: row[0])
        # A successful new observation retires old refs only after it is built.
        record.invalidate_refs(resource)
        elements = []
        for _, description, handle in candidates[offset:offset + limit]:
            record.next_ref += 1
            ref = f"e{record.next_ref}"
            record.refs[ref] = (resource, handle)
            retained.add(id(handle))
            elements.append({"ref": ref, **description})
        result = {"elements": elements, "offset": offset, "total_elements": len(candidates),
                  "selector": selector or "body", "snapshot_scope": snapshot_scope,
                  "snapshot": snapshot[:12000]}
        if offset + len(elements) < len(candidates):
            result['next_offset'] = offset + len(elements)
        if len(handles) > 500:
            result['element_scan_limited'] = True
        if len(snapshot) > 12000:
            result['snapshot_truncated'] = True
        return result
    finally:
        for handle in handles:
            if id(handle) not in retained:
                await handle.dispose()


async def _execute(bound, args):
    record = validate_session(bound.session)
    async with record.lock:
        bound.validate()
        if record.control.mode != 'agent':
            raise ResourceIdentityError('Browser is paused for user control')
        record.touched = time.monotonic()
        action = args["action"]
        page = validate_page(bound.page) if bound.page else None
        resource = bound.page
        timeout = max(1, args.get("timeout_ms", 10000))
        dispatched = False
        try:
            if action == "close":
                dispatched = True
                await _dispose(record)
                return {"output": "Browser session closed.", "exit_code": 0, "executed": True}
            if action == "new_page":
                if len(record.pages) >= 16:
                    raise ResourceIdentityError("Browser tab limit reached; close an unused tab")
                dispatched = True
                page = await record.context.new_page()
                resource = record.add_page(page)
            if action in {"open", "navigate", "new_page"} and args.get("url"):
                from src.url_security import validate_public_http_url
                await asyncio.to_thread(validate_public_http_url, args["url"])
                dispatched = True
                await page.goto(args["url"], wait_until="domcontentloaded", timeout=20000)
            elif action in {"click", "fill", "select", "hover", "press"}:
                ref = str(args.get("ref") or args.get("target") or "").lstrip("@")
                if ref:
                    item = record.refs.get(ref)
                    if item is None or item[0] != resource:
                        raise ResourceIdentityError("Element ref is stale or belongs to another page; take a snapshot")
                    target = item[1]
                elif args.get("selector"):
                    matches = await page.query_selector_all(args["selector"])
                    if len(matches) != 1:
                        for match in matches:
                            await match.dispose()
                        raise ResourceIdentityError("Selector must match exactly one element")
                    target = matches[0]
                elif action == "press":
                    target = await page.query_selector("body")
                else:
                    raise ResourceIdentityError("Use an element ref from snapshot or a unique selector")
                dispatched = True
                if action == "click":
                    await target.click(timeout=timeout)
                elif action == "fill":
                    await target.fill(str(args.get("text", args.get("value", ""))), timeout=timeout)
                elif action == "select":
                    await target.select_option(value=str(args.get("value", "")), timeout=timeout)
                elif action == "hover":
                    await target.hover(timeout=timeout)
                else:
                    await target.press(str(args.get("key") or "Enter"), timeout=timeout)
            elif action in {"reload", "back", "forward"}:
                dispatched = True
                method = {"reload": page.reload, "back": page.go_back, "forward": page.go_forward}[action]
                await method(wait_until="domcontentloaded", timeout=20000)
            elif action == "scroll":
                amount = args.get("amount", 600)
                direction = args.get("direction", "down")
                if direction not in {"down", "up", "left", "right"}:
                    raise ResourceIdentityError("Unsupported scroll direction")
                dispatched = True
                await page.mouse.wheel(amount * (1 if direction == "right" else -1) if direction in {"left", "right"} else 0,
                    amount * (1 if direction == "down" else -1) if direction in {"up", "down"} else 0)
            elif action == "wait":
                dispatched = True
                if args.get("selector"):
                    await page.locator(args["selector"]).wait_for(timeout=timeout)
                else:
                    await page.wait_for_load_state("domcontentloaded", timeout=timeout)
            elif action == "close_page":
                dispatched = True
                await page.close()
                record.invalidate_refs(resource)
                if not record.pages:
                    await _dispose(record)
                return {"output": "Page closed.", "exit_code": 0, "executed": True}
            output = {"session_incarnation": record.resource.observation.session_incarnation}
            if page is not None:
                output.update(page=resource.resolved_alias, url=page.url, title=(await page.title())[:240])
                if action == "read" or action == "find":
                    root = await _observation_root(page, args.get("selector"))
                    text = await root.inner_text(timeout=timeout)
                    if action == "find":
                        query = str(args.get("find") or args.get("text") or "")
                        index = text.casefold().find(query.casefold())
                        text = text[max(0, index - 500):index + 4000] if index >= 0 else "No matching text."
                    output["text"] = text[:12000]
                    if len(text) > 12000:
                        output["text_truncated"] = True
                    if action == "find":
                        output.update(await _snapshot(record, resource, page, selector=args.get("selector"),
                            query=query, offset=args.get("offset", 0), limit=args.get("limit", 24)))
                elif action != "screenshot":
                    output.update(await _snapshot(record, resource, page,
                        selector=args.get("selector") if action == "snapshot" else None,
                        offset=args.get("offset", 0), limit=args.get("limit", 24)))
            with _LOCK:
                output["tabs"] = [{"page": r.resolved_alias, "url": p.url[:300]} for r, p in record.pages.values() if not p.is_closed()]
            result = {"output": _encode_observation(output), "exit_code": 0, "executed": True,
                      "browser_page_operations_supported": True}
            if action == "screenshot":
                raw = await page.screenshot(type="png", timeout=timeout)
                result["images"] = [{"data": base64.b64encode(raw).decode(), "mimeType": "image/png"}]
            if page is not None and (action not in {'read','find','snapshot'} or record.control.frame is None):
                from src.browser_control import capture
                try:
                    result['browser_preview'] = await capture(record, resource, page)
                except Exception:
                    result['browser_preview'] = {'session_id':bound.thread_id, 'status':'unavailable'}
            return result
        except asyncio.CancelledError:
            # A cancelled action has an uncertain outcome. Retire the session,
            # never let a late click run against a resumed/replacement browser.
            await _dispose(record)
            raise
        except Exception as error:
            message = str(error) if isinstance(error, (ResourceIdentityError, ValueError)) else "Browser action failed or timed out. Inspect the page before retrying."
            return {"error": message, "exit_code": 1, "executed": dispatched,
                    "failure_kind": "browser_action_failed", "retryable": False}
        finally:
            record.touched = time.monotonic()


async def shutdown():
    if _LOOP is None:
        return
    async def close_all():
        for record in tuple(_SESSIONS.values()):
            if record is not None:
                async with record.lock:
                    try:
                        await _dispose(record)
                    except Exception:
                        pass
    await _submit(close_all())
