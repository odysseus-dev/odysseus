"""Exclusive human control of chat-owned browsers; no model-authored input API."""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import math
import secrets
import time
from functools import wraps
from src.agent_runtime.resources import ResourceIdentityError

_PLANNED = ContextVar('browser_planning_revision', default=None)
_IN_ACTION = ContextVar('browser_agent_action', default=None)
LEASE_SECONDS = 60

@dataclass
class Control:
    mode: str = 'agent'
    revision: int = 0
    lease: str = field(default='', repr=False)
    client: str = field(default='', repr=False)
    expires: float = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    idle: asyncio.Event = field(default_factory=asyncio.Event)
    active: int = 0
    frame: dict | None = field(default=None, repr=False)
    documents: dict = field(default_factory=dict, repr=False)
    def __post_init__(self):
        self.ready.set()
        self.idle.set()


def _record(owner, session_id):
    from src import managed_browser as browser
    record = browser.current(owner, session_id)
    if record is None: raise ResourceIdentityError('Browser session closed. Ask Odysseus to open a page first.')
    browser.validate_session(record.resource)
    return record


def _expire(record):
    control = record.control
    if control.mode == 'human' and time.monotonic() > control.expires:
        control.mode = 'paused'
        control.lease = ''
        control.client = ''
        control.revision += 1
    return control


def _page(record, alias):
    from src import managed_browser as browser
    pair = record.pages.get(alias)
    if pair is None: raise ResourceIdentityError('Browser tab closed or replaced')
    return pair[0], browser.validate_page(pair[0])


async def capture(record, resource, page):
    """UI-only frame. Never place its bytes in model history or tool replay."""
    from PIL import Image
    import io
    document_revision = record.control.documents.get(resource.resolved_alias, 0)
    raw = await page.screenshot(type='jpeg', quality=65, timeout=5000)
    with Image.open(io.BytesIO(raw)) as img: width, height = img.size
    record.control.frame = dict(id=secrets.token_urlsafe(24), resource=resource,
        revision=record.control.revision, document_revision=document_revision,
        captured_at=time.monotonic(), width=width, height=height, data=raw)
    return frame_metadata(record)


def frame_metadata(record):
    frame = record.control.frame
    return dict(session_id=record.resource.thread_id,
        incarnation=record.resource.observation.session_incarnation,
        page=frame['resource'].resolved_alias if frame else '',
        frame_id=frame['id'] if frame else '', width=frame['width'] if frame else 0,
        height=frame['height'] if frame else 0, revision=record.control.revision,
        status=('ready' if frame['document_revision'] == record.control.documents.get(frame['resource'].resolved_alias,0)
                and frame['revision'] == record.control.revision else 'stale') if frame else 'unavailable')


def _state(record, client='', lease=''):
    c = _expire(record)
    return dict(**frame_metadata(record), mode=c.mode, busy=c.active > 0,
        controlling=bool(c.mode == 'human' and secrets.compare_digest(c.lease, lease) and c.client == client),
        tabs=[{'page':r.resolved_alias, 'url':p.url, 'title':r.observed_url} for r,p in record.pages.values() if not p.is_closed()])


async def call(owner, session_id, action, args=None):
    from src import managed_browser as browser
    args = args or {}
    return await browser._submit(_call(owner, session_id, action, args))


