# _events.py -- chunked from builtin_actions.py
import logging
from datetime import datetime
from typing import Tuple

from src.builtin_actions._email import (
    _email_task_account_id,
    _result_has_work,
    _result_is_config_error,
)
from src.builtin_actions._shared import TaskNoop
from src.interactive_gate import wait_for_interactive_quiet

_TYPE_COLORS = {
    "work":     "#5b8abf",
    "personal": "#a07ae0",
    "health":   "#e06c75",
    "travel":   "#e5a33a",
    "meal":     "#d8b974",
    "social":   "#82c882",
    "admin":    "#888888",
    "other":    "#6b9cb5",
}

_HEURISTIC_TYPES = {
    "health": ["doctor", "dentist", "clinic", "hospital", "appointment", "checkup", "therapy",
               "physio", "chiropract", "vaccine", "blood test", "xray", "scan", "surgery"],
    "travel": ["flight", "airport", "train", "shinkansen", "boarding", "uber", "taxi", "trip",
               "hotel", "airbnb", "depart", "arrival", "check-in", "checkout"],
    "meal": ["lunch", "dinner", "breakfast", "brunch", "coffee", "drinks", "restaurant",
             "reservation", "bar", "cafe"],
    "social": ["birthday", "party", "hangout", "wedding", "date with", "drinks with",
               "anniversary", "baby shower", "graduation", "picnic", "bbq"],
    "admin": ["bill", "renewal", "tax", "deadline", "filing", "submit", "due date",
              "registration", "license", "passport", "visa", "form"],
    "work": ["meeting", "standup", "sync", "1:1", "1on1", "review", "interview",
             "demo", "presentation", "kickoff", "retro", "all-hands", "town hall",
             "call with", "client", "deck"],
}

_HEURISTIC_HIGH = ["flight", "interview", "wedding", "surgery", "exam", "deadline",
                   "court", "presentation", "demo", "kickoff", "launch"]
_HEURISTIC_CRITICAL = ["surgery", "court", "wedding day", "funeral", "delivery date"]

logger = logging.getLogger(__name__)

def _classify_event_heuristic(summary: str) -> tuple:
    """Quick heuristic classification — returns (event_type, importance) or (None, None) if unclear."""
    s = (summary if isinstance(summary, str) else "").lower()
    etype = None
    for t, kws in _HEURISTIC_TYPES.items():
        if any(k in s for k in kws):
            etype = t
            break
    if any(k in s for k in _HEURISTIC_CRITICAL):
        return etype, "critical"
    if any(k in s for k in _HEURISTIC_HIGH):
        return etype, "high"
    return etype, None



def _memory_context_lines(mems, limit: int = 40) -> list:
    """Render Memory rows into short personal-context bullets for event classify.

    Reads the Memory ORM `text` column. The previous inline code read a
    non-existent `content` attribute, so it raised AttributeError on the first
    row, the surrounding except swallowed it, and the classifier ran with no
    personal context at all. getattr keeps it robust to future schema drift.
    """
    lines: list = []
    for m in mems:
        c = (getattr(m, "text", "") or "").strip()
        if c:
            lines.append(f"- {c[:200]}")
        if len(lines) >= limit:
            break
    return lines



