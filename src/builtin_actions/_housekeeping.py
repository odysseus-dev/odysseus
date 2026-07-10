# _housekeeping.py -- chunked from builtin_actions.py
import logging
from datetime import datetime
from typing import Tuple

from src.builtin_actions._shared import TaskNoop
from src.constants import DEEP_RESEARCH_DIR, TIDY_CALENDAR_STATE_FILE
from src.interactive_gate import wait_for_interactive_quiet

logger = logging.getLogger(__name__)

async def action_tidy_sessions(owner: str, **kwargs) -> Tuple[str, bool]:
    """Delete empty sessions for the owner. Pure heuristic —
    the LLM folder-sort phase is skipped (user opted to keep this task
    LLM-free; sorting can be triggered manually via the Chats UI)."""
    try:
        import asyncio
        from src.session_actions import run_auto_sort
        result = await asyncio.wait_for(
            run_auto_sort(owner, skip_llm=True, delete_throwaway=False),
            timeout=60,
        )
        return result, True
    except asyncio.TimeoutError:
        logger.error("tidy_sessions action timed out")
        return "Chat session tidy timed out", False
    except Exception as e:
        logger.error(f"tidy_sessions action failed: {e}")
        return str(e), False



async def action_tidy_documents(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run tidy on documents for the owner."""
    try:
        from src.document_actions import run_document_tidy
        result = await run_document_tidy(owner)
        return result, True
    except Exception as e:
        logger.error(f"tidy_documents action failed: {e}")
        return str(e), False



async def action_consolidate_memory(owner: str, **kwargs) -> Tuple[str, bool]:
    """Consolidate/deduplicate memories for the owner."""
    try:
        import json
        import re
        from src.constants import DATA_DIR
        from src.llm_core import llm_call_async_with_fallback
        from src.memory import MemoryManager

        manager = MemoryManager(DATA_DIR)
        all_memories = manager.load_all()

        _owner_clean = (owner or "").strip()
        text_limit = 2000

        def _memory_owner(mem: dict) -> str:
            return (mem.get("owner") or "").strip()

        # Built-in housekeeping can run without an owner. In that case scan all
        # memories, but keep every AI prompt/apply step owner-local.
        if _owner_clean:
            memory_groups = {
                _owner_clean: [m for m in all_memories if _memory_owner(m) == _owner_clean]
            }
        else:
            memory_groups = {}
            for mem in all_memories:
                memory_groups.setdefault(_memory_owner(mem), []).append(mem)

        memory_groups = {group_owner: group for group_owner, group in memory_groups.items() if group}
        if not memory_groups:
            raise TaskNoop("no memories to consolidate")

        total_removed = 0
        total_cleaned = 0
        total_scanned = 0
        removed_examples = []
        ai_reasons = []
        ai_used = False

        async def _try_ai_tidy_group(group_owner: str, group_memories: list) -> bool:
            nonlocal all_memories, total_removed, total_cleaned, total_scanned, ai_used
            if len(group_memories) < 2:
                return False

            from src.task_endpoint import resolve_task_candidates
            candidates = resolve_task_candidates(owner=group_owner or None)
            if not candidates:
                return False

            try:
                items = [
                    {
                        "id": m.get("id"),
                        "category": m.get("category", "fact"),
                        "text": (m.get("text") or "").strip()[:text_limit],
                        "truncated": len((m.get("text") or "").strip()) > text_limit,
                    }
                    for m in group_memories
                    if m.get("id") and (m.get("text") or "").strip()
                ]
                if len(items) < 2:
                    return False
                truncated_ids = {item["id"] for item in items if item.get("truncated")}
                prompt = (
                    "You are tidying a user's saved personal memories. Return ONLY raw JSON, no markdown.\n"
                    "Remove memories that are empty, broken, trivial conversation filler, duplicates, or obsolete "
                    "because a clearer newer memory replaces them. Preserve useful personal facts, preferences, "
                    "contacts, project context, and instructions. If memories conflict, keep the clearest/latest "
                    "one and drop the obsolete one.\n\n"
                    "JSON shape:\n"
                    "{\"keep\":[{\"id\":\"existing id\",\"text\":\"cleaned text\",\"category\":\"fact|preference|identity|event|contact|project|instruction\"}],"
                    "\"drop\":[{\"id\":\"existing id\",\"reason\":\"short reason\"}]}\n\n"
                    f"MEMORIES:\n{json.dumps(items, ensure_ascii=False)}"
                )
                await wait_for_interactive_quiet("memory consolidation action")
                raw = await llm_call_async_with_fallback(
                    candidates,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=4096,
                    timeout=120,
                )
                from src.text_helpers import strip_think

                raw = strip_think(raw or "", prose=False, prompt_echo=False).strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    decision = json.loads(raw[start:end + 1])
                    keep_items = decision.get("keep") if isinstance(decision, dict) else None
                    drop_items = decision.get("drop") if isinstance(decision, dict) else None
                    if isinstance(keep_items, list) and isinstance(drop_items, list):
                        by_id = {m.get("id"): m for m in group_memories if m.get("id")}
                        cleaned_by_id = {}
                        for item in keep_items:
                            if not isinstance(item, dict):
                                continue
                            mid = item.get("id")
                            if mid not in by_id:
                                continue
                            text = (item.get("text") or "").strip()
                            if not text:
                                continue
                            cleaned = {
                                "category": (item.get("category") or by_id[mid].get("category") or "fact").strip(),
                            }
                            original_text = (by_id[mid].get("text") or "").strip()
                            if len(original_text) <= text_limit:
                                cleaned["text"] = text
                            cleaned_by_id[mid] = cleaned

                        # Delete only memories the model EXPLICITLY dropped, never
                        # ones it merely omitted from `keep`. Treating the
                        # complement of `keep` as deletions meant a model that
                        # forgot to re-list an id (common) silently destroyed that
                        # memory. Honor the explicit `drop` set instead.
                        drop_ids = {
                            d.get("id")
                            for d in drop_items
                            if isinstance(d, dict) and d.get("id") in by_id
                        }
                        # Never delete a memory the model only saw truncated.
                        drop_ids -= truncated_ids

                        if drop_ids or cleaned_by_id:
                            changed_text = 0
                            group_ref_ids = {id(m) for m in group_memories}
                            kept_all = []
                            for mem in all_memories:
                                if id(mem) not in group_ref_ids:
                                    kept_all.append(mem)
                                    continue
                                mid = mem.get("id")
                                if mid in drop_ids:
                                    continue
                                cleaned = cleaned_by_id.get(mid) or {}
                                if mid in truncated_ids:
                                    cleaned.pop("text", None)
                                if cleaned.get("text") and cleaned["text"] != mem.get("text"):
                                    mem["text"] = cleaned["text"]
                                    changed_text += 1
                                if cleaned.get("category"):
                                    mem["category"] = cleaned["category"]
                                kept_all.append(mem)

                            removed = sum(1 for m in group_memories if m.get("id") in drop_ids)
                            total_scanned += len(group_memories)
                            if removed or changed_text:
                                all_memories = kept_all
                                total_removed += removed
                                total_cleaned += changed_text
                                ai_used = True
                                ai_reasons.extend([
                                    (d.get("reason") or "").strip()
                                    for d in drop_items
                                    if isinstance(d, dict) and (d.get("reason") or "").strip()
                                ])
                            return True
            except Exception as ai_err:
                logger.warning("AI memory tidy failed; falling back to duplicate cleanup: %s", ai_err)
            return False

        for group_owner, group_memories in memory_groups.items():
            if await _try_ai_tidy_group(group_owner, group_memories):
                continue

            seen = {}
            keep_refs = set()
            total_scanned += len(group_memories)
            for mem in group_memories:
                text = (mem.get("text") or "").strip()
                key = " ".join(text.lower().split())
                if not key:
                    if len(removed_examples) < 3:
                        removed_examples.append("(empty)")
                    continue
                if key in seen:
                    if len(removed_examples) < 3:
                        removed_examples.append(text[:60] + ("..." if len(text) > 60 else ""))
                    continue
                seen[key] = mem
                keep_refs.add(id(mem))

            group_removed = len(group_memories) - len(keep_refs)
            if group_removed == 0:
                continue

            group_ref_ids = {id(m) for m in group_memories}
            all_memories = [
                m for m in all_memories
                if id(m) not in group_ref_ids or id(m) in keep_refs
            ]
            total_removed += group_removed

        if total_removed or total_cleaned:
            manager.save(all_memories)
            if ai_used:
                reasons = ai_reasons[:3]
                reason_text = f": {'; '.join(reasons)}" if reasons else ""
                return (
                    f"AI tidied {total_scanned} memories: "
                    f"removed {total_removed}, cleaned {total_cleaned}{reason_text}",
                    True,
                )
            preview = "; ".join(removed_examples)
            extra = f" (+{total_removed - len(removed_examples)} more)" if total_removed > len(removed_examples) else ""
            return f"Removed {total_removed} duplicate(s) of {total_scanned}: {preview}{extra}", True

        raise TaskNoop(f"scanned {total_scanned} memories, no duplicates")
    except Exception as e:
        logger.error(f"consolidate_memory action failed: {e}")
        return str(e), False


# Registry: action name -> async function(owner, **kwargs) -> (result_str, success_bool)



async def action_tidy_research(owner: str, **kwargs) -> Tuple[str, bool]:
    """Remove only broken research files (empty or unparseable JSON).

    Research history lives entirely in data/deep_research/<id>.json and is NOT
    backed by chat-session rows — so a file must never be deleted just because
    no chat session matches its id. Only prune files that fail to load."""
    try:
        from pathlib import Path
        import json as _json
        research_dir = Path(DEEP_RESEARCH_DIR)
        if not research_dir.exists():
            raise TaskNoop("no research directory")
        files = list(research_dir.glob("*.json"))
        removed = []
        for p in files:
            try:
                txt = p.read_text(encoding="utf-8").strip()
                if not txt:
                    raise ValueError("empty file")
                _json.loads(txt)  # valid JSON → keep
            except Exception:
                p.unlink(missing_ok=True)
                removed.append(p.stem[:8])
        if not removed:
            raise TaskNoop(f"scanned {len(files)} research file(s), none broken")
        return f"Removed {len(removed)} broken research file(s) of {len(files)}", True
    except Exception as e:
        logger.error(f"tidy_research action failed: {e}")
        return str(e), False



async def action_tidy_calendar(owner: str, **kwargs) -> Tuple[str, bool]:
    """Find duplicate calendar events (same title + start time) and DELETE the dups,
    keeping the oldest (first-seen) instance.

    Incremental: remembers the newest `created_at` already scanned in
    data/tidy_calendar_state.json. If no events have been added since then,
    short-circuits. Otherwise only events newer than the watermark are candidates
    for deletion, but they're checked against the FULL existing set so a new
    duplicate of an old event still gets caught.
    """
    try:
        import json
        from pathlib import Path
        from core.database import SessionLocal, CalendarEvent
        from sqlalchemy import func

        STATE_FILE = Path(TIDY_CALENDAR_STATE_FILE)
        last_watermark = None
        try:
            if STATE_FILE.exists():
                saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if saved.get("last_created_at"):
                    last_watermark = datetime.fromisoformat(saved["last_created_at"])
        except Exception:
            last_watermark = None

        db = SessionLocal()
        try:
            newest = db.query(func.max(CalendarEvent.created_at)).scalar()
            db.query(CalendarEvent).count()

            # Short-circuit: nothing new since last run
            if last_watermark is not None and newest is not None and newest <= last_watermark:
                raise TaskNoop(f"no new events since watermark {last_watermark.strftime('%Y-%m-%d %H:%M')}")

            events = db.query(CalendarEvent).order_by(CalendarEvent.dtstart).all()
            # Build full seen-set from events at or before the watermark (known-clean).
            # Events after the watermark are candidates for deletion.
            seen = {}
            candidates = []
            no_title = 0
            for e in events:
                title = (e.summary or "").strip()
                if not title:
                    no_title += 1
                    continue
                if last_watermark is None or (e.created_at and e.created_at <= last_watermark):
                    # Known-clean region: first occurrence wins
                    key = (title.lower(), e.dtstart)
                    if key not in seen:
                        seen[key] = e
                    # If a dup exists in the known-clean region (first run, or imported later
                    # with the same created_at), still remove it — fall through to candidate check.
                    else:
                        candidates.append(e)
                else:
                    candidates.append(e)

            removed = []
            for e in candidates:
                title = (e.summary or "").strip()
                key = (title.lower(), e.dtstart)
                if key in seen:
                    when = e.dtstart.strftime('%Y-%m-%d %H:%M') if e.dtstart else '?'
                    removed.append(f"{title} @ {when}")
                    db.delete(e)
                else:
                    seen[key] = e

            if removed:
                db.commit()

            # Persist the new watermark (newest created_at among events that survive)
            try:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                if newest is not None:
                    STATE_FILE.write_text(json.dumps({
                        "last_created_at": newest.isoformat(),
                        "last_run_at": datetime.utcnow().isoformat(),
                        "scanned": len(events),
                        "removed": len(removed),
                    }, indent=2), encoding="utf-8")
            except Exception as se:
                logger.warning(f"tidy_calendar watermark save failed: {se}")

            new_since = len(candidates)
            parts = [f"Scanned {len(events)} event(s), {new_since} new since last run"]
            if removed:
                preview = "; ".join(removed[:5])
                if len(removed) > 5:
                    preview += f" (+{len(removed) - 5} more)"
                parts.append(f"removed {len(removed)} duplicate(s): {preview}")
            if no_title:
                parts.append(f"{no_title} untitled (kept)")
            if not removed and not no_title:
                parts.append("no duplicates")
            return " · ".join(parts), True
        finally:
            db.close()
    except Exception as e:
        logger.error(f"tidy_calendar action failed: {e}")
        return str(e), False
