"""Compatibility import for the canonical research handler.

Historically this package carried a second ``ResearchHandler`` implementation
beside ``src.research_handler``. The application runtime instantiates the
``src`` handler, so keeping a parallel implementation here lets fixes drift
between import paths.
"""

from src.research_handler import (
    RESEARCH_DATA_DIR,
    ResearchHandler,
    _bounded_int,
    _format_probe_failure,
    _research_json_path,
)

__all__ = [
    "RESEARCH_DATA_DIR",
    "ResearchHandler",
    "_bounded_int",
    "_format_probe_failure",
    "_research_json_path",
]
