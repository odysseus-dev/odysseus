import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import editor_agent_routes as routes
from src.agent_tools import editor_tools


@pytest.fixture(autouse=True)
def allow_image_generation_privilege(monkeypatch):
    monkeypatch.setattr('src.auth_helpers.require_privilege', lambda *args: 'test-user')


@pytest.mark.parametrize("chosen", ["openai/gpt-5-image", ""])
def test_quick_edit_generation_uses_generation_selection_not_planner_model(monkeypatch, chosen):
    seen = []

    async def execute(self, content, ctx):
        seen.append(json.loads(content))
        return "provider stub", {"exit_code": 1, "error": "test stops before canvas verification"}

    monkeypatch.setattr(editor_tools.GenerateImageLayerTool, "execute", execute)
    monkeypatch.setattr(routes, "_user", lambda request: None)
    monkeypatch.setattr(routes.editor_agent_model, "startup_probe", lambda: None)
    monkeypatch.setattr(routes.style_guide, "strengthen", lambda prompt, steps: steps)
    monkeypatch.setattr(routes.style_guide, "enforce_guards", lambda steps: steps)
    monkeypatch.setattr(routes, "_image_provider_available", lambda owner: True)
    monkeypatch.setattr(routes, "_plan_special", lambda *args: None)
    monkeypatch.setattr(routes, "_ensure_generative", lambda prompt, steps: steps)
    monkeypatch.setattr(routes, "_unsure_plan", lambda prompt, plan: plan)
    routes.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Generate", "steps": [{"op": "generate_image", "params": {
            "prompt": "a cow", "model": "gpt-image-1",
        }}],
    }))
    app = FastAPI()
    app.include_router(routes.setup_editor_agent_routes())
    try:
        with TestClient(app) as client:
            response = client.post("/api/editor/agent", json={
                "prompt": "create a new image of a cow",
                "model": "edit-only-model", "generation_model": chosen,
            })
        assert response.status_code == 200, response.text
        assert len(seen) == 1, response.text
        assert seen[0].get("model", "") == chosen
    finally:
        routes.set_editor_planner(None)


@pytest.mark.parametrize('op,expected', [('generate_image','saved-generation'),('ai_edit_image','saved-editor')])
def test_quick_edit_agent_uses_saved_models_over_browser_and_planner(monkeypatch, op, expected):
    seen = []

    async def execute(self, content, ctx):
        seen.append(json.loads(content))
        return 'stub', {'exit_code':1, 'error':'stop before verification'}

    monkeypatch.setattr('src.editor_image_settings.load_settings', lambda: {
        'image_model':'saved-generation', 'image_edit_model':'saved-editor'})
    monkeypatch.setattr(editor_tools.GenerateImageLayerTool, 'execute', execute)
    monkeypatch.setattr(editor_tools.AiEditImageTool, 'execute', execute)
    monkeypatch.setattr(routes, '_user', lambda request: None)
    monkeypatch.setattr(routes.editor_agent_model, 'startup_probe', lambda: None)
    monkeypatch.setattr(routes.style_guide, 'strengthen', lambda prompt, steps: steps)
    monkeypatch.setattr(routes.style_guide, 'enforce_guards', lambda steps: steps)
    monkeypatch.setattr(routes, '_image_provider_available', lambda owner: True)
    monkeypatch.setattr(routes, '_plan_special', lambda *args: None)
    monkeypatch.setattr(routes, '_ensure_generative', lambda prompt, steps: steps)
    monkeypatch.setattr(routes, '_unsure_plan', lambda prompt, plan: plan)
    routes.set_editor_planner(lambda messages, owner: json.dumps({
        'reply':'Edit', 'steps':[{'op':op,'params':{'prompt':'a cow','model':'planner-model','size':'1024x1024'}}]}))
    app = FastAPI()
    app.include_router(routes.setup_editor_agent_routes())
    try:
        with TestClient(app) as client:
            response = client.post('/api/editor/agent', json={
                'prompt':'create an image of a cow','_use_image_settings':True,
                'model':'stale-editor','generation_model':'stale-generator','state':{'width':1280,'height':720}})
        assert response.status_code == 200, response.text
        assert len(seen) == 1, response.text
        assert seen[0]['model'] == expected
        assert seen[0]['_use_image_settings'] is True
        if op == 'generate_image':
            assert seen[0]['size'] == '1280x720'
    finally:
        routes.set_editor_planner(None)


def test_quick_generation_route_uses_saved_setting(monkeypatch):
    import asyncio
    from routes.gallery import gallery_routes as gallery
    from src import ai_interaction

    seen = []

    async def generate(content, **kwargs):
        seen.append(json.loads(content))
        return {'image_url':'data:image/png;base64,dGVzdA=='}

    monkeypatch.setattr(gallery, 'require_privilege', lambda *args: 'alice')
    monkeypatch.setattr(ai_interaction, 'do_generate_image', generate)
    monkeypatch.setattr('src.editor_image_settings.load_settings', lambda: {'image_model':'saved-generation'})

    class Request:
        async def json(self):
            return {'prompt':'a cow','model':'stale-generator','_use_image_settings':True,'width':1280,'height':720}

    endpoint = next(route.endpoint for route in gallery.setup_gallery_routes().routes if route.path == '/api/image/generate')
    result = asyncio.run(endpoint(Request()))
    assert result['image'] == 'dGVzdA=='
    assert seen[0]['model'] == 'saved-generation'
    assert seen[0]['size'] == '1280x720'


def test_edit_default_uses_generation_only_when_edit_setting_is_empty(monkeypatch):
    from src import editor_image_settings
    monkeypatch.setattr(editor_image_settings, 'load_settings', lambda: {'image_model':'generator','image_edit_model':''})
    assert editor_image_settings.editor_image_models() == {'generation':'generator','edit':'generator'}
