from pathlib import Path

import pytest

from tests.helpers.database import disposable_database


ROOT = Path(__file__).resolve().parents[1]
COOKBOOK_RUNNING = ROOT / "static" / "js" / "cookbookRunning.js"


def _source() -> str:
    return COOKBOOK_RUNNING.read_text(encoding="utf-8")


def test_cookbook_marks_local_endpoint_registration_as_container_local():
    src = _source()
    assert "function _appendCookbookEndpointScope" in src
    assert "fd.append('container_local', 'true')" in src
    assert src.count("_appendCookbookEndpointScope(fd,") >= 3


def test_cookbook_does_not_use_local_as_endpoint_hostname():
    src = _source()
    assert "function _connectHostFromRemote" in src
    assert "if (!host || host === 'local') return fallback;" in src
    assert "const rawHost = task.remoteHost || 'localhost';" not in src


def test_cookbook_advertised_bind_urls_keep_connectable_host():
    src = _source()
    assert "function _endpointFromAdvertisedUrl" in src
    assert "_isAnyBindHost(u.hostname) ? currentHost" in src
    assert "host = u.hostname || host;" not in src


@pytest.fixture
def endpoint_db(tmp_path, monkeypatch):
    import core.database as database
    import routes.model_routes as model_routes

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(model_routes, "_probe_endpoint", lambda *args, **kwargs: None)
        yield factory


def _launch_llm(
    repo_id: str,
    port: int,
    serve_session_id: str | None = None,
) -> str:
    import routes.cookbook_routes as cookbook_routes
    from routes.cookbook_helpers import ServeRequest

    router = cookbook_routes.setup_cookbook_routes()
    serve = next(
        route.endpoint for route in router.routes
        if route.path == "/api/model/serve" and "POST" in route.methods
    )
    cells = dict(zip(serve.__code__.co_freevars, serve.__closure__ or ()))
    register = cells["_auto_register_llm_endpoint"].cell_contents
    endpoint_id = register(
        ServeRequest(repo_id=repo_id, cmd=f"vllm serve {repo_id} --port {port}"),
        None,
        serve_session_id=serve_session_id,
    )
    assert endpoint_id
    return endpoint_id


def _endpoint(factory, endpoint_id: str):
    import core.database as database

    with factory() as db:
        return db.get(database.ModelEndpoint, endpoint_id)


@pytest.mark.parametrize("reuse", [False, True])
def test_launch_keeps_endpoint_of_other_model_with_same_short_name(endpoint_db, reuse):
    if reuse:
        mirror = _launch_llm("unsloth/Llama-3.1-8B-Instruct", 8002)
    original = _launch_llm("meta-llama/Llama-3.1-8B-Instruct", 8001)

    launched = _launch_llm("unsloth/Llama-3.1-8B-Instruct", 8002)

    if reuse:
        assert launched == mirror
    assert _endpoint(endpoint_db, original) is not None
    assert _endpoint(endpoint_db, launched) is not None


@pytest.mark.parametrize("reuse", [False, True])
def test_relaunch_on_another_port_preserves_existing_endpoint(endpoint_db, reuse):
    if reuse:
        current = _launch_llm("meta-llama/Llama-3.1-8B-Instruct", 8002)
    stale = _launch_llm("Qwen/Qwen3-8B", 8001)

    launched = _launch_llm("Qwen/Qwen3-8B", 8002)

    if reuse:
        assert launched == current
    assert _endpoint(endpoint_db, stale) is not None
    assert _endpoint(endpoint_db, launched) is not None


def test_sweep_ignores_endpoint_now_serving_a_different_model(endpoint_db):
    endpoint = _launch_llm("Qwen/Qwen3-8B", 8001)
    _launch_llm("meta-llama/Llama-3.1-8B", 8001)
    _launch_llm("Qwen/Qwen3-8B", 8001)

    _launch_llm("meta-llama/Llama-3.1-8B", 8002)

    assert _endpoint(endpoint_db, endpoint) is not None


