"""
memory_extractor.py

Background auto-extraction of facts from chat conversations.
After each LLM response, this module sends the last few messages to the LLM
asking it to extract memorable facts, then stores them in both memory.json
and the FAISS vector index.

Periodically audits all memories via LLM to consolidate duplicates,
rewrite vague entries, and remove junk.
"""

import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

from src.memory import MemoryStoreUnreadable

logger = logging.getLogger(__name__)


def _tidy_state_path(memory_manager) -> str:
    """Sidecar JSON next to memory.json that remembers the fingerprint of
    the last successfully-audited state per owner. Lets the audit short-
    circuit when nothing has changed since the previous tidy — running
    the LLM again on an already-clean list was wasting 30-120s per call
    and occasionally timing out on the second pass."""
    return os.path.join(os.path.dirname(memory_manager.memory_file), "memory_tidy_state.json")


def _fingerprint_entries(entries) -> str:
    """Stable hash of an owner's memories — order-independent, depends
    only on id+text+category. Any add/edit/delete invalidates it."""
    items = sorted(
        (str(e.get("id", "")), e.get("text", ""), e.get("category", ""))
        for e in _memory_dicts(entries)
    )
    h = hashlib.sha256()
    for triple in items:
        h.update(("\x1f".join(triple) + "\x1e").encode("utf-8"))
    return h.hexdigest()


def _memory_dicts(entries):
    for entry in entries or []:
        if isinstance(entry, dict):
            yield entry


def _load_tidy_state(memory_manager) -> dict:
    path = _tidy_state_path(memory_manager)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_tidy_state(memory_manager, owner: Optional[str], fingerprint: str) -> None:
    path = _tidy_state_path(memory_manager)
    state = _load_tidy_state(memory_manager)
    state[owner or ""] = {"fingerprint": fingerprint}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not persist tidy fingerprint: {e}")

EXTRACT_SYSTEM_PROMPT = (
    "You are a memory extraction assistant. Analyze the conversation and extract ONLY "
    "durable personal facts about the user that would be useful across many future conversations.\n\n"
    "Good examples: name, job title, city, family members, long-term projects, strong preferences.\n"
    "Bad examples: what they asked about today, temporary moods, generic statements, "
    "things the assistant said, one-off tasks, opinions on the current topic.\n\n"
    "Rules:\n"
    "- MAX 2 facts per conversation — only the most important\n"
    "- Only extract facts the USER explicitly stated about themselves\n"
    "- Questions, requests to inspect records, and tool/assistant output are not evidence\n"
    "- Each fact must be a single short sentence (under 15 words)\n"
    "- If a fact is similar to something likely already known, skip it\n"
    "- If nothing durable was revealed, return []\n\n"
    "Return a JSON array of objects with 'text', 'category', and 'evidence' fields. "
    "Evidence must be an exact quote from a user's own factual statement.\n"
    "Categories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\n"
    "Return ONLY valid JSON, no markdown fences."
)

# How many recent messages to include for extraction
CONTEXT_WINDOW = 6

PERSONA_MEMORY_SYSTEM_PROMPT = (
    "You maintain concise continuity notes for one active chat persona. "
    "Update the existing notes using only durable details established in the transcript. "
    "Keep details that help the same persona stay consistent in future conversations: "
    "relationship context, names, preferences, recurring story details, boundaries, and unresolved threads. "
    "Do not store generic chat events, temporary wording, assistant reasoning, or one-off requests. "
    "Never invent details. Return only the updated notes as short bullet points, max 12 bullets. "
    "If there is nothing worth keeping, return the existing notes unchanged or an empty string."
)

HEALTH_PERSONA_MEMORY_SYSTEM_PROMPT = (
    "You maintain a cautious health-record brief for a medical reasoning persona. "
    "Update the existing brief using only medically durable information from the transcript. "
    "Keep facts that may matter in future health conversations: confirmed diagnoses, chronic conditions, "
    "surgeries/procedures, allergies, regular medications/supplements, important test results, clinicians/hospitals, "
    "ongoing symptoms or care plans, and the user's preferences for medical explanations. "
    "Use uncertainty labels when needed: 'reported', 'possible', 'asked about', 'unclear'. "
    "Do not turn guesses into diagnoses. Do not store casual one-off symptoms unless they are recurring, severe, "
    "or tied to an ongoing episode. Never invent facts. Return only the updated brief with these headings when useful: "
    "Medical profile, Medications/allergies, Episodes/open questions, Preferences. Max 16 concise bullets total. "
    "If nothing medically durable changed, return the existing brief unchanged or an empty string."
)

