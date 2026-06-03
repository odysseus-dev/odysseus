"""
triple_extractor.py

LLM-driven extraction of typed (subject, relation, object) triples about the
user. Designed to run alongside the flat-text extractor in `memory_extractor`
so the same chat turn produces both forms — text for fuzzy recall, triples
for graph traversal.

Errors are logged, never raised. If the LLM is unavailable or returns junk,
extraction yields zero triples and the graph stays untouched.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Mirrors RELATIONS in graph_store.py. Kept here as a literal so the prompt
# stays a single source of truth — if you add a relation in the graph,
# add it here and the extractor will start using it.
EXTRACTION_RELATIONS = (
    "KNOWS",          # (User|Person) -> Person
    "LIVES_IN",       # (User|Person) -> Place
    "WORKS_AT",       # (User|Person) -> Organization
    "INTERESTED_IN",  # User -> Concept
    "HAS_GOAL",       # User -> Goal
    "WORKING_ON",     # (User|Person) -> Project
    "RELATED_TO",     # Person -> Person
)

EXTRACTION_LABELS = ("User", "Person", "Place", "Organization", "Concept", "Goal", "Project")


SYSTEM_PROMPT = (
    "You are a knowledge-graph extraction assistant. Read the conversation and "
    "extract structured triples about the USER that would be useful across many "
    "future conversations. Be conservative — better to miss a triple than to "
    "invent one.\n\n"
    "Output a JSON array of triples. Each triple has this shape:\n"
    "  {\n"
    '    "subject":      {"label": "<NODE_LABEL>", "name": "<string>"},\n'
    '    "relation":     "<REL_NAME>",\n'
    '    "object":       {"label": "<NODE_LABEL>", "name": "<string>"},\n'
    '    "confidence":   0.0-1.0,\n'
    '    "extras":       {<optional extra fields, see below>}\n'
    "  }\n\n"
    f"Allowed node labels: {', '.join(EXTRACTION_LABELS)}\n"
    f"Allowed relations:   {', '.join(EXTRACTION_RELATIONS)}\n\n"
    "Type rules (do NOT violate):\n"
    "  - KNOWS:         User|Person -> Person\n"
    "  - LIVES_IN:      User|Person -> Place\n"
    "  - WORKS_AT:      User|Person -> Organization\n"
    "  - INTERESTED_IN: User -> Concept\n"
    "  - HAS_GOAL:      User -> Goal\n"
    "  - WORKING_ON:    User|Person -> Project\n"
    "  - RELATED_TO:    Person -> Person\n\n"
    "When the subject is the user, use {\"label\": \"User\", \"name\": \"self\"}.\n\n"
    "Optional `extras` fields:\n"
    "  - place_kind:    'city' | 'country' | 'region' | 'neighborhood'\n"
    "  - person_email:  the person's email if explicitly stated\n"
    "  - goal_deadline: ISO date string if mentioned\n\n"
    "Rules:\n"
    "  - MAX 4 triples per extraction\n"
    "  - Only triples the USER stated or clearly implied — never the assistant's claims\n"
    "  - Skip transient/conversational facts (today's mood, current topic, one-off tasks)\n"
    "  - For names, use the form the user used (don't normalize casing)\n"
    "  - confidence >= 0.7 for explicit statements, < 0.5 for inferences\n"
    "  - If nothing extractable, return []\n\n"
    "Return ONLY a valid JSON array, no prose, no markdown fences."
)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    # Strip <think> blocks emitted by reasoning models.
    t = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", t, flags=re.I).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return t


def _parse_triples(raw: str) -> list[dict]:
    """Best-effort JSON parse with the same tolerance as the audit pipeline."""
    text = _strip_fences(raw)
    if not text:
        return []

    candidates = [text, re.sub(r",(\s*[}\]])", r"\1", text)]
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if isinstance(parsed, list):
            return parsed

    # Final fallback: pull the first JSON array out of the response.
    lo, hi = text.find("["), text.rfind("]")
    if lo >= 0 and hi > lo:
        try:
            parsed = json.loads(text[lo:hi + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    return []


# Relation type guards mirror graph_store.RELATIONS — kept here to avoid an
# import cycle and so the extractor can pre-filter before hitting the DB.
_REL_TYPES = {
    "KNOWS":         ({"User", "Person"},        {"Person"}),
    "LIVES_IN":      ({"User", "Person"},        {"Place"}),
    "WORKS_AT":      ({"User", "Person"},        {"Organization"}),
    "INTERESTED_IN": ({"User"},                  {"Concept"}),
    "HAS_GOAL":      ({"User"},                  {"Goal"}),
    "WORKING_ON":    ({"User", "Person"},        {"Project"}),
    "RELATED_TO":    ({"Person"},                {"Person"}),
}


def _valid_triple(t: dict) -> bool:
    if not isinstance(t, dict):
        return False
    rel = (t.get("relation") or "").upper()
    if rel not in _REL_TYPES:
        return False
    s, o = t.get("subject"), t.get("object")
    if not isinstance(s, dict) or not isinstance(o, dict):
        return False
    s_lbl = (s.get("label") or "").strip()
    o_lbl = (o.get("label") or "").strip()
    if s_lbl not in EXTRACTION_LABELS or o_lbl not in EXTRACTION_LABELS:
        return False
    s_allowed, o_allowed = _REL_TYPES[rel]
    if s_lbl not in s_allowed or o_lbl not in o_allowed:
        return False
    if not (s.get("name") and o.get("name")):
        return False
    return True


async def extract_triples_async(
    messages: Iterable[dict],
    endpoint_url: str,
    model: str,
    headers: Optional[dict] = None,
    max_messages: int = 6,
) -> list[dict]:
    """Run the LLM extraction over a chat slice and return validated triples.

    Returns normalized triples ready to feed into GraphStore.ingest_triple:
        [
          {
            "subject_label": "User", "subject_name": "self",
            "relation": "LIVES_IN",
            "object_label": "Place", "object_name": "Paris",
            "confidence": 0.95,
            "extras": {"place_kind": "city"}
          },
          ...
        ]
    """
    from src.llm_core import llm_call_async

    msgs = list(messages or [])
    if len(msgs) < 2:
        return []
    if max_messages and len(msgs) > max_messages:
        msgs = msgs[-max_messages:]

    payload = [{"role": "system", "content": SYSTEM_PROMPT}] + msgs

    try:
        raw = await llm_call_async(
            endpoint_url, model, payload,
            temperature=0.1, max_tokens=800, headers=headers,
        )
    except Exception as e:
        logger.warning("triple extraction LLM call failed: %s", e)
        return []

    parsed = _parse_triples(raw)
    if not parsed:
        logger.debug("triple extraction returned no parseable triples")
        return []

    out: list[dict] = []
    for item in parsed:
        if not _valid_triple(item):
            continue
        rel = item["relation"].upper()
        s, o = item["subject"], item["object"]
        out.append({
            "subject_label": s["label"],
            "subject_name":  str(s["name"]).strip(),
            "relation":      rel,
            "object_label":  o["label"],
            "object_name":   str(o["name"]).strip(),
            "confidence":    float(item.get("confidence", 0.7) or 0.7),
            "extras":        item.get("extras") if isinstance(item.get("extras"), dict) else {},
        })
        if len(out) >= 4:
            break
    return out


def ingest_triples(graph_store, owner: Optional[str], triples: list[dict], source_memory_id: Optional[str] = None) -> int:
    """Persist a batch of triples to the graph. Returns count of edges added.

    Silent if graph_store is None or unhealthy — callers should not need to
    check beforehand.
    """
    if not graph_store or not getattr(graph_store, "healthy", False):
        return 0
    added = 0
    for t in triples or []:
        # Drop low-confidence inferences. The threshold is conservative — the
        # extraction prompt already nudges the model away from speculation.
        if float(t.get("confidence", 0.0)) < 0.5:
            continue
        ok = graph_store.ingest_triple(
            owner=owner,
            subject_label=t["subject_label"],
            subject_name=t["subject_name"],
            rel=t["relation"],
            object_label=t["object_label"],
            object_name=t["object_name"],
            source_memory_id=source_memory_id,
            extras=t.get("extras") or {},
        )
        if ok:
            added += 1
    return added
