"""Human takeover against actual Chromium, with shared agent execution gates."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest
from tests.test_managed_browser import live_browser, authority, call, output
from src import browser_control as control, managed_browser as browser
from src.agent_runtime.resources import ResourceIdentityError
from src.agent_runtime.authority import ExactOperation
from src.browser_identity import resolve_browser_operation

async def op(auth, action, **args):
    return await control.call(auth.owner,auth.session_id,action,args)

def input_args(state, **args):
    return dict(client='viewer-a',lease=state['lease'],page=state['page'],
        incarnation=state['incarnation'],frame_id=state['frame_id'],width=state['width'],height=state['height'],**args)

@pytest.mark.slow
async def test_takeover_real_mouse_text_submit_and_fresh_agent_resume(live_browser):
    auth=authority()
    result=await call(auth,'open',url='https://catalog.example/')
    assert result['browser_preview']['status']=='ready'
    assert 'images' not in result  # UI preview is not extra model visual input.
    await control.planning_checkpoint(auth.owner,auth.session_id,[],authority=auth)
    old=resolve_browser_operation(auth,ExactOperation.normalize('private_browser','{"action":"read","page":"t1"}'))
    state=await op(auth,'take',client='viewer-a',page='t1')
    assert state['controlling'] and state['mode']=='human'
    with pytest.raises(ResourceIdentityError,match='stale'): old.validate()
    with pytest.raises(ResourceIdentityError,match='Another viewer'):
        await op(auth,'take',client='viewer-b',page='t1')
    waiting=asyncio.create_task(call(auth,'read',page='t1'))
    await asyncio.sleep(.05)
    assert not waiting.done()
    # Locate test targets only to generate actual mouse coordinates. Human
    # requests use frame coordinates and never expose raw evaluation APIs.
    record=browser.current(auth.owner,auth.session_id)
    async def bounds(selector):
        return await record.pages['t1'][1].locator(selector).bounding_box()
    box=await browser._submit(bounds('input'))
    stale=dict(state)
    state=await op(auth,'input',**input_args(state,kind='click',x=box['x']+5,y=box['y']+5))
    state['lease']=stale['lease']
    with pytest.raises(ResourceIdentityError,match='view changed'):
        await op(auth,'input',**input_args(stale,kind='text',text='stale'))
    state=await op(auth,'input',**input_args(state,kind='text',text='human-entered desk'))
    state['lease']=stale['lease']
    box=await browser._submit(bounds('button'))
    state=await op(auth,'input',**input_args(state,kind='click',x=box['x']+5,y=box['y']+5))
    state['lease']=stale['lease']
    raw=await op(auth,'frame',incarnation=state['incarnation'],frame_id=state['frame_id'])
    assert raw.startswith(b'\xff\xd8')
    await op(auth,'release',client='viewer-a',lease=stale['lease'],page='t1')
    rejected=await asyncio.wait_for(waiting,5)
    assert rejected['failure_kind']=='browser_control_changed' and not rejected['executed']
    messages=[]
    await control.planning_checkpoint(auth.owner,auth.session_id,messages,authority=auth)
    assert 'human-entered desk' in messages[-1]['content']
    assert messages[-1]['metadata']['trusted'] is False
    assert 'human-entered desk' in output(await call(auth,'read',page='t1'))['text']

@pytest.mark.slow
async def test_takeover_waits_for_inflight_tools_and_disconnect_stays_paused(live_browser):
    auth=authority()
    output(await call(auth,'open',url='https://catalog.example/'))
    started=asyncio.Event(); finish=asyncio.Event()
    @control.agent_action_guard
    async def work(*,owner,session_id):
        started.set();await finish.wait()
    active=asyncio.create_task(work(owner=auth.owner,session_id=auth.session_id))
    await started.wait()
    taking=asyncio.create_task(op(auth,'take',client='viewer-a',page='t1'))
    await asyncio.sleep(.05)
    assert not taking.done()
    assert browser.current(auth.owner,auth.session_id).control.mode=='requested'
    finish.set();await active
    state=await asyncio.wait_for(taking,5)
    record=browser.current(auth.owner,auth.session_id)
    record.control.expires=0
    state2=await op(auth,'state',client='viewer-a',lease=state['lease'])
    assert state2['mode']=='paused' and not state2['controlling']
    with pytest.raises(ResourceIdentityError,match='expired'):
        await op(auth,'input',**input_args(state,kind='key',key='Enter'))
    renewed=await op(auth,'take',client='viewer-b',page='t1')
    assert renewed['controlling']
    await op(auth,'release',client='viewer-b',lease=renewed['lease'],page='t1')

@pytest.mark.slow
async def test_control_rejects_other_owner_old_frames_and_unsafe_keys(live_browser):
    auth=authority()
    output(await call(auth,'open',url='https://catalog.example/'))
    with pytest.raises(ResourceIdentityError):
        await control.call('bob',auth.session_id,'state')
    state=await op(auth,'take',client='viewer-a',page='t1')
    with pytest.raises(ValueError,match='outside'):
        await op(auth,'input',**input_args(state,kind='click',x=-1,y=0))
    with pytest.raises(ValueError,match='Unsupported'):
        await op(auth,'input',**input_args(state,kind='key',key='Control+l'))
    with pytest.raises(ResourceIdentityError,match='expired'):
        await op(auth,'frame',incarnation='wrong',frame_id=state['frame_id'])
    await op(auth,'release',client='viewer-a',lease=state['lease'],page='t1')

@pytest.mark.slow
async def test_inflight_standard_model_plan_is_discarded_after_takeover(live_browser, monkeypatch):
    from src import agent_loop as loop
    auth=authority();output(await call(auth,'open',url='https://catalog.example/'))
    monkeypatch.setattr(loop,'get_setting',lambda key,default=None:default)
    monkeypatch.setattr(loop,'get_mcp_manager',lambda:None)
    monkeypatch.setattr(loop,'estimate_tokens',lambda *a,**k:10)
    monkeypatch.setattr(loop,'_configured_model_tool_surface_raw',lambda *a,**k:'full')
    started=asyncio.Event(); finish=asyncio.Event(); calls=[]
    async def model(*args,**kwargs):
        calls.append(args)
        if len(calls)==1:
            started.set();await finish.wait()
            yield 'data: '+json.dumps({'delta':'STALE FINAL ANSWER'})+'\n\n'
        else:
            messages=args[1] if len(args)>1 else kwargs['messages']
            assert any('user returned browser control' in str(m.get('content','')).casefold() for m in messages)
            yield 'data: '+json.dumps({'delta':'Fresh answer after user control.'})+'\n\n'
        yield 'data: [DONE]\n\n'
    monkeypatch.setattr(loop,'stream_llm_with_fallback',model)
    async def collect():
        return [chunk async for chunk in loop.stream_agent_loop('http://fixture','fixture',
            [{'role':'user','content':'Browse the catalog'}],headers={},owner=auth.owner,session_id=auth.session_id,
            request_authority=auth,forced_tools={'private_browser'},relevant_tools={'private_browser'},
            max_rounds=1,context_length=8192,force_textual_tool_transport=True)]
    running=asyncio.create_task(collect());await asyncio.wait_for(started.wait(),5)
    state=await op(auth,'take',client='viewer-a',page='t1')
    finish.set();await asyncio.sleep(.05);assert not running.done()
    await op(auth,'release',client='viewer-a',lease=state['lease'],page='t1')
    chunks=await asyncio.wait_for(running,10)
    assert len(calls)==2
    assert 'STALE FINAL ANSWER' not in ''.join(chunks)
    assert 'Fresh answer after user control.' in ''.join(chunks)

@pytest.mark.slow
async def test_navigation_invalidates_displayed_frame_and_narrow_request_cannot_read(live_browser):
    auth=authority();output(await call(auth,'open',url='https://catalog.example/'))
    state=await op(auth,'take',client='viewer-a',page='t1')
    record=browser.current(auth.owner,auth.session_id)
    async def navigate(): await record.pages['t1'][1].goto('https://catalog.example/next')
    await browser._submit(navigate())
    with pytest.raises(ResourceIdentityError,match='view changed'):
        await op(auth,'input',**input_args(state,kind='text',text='must not enter changed page'))
    narrow=replace(auth,grants=())
    text=await control.fresh_state(auth.owner,auth.session_id,narrow)
    assert 'Search catalog' not in text
    renewed=await op(auth,'refresh',client='viewer-a',lease=state['lease'],page='t1')
    assert renewed['status']=='ready' and renewed['frame_id']!=state['frame_id']
    await op(auth,'release',client='viewer-a',lease=state['lease'],page='t1')