async def _call(owner, session_id, action, args):
    record = _record(owner, session_id)
    c = _expire(record)
    client, lease = str(args.get('client', '')), str(args.get('lease', ''))
    if action == 'state':
        if c.mode == 'human' and c.client == client and secrets.compare_digest(c.lease, lease):
            c.expires = time.monotonic() + LEASE_SECONDS
            record.touched = time.monotonic()
        return _state(record, client, lease)
    if action == 'frame':
        frame = c.frame
        if (frame is None or frame['id'] != args.get('frame_id')
                or record.resource.observation.session_incarnation != args.get('incarnation')):
            raise ResourceIdentityError('Preview expired; refresh the browser view')
        frame['resource'].validate()
        return frame['data']
    if action == 'take':
        if not client or len(client) > 128: raise ValueError('A viewer identity is required')
        if c.mode in {'requested','human'}:
            if not (c.client == client and c.lease and secrets.compare_digest(c.lease, lease)):
                raise ResourceIdentityError('Another viewer has browser control')
            return dict(**_state(record, client, lease), lease=c.lease)
        # Gate new agent work before waiting for an in-flight page action.
        c.mode, c.client = 'requested', client
        c.ready.clear()
        try:
            async with asyncio.timeout(50):
                await c.idle.wait()
                async with record.lock:
                    _record(owner, session_id)
                    resource, page = _page(record, args.get('page') or next(iter(record.pages), ''))
                    c.mode, c.lease = 'human', secrets.token_urlsafe(32)
                    c.revision += 1
                    c.expires = time.monotonic() + LEASE_SECONDS
                    for r, _ in record.pages.values(): record.invalidate_refs(r)
                    await capture(record, resource, page)
                    record.touched = time.monotonic()
                    return dict(**_state(record, client, c.lease), lease=c.lease)
        except BaseException:
            c.mode, c.lease, c.client = 'paused', '', ''
            raise
    if c.mode != 'human' or c.client != client or not c.lease or not secrets.compare_digest(c.lease, lease):
        raise ResourceIdentityError('Browser control expired or belongs to another viewer')
    async with record.lock:
        _record(owner, session_id)
        if action == 'release':
            resource, page = _page(record, args.get('page') or next(iter(record.pages), ''))
            c.revision += 1
            await capture(record, resource, page)
            c.mode, c.lease, c.client = 'agent', '', ''
            c.ready.set()
            return _state(record)
        if action == 'refresh':
            resource, page = _page(record, args.get('page') or next(iter(record.pages), ''))
            await capture(record, resource, page)
        elif action == 'input':
            frame = c.frame
            if (not frame or args.get('frame_id') != frame['id']
                    or frame['revision'] != c.revision
                    or frame['document_revision'] != c.documents.get(frame['resource'].resolved_alias,0)
                    or time.monotonic() - frame['captured_at'] > 15
                    or args.get('incarnation') != record.resource.observation.session_incarnation
                    or args.get('page') != frame['resource'].resolved_alias
                    or args.get('width') != frame['width'] or args.get('height') != frame['height']):
                raise ResourceIdentityError('The page view changed; refresh before interacting')
            resource, page = _page(record, args['page'])
            kind = args.get('kind')
            if kind in {'click','double_click'}:
                x,y = args.get('x'),args.get('y')
                if any(type(v) not in {int,float} or not math.isfinite(v) for v in (x,y)) or not (0 <= x < frame['width'] and 0 <= y < frame['height']):
                    raise ValueError('Click lies outside the displayed page')
                await page.mouse.click(x,y, click_count=2 if kind == 'double_click' else 1)
            elif kind == 'scroll':
                dx,dy = args.get('dx',0),args.get('dy',0)
                if any(type(v) not in {int,float} or not math.isfinite(v) or abs(v)>2000 for v in (dx,dy)):
                    raise ValueError('Invalid scroll distance')
                await page.mouse.wheel(dx,dy)
            elif kind == 'text':
                text = args.get('text')
                if not isinstance(text,str) or len(text)>4096: raise ValueError('Text is limited to 4096 characters')
                await page.keyboard.insert_text(text)
            elif kind == 'key':
                allowed = {'Enter','Tab','Shift+Tab','Escape','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown',
                           'Home','End','PageUp','PageDown','Control+a','Meta+a','Space'}
                if args.get('key') not in allowed: raise ValueError('Unsupported browser key')
                await page.keyboard.press(args['key'])
            elif kind == 'select':
                target = page.locator('select:focus')
                if await target.count()!=1: raise ValueError('Focus a dropdown first')
                await target.select_option(value=str(args.get('value',''))[:4096])
            else: raise ValueError('Unsupported browser input')
            c.revision += 1
            for r,_ in record.pages.values(): record.invalidate_refs(r)
            await capture(record, resource, page)
        else: raise ValueError('Unsupported control operation')
        c.expires = time.monotonic() + LEASE_SECONDS
        record.touched = time.monotonic()
        state = _state(record,client,lease)
        focused = page.locator('select:focus')
        if await focused.count() == 1:
            state['options'] = await focused.locator('option').evaluate_all('(els) => els.slice(0,200).map(e => ({value:e.value,label:e.label,selected:e.selected}))')
        return state


async def _wait(owner, session_id):
    from src import managed_browser as browser
    record = browser.current(owner,session_id)
    if record is None: return None
    await record.control.ready.wait()
    try: browser.validate_session(record.resource)
    except ResourceIdentityError: return -1
    return record.control.revision


