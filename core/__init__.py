# core/__init__.py
"""
Chat Core — the essential chat experience.

This package contains only what's needed for:
- Streaming LLM responses
- Session management
- Model routing
- Authentication
"""

from src.llm_core import (
    llm_call,
    llm_call_async,
    stream_llm,
    list_model_ids,
    normalize_model_id,
    LLMConfig,
)
from .auth import AuthManager
from .middleware import SecurityHeadersMiddleware
from .exceptions import (
    SessionNotFoundError,
    InvalidFileUploadError,
    LLMServiceError,
    WebSearchError,
)
from .models import Session, ChatMessage
from .session_manager import SessionManager

__all__ = [
    # LLM
    "llm_call",
    "llm_call_async",
    "stream_llm",
    "list_model_ids",
    "normalize_model_id",
    "LLMConfig",
    # Auth
    "AuthManager",
    # Middleware
    "SecurityHeadersMiddleware",
    # Exceptions
    "SessionNotFoundError",
    "InvalidFileUploadError",
    "LLMServiceError",
    "WebSearchError",
    # Models
    "Session",
    "ChatMessage",
    "SessionManager",
]
