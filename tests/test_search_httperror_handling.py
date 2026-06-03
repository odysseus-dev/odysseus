"""Regression test for unhandled HTTPStatusError in fetch_webpage_content.

fetch_webpage_content() fetches a URL and calls response.raise_for_status().
httpx.HTTPStatusError (raised on 4xx/5xx responses) was not caught in the
except block — only httpx.RequestError and RateLimitError were handled.

This meant any URL returning a 403, 404, or similar would propagate the
exception up through build_context_preface() and crash the entire chat
request with a 500 Internal Server Error instead of gracefully skipping
the URL.

The fix adds an except httpx.HTTPStatusError block that returns _empty_result()
so the caller can continue normally.
"""
import pytest
import httpx
from unittest.mock import MagicMock, patch

from src.search.content import fetch_webpage_content

def test_fetch_webpage_content_handles_403_gracefully():
    """A 403 response should return an empty result, not raise."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403 Forbidden",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("src.search.content._get_public_url", return_value=mock_response):
        result = fetch_webpage_content("https://www.w3.org/2000/svg")

    assert result is not None, (
        "fetch_webpage_content raised instead of returning an empty result on a 403 response. HTTPStatusError was not caught."
    )
    assert result["success"] is False
    assert result["url"] == "https://www.w3.org/2000/svg"
    assert "error" in result

def test_fetch_webpage_content_handles_404_gracefully():
    """A 404 response should also return an empty result, not raise."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404 Not Found",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("src.search.content._get_public_url", return_value=mock_response):
        result = fetch_webpage_content("https://example.com/404maker")

    assert result is not None, (
        "fetch_webpage_content raised instead of returning an empty result on a 404 response. HTTPStatusError was not caught."
    )
    assert result["success"] is False