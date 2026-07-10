"""Compatibility alias for the canonical model route package.

The application imports :mod:`routes.model`; keeping this legacy path as the
same module object ensures monkeypatches, caches, and helper state cannot drift
between two copies of the route implementation.
"""

import sys

from routes import model as _canonical


sys.modules[__name__] = _canonical