def test_sweep_ignores_endpoints_not_created_by_cookbook(endpoint_db):
    import core.database as database

    with endpoint_db() as db:
        db.add(database.ModelEndpoint(
            id="manual", name="Qwen3-8B", base_url="http://localhost:9000/v1",
            pinned_models='["Qwen/Qwen3-8B"]',
        ))
        db.commit()

    _launch_llm("Qwen/Qwen3-8B", 8002)

    assert _endpoint(endpoint_db, "manual") is not None


def test_manual_pin_order_does_not_change_on_relaunch(endpoint_db):
    import json
    from core.database import ModelEndpoint

    endpoint_id = _launch_llm("organization-a/Model-A", 8001)

    with endpoint_db() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        endpoint.pinned_models = json.dumps([
            "organization-a/Model-A",
            "organization-b/Model-B",
        ])
        db.commit()

    _launch_llm("organization-a/Model-A", 8001)

    with endpoint_db() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        assert json.loads(endpoint.pinned_models) == [
            "organization-a/Model-A",
            "organization-b/Model-B",
        ]


def test_manual_pins_cannot_trigger_endpoint_deletion(endpoint_db):
    import json
    from core.database import ModelEndpoint

    original = _launch_llm("organization-a/Model-A", 8001)

    with endpoint_db() as db:
        endpoint = db.get(ModelEndpoint, original)
        endpoint.pinned_models = json.dumps([
            "organization-a/Model-A",
            "organization-b/Model-B",
        ])
        db.commit()

    _launch_llm("organization-b/Model-B", 8002)

    assert _endpoint(endpoint_db, original) is not None


def test_unavailable_model_probe_never_deletes_another_endpoint(
    endpoint_db, monkeypatch
):
    import routes.model_routes as model_routes

    original = _launch_llm("organization-a/Model-A", 8001)

    monkeypatch.setattr(
        model_routes,
        "_probe_endpoint",
        lambda *args, **kwargs: [],
    )

    _launch_llm("organization-a/Model-A", 8002)

    assert _endpoint(endpoint_db, original) is not None


@pytest.mark.asyncio
async def test_old_watchdog_cannot_delete_reused_endpoint(
    endpoint_db,
    monkeypatch,
    endpoint_http_api,
):
    import asyncio
    import urllib.request
    import routes.cookbook_routes as cookbook_routes

    original = _launch_llm(
        "organization-a/Model-A",
        8001,
        serve_session_id="serve-old",
    )

    reused = _launch_llm(
        "organization-b/Model-B",
        8001,
        serve_session_id="serve-new",
    )

    assert original == reused

    with endpoint_db() as db:
        from core.database import ModelEndpoint

        endpoint = db.get(ModelEndpoint, reused)
        assert endpoint.cookbook_serve_session_id == "serve-new"

    router = cookbook_routes.setup_cookbook_routes()
    serve = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/model/serve"
        and "POST" in route.methods
    )

    cells = dict(
        zip(
            serve.__code__.co_freevars,
            serve.__closure__ or (),
        )
    )

    watchdog = cells["_serve_crash_watchdog"].cell_contents

    class FakeProcess:
        async def communicate(self):
            return (
                b"=== Process exited with code 1 ===\n",
                b"",
            )

    async def fake_subprocess(*args, **kwargs):
        return FakeProcess()

    async def fake_sleep(seconds):
        return None

    def unavailable(*args, **kwargs):
        raise OSError("Server still warming up")

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_subprocess,
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        unavailable,
    )

    await watchdog(
        endpoint_id=original,
        session_id="serve-old",
        remote=None,
        ssh_port=None,
        is_windows=False,
    )

    assert _endpoint(endpoint_db, reused) is not None

    # The current serve's own crash watchdog must still be able
    # to remove its endpoint when it actually fails.
    await watchdog(
        endpoint_id=reused,
        session_id="serve-new",
        remote=None,
        ssh_port=None,
        is_windows=False,
    )

    assert _endpoint(endpoint_db, reused) is None


def test_cookbook_serve_session_migration_is_idempotent(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, inspect, text
    import core.database as database

    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy-endpoints.db'}"
    )

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE model_endpoints "
                    "(id VARCHAR PRIMARY KEY)"
                )
            )

        monkeypatch.setattr(database, "engine", engine)

        database._migrate_add_cookbook_serve_session_id_column()
        database._migrate_add_cookbook_serve_session_id_column()

        columns = {
            column["name"]
            for column in inspect(engine).get_columns(
                "model_endpoints"
            )
        }

        assert "cookbook_serve_session_id" in columns

    finally:
        engine.dispose()


