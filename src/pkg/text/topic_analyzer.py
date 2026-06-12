"""
Topic analysis for conversations — deduplicated from app.py.
Used by /api/conversations/topics and /api/memory/extract fallback.
"""

import re
from typing import Dict, Any, List

TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Technology": ["ai", "machine learning", "python", "code", "programming", "computer", "software", "hardware", "algorithm"],
    "Science": ["science", "physics", "chemistry", "biology", "math", "mathematics", "research", "experiment"],
    "Work": ["work", "job", "career", "project", "task", "deadline", "meeting", "colleague", "manager"],
    "Personal": ["personal", "family", "friend", "relationship", "health", "wellness", "exercise", "diet"],
    "Learning": ["learn", "study", "education", "course", "tutorial", "guide", "how to", "explain"],
    "Creativity": ["write", "story", "create", "design", "art", "music", "draw", "paint"],
    "Planning": ["plan", "schedule", "organize", "arrange", "coordinate", "timeline", "calendar"],
    "Troubleshooting": ["error", "bug", "fix", "problem", "issue", "debug", "troubleshoot"],
}


def analyze_topics(session_manager, owner: str = None) -> Dict[str, Any]:
    """
    Scan non-archived sessions and return topic frequency data.
    If owner is set, only include sessions belonging to that user.

    When `owner` is None or empty the helper returns an empty result. The
    unauthenticated-loopback path in `app.py` produces a None owner, and
    silently aggregating topic frequencies in that case is a cross-tenant
    data leak. Callers that want a system-wide aggregate must pass an
    explicit `owner` string (e.g. a documented "admin" pseudo-owner) or
    the route must reject the request with 401.

    Returns dict with "topics" list and "total_topics" count.
    """
    if not owner:
        return {"topics": [], "total_topics": 0}

    topic_counts: Dict[str, int] = {t: 0 for t in TOPIC_KEYWORDS}
    topic_matches: Dict[str, list] = {t: [] for t in TOPIC_KEYWORDS}

    for session_id, session_data in session_manager.sessions.items():
        if session_data.get("archived", False):
            continue
        # Strict ownership: any session whose owner does not match the
        # caller is excluded. Ownerless sessions are never included
        # unless the caller is itself ownerless (which the early return
        # above already prevents).
        sess_owner = session_data.get("owner") or getattr(session_data, "owner", None)
        if sess_owner != owner:
            continue

        # Hydrate session to load history from DB if needed
        if hasattr(session_manager, "get_session"):
            hydrated_session = session_manager.get_session(session_id)
            history = hydrated_session.history
        else:
            hydrated_session = session_data
            history = session_data.get("history", [])

        for msg in history:
            content_raw = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            if not content_raw:
                continue

            content = str(content_raw).lower()
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            session_name = session_data.get("name", f"Session {session_id[:6]}")

            for topic, keywords in TOPIC_KEYWORDS.items():
                for kw in keywords:
                    if re.search(rf"\b{re.escape(kw)}\b", content):
                        topic_counts[topic] += 1
                        sentences = re.split(r'[.!?]', str(content_raw))
                        for sentence in sentences:
                            if re.search(rf"\b{re.escape(kw)}\b", sentence.lower()):
                                topic_matches[topic].append({
                                    "session_id": session_id,
                                    "session_name": session_name,
                                    "role": role,
                                    "snippet": sentence.strip(),
                                    "keyword": kw,
                                })
                                break

    results = []
    for topic, count in topic_counts.items():
        if count == 0:
            continue
        matches = topic_matches[topic]
        unique, seen = [], set()
        for m in matches:
            key = f"{m['session_id']}-{m['snippet'][:50]}"
            if key not in seen:
                seen.add(key)
                unique.append(m)
        results.append({
            "topic": topic,
            "frequency": count,
            "examples": unique[:5],
            "session_count": len({m["session_id"] for m in unique}),
        })

    results.sort(key=lambda x: x["frequency"], reverse=True)
    return {"topics": results, "total_topics": len(results)}
