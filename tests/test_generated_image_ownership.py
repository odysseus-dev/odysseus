import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import serve_generated_image
from src.generated_images import (
    GENERATED_IMAGE_HEADERS,
    register_generated_image,
)


class DummyState:
    def __init__(self, current_user=None, api_token=False, api_token_owner=None):
        self.current_user = current_user
        self.api_token = api_token
        self.api_token_owner = api_token_owner


def _make_request(current_user=None, api_token=False, api_token_owner=None):
    req = MagicMock(spec=Request)
    req.state = DummyState(current_user, api_token, api_token_owner)
    return req


@pytest.fixture
def sample_image(tmp_path, monkeypatch):
    from src import generated_images
    image_dir = tmp_path / "generated_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    filename = "0123456789ab.png"
    img_file = image_dir / filename
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", image_dir)
    return filename


@pytest.mark.asyncio
async def test_authorized_cookie_session_owner(sample_image):
    """Cookie session owner receives 200 for their owned image."""
    req = _make_request(current_user="alice")
    mock_row = MagicMock()
    mock_row.owner = "alice"
    mock_row.filename = sample_image

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_row
        mock_sl.return_value = mock_db

        resp = await serve_generated_image(sample_image, req)
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "private, no-cache, must-revalidate"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_authorized_api_token_owner(sample_image):
    """API token request matching image owner receives 200."""
    req = _make_request(current_user="api", api_token=True, api_token_owner="alice")
    mock_row = MagicMock()
    mock_row.owner = "alice"
    mock_row.filename = sample_image

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_row
        mock_sl.return_value = mock_db

        resp = await serve_generated_image(sample_image, req)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cross_user_denial_returns_404(sample_image):
    """Bob attempting to access Alice's image receives 404 (preventing existence leak)."""
    req = _make_request(current_user="bob")
    mock_row = MagicMock()
    mock_row.owner = "alice"
    mock_row.filename = sample_image

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_row
        mock_sl.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Image not found"


