"""Re-probe must preserve disable choices and avoid metered completions."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import ModelEndpoint
from routes import model_routes


@pytest.fixture
def probe_state(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    ModelEndpoint.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(model_routes,'SessionLocal',sessions)
    monkeypatch.setattr(model_routes,'require_admin',lambda request: None)
    monkeypatch.setattr(model_routes,'_probe_endpoint',lambda *args, **kwargs:['keep','hidden','fail'])
    calls=[]
    def complete(base, key, model, **kwargs):
        calls.append(model)
        return {'status':'fail' if model=='fail' else 'ok'}
    monkeypatch.setattr(model_routes,'_probe_single_model',complete)
    with sessions() as db:
        db.add(ModelEndpoint(id='ep',name='Fixture',owner='alice',base_url='http://127.0.0.1:9999/v1',endpoint_kind='local',hidden_models='["hidden"]',pinned_models='["pinned"]'))
        db.commit()
    app=FastAPI();app.include_router(model_routes.setup_model_routes(model_discovery=None))
    with TestClient(app) as client:
        yield client,sessions,calls
    engine.dispose()


def run_probe(client):
    response=client.get('/api/model-endpoints/ep/probe')
    assert response.status_code==200,response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_local_probe_skips_hidden_and_unions_failures(probe_state):
    client,sessions,calls=probe_state
    events=run_probe(client)
    assert calls==['keep','fail']
    with sessions() as db:
        ep=db.get(ModelEndpoint,'ep')
        assert json.loads(ep.hidden_models)==['hidden','fail']
        assert json.loads(ep.pinned_models)==['pinned']
        assert json.loads(ep.cached_models)==['keep','hidden','fail']
    assert events[0]['model_count']==2
    assert events[-1]['hidden']==2


@pytest.mark.parametrize('kind,base,key',[('api','http://127.0.0.1:9999/v1',None),('auto','https://api.example.test/v1',None),('auto','http://127.0.0.1:9999/v1','fixture-key'),('proxy','http://127.0.0.1:9999/v1',None)])
def test_api_and_proxy_probe_only_discovers_without_completions(probe_state,kind,base,key):
    client,sessions,calls=probe_state
    with sessions() as db:
        ep=db.get(ModelEndpoint,'ep');ep.endpoint_kind=kind;ep.base_url=base;ep.api_key=key;db.commit()
    events=run_probe(client)
    assert calls==[]
    results=[e for e in events if e['type']=='probe_result']
    assert len(results)==2 and all(e['skipped'] and e['status']=='ok' for e in results)
    with sessions() as db:
        ep=db.get(ModelEndpoint,'ep')
        assert json.loads(ep.hidden_models)==['hidden']
        assert json.loads(ep.pinned_models)==['pinned']


def test_explicit_local_kind_still_probes_when_a_key_is_configured(probe_state):
    client,sessions,calls=probe_state
    with sessions() as db:
        db.get(ModelEndpoint,'ep').api_key='fixture-key';db.commit()
    run_probe(client)
    assert calls==['keep','fail']


def test_all_hidden_models_are_not_auto_healed(probe_state):
    client,sessions,calls=probe_state
    with sessions() as db:
        db.get(ModelEndpoint,'ep').hidden_models='["keep","hidden","fail"]';db.commit()
    events=run_probe(client)
    assert calls==[]
    assert events[0]['model_count']==0 and events[-1]['hidden']==3


def test_probe_merges_with_choices_saved_during_stream(probe_state,monkeypatch):
    client,sessions,calls=probe_state
    def complete(base,key,model,**kwargs):
        calls.append(model)
        if model=='keep':
            with sessions() as db:
                # The user unhides the old ID and hides a different model.
                db.get(ModelEndpoint,'ep').hidden_models='["new-choice"]';db.commit()
        return {'status':'fail' if model=='fail' else 'ok'}
    monkeypatch.setattr(model_routes,'_probe_single_model',complete)
    run_probe(client)
    assert calls==['keep','fail']
    with sessions() as db:
        assert json.loads(db.get(ModelEndpoint,'ep').hidden_models)==['new-choice','fail']


def test_empty_discovery_does_not_erase_saved_visibility(probe_state,monkeypatch):
    client,sessions,calls=probe_state
    monkeypatch.setattr(model_routes,'_probe_endpoint',lambda *args,**kwargs:[])
    run_probe(client)
    assert calls==[]
    with sessions() as db:
        assert json.loads(db.get(ModelEndpoint,'ep').hidden_models)==['hidden']


@pytest.mark.parametrize('kind', ['local', 'api'])
def test_global_probe_respects_visibility_and_discovery_only_kind(probe_state, kind):
    client, sessions, calls = probe_state
    with sessions() as db:
        db.get(ModelEndpoint, 'ep').endpoint_kind = kind
        db.commit()
    response = client.get('/api/probe', params={'endpoint_id': 'ep'})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert calls == (['keep', 'fail'] if kind == 'local' else [])
    assert events[0]['hidden_skipped'] == 1
    assert events[-1]['skipped'] == (0 if kind == 'local' else 2)
    with sessions() as db:
        assert json.loads(db.get(ModelEndpoint, 'ep').hidden_models) == ['hidden']