@pytest.fixture
def endpoint_http_api(endpoint_db, monkeypatch):
    """Route deletion through a disposable in-process FastAPI app."""
    import httpx
    from fastapi import FastAPI
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes
    import core.constants as constants
    import src.cookbook_serve_lifecycle as lifecycle

    monkeypatch.setattr(model_routes, "SessionLocal", endpoint_db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)

    settings = {}
    preferences = {}
    monkeypatch.setattr(model_routes, "_load_settings", lambda: settings)
    monkeypatch.setattr(model_routes, "_save_settings", lambda value: settings.update(value))
    monkeypatch.setattr(prefs_routes, "_load", lambda: preferences)
    monkeypatch.setattr(prefs_routes, "_save", lambda value: preferences.update(value))

    # Endpoint deletion must not touch the user's running sessions.
    import src.ai_interaction as ai_interaction
    monkeypatch.setattr(ai_interaction, "get_session_manager", lambda: None)

    app = FastAPI()
    router = model_routes.setup_model_routes(None)
    app.include_router(router)

    original_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(constants, "internal_api_base", lambda: "http://testserver")
    monkeypatch.setattr(lifecycle, "internal_api_base", lambda: "http://testserver")

    return {"settings": settings, "preferences": preferences, "router": router}


@pytest.mark.asyncio
async def test_ownership_condition_preserves_settings_on_mismatch(
    endpoint_db, endpoint_http_api,
):
    import httpx
    original = _launch_llm("organization-a/Model-A", 8001, "serve-old")
    reused = _launch_llm("organization-b/Model-B", 8001, "serve-new")
    assert reused == original
    settings = endpoint_http_api["settings"]
    settings.update({"default_endpoint_id": reused, "default_model": "organization-b/Model-B"})
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"http://testserver/api/model-endpoints/{reused}",
            params={"expected_cookbook_serve_session_id": "serve-old"},
        )
    assert response.status_code == 409
    assert _endpoint(endpoint_db, reused) is not None
    assert settings["default_endpoint_id"] == reused


@pytest.mark.asyncio
async def test_owned_deletion_clears_settings_and_model_cache(
    endpoint_db, endpoint_http_api,
):
    import httpx
    from core.database import Session as DbSession

    endpoint_id = _launch_llm("organization-a/Model-A", 8001, "serve-current")
    settings = endpoint_http_api["settings"]
    settings.update({
        "default_endpoint_id": endpoint_id,
        "default_model": "organization-a/Model-A",
        "foreground_model_fallbacks": [{"endpoint_id": endpoint_id, "model": "x"}],
    })
    with endpoint_db() as db:
        db.add(DbSession(
            id="owned-delete-session", name="Test", owner=None,
            endpoint_url="http://localhost:8001/v1",
            endpoint_id=endpoint_id, model="organization-a/Model-A",
            headers={"Authorization": "Bearer test-only"},
        ))
        db.commit()

    # Seed the actual model listing cache captured by the route closure.
    router = endpoint_http_api["router"]
    route = next(r for r in router.routes if r.path == "/api/model-endpoints/{ep_id}" and "DELETE" in r.methods)
    closure = dict(zip(route.endpoint.__code__.co_freevars, route.endpoint.__closure__ or ()))
    invalidate = closure["_invalidate_models_cache"].cell_contents
    cache_closure = dict(zip(invalidate.__code__.co_freevars, invalidate.__closure__ or ()))
    models_cache = cache_closure["_models_cache"].cell_contents
    models_cache["test"] = {"data": [], "time": 0}

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"http://testserver/api/model-endpoints/{endpoint_id}",
            params={"expected_cookbook_serve_session_id": "serve-current"},
        )
    assert response.status_code == 200, response.text
    assert _endpoint(endpoint_db, endpoint_id) is None
    assert settings["default_endpoint_id"] == ""
    assert settings["foreground_model_fallbacks"] == []
    assert not models_cache
    with endpoint_db() as db:
        sess = db.get(DbSession, "owned-delete-session")
        assert sess.headers == {}