@pytest.mark.asyncio
async def test_cross_user_api_token_denial_returns_404(sample_image):
    """Bob's API token attempting to access Alice's image receives 404."""
    req = _make_request(current_user="api", api_token=True, api_token_owner="bob")
    mock_row = MagicMock()
    mock_row.owner = "alice"
    mock_row.filename = sample_image

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_row
        mock_sl.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(sample_image):
    """Unauthenticated caller (no user identity) is rejected with 401 when auth is enabled."""
    req = _make_request(current_user=None)

    with patch("src.auth_helpers.auth_disabled", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Not authenticated"


@pytest.mark.asyncio
async def test_missing_gallery_row_fails_closed_404(sample_image):
    """Image on disk with no gallery row, no registry, and no chat reference returns 404."""
    req = _make_request(current_user="bob")

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        # No gallery row
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # No chat message reference
        mock_db.query.return_value.join.return_value.filter.return_value.first.return_value = None
        mock_sl.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Image not found"


@pytest.mark.asyncio
async def test_null_owner_metadata_fails_closed_404(sample_image):
    """Gallery row with owner=None and no session link returns 404."""
    req = _make_request(current_user="bob")
    mock_row = MagicMock()
    mock_row.owner = None
    mock_row.session_id = None
    mock_row.filename = sample_image

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_row
        mock_sl.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_session_linked_null_owner_authorized(sample_image):
    """Gallery row with owner=None resolves owner through session_id."""
    req_alice = _make_request(current_user="alice")
    req_bob = _make_request(current_user="bob")

    mock_row = MagicMock()
    mock_row.owner = None
    mock_row.session_id = "sess-123"
    mock_row.filename = sample_image

    mock_sess = MagicMock()
    mock_sess.owner = "alice"

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_row, mock_sess,  # for Alice
            mock_row, mock_sess,  # for Bob
        ]
        mock_sl.return_value = mock_db

        # Alice is the session owner -> 200
        resp = await serve_generated_image(sample_image, req_alice)
        assert resp.status_code == 200

        # Bob is not the session owner -> 404
        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req_bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_chat_referenced_rowless_image_authorized(sample_image):
    """Rowless image referenced in a chat session owned by user is authorized."""
    req_alice = _make_request(current_user="alice")
    req_bob = _make_request(current_user="bob")

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        # Row is None
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # Alice's chat query returns a match, Bob's does not
        mock_db.query.return_value.join.return_value.filter.return_value.first.side_effect = [
            MagicMock(),  # Alice has a match
            None,         # Bob has no match
        ]
        mock_sl.return_value = mock_db

        resp = await serve_generated_image(sample_image, req_alice)
        assert resp.status_code == 200

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req_bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_freshly_generated_registry_preserves_access(sample_image):
    """Freshly generated image registered to Alice is accessible before DB commit; Bob is denied."""
    req_alice = _make_request(current_user="alice")
    req_bob = _make_request(current_user="bob")

    fresh_filename = "fedcba987654.png"
    from src import generated_images
    img_file = generated_images.GENERATED_IMAGE_DIR / fresh_filename
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")

    register_generated_image(fresh_filename, "alice")

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_sl.side_effect = AssertionError("DB should not be reached for registered owner")

        # Alice retrieves without hitting DB
        resp = await serve_generated_image(fresh_filename, req_alice)
        assert resp.status_code == 200

        # Bob is denied with 404
        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(fresh_filename, req_bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_database_failure_fails_closed_503(sample_image):
    """Database exception during ownership check raises HTTP 503."""
    req = _make_request(current_user="alice")

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("SQLite database locked or corrupted")
        mock_sl.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image(sample_image, req)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Ownership verification unavailable"


@pytest.mark.asyncio
async def test_no_login_compatibility_auth_disabled(sample_image):
    """When AUTH_ENABLED=false, existing images are served with 200, missing images return 404."""
    req = _make_request(current_user=None)

    with patch("src.auth_helpers.auth_disabled", return_value=True):
        resp = await serve_generated_image(sample_image, req)
        assert resp.status_code == 200

        with pytest.raises(HTTPException) as exc_info:
            await serve_generated_image("aaaaaaaaaaaa.png", req)
        assert exc_info.value.status_code == 404


def test_cache_headers_private_no_cache():
    """Verify generated image response headers use private, no-cache directives."""
    assert GENERATED_IMAGE_HEADERS["Cache-Control"] == "private, no-cache, must-revalidate"
    assert GENERATED_IMAGE_HEADERS["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_existence_oracle_eliminated_during_outage(sample_image):
    """During a DB outage, both existing and non-existent files return 503 without revealing existence."""
    req = _make_request(current_user="alice")

    with patch("src.auth_helpers.auth_disabled", return_value=False), \
         patch("core.database.SessionLocal") as mock_sl:
        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("DB down")
        mock_sl.return_value = mock_db

        # Existing file -> 503
        with pytest.raises(HTTPException) as exc_existing:
            await serve_generated_image(sample_image, req)
        assert exc_existing.value.status_code == 503

        # Non-existent file -> 503 (NOT 404)
        with pytest.raises(HTTPException) as exc_missing:
            await serve_generated_image("111122223333.png", req)
        assert exc_missing.value.status_code == 503


@pytest.mark.asyncio
async def test_mcp_image_generation_passes_owner_arg():
    """_call_mcp_tool passes _odysseus_owner to MCP arguments for generate_image."""
    from src import tool_execution

    mock_mcp = MagicMock()
    mock_mcp.call_tool = MagicMock()

    async def fake_call(qualified, args):
        return {"stdout": "Generated image for: cat\nhttps://host/api/generated-image/1234567890ab.png", "exit_code": 0}

    mock_mcp.call_tool.side_effect = fake_call

    with patch.object(tool_execution, "get_mcp_manager", return_value=mock_mcp), \
         patch.object(tool_execution, "active_backend_operation", return_value=None):
        res = await tool_execution._call_mcp_tool("generate_image", "cat", owner="alice")
        assert res.get("image_url") == "https://host/api/generated-image/1234567890ab.png"
        call_args = mock_mcp.call_tool.call_args[0]
        assert call_args[1].get("_odysseus_owner") == "alice"


@pytest.mark.asyncio
async def test_mcp_fallback_propagates_owner():
    """When MCP manager is unavailable, _direct_fallback receives owner."""
    from src import tool_execution

    with patch.object(tool_execution, "get_mcp_manager", return_value=None), \
         patch.object(tool_execution, "active_backend_operation", return_value=None), \
         patch.object(tool_execution, "_direct_fallback", return_value={"output": "ok", "exit_code": 0}) as mock_fallback:
        res = await tool_execution._call_mcp_tool("web_search", "query", owner="alice")
        assert res == {"output": "ok", "exit_code": 0}
        mock_fallback.assert_called_once_with("web_search", "query", progress_cb=None, owner="alice")


@pytest.mark.asyncio
async def test_trusted_owner_overrides_forged_owner_in_tool_args():
    """Trusted owner overrides any forged _odysseus_owner in tool block content."""
    import json
    from src import tool_execution

    mock_mcp = MagicMock()
    mock_mcp.call_tool = MagicMock()

    async def fake_call(qualified, args):
        return {"stdout": "Generated image for: cat\n/api/generated-image/1234567890ab.png", "exit_code": 0}

    mock_mcp.call_tool.side_effect = fake_call
    forged_content = json.dumps({"prompt": "cat", "_odysseus_owner": "bob"})

    with patch.object(tool_execution, "get_mcp_manager", return_value=mock_mcp), \
         patch.object(tool_execution, "active_backend_operation", return_value=None):
        await tool_execution._call_mcp_tool("generate_image", forged_content, owner="alice")
        call_args = mock_mcp.call_tool.call_args[0]
        assert call_args[1].get("_odysseus_owner") == "alice"


@pytest.mark.asyncio
async def test_missing_or_empty_owner_removes_forged_owner_claim():
    """When caller has no trusted owner (None or empty), forged _odysseus_owner is completely removed."""
    import json
    from src import tool_execution

    mock_mcp = MagicMock()
    mock_mcp.call_tool = MagicMock()

    async def fake_call(qualified, args):
        return {"stdout": "Generated image for: cat\n/api/generated-image/1234567890ab.png", "exit_code": 0}

    mock_mcp.call_tool.side_effect = fake_call
    forged_content = json.dumps({"prompt": "cat", "_odysseus_owner": "bob"})

    with patch.object(tool_execution, "get_mcp_manager", return_value=mock_mcp), \
         patch.object(tool_execution, "active_backend_operation", return_value=None):
        # Case 1: owner=None
        await tool_execution._call_mcp_tool("generate_image", forged_content, owner=None)
        call_args = mock_mcp.call_tool.call_args[0]
        assert "_odysseus_owner" not in call_args[1]

        # Case 2: owner=""
        await tool_execution._call_mcp_tool("generate_image", forged_content, owner="")
        call_args = mock_mcp.call_tool.call_args[0]
        assert "_odysseus_owner" not in call_args[1]


@pytest.mark.asyncio
async def test_qualified_mcp_image_gen_sanitizes_forged_owner():
    """Qualified mcp__image_gen__generate_image tool dispatches with sanitized owner."""
    import json
    from unittest.mock import AsyncMock
    from src import tool_execution
    from src.tool_types import ToolBlock

    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value={"stdout": "ok", "exit_code": 0})
    forged_content = json.dumps({"prompt": "cat", "_odysseus_owner": "bob"})

    with patch.object(tool_execution, "get_mcp_manager", return_value=mock_mcp), \
         patch.object(tool_execution, "_owner_is_admin", return_value=True):
        # With trusted owner 'alice' -> sets 'alice'
        await tool_execution._execute_tool_block_impl(
            ToolBlock("mcp__image_gen__generate_image", forged_content),
            owner="alice",
        )
        call_args = mock_mcp.call_tool.call_args[0]
        assert call_args[1].get("_odysseus_owner") == "alice"

        # Without owner -> removes forged owner
        await tool_execution._execute_tool_block_impl(
            ToolBlock("mcp__image_gen__generate_image", forged_content),
            owner=None,
        )
        call_args = mock_mcp.call_tool.call_args[0]
        assert "_odysseus_owner" not in call_args[1]


@pytest.mark.asyncio
async def test_mcp_disconnected_fallback_does_not_resurrect_forged_owner():
    """When MCP is disconnected, direct fallback receives verified owner context, not forged arguments."""
    import json
    from src import tool_execution

    mock_mcp = MagicMock()
    mock_mcp.call_tool = MagicMock()

    async def fake_call(qualified, args):
        return {"error": "Server not connected", "exit_code": 1}

    mock_mcp.call_tool.side_effect = fake_call
    forged_content = json.dumps({"query": "python", "_odysseus_owner": "bob"})

    with patch.object(tool_execution, "get_mcp_manager", return_value=mock_mcp), \
         patch.object(tool_execution, "active_backend_operation", return_value=None), \
         patch.object(tool_execution, "_direct_fallback", return_value={"output": "ok", "exit_code": 0}) as mock_fallback:
        # Trusted owner=None: mock_fallback receives owner=None
        await tool_execution._call_mcp_tool("web_search", forged_content, owner=None)
        mock_fallback.assert_called_with("web_search", forged_content, progress_cb=None, owner=None)

        # Trusted owner="alice": mock_fallback receives owner="alice"
        await tool_execution._call_mcp_tool("web_search", forged_content, owner="alice")
        mock_fallback.assert_called_with("web_search", forged_content, progress_cb=None, owner="alice")


@pytest.mark.asyncio
async def test_image_gen_server_owner_sanitization():
    """mcp_servers.image_gen_server sanitizes whitespace, empty, and None owner claims."""
    from unittest.mock import AsyncMock
    from mcp_servers import image_gen_server

    with patch("src.database.SessionLocal") as mock_sl, \
         patch("httpx.AsyncClient") as mock_http, \
         patch("src.ai_interaction._resolve_model", return_value=("https://host/v1/chat/completions", "gpt-image-1", {})), \
         patch("src.settings.get_setting", return_value="dummy_key"), \
         patch("src.settings.load_settings", return_value={"image_model": "gpt-image-1"}), \
         patch("pathlib.Path.write_bytes"):
        mock_db = MagicMock()
        mock_sl.return_value = mock_db

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"b64_json": "aGVsbG8="}]
        }
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_http.return_value.__aenter__.return_value = mock_client

        # Call with whitespace owner: "   " -> owner becomes None
        await image_gen_server.call_tool(
            "generate_image",
            {"prompt": "cat", "_odysseus_owner": "   "},
        )
        added = mock_db.add.call_args[0][0]
        assert added.owner is None

        # Call with valid owner: "alice" -> owner becomes "alice"
        await image_gen_server.call_tool(
            "generate_image",
            {"prompt": "cat", "_odysseus_owner": "alice"},
        )
        added = mock_db.add.call_args[0][0]
        assert added.owner == "alice"
