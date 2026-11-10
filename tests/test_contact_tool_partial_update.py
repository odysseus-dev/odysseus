"""Tool follow-up updates must preserve fields the user did not change."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import routes.contacts_routes as contacts
from src.tools.contacts import do_manage_contact

OWNER = 'sft_contact_fixture'


@pytest.fixture
def contact_app(monkeypatch, tmp_path):
    monkeypatch.setattr(contacts, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(contacts, 'LOCAL_CONTACTS_FILE', tmp_path / 'contacts.json')
    monkeypatch.setattr(contacts, 'SETTINGS_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(contacts, '_contact_cache', {
        'contacts': [], 'by_owner': {}, 'fetched_at': None, 'failed_at': None,
    })
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == OWNER)
    @app.middleware('http')
    async def test_identity(request, call_next):
        request.state.current_user = OWNER
        return await call_next(request)
    app.include_router(contacts.setup_contacts_routes())
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('patch,expected', [
    ({'name': 'Casey Revised'}, {'name': 'Casey Revised'}),
    ({'email': 'revised@example.test'}, {'emails': ['revised@example.test']}),
    ({'phones': ['+1-202-555-0168']}, {'phones': ['+1-202-555-0168']}),
    ({'emails': []}, {'emails': []}),
    ({'phones': []}, {'phones': []}),
])
async def test_tool_patch_retains_unspecified_contact_fields(contact_app, patch, expected):
    result = await do_manage_contact(json.dumps({
        'action': 'add', 'name': 'Casey Example', 'email': 'casey@example.test',
        'phones': ['+1-202-555-0142'], 'address': '42 Fixture Street',
    }), owner=OWNER)
    assert result['exit_code'] == 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=contact_app), base_url='http://test') as client:
        original = (await client.get('/api/contacts/list')).json()['contacts'][0]
        result = await do_manage_contact(json.dumps({
            'action': 'update', 'uid': original['uid'], **patch,
        }), owner=OWNER)
        assert result['exit_code'] == 0
        updated = (await client.get('/api/contacts/list')).json()['contacts'][0]
    assert updated == {**original, **expected}


@pytest.mark.asyncio
async def test_contact_search_returns_phone_and_address_evidence(contact_app):
    await do_manage_contact(json.dumps({
        'action': 'add', 'name': 'Casey Example', 'email': 'casey@example.test',
        'phones': ['+1-202-555-0142'], 'address': '42 Fixture Street',
    }), owner=OWNER)
    result = await do_manage_contact(json.dumps({'action': 'search', 'query': 'Casey'}), owner=OWNER)
    assert result['exit_code'] == 0
    assert '+1-202-555-0142' in result['output']
    assert '42 Fixture Street' in result['output']


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['missing', 'other-owner'])
async def test_contact_tool_does_not_create_or_modify_an_unavailable_target(contact_app, target):
    other_owner = 'sft_other_contact_fixture'
    await do_manage_contact(json.dumps({'action': 'add', 'name': 'Other Contact',
                                       'email': 'other@example.test'}), owner=other_owner)
    before = await do_manage_contact('{"action":"list"}', owner=other_owner)
    import re
    uid = re.search(r'\[uid=([^\]]+)\]', before['output']).group(1) if target == 'other-owner' else 'missing'
    result = await do_manage_contact(json.dumps({'action': 'update', 'uid': uid, 'name': 'Changed'}), owner=OWNER)
    assert result['exit_code'] == 1
    assert await do_manage_contact('{"action":"list"}', owner=other_owner) == before
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=contact_app), base_url='http://test') as client:
        assert (await client.get('/api/contacts/list')).json()['contacts'] == []
