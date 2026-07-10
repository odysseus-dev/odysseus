"""Compatibility alias for the canonical gallery route module.

Keep the legacy import path as the exact same module object so callers that
monkeypatch dependencies through ``routes.gallery_routes`` affect the routes
registered from ``routes.gallery.gallery_routes``.
"""

import sys

from routes.gallery import gallery_routes as _canonical


sys.modules[__name__] = _canonical
