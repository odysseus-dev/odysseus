# services/research/__init__.py
"""Research service — deep research with LLM-in-the-loop."""

from .research_handler import ResearchHandler
from .service import ResearchResult, ResearchService, ResearchSource

__all__ = [
    "ResearchService",
    "ResearchResult",
    "ResearchSource",
    "ResearchHandler",
]
