"""Compatibility shim for the canonical :mod:`src.youtube_handler` module.

The implementation lives in ``src.youtube_handler``. This module remains for
older imports such as ``services.youtube.youtube_handler`` without carrying a
second copy that can drift.
"""

import sys

from src import youtube_handler as _canonical

globals().update(_canonical.__dict__)
sys.modules[__name__] = _canonical
