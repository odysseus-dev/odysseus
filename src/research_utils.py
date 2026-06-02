# src/research_utils.py
"""Shared utilities for the deep research system.

Centralizes text cleaning, quality filtering, and other logic
used across deep_research.py, research_handler.py, and visual_report.py.
"""

# ---------------------------------------------------------------------------
# Thinking / reasoning block stripping
# ---------------------------------------------------------------------------

def strip_thinking(text):
    """Strip thinking / reasoning patterns from LLM output.

    Delegates to `src.text_helpers.strip_think` (single source of truth).
    Kept as an alias here so existing `from src.research_utils import strip_thinking`
    callers don't break. Preserves None passthrough — many callers pass an
    `Optional[str]` LLM result and expect None back when the call failed.
    """
    if text is None:
        return None
    from src.text_helpers import strip_think
    return strip_think(text, prose=False, prompt_echo=True)


# ---------------------------------------------------------------------------
# Source quality filtering
# ---------------------------------------------------------------------------

# Markers indicating extracted content is boilerplate, error text, or empty.
# If any marker is found (case-insensitive), the content is filtered out.
LOW_QUALITY_MARKERS = [
    "insufficient to",
    "content is insufficient",
    "no substantive data",
    "does not contain",
    "not relevant to",
    "no relevant information",
    "unable to extract",
    "completely unrelated",
    "boilerplate",
    "footer text",
    # Phrases (not bare "cookie"/"copyright") so we still catch boilerplate
    # like consent banners and footers without discarding legitimate findings
    # that merely discuss cookies or copyright as their subject.
    "cookie consent",
    "cookie banner",
    "cookie notice",
    "copyright notice",
    "copyright footer",
    "all rights reserved",
]


def is_low_quality(summary: str) -> bool:
    """Check if a finding summary indicates useless or irrelevant content."""
    try:
        if not summary:
            return True
        low = summary.lower()
        return any(marker in low for marker in LOW_QUALITY_MARKERS)
    except Exception:
        return False  # fail open
