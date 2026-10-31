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


def _launch_llm(repo_id: str, port: int) -> str:
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
        ServeRequest(repo_id=repo_id, cmd=f"vllm serve {repo_id} --port {port}"), None
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
def test_relaunch_on_another_port_sweeps_endpoint_launched_for_same_model(endpoint_db, reuse):
    if reuse:
        current = _launch_llm("meta-llama/Llama-3.1-8B-Instruct", 8002)
    stale = _launch_llm("Qwen/Qwen3-8B", 8001)

    launched = _launch_llm("Qwen/Qwen3-8B", 8002)

    if reuse:
        assert launched == current
    assert _endpoint(endpoint_db, stale) is None
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


def test_relaunch_with_another_model_follows_it_when_not_renamed(endpoint_db):
    endpoint = _launch_llm("Qwen/Qwen3-8B", 8001)

    _launch_llm("meta-llama/Llama-3.1-8B", 8001)

    assert _endpoint(endpoint_db, endpoint).name == "Llama-3.1-8B"


def test_launch_keeps_name_of_endpoint_without_launch_history(endpoint_db):
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


def test_image_relaunch_with_another_model_follows_it_when_not_renamed(endpoint_db, monkeypatch):
    endpoint = _launch_image("black-forest-labs/FLUX.1-schnell", 8100, monkeypatch)

    _launch_image("stabilityai/sdxl-turbo", 8100, monkeypatch)

    assert _endpoint(endpoint_db, endpoint).name == "sdxl-turbo (image)"
