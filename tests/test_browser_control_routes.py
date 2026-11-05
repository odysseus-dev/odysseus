"""Control endpoints reject delegated credentials, cross-owner and cross-site use."""
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from routes import browser_routes

@pytest.fixture
def client(monkeypatch):
    app=FastAPI()
    @app.middleware('http')
    async def identity(request,call_next):
        request.state.current_user=request.headers.get('x-test-user','alice')
        request.state.api_token=request.headers.get('x-test-token')=='yes'
        request.state.api_token_owner='alice'
        return await call_next(request)
    def owns(request,sid,manager):
        if sid!='alice-chat' or browser_routes.effective_user(request)!='alice': raise HTTPException(404)
    monkeypatch.setattr(browser_routes,'_verify_session_owner',owns)
    seen=[]
    async def control(owner,sid,operation,args):
        seen.append((owner,sid,operation,args))
        return b'jpeg' if operation=='frame' else {'mode':'agent','tabs':[]}
    monkeypatch.setattr(browser_routes.browser_control,'call',control)
    app.include_router(browser_routes.setup_browser_routes(SimpleNamespace()))
    with TestClient(app) as client:
        client.seen=seen
        yield client

def test_state_is_owner_scoped_and_not_cached(client):
    result=client.get('/api/browser/alice-chat/state')
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert client.get('/api/browser/alice-chat/state',headers={'x-test-user':'bob'}).status_code==404
    assert client.get('/api/browser/alice-chat/state',headers={'x-test-token':'yes'}).status_code==403
    assert client.get('/api/browser/alice-chat/state',headers={'x-test-user':'internal-tool'}).status_code==403

def test_mutations_require_same_origin_and_json(client):
    path='/api/browser/alice-chat/take'
    assert client.post(path,json={'client':'viewer'}).status_code==403
    assert client.post(path,json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post(path,json={},headers={'Origin':'http://testserver','Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.post(path,content='{}',headers={'Origin':'http://testserver','Content-Type':'text/plain'}).status_code==415
    assert not client.seen
    result=client.post(path,json={'client':'viewer'},headers={'Origin':'http://testserver'})
    assert result.status_code==200
    assert client.seen[-1][2]=='take'

def test_frames_are_private_and_input_is_bounded(client):
    result=client.get('/api/browser/alice-chat/frame/incarnation/frame')
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.headers['content-type']=='image/jpeg'
    count=len(client.seen)
    result=client.post('/api/browser/alice-chat/input',content='x'*32769,
                      headers={'Origin':'http://testserver','Content-Type':'application/json'})
    assert result.status_code==413 and len(client.seen)==count