@pytest.mark.asyncio
async def test_ordinary_admin_delete_does_not_require_serve_owner(
    endpoint_db, endpoint_http_api,
):
    import httpx
    endpoint_id = _launch_llm("organization-a/Model-A", 8001, "serve-new")
    async with httpx.AsyncClient() as client:
        response = await client.delete(f"http://testserver/api/model-endpoints/{endpoint_id}")
    assert response.status_code == 200, response.text
    assert _endpoint(endpoint_db, endpoint_id) is None


def _launch_image(repo_id: str, port: int, monkeypatch) -> str:
    import routes.cookbook_routes as cookbook_routes
    import src.settings as settings
    from routes.cookbook_helpers import ServeRequest

    monkeypatch.setattr(settings, "load_settings", lambda: {"image_gen_enabled": True, "image_model": repo_id})
    monkeypatch.setattr(settings, "save_settings", lambda _settings: None)
    router = cookbook_routes.setup_cookbook_routes()
    serve = next(
        route.endpoint for route in router.routes
        if route.path == "/api/model/serve" and "POST" in route.methods
    )
    cells = dict(zip(serve.__code__.co_freevars, serve.__closure__ or ()))
    register = cells["_auto_register_image_endpoint"].cell_contents
    endpoint_id = register(
        ServeRequest(repo_id=repo_id, cmd=f"python diffusion_server.py --model {repo_id} --port {port}"), None
    )
    assert endpoint_id
    return endpoint_id


def _rename(factory, endpoint_id: str, name: str) -> None:
    import core.database as database

    with factory() as db:
        db.get(database.ModelEndpoint, endpoint_id).name = name
        db.commit()


@pytest.mark.parametrize("relaunched", ["Qwen/Qwen3-8B", "meta-llama/Llama-3.1-8B"])
def test_relaunch_keeps_renamed_endpoint_name(endpoint_db, relaunched):
    endpoint = _launch_llm("Qwen/Qwen3-8B", 8001)
    _rename(endpoint_db, endpoint, "Workstation GPU")

    assert _launch_llm(relaunched, 8001) == endpoint

    assert _endpoint(endpoint_db, endpoint).name == "Workstation GPU"


def test_relaunch_names_endpoint_after_served_model_when_not_renamed(endpoint_db):
    endpoint = _launch_llm("Qwen/Qwen3-8B", 8001)

    for repo_id, name in [
        ("meta-llama/Llama-3.1-8B", "Llama-3.1-8B"),
        ("Qwen/Qwen3-8B", "Qwen3-8B"),
        ("meta-llama/Llama-3.1-8B", "Llama-3.1-8B"),
    ]:
        _launch_llm(repo_id, 8001)
        assert _endpoint(endpoint_db, endpoint).name == name


def test_launch_keeps_name_of_endpoint_without_pinned_models(endpoint_db):
    import core.database as database

    with endpoint_db() as db:
        db.add(database.ModelEndpoint(id="manual", name="My vLLM box", base_url="http://localhost:8001/v1"))
        db.commit()

    assert _launch_llm("Qwen/Qwen3-8B", 8001) == "manual"

    assert _endpoint(endpoint_db, "manual").name == "My vLLM box"


def test_image_relaunch_keeps_renamed_endpoint_name(endpoint_db, monkeypatch):
    endpoint = _launch_image("black-forest-labs/FLUX.1-schnell", 8100, monkeypatch)
    _rename(endpoint_db, endpoint, "Image box")

    _launch_image("stabilityai/sdxl-turbo", 8100, monkeypatch)

    assert _endpoint(endpoint_db, endpoint).name == "Image box"


def test_image_relaunch_names_endpoint_after_served_model_when_not_renamed(endpoint_db, monkeypatch):
    endpoint = _launch_image("black-forest-labs/FLUX.1-schnell", 8100, monkeypatch)

    _launch_image("stabilityai/sdxl-turbo", 8100, monkeypatch)

    assert _endpoint(endpoint_db, endpoint).name == "sdxl-turbo (image)"
