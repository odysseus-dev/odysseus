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


def test_image_generation_global_disabled_user_enabled_rejected(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from routes.gallery import gallery_routes as gallery
    from routes import editor_agent_routes, prefs_routes
    from src import ai_interaction, settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    # Admin globally disables image generation
    monkeypatch.setattr(settings, "load_settings", lambda: {"image_gen_enabled": False, "image_model": "gpt-image-1"})

    # User 'alice' has image_gen_enabled = True in her preferences
    prefs_routes._save_for_user("alice", {"image_gen_enabled": True, "image_model": "alice-model"})

    # 1. get_user_setting must be False despite user preference
    assert settings.get_user_setting("image_gen_enabled", owner="alice", default=True) is False

    # 2. image_editing_provider_available must be False
    assert ai_interaction.image_editing_provider_available(owner="alice") is False

    # 3. Direct generation must be rejected with admin disabled message
    direct_res = asyncio.run(ai_interaction.do_generate_image(
        json.dumps({"prompt": "a dog"}), owner="alice"
    ))
    assert "error" in direct_res
    assert "disabled by the administrator" in direct_res["error"]

    # 4. Gallery route must reject with 503
    app = FastAPI()
    app.include_router(gallery.setup_gallery_routes())
    monkeypatch.setattr(gallery, "require_privilege", lambda request, priv: "alice")
    with TestClient(app) as client:
        gal_res = client.post("/api/image/generate", json={"prompt": "a dog"})
        assert gal_res.status_code == 503
        assert "switched off" in gal_res.json()["detail"].lower()

    # 5. Editor agent route must be blocked
    app_editor = FastAPI()
    app_editor.include_router(editor_agent_routes.setup_editor_agent_routes())
    monkeypatch.setattr(editor_agent_routes, "_user", lambda request: "alice")
    routes.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Gen", "steps": [{"op": "generate_image", "params": {"prompt": "a dog"}}]
    }))
    try:
        with TestClient(app_editor) as client:
            ed_res = client.post("/api/editor/agent", json={"prompt": "create a dog"})
            assert ed_res.status_code == 200
            data = ed_res.json()
            assert data.get("blocked") is True
            assert data.get("needs_provider") is True
    finally:
        routes.set_editor_planner(None)


def test_image_generation_global_enabled_user_disabled_rejected(tmp_path, monkeypatch):
    import asyncio
    from routes.gallery import gallery_routes as gallery
    from routes import editor_agent_routes, prefs_routes
    from src import ai_interaction, settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    # Admin globally enables image generation
    monkeypatch.setattr(settings, "load_settings", lambda: {"image_gen_enabled": True, "image_model": "gpt-image-1"})

    # User 'bob' has image_gen_enabled = False in his preferences
    prefs_routes._save_for_user("bob", {"image_gen_enabled": False, "image_model": "bob-model"})

    # 1. get_user_setting must be False per user preference
    assert settings.get_user_setting("image_gen_enabled", owner="bob", default=True) is False

    # 2. image_editing_provider_available must be False for bob
    assert ai_interaction.image_editing_provider_available(owner="bob") is False

    # 3. Direct generation must be rejected with user preference message
    direct_res = asyncio.run(ai_interaction.do_generate_image(
        json.dumps({"prompt": "a cat"}), owner="bob"
    ))
    assert "error" in direct_res
    assert "disabled by user preference" in direct_res["error"]

    # 4. Gallery route must reject with 503
    app = FastAPI()
    app.include_router(gallery.setup_gallery_routes())
    monkeypatch.setattr(gallery, "require_privilege", lambda request, priv: "bob")
    with TestClient(app) as client:
        gal_res = client.post("/api/image/generate", json={"prompt": "a cat"})
        assert gal_res.status_code == 503

    # 5. Editor agent route must be blocked for bob
    app_editor = FastAPI()
    app_editor.include_router(editor_agent_routes.setup_editor_agent_routes())
    monkeypatch.setattr(editor_agent_routes, "_user", lambda request: "bob")
    routes.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Gen", "steps": [{"op": "generate_image", "params": {"prompt": "a cat"}}]
    }))
    try:
        with TestClient(app_editor) as client:
            ed_res = client.post("/api/editor/agent", json={"prompt": "create a cat"})
            assert ed_res.status_code == 200
            data = ed_res.json()
            assert data.get("blocked") is True
            assert data.get("needs_provider") is True
    finally:
        routes.set_editor_planner(None)