AUDIT_SYSTEM_PROMPT = (
    "You are a memory database curator. Be CONSERVATIVE: remove only TRUE "
    "duplicates and clearly useless entries. Every distinct fact must survive. "
    "When in doubt, KEEP the entry. Return the cleaned list.\n\n"
    "Rules:\n"
    "1. MERGE only entries that state the SAME fact in different words. If you "
    "are not sure two entries are the same fact, KEEP BOTH.\n"
    "   Merge: 'User's name is Sam' + 'The user is called Sam' -> one.\n"
    "   Do NOT merge related-but-distinct facts: 'Likes Python' and 'Uses "
    "Python at work' are DIFFERENT — keep both.\n"
    "2. REMOVE only entries that are genuinely worthless: about what the AI did "
    "(not the user), empty, or meaningless. Do NOT drop a real fact just "
    "because it seems minor or niche.\n"
    "3. Keep the original wording. Only lightly trim obvious redundancy — do "
    "NOT aggressively rewrite or shorten.\n"
    "4. Preserve the 'id' of the entry you keep when merging.\n"
    "5. Never invent facts. When unsure, KEEP.\n\n"
    "Return a JSON array of objects with fields: id, text, category.\n"
    "Return ONLY valid JSON, no markdown fences."
)

AUDIT_INTERVAL = 5  # audit every N new memories added
AUTO_PINNED_IDENTITY_LIMIT = 5


def _is_owner_memory(entry, owner):
    if owner:
        return entry.get("owner") == owner or entry.get("owner") is None
    return True


def _is_auto_pinned_identity(entry):
    return (
        bool(entry.get("pinned"))
        and (entry.get("category") or "").lower() in {"identity", "contact"}
    )
_extractions_since_audit = 0


def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return " ".join(p for p in parts if p).strip()
    return ""


def _message_role(message) -> str:
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
    return str(role or "").lower()


def _clean_memory_value(value: str, max_len: int = 80) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" .,!?:;\"'`“”‘’")
    value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.I)
    if not value or len(value) > max_len:
        return ""
    if re.search(r"https?://|@|[{}<>]", value):
        return ""
    return value


def _fallback_memory_candidates(messages) -> list[dict]:
    """Extract obvious durable facts without relying on the LLM.

    This is deliberately narrow. The LLM remains the main extractor, but
    simple identity/preference/goal statements should not silently vanish just
    because the background model judged them too conversational.
    """
    candidates = []
    seen = set()

    def add(text: str, category: str):
        text = _clean_memory_value(text, 120)
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append({"text": text, "category": category})

    for msg in messages:
        if _message_role(msg) != "user":
            continue
        text = _message_text(msg)
        if not text:
            continue

        m = re.search(
            r"\bmy name is\s+([A-Za-z][A-Za-z0-9 '\-]{0,49}?)"
            r"(?=\s+(?:and|but|please)\b|[.!?;,\n]|$)", text, re.I,
        )
        if m:
            name = _clean_memory_value(m.group(1), 50)
            if name:
                add(f"User's name is {name}.", "identity")

        m = re.search(r"\bcall me\s+([A-Za-z][A-Za-z0-9 .'\-]{1,50})\b", text, re.I)
        if m:
            name = _clean_memory_value(m.group(1), 50)
            if name:
                add(f"User wants to be called {name}.", "identity")

        m = re.search(r"\bi (?:live in|am from|'m from)\s+([^.!?\n]{2,80})", text, re.I)
        if m:
            place = _clean_memory_value(m.group(1), 80)
            if place:
                add(f"User lives in {place}.", "identity")

        m = re.search(r"\bi (prefer|like|love|hate|do not like|don't like)\s+([^.!?\n]{4,100})", text, re.I)
        if m:
            preference = _clean_memory_value(m.group(2), 100)
            if preference:
                # The same pattern catches likes and dislikes; keep the stored
                # sentiment faithful instead of recording every match as a
                # preference ("I hate cilantro" must not become "User prefers
                # cilantro").
                verb = m.group(1).lower()
                if verb in ("hate", "do not like", "don't like"):
                    add(f"User dislikes {preference}.", "preference")
                else:
                    add(f"User prefers {preference}.", "preference")

        m = re.search(
            r"\bi (?:(?:want|would like|plan|hope) to|wanna) "
            r"(?:go|travel|move|visit) to\s+([^.!?\n]{2,80})",
            text,
            re.I,
        )
        if m:
            destination = _clean_memory_value(m.group(1), 80)
            if destination:
                add(f"User wants to visit {destination}.", "goal")

    return candidates[:2]


