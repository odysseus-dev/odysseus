"""Compatibility wrapper for the canonical services.search.core module.

``src.search.core`` remains importable for older agent/deep-research code, but
the implementation now lives in ``services.search.core`` so provider ordering,
cache invalidation, and search route behavior cannot drift between copies.
"""

import sys

from services.search import core as _core

sys.modules[__name__] = _core
