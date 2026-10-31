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


def test_editor_image_models_per_user_isolation_and_fallback(tmp_path, monkeypatch):
    from routes import prefs_routes
    from src import editor_image_settings, settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    global_config = {"image_model": "global-gen", "image_edit_model": "global-edit"}
    monkeypatch.setattr(settings, "load_settings", lambda: global_config)
    monkeypatch.setattr(editor_image_settings, "load_settings", lambda: global_config)

    # Save per-user preferences
    prefs_routes._save_for_user("alice", {"image_model": "alice-gen", "image_edit_model": "alice-edit"})
    prefs_routes._save_for_user("bob", {"image_model": "bob-gen"})  # no edit model

    # Alice gets Alice's distinct models
    alice_models = editor_image_settings.editor_image_models(owner="alice")
    assert alice_models == {"generation": "alice-gen", "edit": "alice-edit"}

    # Bob gets Bob's generation model, and edit model falls back to global edit model
    bob_models = editor_image_settings.editor_image_models(owner="bob")
    assert bob_models == {"generation": "bob-gen", "edit": "global-edit"}

    # When global edit model is empty, edit model falls back to Bob's generation model
    empty_edit = {"image_model": "global-gen", "image_edit_model": ""}
    monkeypatch.setattr(settings, "load_settings", lambda: empty_edit)
    monkeypatch.setattr(editor_image_settings, "load_settings", lambda: empty_edit)
    bob_fallback_models = editor_image_settings.editor_image_models(owner="bob")
    assert bob_fallback_models == {"generation": "bob-gen", "edit": "bob-gen"}

    # Restore global settings with global-edit
    monkeypatch.setattr(settings, "load_settings", lambda: global_config)
    monkeypatch.setattr(editor_image_settings, "load_settings", lambda: global_config)

    # Charlie (no preferences) falls back to global admin settings
    charlie_models = editor_image_settings.editor_image_models(owner="charlie")
    assert charlie_models == {"generation": "global-gen", "edit": "global-edit"}

    # Anonymous/unauthenticated owner falls back to global admin settings
    anon_models = editor_image_settings.editor_image_models(owner="")
    assert anon_models == {"generation": "global-gen", "edit": "global-edit"}


