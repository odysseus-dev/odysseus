# tests/test_sanitizer.py
import pytest
import json
import httpx
import os
from unittest.mock import patch, MagicMock
from src.sanitizer import sanitize_messages, sanitize_messages_sync

# Mock messages
MESSAGES = [
    {"role": "system", "content": "Keep my secrets."},
    {"role": "user", "content": "My email is john.doe@example.com and my phone is 555-1234."},
    {"role": "assistant", "content": [{"type": "text", "text": "I will redact John Doe's info."}]}
]

@pytest.mark.asyncio
async def test_sanitizer_disabled():
    """Test that messages are not changed when sanitizer is disabled."""
    with patch.dict(os.environ, {"PII_SANITIZATION_ENABLED": "False", "PII_SANITIZER_URL": "http://mock-sanitizer/sanitize"}):
        result = await sanitize_messages(MESSAGES)
        assert result == MESSAGES

@pytest.mark.asyncio
async def test_sanitizer_no_url():
    """Test that messages are not changed when URL is missing."""
    with patch.dict(os.environ, {"PII_SANITIZATION_ENABLED": "True", "PII_SANITIZER_URL": ""}):
        result = await sanitize_messages(MESSAGES)
        assert result == MESSAGES

@pytest.mark.asyncio
async def test_sanitizer_success():
    """Test successful sanitization."""
    env = {
        "PII_SANITIZATION_ENABLED": "True",
        "PII_SANITIZER_URL": "http://mock-sanitizer/sanitize"
    }
    
    # Mock responses for different segments
    mock_responses = [
        {"sanitized_text": "Keep my secrets."},
        {"sanitized_text": "My email is [REDACTED] and my phone is [REDACTED]."},
        {"sanitized_text": "I will redact [REDACTED]'s info."}
    ]
    
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json=None, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_responses.pop(0) if mock_responses else {"text": "default"}
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

    with patch.dict(os.environ, env):
        with patch("httpx.AsyncClient", side_effect=MockClient):
            result = await sanitize_messages(MESSAGES)
            
            assert result[0]["content"] == "Keep my secrets."
            assert "[REDACTED]" in result[1]["content"]
            assert "john.doe@example.com" not in result[1]["content"]
            assert "[REDACTED]" in result[2]["content"][0]["text"]

@pytest.mark.asyncio
async def test_sanitizer_timeout_continue():
    """Test policy='continue' on timeout."""
    env = {
        "PII_SANITIZATION_ENABLED": "True",
        "PII_SANITIZER_URL": "http://mock-sanitizer/sanitize",
        "PII_SANITIZATION_POLICY": "continue"
    }
    
    with patch.dict(os.environ, env):
        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Timeout")):
            result = await sanitize_messages(MESSAGES)
            assert result == MESSAGES # Should return original messages

@pytest.mark.asyncio
async def test_sanitizer_failure_block():
    """Test policy='block' on failure."""
    env = {
        "PII_SANITIZATION_ENABLED": "True",
        "PII_SANITIZER_URL": "http://mock-sanitizer/sanitize",
        "PII_SANITIZATION_POLICY": "block"
    }
    
    with patch.dict(os.environ, env):
        with patch("httpx.AsyncClient.post", side_effect=httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock())):
            with pytest.raises(RuntimeError) as excinfo:
                await sanitize_messages(MESSAGES)
            assert "Request blocked" in str(excinfo.value)

def test_sanitizer_sync_disabled():
    """Test sync wrapper when disabled."""
    with patch.dict(os.environ, {"PII_SANITIZATION_ENABLED": "False"}):
        result = sanitize_messages_sync(MESSAGES)
        assert result == MESSAGES