_USER_FACT_ASSERTION = re.compile(
    r"\b(?:i(?:'m| am| live| work| use| have| prefer| like| love| hate| own| study| grew up)\b"
    r"|my\s+[a-z][a-z -]{0,40}\s+(?:is|are|was|works|lives)\b"
    r"|we\s+(?:are|live|work|have|own)\b"
    r"|our\s+[a-z][a-z -]{0,40}\s+(?:is|are|was)\b)",
    re.IGNORECASE,
)


def _has_user_fact_evidence(fact: dict, messages) -> bool:
    """Require an exact user assertion before persisting an LLM-inferred memory."""
    evidence = fact.get("evidence")
    if not isinstance(evidence, str):
        return False
    evidence = re.sub(r"\s+", " ", evidence).strip()
    if len(evidence.split()) < 3 or not _USER_FACT_ASSERTION.match(evidence):
        return False
    for message in messages:
        if _message_role(message) != "user":
            continue
        utterance = re.sub(r"\s+", " ", _message_text(message)).strip()
        position = utterance.casefold().find(evidence.casefold())
        if position < 0:
            continue
        # Judge the clause containing the quote, so a later question does not
        # erase an earlier factual statement in the same user turn.
        clause_start = max(
            utterance.rfind(mark, 0, position) for mark in (".", "?", "!", ";")
        ) + 1
        clause_end = min(
            [end for mark in (".", "?", "!", ";")
             if (end := utterance.find(mark, position)) >= 0] or [len(utterance)]
        )
        clause = utterance[clause_start:clause_end].strip()
        # A question about one's records is not a statement of the answer.
        if utterance[clause_end:clause_end + 1] == "?" or re.match(
            r"^(?:who|what|where|when|why|how|do|does|did|can|could|would|"
            r"show|list|find|search|tell|summarize|open|read)\b",
            clause, re.IGNORECASE,
        ):
            continue
        return True
    return False


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words carry no distinguishing content. Dropping them before
# comparing two facts keeps punctuation and harmless rephrasing from defeating
# the duplicate check.
_STOPWORDS = frozenset(
    "a an the and or but if then of to in on at for with by from as is are was "
    "were be been being do does did has have had it its this that these those "
    "there here user users someone somebody they them their he she his her him i "
    "we you your my our me us who whom whose what when where why how also "
    "currently now still about into over under again more most very can could "
    "may might must will would should".split()
)

# Words that frame a name/username statement; excluded when reading the value
# so the actual name tokens can be compared.
_NAME_TEMPLATE_WORDS = frozenset(
    "name names named called call appears appear seems seem the user users his "
    "her their it is are was were be been being username usernames login "
    "account handle".split()
)

_NAME_KEYWORDS = frozenset({"name", "named", "called"})
_USERNAME_KEYWORDS = frozenset({"username", "login"})


def _content_tokens(text: str) -> set:
    """Lowercased content tokens with punctuation and stopwords removed."""
    toks = _TOKEN_RE.findall((text or "").lower())
    return {t for t in toks if t not in _STOPWORDS and len(t) >= 2}


def _name_slot(text: str):
    """Return (slot, value_tokens) when text states a name/username, else None.

    Tokens are scanned so "username" is never mistaken for "name", and the
    first keyword decides the slot (a sentence about a server *named* X that
    also mentions a *username* Y is a 'name' fact, not a username fact)."""
    words = _TOKEN_RE.findall((text or "").lower())
    slot = None
    for w in words:
        if w in _NAME_KEYWORDS:
            slot = "name"
            break
        if w in _USERNAME_KEYWORDS:
            slot = "username"
            break
    if not slot:
        return None
    value = {
        w for w in words
        if w not in _NAME_TEMPLATE_WORDS and w not in _STOPWORDS and len(w) >= 2
    }
    return (slot, value) if value else None


def _name_slot_tokens(text: str):
    slot = _name_slot(text)
    return slot[1] if slot else None


def _name_vals_close(a: set, b: set) -> bool:
    """True when two name values state the same name: identical, or one is a
    strict refinement of the other where the larger value is a clean name
    (<=4 alphabetic tokens). Guards against merging a username into a fact
    like 'a server named Odysseus ... with username pewds'."""
    if a == b:
        return True
    if not a or not b:
        return False
    bigger = a if len(a) >= len(b) else b
    clean = len(bigger) <= 4 and all(t.isalpha() for t in bigger)
    return clean and (a <= b or b <= a)


