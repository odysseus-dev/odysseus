"""Compatibility shim for the canonical :mod:`src.research_handler` module.

The implementation lives in ``src.research_handler``. This module remains for
older imports such as ``services.research.research_handler`` without carrying a
second copy that can drift.
"""

import sys

from src import research_handler as _canonical

globals().update(_canonical.__dict__)
sys.modules[__name__] = _canonical
