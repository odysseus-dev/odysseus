"""Shared publication-recency semantics for query repair and search execution."""
import re


def reference_lookup_without_date_window(text: str, query_hint: str = '') -> bool:
    """Current reference information need not have been published recently."""
    reference = re.search(
        r'\b(?:documentation|docs|manuals?|guides?|reference|installation|configuration|versions?|releases?)\b'
        r'|\bprivacy\s+(?:features|settings|protections)\b', text + ' ' + query_hint, re.I,
    )
    publication = re.search(
        r'\b(?:published|publication|announced|released|news|headlines|recent|today|yesterday)\b'
        r'|\b(?:this|last|past)\s+(?:\d+\s+)?(?:days?|weeks?|months?|years?)\b'
        r'|\b(?:since|after|before|between|during)\b|\b20\d{2}\b', text, re.I,
    )
    return bool(reference and not publication)


def requested_search_publication_window(text: str) -> str | None:
    """Recognize explicit named publication windows."""
    if re.search(r'\b(?:today|yesterday|this morning|right now)\b', text, re.I):
        return 'day'
    for unit, value in [('week', 'week'), ('month', 'month'), ('year', 'year')]:
        if re.search(rf'\b(?:this|past|last)\s+{unit}\b', text, re.I):
            return value
    return None


def inferred_search_publication_window(text: str) -> str | None:
    """Infer publication recency, not freshness of every requested fact."""
    requested = requested_search_publication_window(text)
    if requested:
        return requested
    if reference_lookup_without_date_window(text):
        return None
    if re.search(r"\b(?:current events|what(?:'s| is) happening)\b", text, re.I):
        return 'day'
    if re.search(r'\b(?:news|neews|headlines|breaking|latest developments)\b', text, re.I):
        return 'week'
    if re.search(r'\brecent\b', text, re.I):
        return 'month'
    return None
