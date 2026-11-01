import json

import pytest

from src import cookbook_serve_lifecycle as lifecycle

from tests.test_cookbook_endpoint_registration import (
    endpoint_db,
    endpoint_http_api,
    _endpoint,
    _launch_llm,
)


@pytest.mark.asyncio
async def test_tick_persists_only_successfully_stopped_serves(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({
            "tasks": [
                {
                    "id": "stop-succeeds",
                    "type": "serve",
                    "status": "running",
                    "_scheduledStopAtMs": 0,
                },
                {
                    "id": "stop-fails",
                    "type": "serve",
                    "status": "running",
                    "_scheduledStopAtMs": 0,
                },
            ]
        }),
        encoding="utf-8",
    )

    async def fake_stop_serve(session_id, remote_host="", ssh_port=""):
        return session_id == "stop-succeeds"

    async def fake_delete_endpoint(task):
        return None

    monkeypatch.setattr(lifecycle, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(lifecycle, "_stop_serve", fake_stop_serve)
    monkeypatch.setattr(lifecycle, "_delete_endpoint_for_task", fake_delete_endpoint)

    await lifecycle._tick()

    tasks = {
        task["id"]: task
        for task in json.loads(state_path.read_text(encoding="utf-8"))["tasks"]
    }
    assert tasks["stop-succeeds"]["status"] == "stopped"
    assert tasks["stop-succeeds"]["_scheduledStopAtMs"] is None
    assert tasks["stop-fails"]["status"] == "running"
    assert tasks["stop-fails"]["_scheduledStopAtMs"] == 0



@pytest.mark.asyncio
async def test_scheduled_stop_preserves_endpoint_reused_by_newer_serve(
    endpoint_db,
    endpoint_http_api,
):
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

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": original,
        "sessionId": "serve-old",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, reused) is not None

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": reused,
        "sessionId": "serve-new",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, reused) is None


@pytest.mark.asyncio
async def test_scheduled_stop_preserves_legacy_endpoint_without_owner(
    endpoint_db,
    endpoint_http_api,
):
    endpoint_id = _launch_llm(
        "organization-a/Model-A",
        8002,
    )

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": endpoint_id,
        "sessionId": "serve-old",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, endpoint_id) is not None


@pytest.mark.asyncio
async def test_scheduled_stop_cannot_delete_manual_endpoint(
    endpoint_db,
    endpoint_http_api,
):
    from core.database import ModelEndpoint

    with endpoint_db() as db:
        db.add(ModelEndpoint(
            id="manual-endpoint",
            name="Manual",
            base_url="http://localhost:9000/v1",
            cookbook_serve_session_id="serve-old",
        ))
        db.commit()

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": "manual-endpoint",
        "sessionId": "serve-old",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, "manual-endpoint") is not None


@pytest.mark.asyncio
async def test_scheduled_stop_requires_session_identity(endpoint_db, endpoint_http_api):
    endpoint_id = _launch_llm(
        "organization-a/Model-A",
        8003,
        serve_session_id="serve-current",
    )

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": endpoint_id,
        "type": "serve",
    })

    assert _endpoint(endpoint_db, endpoint_id) is not None



def _launch_image(repo_id, port, serve_session_id):
    import routes.cookbook_routes as cookbook_routes
    from routes.cookbook_helpers import ServeRequest

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

    register = cells["_auto_register_image_endpoint"].cell_contents

    endpoint_id = register(
        ServeRequest(
            repo_id=repo_id,
            cmd=f"python scripts/diffusion_server.py --port {port}",
        ),
        None,
        serve_session_id=serve_session_id,
    )

    assert endpoint_id
    return endpoint_id


@pytest.mark.asyncio
async def test_scheduled_stop_deletes_owned_image_endpoint(
    endpoint_db,
    monkeypatch,
    endpoint_http_api,
):
    import src.settings as settings
    from core.database import ModelEndpoint

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(
        settings,
        "save_settings",
        lambda value: None,
    )

    endpoint_id = _launch_image(
        "organization-a/Image-A",
        8101,
        "serve-image-a",
    )

    assert endpoint_id.startswith("img-")

    with endpoint_db() as db:
        endpoint = db.get(ModelEndpoint, endpoint_id)
        assert endpoint.cookbook_serve_session_id == "serve-image-a"

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": endpoint_id,
        "sessionId": "serve-image-a",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, endpoint_id) is None


@pytest.mark.asyncio
async def test_old_image_serve_cannot_delete_reused_endpoint(
    endpoint_db,
    monkeypatch,
    endpoint_http_api,
):
    import src.settings as settings

    monkeypatch.setattr(settings, "load_settings", lambda: {})
    monkeypatch.setattr(
        settings,
        "save_settings",
        lambda value: None,
    )

    original = _launch_image(
        "organization-a/Image-A",
        8102,
        "serve-old",
    )

    reused = _launch_image(
        "organization-b/Image-B",
        8102,
        "serve-new",
    )

    assert original == reused

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": original,
        "sessionId": "serve-old",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, reused) is not None

    await lifecycle._delete_endpoint_for_task({
        "_endpointId": reused,
        "sessionId": "serve-new",
        "type": "serve",
    })

    assert _endpoint(endpoint_db, reused) is None
