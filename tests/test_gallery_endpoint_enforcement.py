import asyncio
import http.server
import json
import threading
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.exceptions import HTTPException

from core.database import SessionLocal, ModelEndpoint
from routes.gallery.gallery_routes import setup_gallery_routes, _visible_image_endpoint_for_base


class TrackingPollHandler(http.server.BaseHTTPRequestHandler):
    requests_received: List[str] = []

    def do_GET(self):
        TrackingPollHandler.requests_received.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"available": true, "progress": 50}')

    def do_POST(self):
        TrackingPollHandler.requests_received.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"images": ["data:image/png;base64,AAAA"]}')

    def log_message(self, format, *args):
        pass


@pytest.fixture
def mock_provider_listener():
    TrackingPollHandler.requests_received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), TrackingPollHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def create_gallery_request(method: str, path: str, query_params: dict = None, body: dict = None, user: str = "alice"):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": "&".join(f"{k}={v}" for k, v in (query_params or {}).items()).encode("utf-8"),
        "app": MagicMock(),
    }
    req = Request(scope)

    async def mock_json():
        return body or {}

    req.json = mock_json
    return req


def test_edit_progress_disabled_endpoint_causes_no_outbound_polling(mock_provider_listener):
    """GET /api/image/edit-progress with disabled endpoint causes zero outbound requests."""
    db = SessionLocal()
    ep_id = "ep-test-disabled-progress"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Disabled Endpoint",
            base_url=mock_provider_listener,
            model_type="image",
            is_enabled=False,
            owner="alice",
            cached_models=json.dumps(["test-diff"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "req-123", "endpoint_id": ep_id},
            user="alice",
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            resp = asyncio.run(route.endpoint(req))

        assert isinstance(resp, dict)
        assert resp["available"] is False
        assert resp.get("reason") == "endpoint not found or disabled"
        # Zero outbound polling to the provider!
        assert len(TrackingPollHandler.requests_received) == 0
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_edit_progress_foreign_owned_endpoint_causes_no_outbound_polling(mock_provider_listener):
    """GET /api/image/edit-progress with foreign-owned endpoint causes zero outbound requests."""
    db = SessionLocal()
    ep_id = "ep-test-foreign-progress"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Victim Endpoint",
            base_url=mock_provider_listener,
            model_type="image",
            is_enabled=True,
            owner="victim_user",
            cached_models=json.dumps(["test-diff"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        # Attacker user 'bob' requests victim's endpoint
        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "req-456", "endpoint_id": ep_id},
            user="bob",
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="bob"):
            resp = asyncio.run(route.endpoint(req))

        assert isinstance(resp, dict)
        assert resp["available"] is False
        assert resp.get("reason") == "endpoint not found or disabled"
        assert len(TrackingPollHandler.requests_received) == 0
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_edit_progress_rejects_chat_only_endpoint_causes_no_polling(mock_provider_listener):
    """GET /api/image/edit-progress rejects chat-only endpoint ID with zero outbound requests."""
    db = SessionLocal()
    ep_id = "ep-test-chat-progress"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Chat Endpoint",
            base_url=mock_provider_listener,
            model_type="llm",  # chat only!
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["llama-3-8b"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "req-789", "endpoint_id": ep_id},
            user="alice",
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            resp = asyncio.run(route.endpoint(req))

        assert isinstance(resp, dict)
        assert resp["available"] is False
        assert resp.get("reason") == "endpoint not found or disabled"
        # Zero outbound polling to the chat provider!
        assert len(TrackingPollHandler.requests_received) == 0
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_edit_progress_blocks_private_ip_when_policy_enabled(mock_provider_listener, monkeypatch):
    """GET /api/image/edit-progress enforces IMAGE_BLOCK_PRIVATE_IPS policy."""
    monkeypatch.setenv("IMAGE_BLOCK_PRIVATE_IPS", "true")
    db = SessionLocal()
    ep_id = "ep-test-private-progress"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Private Image Endpoint",
            base_url=mock_provider_listener,  # 127.0.0.1 loopback
            model_type="image",
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["sd-v1"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "req-priv", "endpoint_id": ep_id},
            user="alice",
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            resp = asyncio.run(route.endpoint(req))

        assert isinstance(resp, dict)
        assert resp["available"] is False
        assert resp.get("reason") == "endpoint URL rejected"
        # Zero outbound polling because private IP was blocked by policy!
        assert len(TrackingPollHandler.requests_received) == 0
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_edit_progress_rejection_does_not_expose_exception_details_or_secrets(mock_provider_listener, caplog):
    """CodeQL #1203: Outbound URL rejection must never leak exception strings, tokens or internal destinations into responses or logs."""
    db = SessionLocal()
    ep_id = "ep-test-sensitive-exception"
    synthetic_secret = "synthetic-resolver-secret-marker-42"
    synthetic_token = "synthetic-token-xyz-999"
    synthetic_query_secret = "synthetic-query-key-777"
    internal_host = "cluster-internal-worker.prod.corp"
    internal_ip = "10.128.0.42"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Internal Worker Endpoint",
            base_url=f"http://user:{synthetic_secret}@{internal_host}:8000?api_key={synthetic_query_secret}",
            model_type="image",
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["sd-v1"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "req-safe-test", "endpoint_id": ep_id},
            user="alice",
        )

        synthetic_rejection_reason = (
            f"host does not resolve: [Errno -2] Name or service not known: "
            f"http://custom_user:custom_pass@{internal_host} -> {internal_ip} with token={synthetic_token}"
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            with patch("src.url_safety.check_outbound_url", return_value=(False, synthetic_rejection_reason)):
                TrackingPollHandler.requests_received = []
                resp = asyncio.run(route.endpoint(req))

        # 1. Operation is rejected with a stable generic reason
        assert isinstance(resp, dict)
        assert resp["available"] is False
        assert resp.get("reason") == "endpoint URL rejected"

        # 2. No synthetic secrets or internal host details exposed to API client
        resp_str = json.dumps(resp)
        assert synthetic_secret not in resp_str
        assert synthetic_token not in resp_str
        assert synthetic_query_secret not in resp_str
        assert internal_host not in resp_str
        assert internal_ip not in resp_str
        assert "Errno" not in resp_str
        assert "resolve" not in resp_str
        assert "custom_pass" not in resp_str

        # 3. No outbound HTTP request occurred
        assert len(TrackingPollHandler.requests_received) == 0

        # 4. Server-side log uses a stable message without interpolating untrusted exception or destination strings
        log_text = " ".join(rec.message for rec in caplog.records)
        assert "edit-progress endpoint rejected: outbound URL policy check failed" in log_text
        assert synthetic_secret not in log_text
        assert synthetic_token not in log_text
        assert synthetic_query_secret not in log_text
        assert internal_host not in log_text
        assert internal_ip not in log_text
        assert "Errno" not in log_text
        assert "custom_pass" not in log_text
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_edit_progress_valid_endpoint_succeeds(mock_provider_listener, monkeypatch):
    """Valid authorized endpoint allows progress polling and returns provider payload."""
    monkeypatch.setenv("IMAGE_BLOCK_PRIVATE_IPS", "false")
    db = SessionLocal()
    ep_id = "ep-test-valid-progress"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Valid SD Provider",
            base_url=mock_provider_listener,
            model_type="image",
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["sd-v1"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/edit-progress")

        req = create_gallery_request(
            "GET",
            "/api/image/edit-progress",
            query_params={"request_id": "valid-progress-req", "endpoint_id": ep_id},
            user="alice",
        )

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            TrackingPollHandler.requests_received = []
            resp = asyncio.run(route.endpoint(req))

        assert isinstance(resp, dict)
        assert resp["available"] is True
        assert resp.get("progress") == 50
        # Exactly one outbound request to the provider progress route
        assert len(TrackingPollHandler.requests_received) == 1
        assert TrackingPollHandler.requests_received[0].endswith("/images/progress/valid-progress-req")
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_harmonize_rejects_chat_only_endpoint(mock_provider_listener):
    """POST /api/image/harmonize rejects a chat-only endpoint that has no image capability."""
    db = SessionLocal()
    ep_id = "ep-test-chat-only"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Chat Only Llama",
            base_url=mock_provider_listener,
            model_type="llm",  # chat only
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["meta-llama/Llama-3-8B-Instruct", "mistral-7b"]),
        )
        db.merge(ep)
        db.commit()

        router = setup_gallery_routes()
        route = next(r for r in router.routes if r.path == "/api/image/harmonize")

        payload = {
            "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "_endpoint": ep_id,
            "_model": "meta-llama/Llama-3-8B-Instruct",
        }
        req = create_gallery_request("POST", "/api/image/harmonize", body=payload, user="alice")

        with patch("routes.gallery.gallery_routes.require_privilege", return_value="alice"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(route.endpoint(req))
            assert exc_info.value.status_code == 403
            assert "Choose a registered image endpoint" in exc_info.value.detail

        # Zero outbound requests to chat provider
        assert len(TrackingPollHandler.requests_received) == 0
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_harmonize_accepts_eligible_image_model_on_mixed_endpoint(mock_provider_listener):
    """POST /api/image/harmonize accepts a mixed endpoint when an image-capable model is selected."""
    db = SessionLocal()
    ep_id = "ep-test-mixed-endpoint"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Mixed Gateway",
            base_url=mock_provider_listener,
            model_type="llm",  # mixed gateway
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["gpt-4o", "qwen-image-edit"]),
        )
        db.merge(ep)
        db.commit()

        # Testing capability helper directly with image model
        ep_resolved = _visible_image_endpoint_for_base(db, ep_id, "alice", "qwen-image-edit")
        assert ep_resolved is not None
        assert ep_resolved.id == ep_id

        # Testing with chat model fails
        ep_chat = _visible_image_endpoint_for_base(db, ep_id, "alice", "gpt-4o")
        assert ep_chat is None
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()


def test_harmonize_preserves_legitimate_image_capable_endpoint(mock_provider_listener):
    """Legitimate image endpoint (model_type == 'image') resolves and works properly."""
    db = SessionLocal()
    ep_id = "ep-test-sd-diffusion"
    try:
        ep = ModelEndpoint(
            id=ep_id,
            name="Forge SD WebUI",
            base_url=mock_provider_listener,
            model_type="image",
            is_enabled=True,
            owner="alice",
            cached_models=json.dumps(["sd_xl_base_1.0.safetensors"]),
        )
        db.merge(ep)
        db.commit()

        ep_resolved = _visible_image_endpoint_for_base(db, ep_id, "alice", "sd_xl_base_1.0.safetensors")
        assert ep_resolved is not None
        assert ep_resolved.id == ep_id
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).delete()
        db.commit()
        db.close()