def test_image_generation_authorized_generation_and_privilege_checks(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from routes.gallery import gallery_routes as gallery
    from routes import editor_agent_routes, prefs_routes
    from src import ai_interaction, settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    # Admin globally enables image generation
    monkeypatch.setattr(settings, "load_settings", lambda: {"image_gen_enabled": True, "image_model": "gpt-image-1"})

    # User 'charlie' has image generation enabled (no disabling pref)
    prefs_routes._save_for_user("charlie", {"image_model": "charlie-gen"})

    assert settings.get_user_setting("image_gen_enabled", owner="charlie", default=True) is True

    # Check privilege enforcement: user lacking can_generate_images is forbidden
    def require_privilege_mock(request, privilege):
        if privilege == "can_generate_images":
            raise HTTPException(403, "Permission denied")
        return "charlie"

    monkeypatch.setattr("src.auth_helpers.require_privilege", require_privilege_mock)
    monkeypatch.setattr(gallery, "require_privilege", require_privilege_mock)

    # Gallery checks privilege and returns 403
    app = FastAPI()
    app.include_router(gallery.setup_gallery_routes())
    with TestClient(app) as client:
        gal_res = client.post("/api/image/generate", json={"prompt": "a bird"})
        assert gal_res.status_code == 403
        assert "Permission denied" in gal_res.text

    # Editor checks privilege on AI content and raises 403
    app_editor = FastAPI()
    app_editor.include_router(editor_agent_routes.setup_editor_agent_routes())
    monkeypatch.setattr(editor_agent_routes, "_user", lambda request: "charlie")
    monkeypatch.setattr(editor_agent_routes, "_image_provider_available", lambda owner: True)
    routes.set_editor_planner(lambda messages, owner: json.dumps({
        "reply": "Gen", "steps": [{"op": "generate_image", "params": {"prompt": "a bird"}}]
    }))
    try:
        with TestClient(app_editor) as client:
            ed_res = client.post("/api/editor/agent", json={"prompt": "create a bird"})
            assert ed_res.status_code == 403
    finally:
        routes.set_editor_planner(None)

    # Now grant privilege: generation proceeds
    monkeypatch.setattr("src.auth_helpers.require_privilege", lambda request, priv: "charlie")
    monkeypatch.setattr(gallery, "require_privilege", lambda request, priv: "charlie")
    seen = []
    async def fake_do_generate(content, **kwargs):
        seen.append(kwargs)
        return {"image_url": "data:image/png;base64,dGVzdA=="}
    monkeypatch.setattr(ai_interaction, "do_generate_image", fake_do_generate)

    with TestClient(app) as client:
        gal_ok = client.post("/api/image/generate", json={"prompt": "a bird"})
        assert gal_ok.status_code == 200
        assert seen[0]["owner"] == "charlie"


def test_put_pref_clears_key_when_value_is_null(tmp_path, monkeypatch):
    from routes import prefs_routes
    from src import settings

    prefs_file = str(tmp_path / "user_prefs.json")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", prefs_file)

    # Set initial preference
    prefs_routes._save_for_user("admin_user", {"image_model": "old-admin-model"})
    assert prefs_routes._load_for_user("admin_user").get("image_model") == "old-admin-model"

    app = FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    monkeypatch.setattr("src.auth_helpers.get_current_user", lambda request: "admin_user")
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: "admin_user")

    with TestClient(app) as client:
        res = client.put("/api/prefs/image_model", json={"value": None})
        assert res.status_code == 200
        assert res.json() == {"key": "image_model", "value": None}

    # Verify key is deleted from user's record
    user_prefs = prefs_routes._load_for_user("admin_user")
    assert "image_model" not in user_prefs

    # Verify get_user_setting now falls back to global setting
    monkeypatch.setattr(settings, "load_settings", lambda: {"image_model": "new-global-model"})
    assert settings.get_user_setting("image_model", owner="admin_user") == "new-global-model"
