"""Heavy in-process image ops must not load GB-scale models by default.

remove-bg / SAM mask / Real-ESRGAN upscale+denoise used to load rembg / torch
models inside the web process and OOM-killed the host. They now default to a
graceful 503, proxy to the seneca remote ops service when
``ODYSSEUS_IMAGE_OPS_BASE`` is set (translate to the /v1/images/* contract), and
only run in-process when explicitly enabled via ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.gallery import gallery_routes as gr


HEAVY_ROUTES = [
    ("/api/image/remove-bg", {"image": "x"}, "Background removal"),
    ("/api/image/mask", {"image": "x"}, "Smart mask"),
    ("/api/image/upscale-local", {"image": "x"}, "AI upscale"),
    ("/api/image/denoise", {"image": "x"}, "AI denoise"),
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setattr(gr, "require_privilege", lambda *a, **k: None)
    monkeypatch.delenv("ODYSSEUS_IMAGE_OPS_BASE", raising=False)
    monkeypatch.delenv("ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS", raising=False)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(gr.setup_gallery_routes())
    return TestClient(app)


def test_local_image_models_disabled_by_default():
    assert gr._local_image_models_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "Yes", "ON"])
def test_local_image_models_enabled_env(monkeypatch, value):
    monkeypatch.setenv("ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS", value)
    assert gr._local_image_models_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "no", "off", "maybe"])
def test_local_image_models_disabled_env(monkeypatch, value):
    monkeypatch.setenv("ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS", value)
    assert gr._local_image_models_enabled() is False


def test_rss_mb_is_positive():
    assert gr._rss_mb() > 0


def test_route_heavy_image_op_returns_none_when_enabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS", "1")
    assert asyncio.run(gr._route_heavy_image_op("/api/image/denoise", {}, label="x")) is None


@pytest.mark.parametrize("path,body,label", HEAVY_ROUTES)
def test_heavy_ops_503_when_disabled(client, path, body, label):
    resp = client.post(path, json=body)
    assert resp.status_code == 503
    detail = resp.json().get("detail", "")
    assert "temporarily unavailable" in detail
    assert label in detail


def test_remove_bg_disabled_does_not_import_rembg(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "rembg", None)

    app = FastAPI()
    app.include_router(gr.setup_gallery_routes())
    client = TestClient(app)
    resp = client.post("/api/image/remove-bg", json={"image": "x"})
    assert resp.status_code == 503


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeAsyncClient:
    calls = []
    response = _FakeResponse({})

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, files=None, data=None):
        _FakeAsyncClient.calls.append((url, {"json": json, "files": files, "data": data}))
        return _FakeAsyncClient.response


def _client():
    app = FastAPI()
    app.include_router(gr.setup_gallery_routes())
    return TestClient(app)


def test_remove_bg_proxies_to_remote_contract(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(
        {"data": [{"b64_json": "RB", "mask_b64": "M", "bbox": [0, 0, 1, 1]}], "model": "birefnet"}
    )

    resp = _client().post("/api/image/remove-bg", json={"image": "AAAA"})
    assert resp.status_code == 200
    assert resp.json()["image"] == "RB"
    url, kw = _FakeAsyncClient.calls[-1]
    assert url == "http://seneca.test:8188/v1/images/remove-background"
    assert "image" in kw["files"]


def test_mask_proxies_to_remote_contract(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(
        {"masks": [{"b64_json": "MASK", "bbox": [10, 20, 30, 40], "score": 0.9, "label": "person"}],
         "model": "sam+grounding"}
    )

    resp = _client().post("/api/image/mask", json={"image": "AAAA", "text": "person"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mask"] == "MASK"
    # remote [x,y,w,h] -> app [x1,y1,x2,y2]
    assert body["bbox"] == [10, 20, 40, 60]
    url, kw = _FakeAsyncClient.calls[-1]
    assert url == "http://seneca.test:8188/v1/images/mask"
    assert kw["data"]["prompt"] == "person"


def test_mask_picks_top_score_not_first(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.response = _FakeResponse(
        {"masks": [
            {"b64_json": "LOW", "bbox": [0, 0, 1, 1], "score": 0.2},
            {"b64_json": "HIGH", "bbox": [0, 0, 2, 2], "score": 0.95},
            {"b64_json": "MID", "bbox": [0, 0, 3, 3], "score": 0.5},
        ], "model": "sam+grounding"}
    )
    resp = _client().post("/api/image/mask", json={"image": "AAAA", "text": "person"})
    assert resp.status_code == 200
    assert resp.json()["mask"] == "HIGH"


def test_mask_box_is_sent_as_xywh(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse({"masks": [{"b64_json": "M", "bbox": [0, 0, 1, 1], "score": 1.0}]})
    resp = _client().post("/api/image/mask", json={"image": "AAAA", "box": [10, 20, 40, 60]})
    assert resp.status_code == 200
    _, kw = _FakeAsyncClient.calls[-1]
    assert kw["data"]["box"] == "10,20,30,40"


def test_mask_remote_empty_masks_is_200_nothing_selected(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.response = _FakeResponse({"masks": [], "model": "sam+grounding"})
    resp = _client().post("/api/image/mask", json={"image": "AAAA", "text": "person"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mask"] is None
    assert body["masks"] == []
    assert "Nothing selected" in body["error"]


def test_remote_proxy_surfaces_error_status(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_IMAGE_OPS_BASE", "http://seneca.test:8188")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.response = _FakeResponse({}, status_code=500)
    resp = _client().post("/api/image/remove-bg", json={"image": "AAAA"})
    assert resp.status_code == 503
    assert "temporarily unavailable" in resp.json().get("detail", "")