def test_non_admin_user_prefs_api_routing_and_isolation(tmp_path, monkeypatch):
    import time
    from routes import editor_agent_routes, prefs_routes
    from src import editor_image_settings, settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    global_config = {"image_model": "admin-global-gen", "image_edit_model": "admin-global-edit"}
    monkeypatch.setattr(settings, "load_settings", lambda: global_config)
    monkeypatch.setattr(editor_image_settings, "load_settings", lambda: global_config)

    current_user = "alice"
    app = FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    app.include_router(editor_agent_routes.setup_editor_agent_routes())

    monkeypatch.setattr("src.auth_helpers.get_current_user", lambda request: current_user)
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: current_user)
    monkeypatch.setattr(editor_agent_routes, "_user", lambda request: current_user)
    monkeypatch.setattr(editor_agent_routes.editor_agent_model, "startup_probe", lambda: None)
    monkeypatch.setattr(editor_agent_routes.style_guide, "strengthen", lambda prompt, steps: steps)
    monkeypatch.setattr(editor_agent_routes.style_guide, "enforce_guards", lambda steps: steps)
    monkeypatch.setattr(editor_agent_routes, "_image_provider_available", lambda owner: True)
    monkeypatch.setattr(editor_agent_routes, "_plan_special", lambda *args: None)
    monkeypatch.setattr(editor_agent_routes, "_ensure_generative", lambda prompt, steps: steps)
    monkeypatch.setattr(editor_agent_routes, "_unsure_plan", lambda prompt, plan: plan)

    seen = []

    async def execute_gen(self, content, ctx):
        seen.append(("gen", json.loads(content)))
        return "stub", {"exit_code": 1, "error": "stop"}

    async def execute_edit(self, content, ctx):
        seen.append(("edit", json.loads(content)))
        return "stub", {"exit_code": 1, "error": "stop"}

    monkeypatch.setattr(editor_tools.GenerateImageLayerTool, "execute", execute_gen)
    monkeypatch.setattr(editor_tools.AiEditImageTool, "execute", execute_edit)

    editor_agent_routes.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Gen", "steps": [
            {"op": "generate_image", "params": {"prompt": "cat", "model": "stale-planner", "size": "512x512"}}
        ]
    }))

    try:
        with TestClient(app) as client:
            # Alice configures her preferences via PUT /api/prefs/{key}
            put_gen = client.put("/api/prefs/image_model", json={"value": "alice-fav-gen"})
            assert put_gen.status_code == 200
            put_edit = client.put("/api/prefs/image_edit_model", json={"value": "alice-fav-edit"})
            assert put_edit.status_code == 200

            # Verify GET /api/prefs reflects Alice's choices
            get_prefs = client.get("/api/prefs")
            assert get_prefs.status_code == 200
            assert get_prefs.json()["image_model"] == "alice-fav-gen"
            assert get_prefs.json()["image_edit_model"] == "alice-fav-edit"

            # Alice invokes quick edit with _use_image_settings: True
            res = client.post("/api/editor/agent", json={
                "prompt": "generate a cat",
                "_use_image_settings": True,
                "model": "stale-browser-edit",
                "generation_model": "stale-browser-gen",
            })
            assert res.status_code == 200
            assert len(seen) == 1
            # Authoritative Alice preference overrides stale planner and browser models
            assert seen[0][1]["model"] == "alice-fav-gen"

            # Now test Bob (who has no preferences) - should receive global admin settings
            current_user = "bob"
            seen.clear()
            res_bob = client.post("/api/editor/agent", json={
                "prompt": "generate a dog",
                "_use_image_settings": True,
                "model": "stale-browser-edit",
                "generation_model": "stale-browser-gen",
            })
            assert res_bob.status_code == 200
            assert len(seen) == 1
            # Bob falls back to global default, not Alice's preference
            assert seen[0][1]["model"] == "admin-global-gen"
    finally:
        editor_agent_routes.set_editor_planner(None)


def test_is_background_removal_intent_grammar_and_dos_safety():
    from routes.editor_agent_routes import _is_background_removal_intent
    import time

    # Valid commands across all verbs and targets
    assert _is_background_removal_intent("remove background")
    assert _is_background_removal_intent("please remove the background")
    assert _is_background_removal_intent("erase bg")
    assert _is_background_removal_intent("delete backdrop")
    assert _is_background_removal_intent("clear the bg from the active layer")
    assert _is_background_removal_intent("strip background of current layer!")
    assert _is_background_removal_intent("erase backdrop from selected layer...")
    assert _is_background_removal_intent("  PLEASE  REMOVE  THE  BG  FROM  THE  SELECTED  LAYER . ! ")

    # Invalid commands rejected
    assert not _is_background_removal_intent("")
    assert not _is_background_removal_intent("remove foreground")
    assert not _is_background_removal_intent("please blur the background")
    assert not _is_background_removal_intent("remove background and make it dark")
    assert not _is_background_removal_intent("remove")
    assert not _is_background_removal_intent("background")
    assert not _is_background_removal_intent("please remove")
    assert not _is_background_removal_intent("remove the")
    assert not _is_background_removal_intent("remove background from layer")  # missing active/current/selected
    assert not _is_background_removal_intent("remove background?")  # unallowed punctuation

    # ReDoS safety test: large repeated whitespace must run linearly in < 50ms
    evil_padding = " \t " * 10000
    evil_prompt = evil_padding + "please" + evil_padding + "remove" + evil_padding + "the" + evil_padding + "bg" + evil_padding + "!"
    start = time.perf_counter()
    assert _is_background_removal_intent(evil_prompt)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.1, f"Intent parsing took too long: {elapsed:.4f}s (polynomial backtracking risk)"

    evil_invalid = evil_padding + "remove" + evil_padding + "everything"
    start = time.perf_counter()
    assert not _is_background_removal_intent(evil_invalid)
    elapsed_invalid = time.perf_counter() - start
    assert elapsed_invalid < 0.1, f"Invalid intent parsing took too long: {elapsed_invalid:.4f}s"
