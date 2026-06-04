"""Compatibility wrapper for the canonical services.search.analytics module.

``src.search.analytics`` remains importable for older code, but the
implementation now lives in ``services.search.analytics`` so metrics, error
types, and analytics file handling cannot drift between copies.
"""

import sys

from services.search import analytics as _analytics

sys.modules[__name__] = _analytics
