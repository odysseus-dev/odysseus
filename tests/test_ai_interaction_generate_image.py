"""
Tests for do_generate_image() and its extracted sub-functions.

Uses mocks for httpx, _resolve_model, and database to stay unit-level.
Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict


async def _gen(content: str, session_id: str = "s1", owner: str = "u1") -> Dict:
    from src.ai_interaction import do_generate_image
    return await do_generate_image(content, session_id=session_id, owner=owner)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestGenerateImageInput:

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_error(self) -> None:
        result = await _gen("")
        assert "error" in result
        assert "prompt" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_prompt_returns_error(self) -> None:
        result = await _gen("   \n   ")
        assert "error" in result


# ---------------------------------------------------------------------------
# Image model type detection helpers
# ---------------------------------------------------------------------------

class TestImageModelTypeDetection:

    def test_detect_gpt_image(self) -> None:
        from src.ai_interaction import _image_classify_model
        assert _image_classify_model("gpt-image-1")["is_gpt_image"] is True
        assert _image_classify_model("gpt-image-1.5")["is_gpt_image"] is True

    def test_detect_dalle(self) -> None:
        from src.ai_interaction import _image_classify_model
        assert _image_classify_model("dall-e-3")["is_dalle"] is True

    def test_detect_local_diffusion(self) -> None:
        from src.ai_interaction import _image_classify_model
        result = _image_classify_model("stable-diffusion-xl")
        assert result["is_local_diffusion"] is True
        assert result["is_gpt_image"] is False
        assert result["is_dalle"] is False


# ---------------------------------------------------------------------------
# Size validation helpers
# ---------------------------------------------------------------------------

class TestImageSizeValidation:

    def test_gpt_image_valid_size_unchanged(self) -> None:
        from src.ai_interaction import _image_clamp_size
        assert _image_clamp_size("1024x1024", is_gpt_image=True, is_dalle=False) == "1024x1024"

    def test_gpt_image_invalid_size_clamped(self) -> None:
        from src.ai_interaction import _image_clamp_size
        assert _image_clamp_size("9999x9999", is_gpt_image=True, is_dalle=False) == "1024x1024"

    def test_dalle_valid_size_unchanged(self) -> None:
        from src.ai_interaction import _image_clamp_size
        assert _image_clamp_size("1024x1792", is_gpt_image=False, is_dalle=True) == "1024x1792"

    def test_dalle_invalid_size_clamped(self) -> None:
        from src.ai_interaction import _image_clamp_size
        assert _image_clamp_size("512x512", is_gpt_image=False, is_dalle=True) == "1024x1024"

    def test_local_diffusion_any_size_accepted(self) -> None:
        from src.ai_interaction import _image_clamp_size
        assert _image_clamp_size("512x512", is_gpt_image=False, is_dalle=False) == "512x512"
        assert _image_clamp_size("768x1152", is_gpt_image=False, is_dalle=False) == "768x1152"


# ---------------------------------------------------------------------------
# Payload building helper
# ---------------------------------------------------------------------------

class TestImagePayloadBuilding:

    def test_gpt_image_includes_quality(self) -> None:
        from src.ai_interaction import _image_build_payload
        payload = _image_build_payload("gpt-image-1", "a cat", "1024x1024", "high",
                                       is_gpt_image=True, is_dalle=False, is_local_diffusion=False)
        assert payload["quality"] == "high"

    def test_dalle_excludes_quality(self) -> None:
        from src.ai_interaction import _image_build_payload
        payload = _image_build_payload("dall-e-3", "a cat", "1024x1024", "high",
                                       is_gpt_image=False, is_dalle=True, is_local_diffusion=False)
        assert "quality" not in payload

    def test_local_diffusion_includes_quality(self) -> None:
        from src.ai_interaction import _image_build_payload
        payload = _image_build_payload("sd-xl", "a cat", "512x512", "low",
                                       is_gpt_image=False, is_dalle=False, is_local_diffusion=True)
        assert payload["quality"] == "low"

    def test_invalid_quality_defaults_to_medium(self) -> None:
        from src.ai_interaction import _image_build_payload
        payload = _image_build_payload("gpt-image-1", "a cat", "1024x1024", "ultra",
                                       is_gpt_image=True, is_dalle=False, is_local_diffusion=False)
        assert payload["quality"] == "medium"

    def test_payload_contains_required_fields(self) -> None:
        from src.ai_interaction import _image_build_payload
        payload = _image_build_payload("gpt-image-1", "a cat", "1024x1024", "medium",
                                       is_gpt_image=True, is_dalle=False, is_local_diffusion=False)
        assert payload["model"] == "gpt-image-1"
        assert payload["prompt"] == "a cat"
        assert payload["n"] == 1
        assert payload["size"] == "1024x1024"


# ---------------------------------------------------------------------------
# Images URL derivation
# ---------------------------------------------------------------------------

class TestImageUrlDerivation:

    def test_derives_from_chat_completions_url(self) -> None:
        from src.ai_interaction import _image_derive_generations_url
        url = _image_derive_generations_url("https://api.openai.com/v1/chat/completions")
        assert url == "https://api.openai.com/v1/images/generations"

    def test_derives_from_anthropic_messages_url(self) -> None:
        from src.ai_interaction import _image_derive_generations_url
        url = _image_derive_generations_url("https://api.anthropic.com/v1/messages")
        # /v1/messages is stripped, leaving base + /images/generations
        assert url.endswith("/images/generations")
        assert "anthropic.com" in url


# ---------------------------------------------------------------------------
# End-to-end with mocked HTTP
# ---------------------------------------------------------------------------

class TestGenerateImageEndToEnd:

    @pytest.mark.asyncio
    async def test_no_model_found_returns_error(self) -> None:
        with patch("src.ai_interaction._resolve_model", side_effect=ValueError("not found")), \
             patch("src.ai_interaction._image_auto_detect_model_spec", return_value=""):
            result = await _gen("A sunset over mountains")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_http_timeout_returns_error(self) -> None:
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"b64_json": ""}]}

        with patch("src.ai_interaction._resolve_model", return_value=("http://url", "gpt-image-1", {})), \
             patch("src.ai_interaction._image_auto_detect_model_spec", return_value="gpt-image-1"), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            mock_client_cls.return_value = mock_client

            result = await _gen("A sunset")
        assert "error" in result
        assert "timed out" in result["error"].lower() or "timeout" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_api_error_status_returns_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"
        mock_resp.json.side_effect = Exception("no json")

        with patch("src.ai_interaction._resolve_model", return_value=("http://url", "gpt-image-1", {})), \
             patch("src.ai_interaction._image_auto_detect_model_spec", return_value="gpt-image-1"), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await _gen("A sunset")
        assert "error" in result
        assert "400" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_data_list_returns_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        with patch("src.ai_interaction._resolve_model", return_value=("http://url", "gpt-image-1", {})), \
             patch("src.ai_interaction._image_auto_detect_model_spec", return_value="gpt-image-1"), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await _gen("A sunset")
        assert "error" in result
