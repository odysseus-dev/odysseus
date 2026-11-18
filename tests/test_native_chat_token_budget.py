"""Exercise the native generation-settings handler without starting app services."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def generation_handler():
    path = Path(__file__).parents[1] / 'routes/history/history_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == 'set_session_generation_settings')
    node.decorator_list = []
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    session = SimpleNamespace(model='qwen2.5', thinking_mode='off',
                              temperature_override=None, max_tokens_override=None)
    row = SimpleNamespace()
    calls = []

    class Database:
        def query(self, model):
            return self

        def filter(self, condition):
            return self

        def first(self):
            return row

        def commit(self):
            calls.append('commit')

        def close(self):
            calls.append('close')

    def verify_owner(request, sid, manager):
        calls.append('owner-check')
        if not request.allowed:
            raise HTTPException(403)

    namespace = {
        'HTTPException': HTTPException,
        '_verify_session_owner': verify_owner,
        'session_manager': SimpleNamespace(get_session=lambda sid: session),
        'SessionLocal': Database,
        'DbSession': SimpleNamespace(id='id'),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(path), 'exec'), namespace)
    return namespace[node.name], session, row, calls


@pytest.mark.asyncio
async def test_native_settings_preserve_bounded_32_token_budget():
    handler, session, row, calls = generation_handler()

    async def body():
        return {'max_tokens_override': 32}

    result = await handler(SimpleNamespace(json=body, allowed=True), 'cpu-check')
    assert result['max_tokens_override'] == 32
    assert session.max_tokens_override == row.max_tokens_override == 32
    assert calls == ['owner-check', 'commit', 'close']


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,expected', [(0, None), (None, None), (99999, 32768)])
async def test_native_settings_keep_existing_default_and_upper_bound(requested, expected):
    handler, session, row, _ = generation_handler()

    async def body():
        return {'max_tokens_override': requested}

    result = await handler(SimpleNamespace(json=body, allowed=True), 'cpu-check')
    assert result['max_tokens_override'] == expected
    assert session.max_tokens_override == row.max_tokens_override == expected


@pytest.mark.asyncio
async def test_native_budget_change_still_checks_owner_before_mutation():
    handler, _, row, calls = generation_handler()

    async def body():
        return {'max_tokens_override': 32}

    with pytest.raises(HTTPException) as error:
        await handler(SimpleNamespace(json=body, allowed=False), 'cpu-check')
    assert error.value.status_code == 403
    assert calls == ['owner-check']
    assert vars(row) == {}
