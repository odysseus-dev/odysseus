import pytest
import httpx
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.cookbook_routes import setup_cookbook_routes
from src.auth_helpers import require_user

app = FastAPI()
router = setup_cookbook_routes()
app.include_router(router)
app.dependency_overrides[require_user] = lambda: "test_user"

client = TestClient(app)


def test_hf_gguf_files_success():
    mock_resp = httpx.Response(
        200,
        json={
            "siblings": [
                {"rfilename": "model-q4_k_m.gguf"},
                {"rfilename": "model-q8_0.gguf"},
                {"rfilename": "README.md"},
            ]
        },
        request=httpx.Request("GET", "https://huggingface.co/api/models/TheBloke/Llama-2-7B-GGUF"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        response = client.get("/api/cookbook/hf-gguf-files?repo_id=TheBloke/Llama-2-7B-GGUF")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["repo_id"] == "TheBloke/Llama-2-7B-GGUF"
        assert data["files"] == ["model-q4_k_m.gguf", "model-q8_0.gguf"]


def test_hf_gguf_files_http_error():
    mock_resp = httpx.Response(
        404,
        json={"error": "Model not found"},
        request=httpx.Request("GET", "https://huggingface.co/api/models/invalid/repo"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        response = client.get("/api/cookbook/hf-gguf-files?repo_id=invalid/repo")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "HF API HTTP 404" in data["error"]


def test_hf_gguf_files_exception_handling():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("Network unreachable")):
        response = client.get("/api/cookbook/hf-gguf-files?repo_id=TheBloke/Llama-2-7B-GGUF")
        # Should gracefully return JSON error payload, not raise NameError / 500
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "HF API request failed"