def _is_text_duplicate(new_text: str, existing: list, threshold: float = 0.6) -> bool:
    """Check if new_text states the same fact as an existing memory.

    Three tests, cheapest first: symmetric Jaccard over content tokens,
    containment (one fact is a strict refinement of the other), and a
    name/username-slot match. The naive whole-string comparison this replaces
    kept punctuation (so "Alex." != "alex") and missed short-vs-full names
    ("Felix" vs "FELIX ARVID ULF KJELLBERG" scored 0.43).
    """
    new_tokens = _content_tokens(new_text)
    if not new_tokens:
        return False
    new_slot = _name_slot(new_text)
    for entry in _memory_dicts(existing):
        old_text = entry.get("text", "")
        old_tokens = _content_tokens(old_text)
        if not old_tokens:
            continue
        intersection = new_tokens & old_tokens
        if not intersection:
            continue
        union = new_tokens | old_tokens
        if len(intersection) / len(union) >= threshold:
            return True
        # Containment: one fact is a strict refinement of the other. Guard on
        # the shorter side so two-token facts ("Likes Python") are not merged
        # into longer sentences that merely start the same way.
        if len(intersection) >= 3 and len(intersection) / min(len(new_tokens), len(old_tokens)) >= 0.85:
            return True
        # Name slot: both state the same kind of name and the values match
        # (short vs full name, or the same name behind different phrasing).
        old_slot = _name_slot(old_text)
        if new_slot and old_slot and new_slot[0] == old_slot[0]:
            if _name_vals_close(new_slot[1], old_slot[1]):
                return True
    return False


def _name_enrichment_target(new_text: str, existing: list):
    """Return an existing entry whose name value is a strict subset of the
    new fact's, so the fuller name replaces it in place; else None."""
    ns = _name_slot(new_text)
    if not ns:
        return None
    slot, new_val = ns
    best = None
    for entry in _memory_dicts(existing):
        old_slot = _name_slot(entry.get("text", ""))
        if not old_slot or old_slot[0] != slot:
            continue
        old_val = old_slot[1]
        if old_val < new_val and _name_vals_close(old_val, new_val) and len(new_val) <= 4 and all(t.isalpha() for t in new_val):
            if best is None or len(entry.get("text", "")) < len(best.get("text", "")):
                best = entry
    return best


def _parse_extraction_json(raw: str) -> list:
    """Parse the extraction LLM's reply into a list of facts, tolerating
    reasoning-model noise.

    The model emits <think>…</think> (and sometimes a prose preamble or a
    ```json fence) AROUND the JSON array; without stripping it, json.loads
    bombs and the run silently yields "0 candidates". Pure str -> list (no
    LLM/network); returns [] on any parse failure instead of raising.
    """
    text = (raw or "").strip()
    try:
        from src.text_helpers import strip_think as _strip_think
        text = _strip_think(text, prose=True, prompt_echo=True).strip()
    except Exception:
        pass
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    # JSON may still be embedded in surrounding commentary (leading prose or
    # trailing remarks like "[...] Done!") — slice from the first '[' to the
    # last ']' whenever both exist. Slice unconditionally: a reply that starts
    # with '[' can still carry trailing commentary that breaks json.loads.
    _start = text.find("[")
    _end = text.rfind("]")
    if 0 <= _start < _end:
        text = text[_start : _end + 1]

    try:
        facts = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("Memory extraction returned non-JSON: %r", (raw or "")[:120])
        return []
    except Exception:
        logger.debug("Memory extraction returned non-JSON: %r", (raw or "")[:120])
        return []
    return facts if isinstance(facts, list) else []


