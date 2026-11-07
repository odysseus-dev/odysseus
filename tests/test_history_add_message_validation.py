import asyncio

import pytest
from fastapi import HTTPException

from tests.helpers.database import isolated_session_database


@pytest.fixture
def manager_database(tmp_path):
    with isolated_session_database(tmp_path) as resources:
        yield resources


def add_message(sm, monkeypatch, body):
    import routes.history.history_routes as history

    monkeypatch.setattr(history, '_verify_session_owner', lambda *args, **kwargs: None)
    monkeypatch.setattr(history, 'effective_user', lambda request: 'u')
    monkeypatch.setattr(history, 'reserve_message_upload_references', lambda *args, **kwargs: None)
    route = next(r for r in history.setup_history_routes(sm).routes
                 if r.path == '/api/session/{session_id}/message')

    class Request:
        async def json(self):
            return body

    return asyncio.run(route.endpoint(Request(), 'sid'))


@pytest.mark.parametrize('body', [
    {'role': 'bogus', 'content': 'x'},
    {'role': 'system', 'content': 'x'},
    {'role': 'user', 'content': 123},
    {'content': ['a']},
    {'role': ['user'], 'content': 'x'},
    [1],
])
def test_bad_role_or_content_is_a_client_error(manager_database, monkeypatch, body):
    sm, _ = manager_database
    sm.create_session(session_id='sid', name='t', endpoint_url='x', model='m', rag=False, owner='u')

    with pytest.raises(HTTPException) as exc:
        add_message(sm, monkeypatch, body)

    assert exc.value.status_code == 400
    assert sm.get_session('sid').history == []


@pytest.mark.parametrize('body, role', [
    ({'role': 'user', 'content': 'hi'}, 'user'),
    ({'role': 'assistant', 'content': 'hello'}, 'assistant'),
    ({'content': 'default role'}, 'assistant'),
])
def test_user_and_assistant_text_are_stored(manager_database, monkeypatch, body, role):
    sm, _ = manager_database
    sm.create_session(session_id='sid', name='t', endpoint_url='x', model='m', rag=False, owner='u')

    assert add_message(sm, monkeypatch, body) == {'status': 'ok'}

    stored = sm.get_session('sid').history
    assert [(m.role, m.content) for m in stored] == [(role, body['content'])]
