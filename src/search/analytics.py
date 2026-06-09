"""Compatibility re-export shim for the live analytics module.

The real implementation lives in :mod:`services.search.analytics`, which is
what the search runtime imports. Alias this module to that implementation so
mutable module state such as ``ANALYTICS_FILE`` cannot drift out of sync.
"""

import sys

from services.search import analytics as _analytics

sys.modules[__name__] = _analytics
