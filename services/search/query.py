"""Query enhancement, entity extraction, and cache duration helpers."""

import re
import logging
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# LLM-assisted query rewrite (optional, additive)
# ----------------------------------------------------------------------
# A conversational question ("is albert einstein still alive?") makes a poor
# search query — engines do far better with keywords ("Albert Einstein death date").
# When a utility LLM is available we ask it to reformulate; on ANY failure we
# return the original query unchanged, so search is never worse than before.

_REWRITE_SYSTEM = (
    "You turn a user's question into the best possible web-search query. "
    "Output ONLY the search query — no quotes, no explanation, no punctuation "
    "beyond what helps the search. Use keywords a search engine matches well: "
    "drop filler words (is/are/the/a/do), keep proper nouns, and add the term "
    "that surfaces the answer (e.g. a 'is X dead?' question -> 'X death date'; "
    "'how do I Y' -> 'Y tutorial'). Keep it under 12 words."
)


async def rewrite_search_query(question: str, owner: Optional[str] = None,
                               max_chars: int = 200) -> str:
    """Reformulate a natural-language question into a good keyword search query
    using the utility LLM. Returns the rewritten query, or the original on any
    failure (no LLM configured, timeout, empty/over-long output, etc.).

    Safe by construction: the worst case is the unchanged query, i.e. today's
    behaviour. Best case turns "is albert einstein still alive?" into
    "Albert Einstein death date".
    """
    q = (question or "").strip()
    if not q or len(q) > 400:
        return q
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url or not model:
            return q
        messages = [
            {"role": "system", "content": _REWRITE_SYSTEM},
            {"role": "user", "content": q},
        ]
        out = await llm_call_async(url, model, messages, temperature=0.0,
                                   max_tokens=40, headers=headers)
        # Take the first line only, THEN strip wrapping quotes/space — so a
        # quoted query followed by an explanation line ('"foo"\n(why)') yields
        # the clean 'foo', not 'foo"'. Reject empties, refusals, or junk that's
        # longer than the original (model explained instead of rewriting).
        out = (out or "").strip()
        out = out.splitlines()[0].strip() if out else ""
        out = out.strip('"').strip("'").strip()
        if not out or len(out) > max_chars or len(out) > len(q) + 60:
            return q
        return out
    except Exception as exc:
        logger.debug("query rewrite failed, using original: %s", exc)
        return q


# ----------------------------------------------------------------------
# Query processing helpers
# ----------------------------------------------------------------------
def _detect_question_type(query: str) -> Optional[str]:
    """Return the leading question word if present (who, what, when, where, why, how)."""
    q = query.strip().lower()
    for word in ("who", "what", "when", "where", "why", "how"):
        if q.startswith(word):
            return word
    return None


def _extract_entities(query: str) -> Dict[str, List[str]]:
    """Lightweight entity extraction: capitalized words and date patterns."""
    entities: Dict[str, List[str]] = {"names": [], "dates": []}
    qtype = _detect_question_type(query)
    cleaned = query
    if qtype:
        cleaned = re.sub(rf"^{qtype}\b", "", cleaned, flags=re.I).strip()
    for token in re.findall(r"\b[A-Z][a-zA-Z]+\b", cleaned):
        entities["names"].append(token)
    for year in re.findall(r"\b(?:19|20)\d{2}\b", cleaned):
        entities["dates"].append(year)
    month_day_year = re.findall(
        r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+\d{1,2},?\s*\d{4}\b",
        cleaned,
        flags=re.I,
    )
    entities["dates"].extend(month_day_year)
    return entities


def _split_multi_part(query: str) -> List[str]:
    """Split a query into sub-queries on common conjunctions."""
    parts = re.split(r"\s+and\s+|\s+or\s+|;", query, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def _extract_site_filter(query: str) -> Tuple[str, Optional[str]]:
    """Detect a 'site:example.com' token. Returns (query_without_token, site_or_None)."""
    match = re.search(r"\bsite:([^\s]+)", query, flags=re.I)
    if match:
        site = match.group(1)
        new_query = re.sub(r"\bsite:[^\s]+", "", query, flags=re.I).strip()
        return new_query, site
    return query, None


def _boost_entities_in_query(base_query: str, entities: Dict[str, List[str]]) -> str:
    """Append extracted entities to the query using OR to increase relevance."""
    parts = [base_query]
    if entities.get("names"):
        parts.append(" OR ".join(f'"{n}"' for n in entities["names"]))
    if entities.get("dates"):
        parts.append(" OR ".join(f'"{d}"' for d in entities["dates"]))
    return " ".join(parts)


def enhance_query(original_query: str) -> Tuple[str, Optional[str]]:
    """Process the original query: site filter, question type boosts, entity extraction."""
    query_without_site, site = _extract_site_filter(original_query)
    sub_queries = _split_multi_part(query_without_site)

    enhanced_subs: List[str] = []
    for sub in sub_queries:
        qtype = _detect_question_type(sub)
        boost_keywords = []
        if qtype == "who":
            boost_keywords.append("person")
        elif qtype == "when":
            boost_keywords.append("date")
        elif qtype == "where":
            boost_keywords.append("location")
        elif qtype == "why":
            boost_keywords.append("reason")
        elif qtype == "how":
            boost_keywords.append("method")
        entities = _extract_entities(sub)
        boosted = _boost_entities_in_query(sub, entities)
        if boost_keywords:
            boosted = f'({boosted}) OR ({" OR ".join(boost_keywords)})'
        enhanced_subs.append(boosted)

    final_query = " AND ".join(f"({s})" for s in enhanced_subs)
    if site:
        final_query = f"{final_query} site:{site}"
    return final_query, site


def build_enhanced_query(query: str, time_filter: str = None) -> str:
    """Build an enhanced search query with optional time filtering."""
    enhanced_query, _ = enhance_query(query)

    if time_filter:
        time_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        if time_filter in time_map:
            enhanced_query = f"{enhanced_query} after:{time_map[time_filter]}"
            logger.info(f"Added time filter '{time_filter}' to query")

    logger.info(f"Enhanced query: '{query}' -> '{enhanced_query}'")
    return enhanced_query


# ----------------------------------------------------------------------
# Cache duration helpers
# ----------------------------------------------------------------------
def _is_news_query(query: str) -> bool:
    """Lightweight heuristic to decide if a query is news-oriented."""
    news_terms = {"news", "latest", "breaking", "today", "today's", "current", "updates", "happening"}
    tokens = set(re.findall(r"\b\w+\b", query.lower()))
    return bool(tokens & news_terms)


def _cache_duration_for_query(query: str) -> timedelta:
    """News queries -> 30 minutes, reference queries -> 24 hours."""
    if _is_news_query(query):
        return timedelta(minutes=30)
    return timedelta(hours=24)
