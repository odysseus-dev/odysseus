"""
memory_extractor.py

Background auto-extraction of facts from chat conversations.
After each LLM response, this module sends the last few messages to the LLM
asking it to extract memorable facts, then stores them in both memory.json
and the FAISS vector index.

Periodically audits all memories via LLM to consolidate duplicates,
rewrite vague entries, and remove junk.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Optional

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
    "- Only extract facts the USER stated or clearly implied\n"
    "- Each fact must be a single short sentence (under 15 words)\n"
    "- If a fact is similar to something likely already known, skip it\n"
    "- If nothing durable was revealed, return []\n\n"
    "Return a JSON array of objects with 'text' and 'category' fields.\n"
    "Categories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\n"
    "Return ONLY valid JSON, no markdown fences."
)

# How many recent messages to include for extraction
CONTEXT_WINDOW = 6

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

        m = re.search(r"\bmy name is\s+([A-Za-z][A-Za-z0-9 .'\-]{1,50})\b", text, re.I)
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

        m = re.search(r"\bi (?:prefer|like|love|hate|do not like|don't like)\s+([^.!?\n]{4,100})", text, re.I)
        if m:
            preference = _clean_memory_value(m.group(1), 100)
            if preference:
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


def _is_text_duplicate(new_text: str, existing: list, threshold: float = 0.6) -> bool:
    """Check if new_text is too similar to any existing memory (Jaccard similarity)."""
    new_tokens = set(new_text.lower().split())
    if not new_tokens:
        return False
    for entry in _memory_dicts(existing):
        old_tokens = set(entry.get("text", "").lower().split())
        if not old_tokens:
            continue
        intersection = new_tokens & old_tokens
        union = new_tokens | old_tokens
        if len(intersection) / len(union) >= threshold:
            return True
    return False


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

        extraction_messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        ] + stripped_recent

        facts = []
        try:
            raw = await llm_call_async(
                endpoint_url,
                model,
                extraction_messages,
                temperature=0.1,
                max_tokens=500,
                headers=headers,
            )

            # Parse JSON from response (handle markdown fences if model wraps them)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            try:
                facts = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("Memory extraction returned non-JSON")
        except Exception as e:
            logger.warning(f"LLM memory extraction failed; using fallback candidates if available: {e}")

        if not isinstance(facts, list):
            facts = []

        if fallback_facts:
            facts = list(facts) + fallback_facts

        if not facts:
            logger.info("Auto memory extraction ran: 0 candidates")
            return

        # Get owner from session
        _owner = getattr(session, 'owner', None)

        existing = memory_manager.load_all()
        # New entries are collected separately and merged onto the FRESH on-disk
        # list under the lock at the end. We still append them to `existing` too
        # so the in-loop dedup (user_existing / _is_text_duplicate) sees facts
        # added earlier in this same batch. Saving `existing` wholesale would
        # re-introduce the lost-update race this migration removes.
        new_entries = []
        added = 0

        for fact in facts:
            if isinstance(fact, str):
                fact_text = fact
                category = "fact"
            elif isinstance(fact, dict):
                fact_text = fact.get("text", "").strip()
                category = fact.get("category", "fact")
            else:
                continue

            if not fact_text or len(fact_text) < 5:
                continue

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
                    logger.debug(f"Memory dedup (vector): '{fact_text[:50]}' matches {existing_id}")
                    continue

            # Text dedup fallback: exact match + fuzzy similarity
            user_existing = [e for e in existing if e.get("owner") == _owner or e.get("owner") is None] if _owner else existing
            if memory_manager.find_duplicates(fact_text, user_existing):
                continue
            # Fuzzy text similarity check (catches rephrased duplicates when vector index is unavailable)
            if _is_text_duplicate(fact_text, user_existing):
                logger.debug(f"Memory dedup (fuzzy): '{fact_text[:50]}' too similar to existing")
                continue

            entry = memory_manager.add_entry(fact_text, source="auto", category=category, owner=_owner)
            # Auto-pin identity facts (name, job, location) — core context
            if category == "identity":
                entry["pinned"] = True
            if hasattr(session, "session_id"):
                entry["session_id"] = session.session_id
            elif hasattr(session, "name"):
                entry["session_id"] = session.name

            existing.append(entry)
            new_entries.append(entry)

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
            # Atomically append the new entries onto the FRESH on-disk list so a
            # concurrent writer (e.g. a manual add or the audit) during the LLM
            # call is not clobbered. fn must be fast/in-memory.
            def _append(entries):
                entries.extend(new_entries)
                return entries, None

            await asyncio.to_thread(memory_manager.mutate, _append)
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
        )

        # Parse the JSON list, tolerating reasoning-model noise: <think> blocks,
        # markdown fences, leading prose, and trailing commas.
        import re as _re
        text = (raw or "").strip()
        text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', text, flags=_re.I).strip()

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
            _m = _re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)
            if _m:
                cleaned = _loads_list(_m.group(1).strip())
        if cleaned is None:
            _a, _b = text.find('['), text.rfind(']')
            if _a >= 0 and _b > _a:
                cleaned = _loads_list(text[_a:_b + 1])
        if cleaned is None:
            logger.error(f"Memory audit returned non-JSON: {text[:300]}")
            return {"before": before_count, "after": before_count, "error": "bad_json"}

        # The LLM ran UNLOCKED against a snapshot (`existing`). Build the
        # keep/clean/drop decision keyed by id here, then apply it atomically
        # against the FRESH on-disk list inside one locked mutate(). A blind
        # save of `final_entries` would silently clobber any memory written
        # while the (slow) LLM call was in flight — the lost-update bug.
        #
        # `audited_ids`   — the ids this run actually scanned (this owner's slice).
        # `snapshot_text` — the text each id had when we snapshotted it; an id is
        #                   only dropped/rewritten when the fresh on-disk text
        #                   still matches, so a concurrent edit survives.
        # `clean_ops`     — per-id text/category rewrites the model asked for.
        audited_ids = {m["id"] for m in existing if m.get("id")}
        snapshot_text = {m["id"]: m.get("text") for m in existing if m.get("id")}

        kept_ids = set()
        clean_ops = {}
        for item in cleaned:
            if not isinstance(item, dict):
                continue
            mid = item.get("id", "")
            new_text = item.get("text", "").strip()
            if not new_text:
                continue
            if mid not in audited_ids:
                # ID not in this audit's scope — skip to avoid inventing entries
                logger.debug(f"Audit returned unknown id {mid}, skipping")
                continue
            kept_ids.add(mid)
            ops = {}
            if new_text != snapshot_text.get(mid):
                ops["text"] = new_text
            if item.get("category"):
                ops["category"] = item["category"]
            if ops:
                clean_ops[mid] = ops

        drop_ids = audited_ids - kept_ids
        after_count = len(kept_ids)

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

        # Apply the decision atomically against the fresh on-disk list. Entries
        # outside this audit's scope (other owners, legacy rows, and anything
        # added concurrently during the LLM call) are kept verbatim. Within the
        # scope, drops and rewrites apply ONLY when the fresh text still equals
        # the snapshot, so a concurrent edit to a would-be-dropped/rewritten id
        # wins. Returns (new_list, new_list) so rebuild() gets the full saved set.
        def _merge(entries):
            out = []
            survivors = 0
            for e in entries:
                mid = e.get("id")
                if mid not in audited_ids:
                    out.append(e)
                    continue
                fresh_unchanged = e.get("text") == snapshot_text.get(mid)
                if mid in drop_ids and fresh_unchanged:
                    continue  # dropped by the audit, no concurrent edit
                if mid in clean_ops and fresh_unchanged:
                    ops = clean_ops[mid]
                    if ops.get("text"):
                        e["text"] = ops["text"]
                    if ops.get("category"):
                        e["category"] = ops["category"]
                out.append(e)
                survivors += 1
            return out, (out, survivors)

        saved_entries, after_count = await asyncio.to_thread(memory_manager.mutate, _merge)
        logger.info(
            f"Memory audit complete: {before_count} -> {after_count} entries "
            f"({before_count - after_count} removed/merged)"
        )

        # Rebuild vector index from the full saved set, not just this owner's
        # slice — otherwise the shared collection is wiped of every other
        # owner's entries until they happen to run their own audit.
        if memory_vector and memory_vector.healthy:
            memory_vector.rebuild(saved_entries)

        # Persist the post-tidy fingerprint so the next call short-circuits if
        # nothing has changed. Fingerprint the PERSISTED owner slice so a memory
        # added concurrently (and merged in above) correctly re-triggers a tidy.
        _save_tidy_state(
            memory_manager,
            owner,
            _fingerprint_entries(
                [e for e in saved_entries if e.get("id") in audited_ids]
                if owner
                else saved_entries
            ),
        )

        return {"before": before_count, "after": after_count}

    except Exception as e:
        logger.error(f"Memory audit failed: {e}")
        return {"error": str(e)}