async def action_classify_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Hybrid classification of upcoming calendar events: fast heuristic for
    obvious cases, LLM fallback for ambiguous ones. Assigns event_type +
    importance + color. Re-classifies anything not already set."""
    try:
        from datetime import timedelta
        from core.database import SessionLocal, CalendarEvent
        from src.llm_core import llm_call_async_with_fallback
        import re as _re, json as _json

        db = SessionLocal()
        try:
            now = datetime.utcnow()
            horizon = now + timedelta(days=30)
            events = db.query(CalendarEvent).filter(
                CalendarEvent.dtstart >= now,
                CalendarEvent.dtstart <= horizon,
                CalendarEvent.status != "cancelled",
            ).all()
            if not events:
                return "No upcoming events to classify", True

            from src.task_endpoint import resolve_task_candidates
            llm_candidates = resolve_task_candidates(owner=owner)
            llm_available = bool(llm_candidates)

            # Pull user memories so the LLM has personal context (relationships,
            # job, hobbies). Helps it know e.g. "<name> is your spouse" so their
            # events are personal/social, not work.
            _memory_context = ""
            try:
                from core.database import Memory as _Mem
                _mems = db.query(_Mem).filter(_Mem.owner == owner).limit(60).all() if owner else []
                _lines = _memory_context_lines(_mems)
                if _lines:
                    _memory_context = "USER CONTEXT (relationships, work, life):\n" + "\n".join(_lines) + "\n\n"
            except Exception as _me:
                logger.warning(f"Could not load memory for classify: {_me}")

            classified_h = 0
            classified_llm = 0
            failed = 0
            unchanged = 0
            # Pass 1: heuristic for obvious cases, collect ambiguous for LLM batch
            llm_queue = []  # list of CalendarEvent objects needing LLM
            for ev in events:
                if ev.event_type and ev.importance and ev.importance != "normal":
                    unchanged += 1
                    continue
                etype, importance = _classify_event_heuristic(ev.summary or "")
                if etype and importance:
                    ev.event_type = etype
                    ev.color = _TYPE_COLORS.get(etype)
                    ev.importance = importance
                    classified_h += 1
                    continue
                # Apply partial heuristic; queue for LLM to fill missing
                if etype:
                    ev.event_type = etype
                    ev.color = _TYPE_COLORS.get(etype)
                if llm_available:
                    llm_queue.append(ev)
                elif etype:
                    classified_h += 1
            # Persist heuristic results before LLM pass (in case LLM is slow/unavailable)
            try:
                db.commit()
            except Exception:
                pass

            # Pass 2: batch LLM classification (10 events per call)
            BATCH = 10
            for i in range(0, len(llm_queue), BATCH):
                batch = llm_queue[i:i+BATCH]
                items = [
                    {"i": idx, "title": (ev.summary or "")[:120],
                     "when": ev.dtstart.isoformat() if ev.dtstart else "",
                     "loc": (ev.location or "")[:80]}
                    for idx, ev in enumerate(batch)
                ]
                prompt = (
                    _memory_context +
                    "Classify these calendar events using the USER CONTEXT above (people they know, "
                    "their job, hobbies). Return ONLY a raw JSON array, no prose, no markdown.\n"
                    "Each item: {\"i\": <index>, \"type\": \"work|personal|health|travel|meal|social|admin|other\", "
                    "\"importance\": \"low|normal|high|critical\"}\n\n"
                    "Type guidance:\n"
                    "- personal = family, partner, kids, pets, errands, home stuff\n"
                    "- social = friends, parties, birthdays, hangouts\n"
                    "- work = the user's own job/career commitments only (not their partner's)\n"
                    "- health = doctor, gym, therapy\n"
                    "- travel = flights, trips, hotels\n"
                    "- meal = lunch/dinner/coffee specifically\n"
                    "- admin = bills, taxes, paperwork\n"
                    "- other = anything else\n\n"
                    "Importance guide: critical = surgery/court/wedding day; high = flight/interview/big presentation/exam; "
                    "normal = regular meetings/appointments; low = recurring routine.\n\n"
                    f"EVENTS: {_json.dumps(items)}"
                )
                try:
                    await wait_for_interactive_quiet("calendar classification action")
                    raw = await llm_call_async_with_fallback(
                        llm_candidates,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1, max_tokens=16384,
                        timeout=180,
                    )
                    from src.text_helpers import strip_think as _st
                    raw = _st(raw or "", prose=False, prompt_echo=False)
                    raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.MULTILINE).strip()
                    m = _re.search(r"\[.*\]", raw, _re.DOTALL)
                    if not m:
                        logger.warning(f"[classify-llm] no JSON array in response: {raw[:300]!r}")
                        failed += len(batch)
                        continue
                    arr = _json.loads(m.group())
                    by_idx = {x.get("i"): x for x in arr if isinstance(x, dict)}
                    for idx, ev in enumerate(batch):
                        x = by_idx.get(idx)
                        if not x:
                            failed += 1
                            continue
                        t = (x.get("type") or "other").lower()
                        imp = (x.get("importance") or "normal").lower()
                        if t in _TYPE_COLORS:
                            ev.event_type = t
                            ev.color = _TYPE_COLORS[t]
                        if imp in ("low", "normal", "high", "critical"):
                            ev.importance = imp
                        classified_llm += 1
                        logger.info(f"[classify-llm] '{ev.summary}' → type={t} importance={imp}")
                except Exception as e:
                    logger.warning(f"[classify-llm] batch failed: {e}")
                    failed += len(batch)
                # Commit after each batch so partial progress persists
                try:
                    db.commit()
                except Exception as ce:
                    logger.warning(f"[classify-llm] commit failed: {ce}")
            # Final commit covers heuristic-only updates from pass 1
            db.commit()
            parts = [f"Scanned {len(events)} upcoming event(s)"]
            if classified_h:
                parts.append(f"{classified_h} via heuristic")
            if classified_llm:
                parts.append(f"{classified_llm} via LLM")
            if unchanged:
                parts.append(f"{unchanged} already set (skipped)")
            if failed:
                parts.append(f"{failed} LLM failed")
            return " · ".join(parts), True
        finally:
            db.close()
    except Exception as e:
        logger.error(f"classify_events action failed: {e}")
        return str(e), False



async def action_ping_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Calendar event reminders are now dispatched by Notes."""
    raise TaskNoop("calendar event reminders are handled by Notes")



