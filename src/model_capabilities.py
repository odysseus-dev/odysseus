"""Shared model capability classification helpers."""

import re
from urllib.parse import urlparse


_RESPONSES_REQUIRED_MODEL_RE = re.compile(
    r"^(?:"
    r"o[13]-pro(?:-\d{4}-\d{2}-\d{2})?|"
    r"gpt-5(?:\.\d+)?-pro(?:-\d{4}-\d{2}-\d{2})?|"
    r"gpt-5(?:\.\d+)?-codex(?:-max)?(?:-\d{4}-\d{2}-\d{2})?"
    r")$",
    re.IGNORECASE,
)


def _host_match(url: str, *domains: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def is_openai_responses_required_model(model: str) -> bool:
    model_id = str(model or "").strip().split("/")[-1]
    return bool(model_id and _RESPONSES_REQUIRED_MODEL_RE.match(model_id))


def requires_openai_responses_api(url: str, model: str) -> bool:
    return _host_match(url, "openai.com") and is_openai_responses_required_model(model)


__all__ = [
    "is_openai_responses_required_model",
    "requires_openai_responses_api",
]
