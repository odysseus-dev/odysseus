"""Odysseus local web_extract backend (extract only)."""
from __future__ import annotations

from .provider import LocalExtractProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(LocalExtractProvider())