async def extract_and_store(
    session,
    memory_manager,
    memory_vector,
    endpoint_url: str,
    model: str,
    headers: Optional[dict] = None,
):
    """Extract facts from recent conversation and store them.

    Designed to run as a background task (asyncio.create_task).
    Errors are logged, never raised.
    """
    if not endpoint_url or not model:
        logger.debug("[memory-extract] No model or URL provided, skipping")
        return

    try:
        from src.llm_core import llm_call_async
        from src.utility_effort import effort_for_call

        # Get last N messages from session
        messages = session.get_context_messages()
        recent = messages[-CONTEXT_WINDOW:] if len(messages) > CONTEXT_WINDOW else messages

        if len(recent) < 2:
            return  # Need at least a user message and assistant response

        # Strip media (images/audio) from messages — background memory extraction
        # only needs the text. The VL-generated descriptions are already in the
        # text content of the messages. This avoids sending image tokens to
        # non-vision models and prevents accidental "vision grounding" triggers.
        stripped_recent = []
        for msg in recent:
            role = msg.get("role")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Filter out multimodal blocks that aren't text
                text_only = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if not text_only and content:
                    continue
                content = text_only
            stripped_recent.append({"role": role, "content": content})

        if not stripped_recent:
            return

        fallback_facts = _fallback_memory_candidates(stripped_recent)

        # Flatten the window into a SINGLE user message instead of appending the
        # raw alternating role messages. Passed as raw chat messages, the model
        # treats the window as a conversation to CONTINUE rather than a transcript
        # to ANALYZE, so it reliably extracts nothing — typically returning `[]`
        # (and, depending on the input, sometimes an empty or <think>-only
        # completion when the window ends on an assistant turn). This was the real
        # cause of auto-memory logging "0 candidates" on every run. Reframing it as
        # one "analyze this transcript, return the JSON array" user message makes
        # the model actually extract. Controlled repro on this model: 0/6 trials
        # with the old structure vs 6/6 with this one. The skill extractor flattens
        # for the same reason.
        def _flatten_msg(m):
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(
                    b.get("text", "") for b in c
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            return f"{m.get('role', '?')}: {c}"

        transcript = "\n\n".join(_flatten_msg(m) for m in stripped_recent)
        extraction_messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": (
                "Conversation to analyze:\n\n" + transcript
                + "\n\nReturn the JSON array of durable facts now (or [] if none)."
            )},
        ]

        facts = []
        try:
            raw = await llm_call_async(
                endpoint_url,
                model,
                extraction_messages,
                temperature=0.1,
                # A reasoning model spends most of its budget on <think> tokens
                # BEFORE emitting the JSON, so the old 500 truncated the response
                # before any JSON appeared → every run logged "0 candidates". The
                # audit path hit the same wall and raised to 16384; extraction's
                # output (a short facts list) is small, so an ample ceiling is
                # enough once thinking has room.
                max_tokens=4096,
                headers=headers,
                reasoning_effort=effort_for_call(
                    endpoint_url, model, getattr(session, "owner", None), session
                ),
            )

            # Parse JSON, tolerating reasoning-model noise (<think> blocks, a
            # ```json fence, and leading/trailing commentary). See
            # _parse_extraction_json — returns [] rather than raising.
            facts = _parse_extraction_json(raw)
        except Exception as e:
            logger.warning(f"LLM memory extraction failed; using fallback candidates if available: {e}")

        if not isinstance(facts, list):
            facts = []

        # The extraction model sees assistant/tool text and can mistake a
        # failed lookup or a one-off request for a durable personal fact.
        # Only its claims with a quoted user assertion may enter memory.
        facts = [fact for fact in facts if isinstance(fact, dict)
                 and _has_user_fact_evidence(fact, stripped_recent)]
        if fallback_facts:
            facts.extend(fallback_facts)

        if not facts:
            logger.info("Auto memory extraction ran: 0 candidates")
            return

        # Get owner from session
        _owner = getattr(session, 'owner', None)

        # Strict load: this is a read-modify-write. Degrading to [] here would
        # save only the newly extracted facts and drop the entire store.
        try:
            existing = memory_manager.load_all_for_update()
        except MemoryStoreUnreadable as e:
            logger.error("Skipping auto memory extraction, store unreadable: %s", e)
            return
        added = 0
        auto_pinned_identity_count = sum(
            1 for entry in existing
            if _is_owner_memory(entry, _owner) and _is_auto_pinned_identity(entry)
        )

        for fact in facts:
            if isinstance(fact, str):
                fact_text = fact
                category = "fact"
            elif isinstance(fact, dict):
                fact_text = fact.get("text", "").strip()
                category = str(fact.get("category", "fact") or "fact")
            else:
                continue

            if not fact_text or len(fact_text) < 5:
                continue

            # A fuller owner-specific name must not rewrite a shared legacy
            # entry, but that less-specific entry must not suppress it either.
            legacy_refinements = {
                id(entry) for entry in existing
                if _owner and entry.get("owner") is None
                and _name_enrichment_target(fact_text, [entry]) is entry
            }

            # Dedup: check vector similarity first (fast), then exact text match.
            # A runtime embedding/ChromaDB failure (backend OOM, model evicted,
            # remote endpoint down) must not abort the whole batch — fall through
            # to the text/fuzzy dedup below instead of losing every validated
            # fact extracted this session. (`.healthy` is only set at init, so
            # it does not catch failures that develop later.)
            if memory_vector and memory_vector.healthy:
                try:
                    existing_id = memory_vector.find_similar(fact_text, threshold=0.72)
                except Exception as e:
                    logger.warning(f"Memory dedup (vector) unavailable, using text fallback: {e}")
                    existing_id = None
                if existing_id:
                    # The vector store is a single shared collection with no
                    # owner metadata, so find_similar can return ANOTHER
                    # tenant's memory. Only treat it as a duplicate when the
                    # match is this user's own (or a legacy unowned) memory —
                    # otherwise the user's freshly-extracted fact would be
                    # silently dropped. Mirror the owner predicate used by the
                    # text dedup below; cross-tenant/stale matches fall through.
                    _match = next((e for e in existing if e.get("id") == existing_id), None)
                    if (_match is not None and id(_match) not in legacy_refinements
                            and (_match.get("owner") == _owner or _match.get("owner") is None)):
                        logger.debug(f"Memory dedup (vector): '{fact_text[:50]}' matches {existing_id}")
                        continue

            # Text dedup fallback: exact match + fuzzy similarity
            user_existing = [e for e in existing
                             if (e.get("owner") == _owner or e.get("owner") is None)
                             and id(e) not in legacy_refinements] if _owner else existing

            # Name enrichment: if an existing name fact is a strict prefix of
            # this more complete one, upgrade it in place instead of adding a
            # near-duplicate ("name is Felix" -> "name is Felix Arvid Ulf
            # Kjellberg"). The vector store upserts by id, so re-adding is safe.
            # Legacy unowned entries may be visible to multiple owners; never
            # rewrite one with a private fact from the current owner.
            _enrich = _name_enrichment_target(
                fact_text, [e for e in user_existing if e.get("owner") == _owner]
            )
            if _enrich is not None:
                _enrich["text"] = fact_text
                _enrich["timestamp"] = int(time.time())
                if category:
                    _enrich["category"] = category
                if memory_vector and memory_vector.healthy:
                    try:
                        memory_vector.add(_enrich["id"], fact_text)
                    except Exception as e:
                        logger.warning(f"Memory vector update failed for {_enrich['id']}: {e}")
                added += 1
                continue

            if memory_manager.find_duplicates(fact_text, user_existing):
                continue
            # Fuzzy text similarity check (catches rephrased duplicates when vector index is unavailable)
            if _is_text_duplicate(fact_text, user_existing):
                logger.debug(f"Memory dedup (fuzzy): '{fact_text[:50]}' too similar to existing")
                continue

            entry = memory_manager.add_entry(fact_text, source="auto", category=category, owner=_owner)
            # Auto-pin only the first few identity/contact facts. Extra identity
            # memories are still saved, but they must be recalled by relevance
            # instead of riding along in every prompt forever.
            if (
                category.lower() in {"identity", "contact"}
                and auto_pinned_identity_count < AUTO_PINNED_IDENTITY_LIMIT
            ):
                entry["pinned"] = True
                auto_pinned_identity_count += 1
            if hasattr(session, "session_id"):
                entry["session_id"] = session.session_id
            elif hasattr(session, "name"):
                entry["session_id"] = session.name

            existing.append(entry)

            # Add to vector index. The JSON store (saved below) is the source of
            # truth and the keyword path can still retrieve this entry, so a vector
            # write failure must not drop the fact or abort the remaining batch.
            if memory_vector and memory_vector.healthy:
                try:
                    memory_vector.add(entry["id"], fact_text)
                except Exception as e:
                    logger.warning(f"Memory vector add failed for {entry['id']}: {e}")

            added += 1

        if added > 0:
            memory_manager.save(existing)
            try:
                from src.event_bus import fire_event
                for _ in range(added):
                    fire_event("memory_added", _owner)
            except Exception:
                logger.debug("memory_added event dispatch failed", exc_info=True)
            logger.info(f"Auto-extracted {added} memories from session")

            global _extractions_since_audit
            _extractions_since_audit += added
            if _extractions_since_audit >= AUDIT_INTERVAL:
                _extractions_since_audit = 0
                logger.info("Audit threshold reached, running memory audit")
                await audit_memories(
                    memory_manager, memory_vector, endpoint_url, model, headers, owner=_owner
                )
        else:
            logger.info("Auto memory extraction ran: 0 added")

    except Exception as e:
        logger.error(f"Memory extraction failed: {e}")


