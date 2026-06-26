"""Shared helpers used across tool implementation domains.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Domain modules under src/tools/ import from here.
"""
from typing import Dict, Optional

from core.constants import internal_api_base
from src.tool_utils import _parse_tool_args  # noqa: F401 — single source of the tool-arg parser; tool_utils is a leaf module (imports nothing from src)


# In-process loopback base for agent tools that call Odysseus's own API
# (cookbook state, model serve, gallery, email, calendar). We ride the
# per-process internal token so require_admin lets us through. See
# core/middleware.py. Resolution (override / APP_PORT / 7000) lives in
# core.constants.internal_api_base().
_INTERNAL_BASE = internal_api_base()


def _internal_headers(owner: Optional[str] = None) -> Dict[str, str]:
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner
    return headers
