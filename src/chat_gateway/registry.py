"""Instantiate the enabled platform adapters from config."""

from __future__ import annotations

import logging
from typing import List

from .adapters import get_adapter_class
from .base import PlatformAdapter
from .config import GatewayConfig

logger = logging.getLogger("chat_gateway")


def build_adapters(cfg: GatewayConfig) -> List[PlatformAdapter]:
    adapters: List[PlatformAdapter] = []
    for pcfg in cfg.enabled_platforms():
        cls = get_adapter_class(pcfg.name)
        if cls is None:
            logger.warning("chat_gateway: no adapter for platform %r (enabled in config) — skipping", pcfg.name)
            continue
        try:
            adapters.append(cls(pcfg.options))
        except Exception:
            logger.exception("chat_gateway: failed to construct %s adapter", pcfg.name)
    return adapters
