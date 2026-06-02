"""Compatibility wrapper for the canonical services.search.providers module.

Historically Odysseus carried duplicate provider implementations under both
``src.search`` and ``services.search``. Keep the old import path working, but
make provider behavior come from one source of truth.
"""

import sys

from services.search import providers as _providers

sys.modules[__name__] = _providers