async def action_extract_email_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Scan recent emails for booking confirmations / meetings / events
    and auto-add them to the calendar."""
    import asyncio as _aio
    try:
        from routes.email_pollers import _run_auto_summarize_once
        account_id = _email_task_account_id(kwargs)
        attempts = [
            ("3d window, 3 emails", 3, 3, 240),
            ("3d window, 2 emails", 3, 2, 150),
            ("1d window, 1 email", 1, 1, 90),
        ]
        timed_out = []
        last_result = ""
        for label, days_back, max_process, timeout in attempts:
            try:
                result = await _aio.wait_for(
                    _run_auto_summarize_once(
                        do_summary=False,
                        do_reply=False,
                        do_calendar=True,
                        days_back=days_back,
                        account_id=account_id,
                        max_process=max_process,
                    ),
                    timeout=timeout,
                )
                last_result = result or ""
                if _result_is_config_error(result):
                    return f"{result} ({label})", False
                if _result_has_work(result):
                    suffix = f"{label}" if not timed_out else f"{label}; retried after timeout"
                    return f"{result} ({suffix})", True
                raise TaskNoop(f"email→calendar: {result or 'no new emails'} ({label})")
            except _aio.TimeoutError:
                timed_out.append(label)
                logger.warning(f"email calendar extraction timed out for {label}; retrying smaller batch")
                continue
        if timed_out:
            raise TaskNoop(
                "email→calendar: calendar extraction timed out on smaller batches; "
                "will retry on the next scheduled run"
            )
        raise TaskNoop(f"email→calendar: {last_result or 'no new emails'}")
    except Exception as e:
        logger.error(f"extract_email_events action failed: {e}")
        return str(e), False



# Sender local-parts (matched exactly or by prefix) whose mail never carries a
# personal signature worth learning. These compare against the local-part
# (before "@"), so role names must NOT include a trailing "@" — "support@" etc.
# could never match a local-part of "support" and were silently dead.
_SIG_SKIP_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "notifications", "notification", "bounce",
    "newsletter", "support", "info", "admin",
)



async def action_learn_sender_signatures(owner: str, **kwargs) -> Tuple[str, bool]:
    """For each sender with ≥3 recent inbox emails, ask the LLM to extract
    the common signature block across their messages. The cached sig is
    served on the `/read` endpoint so the renderer can fold signatures
    consistently from that address (no more heuristic regex juggling).
    Caps at 20 senders per pass; re-runs after 30 days per sender."""
    try:
        import sqlite3 as _sql3
        import re as _re
        import email as _email_mod
        import asyncio as _aio
        from datetime import datetime as _dt, timedelta as _td
        from routes.email_helpers import _email_cache_owner_clause, _imap_connect, SCHEDULED_DB
        from src.llm_core import llm_call_async_with_fallback

        # 1. Pull recent UIDs + From headers cheaply (header-only fetch).
        def _pull_headers():
            results = []
            conn = _imap_connect(None, owner=owner)
            try:
                conn.select("INBOX", readonly=True)
                status, data = conn.search(None, "ALL")
                if status != "OK" or not data or not data[0]:
                    return results
                uids = data[0].split()[-300:][::-1]  # newest 300
                for uid in uids:
                    try:
                        st, msg_data = conn.fetch(
                            uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])"
                        )
                        if st != "OK" or not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                        if not raw:
                            continue
                        msg = _email_mod.message_from_bytes(raw)
                        from_raw = msg.get("From", "")
                        from_addr = _email_mod.utils.parseaddr(from_raw)[1].lower().strip()
                        if not from_addr or "@" not in from_addr:
                            continue
                        results.append({
                            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                            "from_address": from_addr,
                        })
                    except Exception:
                        continue
            finally:
                try: conn.logout()
                except Exception: pass
            return results

        mails = await _aio.to_thread(_pull_headers)
        if not mails:
            return "No emails to scan", True

        # 2. Group by sender; drop addresses that don't carry useful sigs.
        by_sender: dict[str, list[dict]] = {}
        for m in mails:
            addr = m["from_address"]
            local = addr.split("@", 1)[0]
            if any(local == p or local.startswith(p) for p in _SIG_SKIP_PREFIXES):
                continue
            # Skip plus-aliases / list-style addresses too.
            if "+" in local or "-noreply" in addr or "-bounces" in addr:
                continue
            by_sender.setdefault(addr, []).append(m)

        # 3. Eligibility: ≥3 emails AND (no cache OR cache > 30 days old).
        try:
            conn = _sql3.connect(SCHEDULED_DB)
            owner_clause, owner_params = _email_cache_owner_clause(owner)
            cached = {
                r[0]: r[1] for r in conn.execute(
                    f"SELECT from_address, last_built_at FROM sender_signatures WHERE {owner_clause}",
                    owner_params,
                ).fetchall()
            }
            conn.close()
        except Exception:
            cached = {}

        cutoff_iso = (_dt.utcnow() - _td(days=30)).isoformat()
        eligible: list[tuple[str, list[dict]]] = []
        for addr, msgs in by_sender.items():
            if len(msgs) < 3:
                continue
            if cached.get(addr, "") > cutoff_iso:
                continue
            eligible.append((addr, msgs[:5]))  # use up to last 5 emails

        if not eligible:
            return "All sender sigs already cached (or no eligible senders)", True

        from src.task_endpoint import resolve_task_candidates
        candidates = resolve_task_candidates(owner=owner)
        if not candidates:
            return "No LLM endpoint available", False
        model = candidates[0][1]

        analyzed = 0
        no_sig = 0
        for addr, msgs in eligible[:20]:  # cost cap per run

            def _fetch_bodies(_msgs):
                bodies = []
                conn2 = _imap_connect(None, owner=owner)
                try:
                    conn2.select("INBOX", readonly=True)
                    for mm in _msgs:
                        try:
                            st, data = conn2.fetch(mm["uid"], "(BODY.PEEK[TEXT])")
                            if st != "OK" or not data or not data[0]:
                                continue
                            raw = data[0][1] if isinstance(data[0], tuple) else None
                            if not raw:
                                continue
                            text = raw.decode("utf-8", errors="replace")
                            bodies.append(text[:4000])
                        except Exception:
                            continue
                finally:
                    try: conn2.logout()
                    except Exception: pass
                return bodies

            try:
                bodies = await _aio.to_thread(_fetch_bodies, msgs)
            except Exception as e:
                logger.warning(f"sig learner: fetch bodies failed for {addr}: {e}")
                continue
            if len(bodies) < 2:
                continue

            joined = "\n\n---NEXT EMAIL---\n\n".join(bodies[:5])
            prompt = (
                "You are extracting the literal common SIGNATURE block that "
                "appears at the END of multiple emails from the same sender.\n\n"
                "Return ONLY the exact signature text, verbatim, with original "
                "line breaks preserved. If there is no clear common signature "
                "block across these emails, respond with the single token: "
                "NONE\n\n"
                "INCLUDE: title, company, address, phone, email/url lines, "
                "legal disclaimer block.\n"
                "EXCLUDE: greetings ('Hi', 'Dear'), closing phrases on their "
                "own ('Best regards'), the sender's name on its own line, the "
                "body content, quoted/forwarded threads (lines starting with "
                "'>' or 'On ... wrote:' or 'From: ... Sent:').\n\n"
                f"EMAILS FROM {addr}:\n{joined}"
            )

            try:
                await wait_for_interactive_quiet("sender signature action")
                raw = await llm_call_async_with_fallback(
                    candidates,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0, max_tokens=600,
                    timeout=60,
                )
                from src.text_helpers import strip_think as _st
                sig = _st(raw or "", prose=False, prompt_echo=False).strip()
                # Strip surrounding code fences if the LLM added them.
                sig = _re.sub(r"^```[\w]*\n?", "", sig)
                sig = _re.sub(r"\n?```\s*$", "", sig)
                sig = sig.strip()
            except Exception as e:
                logger.warning(f"sig LLM call failed for {addr}: {e}")
                continue

            # NONE sentinel or out-of-bounds → cache a NULL row so we don't
            # re-try for 30 days, then move on.
            if (
                not sig
                or sig.upper().strip().strip(".") == "NONE"
                or len(sig) < 15
                or len(sig) > 3000
            ):
                cached_sig: str | None = None
                no_sig += 1
            else:
                cached_sig = sig

            try:
                conn = _sql3.connect(SCHEDULED_DB)
                owner_value = (owner or "").strip()
                conn.execute(
                    "INSERT OR REPLACE INTO sender_signatures "
                    "(from_address, owner, signature_text, sample_count, last_built_at, model_used, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (addr, owner_value, cached_sig, len(bodies), _dt.utcnow().isoformat(), model, "llm"),
                )
                conn.commit()
                conn.close()
                analyzed += 1
            except Exception as e:
                logger.warning(f"sig cache write failed for {addr}: {e}")

        return f"Learned sigs: {analyzed - no_sig} found, {no_sig} no-sig, of {len(eligible)} eligible", True
    except Exception as e:
        logger.error(f"learn_sender_signatures failed: {e}")
        return str(e), False