async def update_persona_memory(
    session,
    preset_manager,
    character_name: str,
    endpoint_url: str,
    model: str,
    headers: Optional[dict] = None,
    schema: str = "general",
):
    """Update the active persona's continuity notes from recent conversation.

    Persona memory is stored with the persona/template data, not in the global
    memory DB, so deleting a saved persona also deletes its notes.
    """
    character_name = (character_name or "").strip()
    if not character_name or not endpoint_url or not model or preset_manager is None:
        return

    try:
        from src.llm_core import llm_call_async
        from src.utility_effort import effort_for_call
        from src.text_helpers import strip_think

        custom = {}
        try:
            custom = preset_manager.presets.get("custom", {}) if isinstance(preset_manager.presets, dict) else {}
        except Exception:
            custom = {}
        existing_memory = ""
        if isinstance(custom, dict) and custom.get("character_name") == character_name:
            existing_memory = custom.get("persona_memory", "") or ""

        messages = session.get_context_messages()
        recent = messages[-CONTEXT_WINDOW:] if len(messages) > CONTEXT_WINDOW else messages
        if len(recent) < 2:
            return

        lines = []
        for msg in recent:
            role = msg.get("role")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            content = str(content or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        if not lines:
            return

        system_prompt = HEALTH_PERSONA_MEMORY_SYSTEM_PROMPT if schema == "health" else PERSONA_MEMORY_SYSTEM_PROMPT
        raw = await llm_call_async(
            endpoint_url,
            model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"Persona name: {character_name}\n\n"
                    f"Existing continuity notes:\n{existing_memory or '(none)'}\n\n"
                    "Recent transcript:\n"
                    + "\n\n".join(lines)
                    + "\n\nReturn only the updated continuity notes."
                )},
            ],
            temperature=0.1,
            max_tokens=1200,
            headers=headers,
            reasoning_effort=effort_for_call(
                endpoint_url, model, getattr(session, "owner", None), session
            ),
        )

        updated = strip_think(str(raw or ""), prose=True, prompt_echo=True).strip()
        # No leading `\s*` before the closing fence: .strip() drops that
        # whitespace anyway, and scanning it from every offset was quadratic.
        updated = re.sub(r"^```(?:text|markdown)?\s*|```$", "", updated, flags=re.I | re.S).strip()
        if len(updated) > 6000:
            updated = updated[:6000].rstrip()
        if updated == existing_memory:
            return
        if preset_manager.update_persona_memory(character_name, updated):
            logger.info("Updated persona memory for %s", character_name)
    except Exception as e:
        logger.warning("Persona memory update failed: %s", e)