async def planning_checkpoint(owner, session_id, messages=None, security=None, authority=None):
    from src import managed_browser as browser
    owner, session_id = str(owner or '').strip().casefold(), str(session_id or '')
    revision = await browser._submit(_wait(owner,session_id)) if browser.current(owner,session_id) else 0
    previous = _PLANNED.get()
    _PLANNED.set((owner,session_id,revision))
    changed = previous and previous[:2] == (owner,session_id) and previous[2] != revision
    new_request = (not previous or previous[:2] != (owner,session_id)) and revision and revision > 0
    if changed or new_request:
        text = await fresh_state(owner,session_id,authority)
        if messages is not None:
            messages.append({'role':'user','content':'The user returned browser control. Discard previous tool proposals and continue from this fresh page state:\n'+text,
                'metadata':{'trusted':False,'tool_gate_untrusted':True,'source':'private_browser'}})
            if security is not None: security.observe_messages(messages)


async def generation_changed(owner, session_id):
    """Pause an in-flight model stream and discard its pre-takeover plan."""
    from src import managed_browser as browser
    owner, session_id = str(owner or '').strip().casefold(), str(session_id or '')
    previous = _PLANNED.get()
    if not previous or previous[:2] != (owner,session_id): return False
    record = browser.current(owner,session_id)
    if record is None: return previous[2] != 0
    if record.control.mode == 'agent' and record.control.revision == previous[2]: return False
    revision = await browser._submit(_wait(owner,session_id))
    return previous[2] != revision


async def tool_checkpoint(owner, session_id, authority=None):
    from src import managed_browser as browser
    owner, session_id = str(owner or '').strip().casefold(), str(session_id or '')
    if not browser.current(owner,session_id): return None
    revision = (browser.current(owner,session_id).control.revision
        if _IN_ACTION.get() == (owner,session_id)
        else await browser._submit(_wait(owner,session_id)))
    previous = _PLANNED.get()
    if previous and previous[:2] == (owner,session_id) and previous[2] != revision:
        return {'error':'Browser control changed after this action was planned. Inspect the fresh page state and plan again.',
                'output':await fresh_state(owner,session_id,authority), 'exit_code':1, 'blocked':True,
                'failure_kind':'browser_control_changed', 'executed':False}
    return None


async def fresh_state(owner, session_id, authority=None):
    from src import managed_browser as browser
    from src.agent_runtime.authority import active_request_authority, ExactOperation, RequestAuthority
    from src.browser_identity import resolve_browser_operation
    import json
    authority = authority or active_request_authority()
    if not isinstance(authority,RequestAuthority):
        return 'Browser control returned. No browser read is admitted for this request.'
    async def observe():
        record = _record(owner,session_id)
        async with record.lock:
            alias = record.control.frame['resource'].resolved_alias if record.control.frame else next(iter(record.pages),'')
            operation = ExactOperation.normalize('private_browser',json.dumps({'action':'snapshot','page':alias}))
            if not authority.permits(operation):
                raise ResourceIdentityError('Browser read is outside request authority')
            resolve_browser_operation(authority,operation)
            resource,page = _page(record,alias)
            result = dict(page=resource.resolved_alias,url=page.url)
            result['tabs'] = [{'page':r.resolved_alias,'url':p.url} for r,p in record.pages.values() if not p.is_closed()]
            result.update(await browser._snapshot(record,resource,page))
            return browser._encode_observation(result)
    try: return await browser._submit(observe())
    except ResourceIdentityError: return 'Browser session closed; open a new page.'


def agent_action_guard(func):
    """A takeover acknowledges only after the current tool finishes."""
    @wraps(func)
    async def wrapped(*args, **kwargs):
        from src import managed_browser as browser
        owner, sid = str(kwargs.get('owner') or '').strip().casefold(), str(kwargs.get('session_id') or '')
        record = browser.current(owner,sid)
        if record is None or _IN_ACTION.get() == (owner,sid):
            return await func(*args,**kwargs)
        async def enter():
            await record.control.ready.wait()
            browser.validate_session(record.resource)
            record.control.active += 1
            record.control.idle.clear()
        async def leave():
            record.control.active -= 1
            if record.control.active == 0: record.control.idle.set()
        try: await browser._submit(enter())
        except ResourceIdentityError:
            return 'browser: CLOSED', {'error':'Browser session closed while waiting for control',
                'exit_code':1,'blocked':True,'executed':False,'failure_kind':'browser_control_changed'}
        token = _IN_ACTION.set((owner,sid))
        try: return await func(*args,**kwargs)
        finally:
            _IN_ACTION.reset(token)
            await asyncio.shield(browser._submit(leave()))
    return wrapped
