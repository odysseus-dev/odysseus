import sys
for mod_name in ["src.endpoint_resolver", "src.database", "core.database"]:
    _mod = sys.modules.get(mod_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(mod_name, None)

import json
import ast
import asyncio
import inspect
import time
from types import SimpleNamespace

import pytest

from src.tool_policy import build_effective_tool_policy

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules

clear_fake_endpoint_resolver_modules("routes.chat_routes")

from routes import chat_routes


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return self

    def all(self):
        return list(self.rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        self.closed = True


def _session(model="qwen3.5:latest", endpoint_url="http://localhost:11434/v1/chat/completions"):
    return SimpleNamespace(model=model, endpoint_url=endpoint_url)


def _endpoint(base_url, model_type="image", models=None):
    cached_models = None if models is None else json.dumps(models)
    return SimpleNamespace(
        base_url=base_url,
        model_type=model_type,
        is_enabled=True,
        cached_models=cached_models,
    )


def test_image_model_prefix_routes_to_image_generation_without_endpoint_lookup(monkeypatch):
    def fail_if_called():
        raise AssertionError("prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)

    assert chat_routes._is_image_generation_session(_session(model="dall-e-3"))


def test_namespaced_gpt_image_model_routes_to_image_generation_without_endpoint_lookup(monkeypatch):
    def fail_if_called():
        raise AssertionError("provider-prefixed image models should not need a DB lookup")

    monkeypatch.setattr(chat_routes, "SessionLocal", fail_if_called)

    assert chat_routes._is_image_generation_session(_session(model="openai/gpt-5-image"))


def test_image_endpoint_does_not_catch_text_model_on_different_path(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1/images", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session())
    assert db.closed


def test_image_endpoint_cache_must_contain_selected_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert not chat_routes._is_image_generation_session(_session(model="qwen3.5:latest"))


def test_matching_image_endpoint_routes_selected_image_model(monkeypatch):
    db = _FakeDb([
        _endpoint("http://localhost:11434/v1", models=["sdxl-local"]),
    ])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    assert chat_routes._is_image_generation_session(_session(model="sdxl-local"))


def test_image_model_bypasses_text_agent_inventory_only():
    tree = ast.parse(inspect.getsource(chat_routes))
    guard = next(node.test for node in ast.walk(tree)
                 if isinstance(node, ast.If)
                 and ast.unparse(node.test).startswith('_use_turn_contract and chat_mode'))
    expression = compile(ast.Expression(guard), '<route guard>', 'eval')
    for image_session in (True, False):
        assert eval(expression, dict(_use_turn_contract=True, chat_mode='agent',
                                     image_generation_session=image_session)) is not image_session


@pytest.mark.parametrize('editing', [False, True])
@pytest.mark.parametrize('restriction', ['none', 'generate_image', 'edit_image', 'guide', 'admin'])
def test_direct_image_dispatch_preserves_permissions(monkeypatch, editing, restriction):
    # Execute the actual route branch with fake providers, avoiding paid calls
    # and unrelated chat-context/database setup.
    tree = ast.parse(inspect.getsource(chat_routes))
    branch = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.If)
                  and ast.unparse(node.test) == 'image_generation_session'
                  and any(isinstance(child, ast.Yield) for child in ast.walk(node)))
    function = ast.AsyncFunctionDef(
        name='dispatch', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                          kw_defaults=[], defaults=[]),
        body=branch.body, decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    calls = []

    async def provider(*args, **kwargs):
        calls.append((args, kwargs))
        return {'results': 'Generated', 'image_url': '/test-image.png'}

    from src import ai_interaction, settings
    monkeypatch.setattr(ai_interaction, 'do_generate_image', provider)
    monkeypatch.setattr(ai_interaction, 'do_edit_image', provider)
    monkeypatch.setattr(settings, 'get_setting', lambda *args: restriction != 'admin')
    policy = build_effective_tool_policy(
        disabled_tools={restriction} if restriction.endswith('_image') else set(),
        last_user_message='Do not use tools' if restriction == 'guide' else 'A thumbnail',
    )
    namespace = dict(
        tool_policy=policy, chat_handler=None, att_ids=[], _user='test',
        _first_image_attachment=lambda *args, **kwargs: {'path': '/test.png'} if editing else None,
        message='A thumbnail', session='test-session', sess=_session(model='gpt-5-image'),
        incognito=True, _active_streams={'test-session': object()},
        asyncio=asyncio, time=time, json=json, Dict=dict, Any=object,
    )
    exec(compile(module, '<image route>', 'exec'), namespace)

    async def collect():
        return [event async for event in namespace['dispatch']()]

    events = asyncio.run(collect())
    blocked = restriction in {'generate_image', 'guide', 'admin'} or (editing and restriction == 'edit_image')
    assert bool(calls) is not blocked
    assert any('generated_image' in event for event in events) is not blocked
    assert events[-1] == 'data: [DONE]\n\n'
    assert not namespace['_active_streams']
