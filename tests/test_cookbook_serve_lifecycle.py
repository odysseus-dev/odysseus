import json

import pytest

from src import cookbook_serve_lifecycle as lifecycle


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
async def test_delete_endpoint_skips_when_task_has_no_endpoint_id(monkeypatch):
    deleted_urls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("httpx client must not run without an endpoint id")

    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", FakeClient)

    task = {
        "payload": {"_cmd": "OLLAMA_HOST=[::1]:11435 ollama serve"},
        "remoteHost": "",
    }
    await lifecycle._delete_endpoint_for_task(task)

    assert deleted_urls == []


@pytest.mark.asyncio
async def test_delete_endpoint_selects_by_endpoint_id_not_url(monkeypatch):
    deleted_urls = []

    class FakeResponse:
        status_code = 200
        content = (
            b'[{"id":"ep-wrong","base_url":"http://localhost:1/v1"},'
            b'{"id":"ep-ipv6","base_url":"http://localhost:11435/v1"}]'
        )

        def json(self):
            return [
                {"id": "ep-wrong", "base_url": "http://localhost:1/v1"},
                {"id": "ep-ipv6", "base_url": "http://localhost:11435/v1"},
            ]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse()

        async def delete(self, url, **kwargs):
            deleted_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(lifecycle, "internal_api_base", lambda: "http://test")

    task = {
        "_endpointId": "ep-ipv6",
        "payload": {"_cmd": "OLLAMA_HOST=[::1]:11435 ollama serve"},
        "remoteHost": "",
    }
    await lifecycle._delete_endpoint_for_task(task)

    assert deleted_urls == ["http://test/api/model-endpoints/ep-ipv6"]