async def audit_memories(
    memory_manager,
    memory_vector,
    endpoint_url: str,
    model: str,
    headers: Optional[dict] = None,
    owner: Optional[str] = None,
):
    """Send all memories to the LLM for deduplication and consolidation.

    - Merges near-duplicate entries
    - Rewrites vague entries to be concise
    - Removes junk / non-personal entries
    - Rebuilds the vector index afterwards

    Safe to call manually or from the automatic trigger in extract_and_store.
    Errors are logged, never raised.
    """
    try:
        from src.llm_core import llm_call_async
        from src.utility_effort import effort_for_call

        existing = memory_manager.load(owner=owner)
        if not existing:
            logger.info("Memory audit: nothing to audit")
            return {"before": 0, "after": 0}

        before_count = len(existing)

        # Skip the LLM call entirely when this exact set of memories was
        # already audited — the previous tidy left them in a clean state
        # and nothing has changed since. Returns instantly so the UI shows
        # "Already clean" without spending 30-120s on a wasted LLM round.
        # The fingerprint includes id+text+category; any add/edit/delete
        # invalidates it and the audit runs normally.
        current_fp = _fingerprint_entries(existing)
        last_state = _load_tidy_state(memory_manager).get(owner or "") or {}
        if last_state.get("fingerprint") == current_fp:
            logger.info("Memory audit: state unchanged since last tidy — skipping LLM")
            return {
                "before": before_count,
                "after": before_count,
                "already_tidy": True,
            }

        # Build payload: list of {id, text, category} for the LLM
        memory_payload = [
            {"id": m["id"], "text": m["text"], "category": m.get("category", "fact")}
            for m in existing
        ]

        audit_messages = [
            {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(memory_payload, ensure_ascii=False)},
        ]

        raw = await llm_call_async(
            endpoint_url,
            model,
            audit_messages,
            temperature=0.1,
            # 16384 (was 2000): the deduped list of all memories can be large,
            # and a reasoning model spends tokens thinking first — 2000 truncated
            # the JSON so it never parsed ("bad_json").
            max_tokens=16384,
            headers=headers,
            # Bound the call so the Tidy whirlpool can't spin indefinitely on a
            # slow/large generation.
            timeout=120,
            reasoning_effort=effort_for_call(endpoint_url, model, owner),
        )

        # Parse the JSON list, tolerating reasoning-model noise: <think> blocks,
        # markdown fences, leading prose, and trailing commas.
        import re as _re
        from src.text_helpers import strip_closed_think_blocks
        text = (raw or "").strip()
        text = strip_closed_think_blocks(text).strip()

        def _loads_list(s):
            if not s:
                return None
            for cand in (s, _re.sub(r',(\s*[}\]])', r'\1', s)):
                try:
                    v = json.loads(cand)
                    if isinstance(v, list):
                        return v
                except Exception:
                    continue
            return None

        cleaned = _loads_list(text)
        if cleaned is None:
            # Possessive `\s*+`: handing fence whitespace back to the body on a
            # missing closing fence only rescanned the same tail (ReDoS).
            _m = _re.search(r'```(?:json)?\s*+\n?([\s\S]*?)```', text)
            if _m:
                cleaned = _loads_list(_m.group(1).strip())
        if cleaned is None:
            _a, _b = text.find('['), text.rfind(']')
            if _a >= 0 and _b > _a:
                cleaned = _loads_list(text[_a:_b + 1])
        if cleaned is None:
            logger.error(f"Memory audit returned non-JSON: {text[:300]}")
            return {"before": before_count, "after": before_count, "error": "bad_json"}

        # Build lookup of original entries by ID so we can preserve metadata
        originals = {m["id"]: m for m in existing}

        final_entries = []
        for item in cleaned:
            if not isinstance(item, dict):
                continue
            mid = item.get("id", "")
            new_text = item.get("text", "").strip()
            if not new_text:
                continue

            if mid in originals:
                # Preserve original metadata, update text + category
                entry = originals[mid].copy()
                entry["text"] = new_text
                if item.get("category"):
                    entry["category"] = item["category"]
            else:
                # ID not found — skip to avoid inventing entries
                logger.debug(f"Audit returned unknown id {mid}, skipping")
                continue

            final_entries.append(entry)

        after_count = len(final_entries)

        # Safety net against catastrophic over-deletion. A conservative tidy
        # should never wipe out half the store in one pass — if the model
        # returned far fewer entries than it was given (over-consolidation, a
        # dropped/truncated list, or it ignored ids), treat it as a misfire and
        # DON'T save. Better to no-op than to silently lose memories.
        if before_count >= 8 and after_count < before_count * 0.5:
            logger.warning(
                f"Memory audit would cut {before_count} -> {after_count} "
                f"(>50% removed) — refusing as unsafe, keeping originals"
            )
            return {"before": before_count, "after": before_count, "error": "unsafe_removal"}

        # Merge audited entries back with other users' entries
        if owner:
            # Strict load: the merge below reconstructs the whole file. If this
            # degraded to [] we would save only this owner's audited slice and
            # destroy every other tenant's memories.
            try:
                all_entries = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable as e:
                logger.error("Aborting memory audit save, store unreadable: %s", e)
                return {
                    "before": before_count,
                    "after": before_count,
                    "error": "store_unreadable",
                }
            audited_ids = {e["id"] for e in final_entries}
            other_entries = [e for e in all_entries if e.get("owner") != owner and (e.get("owner") is not None)]
            # Also keep legacy entries that weren't part of this audit
            for e in all_entries:
                if e.get("owner") is None and e["id"] not in audited_ids and e["id"] not in {o["id"] for o in other_entries}:
                    other_entries.append(e)
            saved_entries = final_entries + other_entries
        else:
            saved_entries = final_entries
        memory_manager.save(saved_entries)
        logger.info(
            f"Memory audit complete: {before_count} -> {after_count} entries "
            f"({before_count - after_count} removed/merged)"
        )

        # Rebuild vector index from the full saved set, not just this owner's
        # slice — otherwise the shared collection is wiped of every other
        # owner's entries until they happen to run their own audit.
        if memory_vector and memory_vector.healthy:
            memory_vector.rebuild(saved_entries)

        # Persist the post-tidy fingerprint so the next call short-circuits
        # if nothing has changed in the meantime.
        _save_tidy_state(memory_manager, owner, _fingerprint_entries(final_entries))

        return {"before": before_count, "after": after_count}

    except Exception as e:
        logger.error(f"Memory audit failed: {e}")
        return {"error": str(e)}
