"""Compatibility wrapper for the canonical services.search.query module.

``src.search.query`` stays importable for older agent/deep-research code, but the
implementation now lives in ``services.search.query`` so the two cannot drift.
"""

import sys

from services.search import query as _query

sys.modules[__name__] = _query
