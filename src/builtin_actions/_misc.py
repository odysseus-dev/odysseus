# _misc.py -- chunked from builtin_actions.py
import logging
from typing import Tuple

from core.constants import internal_api_base
from src.auth_helpers import owner_filter
from src.builtin_actions._shared import TaskDeferred, TaskNoop
from src.constants import COOKBOOK_STATE_FILE, DATA_DIR

logger = logging.getLogger(__name__)

async def action_daily_brief(owner: str, **kwargs) -> Tuple[str, bool]:
    """Build a short morning digest: today's calendar events, unread email count
    + top-N senders/subjects, active todos."""
    try:
        from datetime import datetime as _dt, timedelta as _td
        import json as _json

        from core.database import SessionLocal, CalendarEvent, CalendarCal, Note
        from routes.email_helpers import _imap_connect, _decode_header

        # ----- Calendar: today's events -----
        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + _td(days=1)
        # v2 review HIGH-12: gate the OR-null branch on single-user
        # (unconfigured) deploys only. In a multi-user deploy, one
        # user's daily brief must not include another user's notes or
        # events that happen to be stored with owner=None.
        try:
            from core.auth import AuthManager
            _allow_null = not AuthManager().is_configured
        except Exception:
            _allow_null = False
        db = SessionLocal()
        try:
            ev_q = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.dtstart < tomorrow,
                CalendarEvent.dtend > today,
                CalendarEvent.status != "cancelled",
            )
            if owner:
                ev_q = owner_filter(ev_q, CalendarCal, owner, include_shared=_allow_null)
            events = ev_q.order_by(CalendarEvent.dtstart).all()
            # ----- Notes: pinned + non-archived todos with at least one undone item -----
            n_q = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner:
                n_q = owner_filter(n_q, Note, owner, include_shared=_allow_null)
            notes = n_q.all()
        finally:
            db.close()

        # ----- Email: unread count + top 5 inbox subjects (best-effort) -----
        # Direct IMAP: cheaper than the full _list_emails_sync helper and
        # avoids the module/import coupling that broke this once already.
        unread_count = 0
        recent_subjects: list[tuple[str, str]] = []
        try:
            import email as _email
            conn = _imap_connect(None)
            try:
                conn.select("INBOX", readonly=True)
                status, data = conn.search(None, "UNSEEN")
                uids = (data[0].split() if status == "OK" and data and data[0] else [])
                unread_count = len(uids)
                # Grab headers for the most recent 5 unread (UIDs increase with arrival)
                for uid in uids[-5:][::-1]:
                    try:
                        _, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                        if not msg_data or not msg_data[0]:
                            continue
                        hdr = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                        parsed = _email.message_from_bytes(hdr)
                        subject = _decode_header(parsed.get("Subject") or "") or "(no subject)"
                        from_raw = _decode_header(parsed.get("From") or "") or "?"
                        # Extract just the display name if "Name <addr>" form
                        if "<" in from_raw:
                            name = from_raw.split("<", 1)[0].strip().strip('"') or from_raw
                        else:
                            name = from_raw
                        recent_subjects.append((name, subject))
                    except Exception as fe:
                        logger.debug(f"daily_brief: header fetch for uid {uid} failed: {fe}")
            finally:
                try: conn.logout()
                except Exception: pass
        except Exception as ee:
            logger.debug(f"daily_brief: email fetch failed: {ee}")

        # Pull active todo items from notes
        todo_lines: list[str] = []
        for n in notes:
            if n.note_type == "checklist" and n.items:
                try:
                    items = _json.loads(n.items)
                    pending = [it.get("text", "") for it in items if not it.get("done")]
                    for t in pending[:3]:
                        if t:
                            todo_lines.append(f"{n.title or 'Checklist'}: {t}")
                except Exception:
                    continue
            elif n.pinned and n.title:
                todo_lines.append(n.title)

        # ----- Compose -----
        # %-d is GNU-only; format the day with str() so the brief works on
        # Windows / non-glibc Python builds too.
        date_label = today.strftime(f"%A, %B {today.day}, %Y")

        plain = [f"Daily brief — {date_label}", ""]
        if events:
            plain.append("Calendar:")
            for e in events:
                t = e.dtstart.strftime("%H:%M") if not e.all_day else "all day"
                loc = f" @ {e.location}" if e.location else ""
                plain.append(f"  {t}  {e.summary}{loc}")
            plain.append("")
        else:
            plain.append("Calendar: nothing scheduled.")
            plain.append("")

        plain.append(f"Email: {unread_count} unread")
        for sender, subj in recent_subjects:
            plain.append(f"  · {sender} — {subj}")
        plain.append("")

        if todo_lines:
            plain.append("Todos:")
            for t in todo_lines[:10]:
                plain.append(f"  · {t}")
        else:
            plain.append("Todos: none active.")

        plain_body = "\n".join(plain)

        return plain_body, True
    except Exception as e:
        logger.error(f"daily_brief action failed: {e}")
        return str(e), False



async def action_test_skills(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run the per-skill Test on every skill: agent runs the procedure in a
    sandbox, LLM judges the transcript, verdict is recorded on the skill.
    ADVISORY ONLY — only writes set_audit (never rewrites SKILL.md, never
    demotes status, never overrides confidence)."""
    try:
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR
        from routes.skills_routes import _run_skill_test_once, _skill_test_task

        # #3 SCOPE GUARD: refuse to run on a None/empty owner — otherwise
        # `sm.load(owner=None)` returns every user's skills and we'd cross-
        # test (and write audit verdicts to) other users' data in a
        # multi-user deployment.
        if not owner:
            return "test_skills requires an owner on the task — refusing to run without scope.", False

        sm = SkillsManager(DATA_DIR)
        skills = sm.load(owner=owner)
        names = [s.get("name") for s in skills if s.get("name")]
        if not names:
            raise TaskNoop("no skills to test")

        from src.task_endpoint import resolve_task_candidates
        candidates = resolve_task_candidates(owner=owner)
        if not candidates:
            return "No Default/Utility model configured — set one in Settings.", False

        # #2 NO SILENT MODEL SWAP: if the configured model isn't served by the
        # endpoint, try a basename match — but fail loudly instead of grabbing
        # `avail[0]` which could be an embedding-only model and produce 36
        # garbage transcripts → 36 'unknown' verdicts with no hint why.
        url, model, headers = candidates[0]
        try:
            from src.llm_core import list_model_ids
            import os as _os

            selected = None
            mismatch_notes = []
            for cand_url, cand_model, cand_headers in candidates:
                avail = list_model_ids(cand_url, headers=cand_headers)
                if not avail or cand_model in avail:
                    selected = (cand_url, cand_model, cand_headers)
                    break
                base = _os.path.basename((cand_model or "").rstrip("/"))
                matched = next((a for a in avail if _os.path.basename(a.rstrip("/")) == base), None)
                if matched:
                    selected = (cand_url, matched, cand_headers)
                    break
                mismatch_notes.append(
                    f"{cand_model} not served by {cand_url}; available: "
                    f"{', '.join(avail[:8])}{'...' if len(avail) > 8 else ''}"
                )
            if selected:
                url, model, headers = selected
            elif mismatch_notes:
                return "No configured task fallback model is served. " + " | ".join(mismatch_notes[:3]), False
        except Exception as _e:
            logger.warning(f"test_skills model resolve check failed (continuing): {_e}")

        logger.info(f"test_skills: starting on {len(names)} skills, model={model}, owner={owner!r}")

        from collections import Counter
        tally = Counter()
        per_skill_log = []
        for skill in skills:
            name = skill.get("name")
            if not name:
                continue
            md = sm.read_skill_md(name, owner=owner) or ""
            if not md:
                tally["skipped"] += 1
                per_skill_log.append(f"{name}: skipped (no SKILL.md)")
                continue
            task = _skill_test_task(skill)
            try:
                transcript, verdict = await _run_skill_test_once(md, task, url, model, headers, owner)
                v = (verdict or {}).get("verdict") or "unknown"
                tally[v] += 1
                summary = (verdict or {}).get("summary") or ""
                tlen = len(transcript or "")
                detail = ""
                if v in ("unknown", "inconclusive", "fail", "needs_work"):
                    bits = []
                    if summary: bits.append(summary[:160])
                    if tlen < 200: bits.append(f"transcript {tlen}b")
                    if bits: detail = " — " + "; ".join(bits)
                per_skill_log.append(f"{name}: {v}{detail}")
                # #4 + #8 + #12: ONLY persist a real verdict (pass / needs_work /
                # fail / inconclusive). Skip 'unknown' — that's the judge's
                # "couldn't parse" sentinel, not a real result, and persisting
                # it pollutes the verified-badge UI. Also skip the confidence
                # rewrite entirely — update_skill() re-serialises SKILL.md
                # (contradicts "advisory only" docstring) and overwriting a
                # user-set value (e.g. 1.0 → 0.95) is destructive.
                if v in ("pass", "needs_work", "fail", "inconclusive"):
                    try:
                        sm.set_audit(name, v, by_teacher=False, worker_model=model, owner=owner)
                    except Exception as _e:
                        logger.warning(f"test_skills set_audit({name}) failed: {_e}")
                if v == "unknown":
                    logger.warning(f"test_skills: {name} → unknown — {summary[:200]}; transcript_len={tlen}")
            except Exception as e:
                logger.exception(f"test_skills: {name} errored")
                tally["error"] += 1
                per_skill_log.append(f"{name}: error — {str(e)[:200]}")

        parts = []
        for k in ("pass", "needs_work", "fail", "inconclusive", "unknown", "skipped", "error"):
            if tally.get(k):
                parts.append(f"{tally[k]} {k}")
        header = f"Tested {len(names)} skill(s): " + (" · ".join(parts) or "0")
        # Multi-line result: summary first, then per-skill detail. The Tasks
        # Activity feed renders this verbatim, so the user can see per-skill
        # outcomes + the judge's "why" without checking uvicorn stdout.
        body = "\n".join(per_skill_log)
        return f"{header}\nmodel={model}\n\n{body}", True
    except TaskNoop:
        raise
    except Exception as e:
        logger.error(f"test_skills action failed: {e}")
        return str(e), False



async def action_audit_skills(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run the real skills audit pipeline for skills that have not been audited.

    Unlike test_skills, this uses the same audit logic as the UI Audit all flow:
    metadata narrowing, self-edit/retry, optional teacher rewrite, necessity
    tagging, and publish/draft finalization from the user's confidence threshold.
    """
    try:
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR
        from routes.skills_routes import (
            _resolve_audit_models, _run_audit_all_job, _skill_audit_jobs,
        )

        if not owner:
            return "audit_skills requires an owner — refusing to run without scope.", False

        key = (owner or "",)
        existing = _skill_audit_jobs.get(key)
        if existing and existing.get("status") == "running":
            raise TaskNoop("skill audit already running")

        sm = SkillsManager(DATA_DIR)
        skills = sm.load(owner=owner)
        names = [
            s.get("name") for s in skills
            if s.get("name") and not s.get("audit_verdict")
        ]
        if not names:
            raise TaskNoop("no unaudited skills")

        url, model, headers, teacher = _resolve_audit_models()
        try:
            from src.llm_core import seconds_since_model_activity
            recent = seconds_since_model_activity(url, model)
        except Exception:
            recent = None
        if recent is not None and recent < (20 * 60):
            raise TaskDeferred(
                f"audit model {model} was used {int(recent)}s ago; waiting for quiet window",
                delay_seconds=20 * 60,
            )

        import time as _time
        _skill_audit_jobs[key] = {
            "status": "running", "scope": "scheduled-unchecked", "model": model,
            "teacher": teacher[1] if teacher else None,
            "total": len(names), "done": 0, "current": None,
            "results": [], "log": [
                f"Scheduled audit of {len(names)} unaudited skill(s) with {model}"
                + (f"; teacher {teacher[1]}" if teacher else "")
            ],
            "started": _time.time(), "cancel": False,
        }
        await _run_audit_all_job(key, sm, names, url, model, headers, teacher, owner)
        job = _skill_audit_jobs.get(key, {})
        counts = {}
        for r in job.get("results", []):
            k = r.get("result") or "unknown"
            counts[k] = counts.get(k, 0) + 1
        summary = " · ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "0 results"
        return f"Audited {job.get('done', 0)}/{len(names)} unaudited skill(s): {summary}", True
    except TaskNoop:
        raise
    except Exception as e:
        logger.error(f"audit_skills action failed: {e}")
        return str(e), False



async def action_ping_notes(owner: str, **kwargs) -> Tuple[str, bool]:
    """Background note-due scanner. Fires a reminder for any note whose
    `due_date` falls in the current ±5-minute window and hasn't been pinged
    within the last 25 minutes. Mirrors `action_ping_events` for calendar.

    State (`data/note_pings.json`): {note_id: iso_ts_of_last_ping}. Pruned
    on each run by dropping entries for notes that are gone/archived/replied.
    """
    try:
        import json as _json
        import time as _time
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from pathlib import Path as _P
        from core.database import SessionLocal as _SL, Note as _N

        # Per-owner state file so cache-pruning doesn't cross-delete other
        # users' entries (review C4). Legacy path kept as fallback so a
        # single-user install (empty owner) doesn't lose its history.
        _owner_slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
        STATE = _P(DATA_DIR) / f"note_pings_{_owner_slug}.json"
        STATE.parent.mkdir(parents=True, exist_ok=True)
        # One-time migration: if legacy global file exists and per-owner file
        # doesn't, seed from global (entries for OTHER owners still get pruned
        # on their first run — acceptable, prevents silent loss).
        _legacy = _P(DATA_DIR) / "note_pings.json"
        if _legacy.exists() and not STATE.exists():
            try:
                STATE.write_text(_legacy.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        # Scanner ticks every 60s in _note_pings_loop. 90s window guarantees
        # every note's due time lands inside at least one tick's window.
        WINDOW_SEC = 90
        REPING_MIN = 25     # don't re-ping same note more often than this

        def _parse_due(s: str):
            """Accept '2026-05-29T16:31' (local) or '...Z' (UTC). Returns UTC datetime."""
            if not s:
                return None
            try:
                # Handle the JS-style 'Z' suffix.
                if s.endswith("Z"):
                    return _dt.fromisoformat(s[:-1]).replace(tzinfo=_tz.utc)
                # Naive → assume local server time.
                d = _dt.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.astimezone().astimezone(_tz.utc)
                return d.astimezone(_tz.utc)
            except Exception:
                return None

        try:
            cache = _json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        except Exception:
            cache = {}

        db = _SL()
        try:
            q = db.query(_N).filter(_N.archived == False)  # noqa: E712
            q = q.filter(_N.due_date.isnot(None), _N.due_date != "")
            if owner:
                # Match owner OR legacy null-owner notes (single-user installs).
                q = owner_filter(q, _N, owner)
            notes = q.all()
            if not notes:
                raise TaskNoop("no notes with due dates")

            now = _dt.now(_tz.utc)
            window = _td(seconds=WINDOW_SEC)
            reping_cutoff = now - _td(minutes=REPING_MIN)
            seen_ids = set()
            sent = []

            for n in notes:
                seen_ids.add(n.id)
                due = _parse_due(n.due_date)
                if not due:
                    continue
                # Inside the ±5min window?
                if abs((due - now).total_seconds()) > window.total_seconds():
                    continue
                # Recently pinged? Skip.
                last = cache.get(n.id)
                if last:
                    try:
                        if isinstance(last, dict):
                            last = last.get("at")
                        last_dt = _dt.fromisoformat(str(last))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=_tz.utc)
                        if last_dt >= reping_cutoff:
                            continue
                    except Exception:
                        pass
                # Compose + dispatch.
                title = (n.title or "Reminder").strip() or "Reminder"
                body_parts = []
                if n.content:
                    body_parts.append(n.content[:400])
                # Items: list pending checklist entries inline.
                if n.items:
                    try:
                        items = _json.loads(n.items)
                        pending = [
                            it.get("text", "")
                            for it in items
                            if not it.get("done") and not it.get("checked")
                        ]
                        if pending:
                            body_parts.append("Pending:\n" + "\n".join(f"- {t}" for t in pending[:8]))
                    except Exception:
                        pass
                body = "\n\n".join(p for p in body_parts if p) or title
                try:
                    from routes.note_routes import dispatch_reminder
                    await dispatch_reminder(
                        title=title, note_body=body, note_id=n.id,
                        owner=n.owner or owner or "",
                    )
                    cache[n.id] = now.isoformat()
                    sent.append(title)
                except Exception as e:
                    logger.warning(f"ping_notes: dispatch failed for {n.id}: {e}")

            # Prune cache entries for notes that no longer exist.
            for stale in [k for k in cache if k not in seen_ids]:
                cache.pop(stale, None)

            try:
                STATE.write_text(_json.dumps(cache), encoding="utf-8")
            except Exception as e:
                logger.warning(f"ping_notes: cache write failed: {e}")

            if not sent:
                raise TaskNoop(f"scanned {len(notes)} note(s), none due in ±{WINDOW_SEC}s")
            preview = "; ".join(sent[:3])
            extra = f" (+{len(sent) - 3} more)" if len(sent) > 3 else ""
            return f"Pinged {len(sent)} note(s): {preview}{extra}", True
        finally:
            db.close()
    except TaskNoop:
        raise
    except Exception as e:
        logger.exception("ping_notes action failed")
        return str(e), False



async def action_cookbook_serve(
    owner: str,
    task_name: str = "",
    progress_cb=None,
    command: str = "",
    **kwargs,
) -> Tuple[str, bool]:
    """Launch a Cookbook model serve as a scheduled task.

    `command` is the JSON config string the task carries in `prompt`,
    of shape: {"preset": "name"} OR {"repo_id": "...", "cmd": "...", "host": "..."}.
    Optional `end_after_min: N` schedules a hard-stop N minutes after launch
    (handled by cookbook_serve_lifecycle_loop in src/cookbook_serve_lifecycle.py).
    """
    import json
    import time as _time
    import httpx
    from pathlib import Path
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from core.atomic_io import atomic_write_json

    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    try:
        cfg = json.loads(command or "{}")
    except Exception:
        return f"Invalid JSON config: {command!r}", False
    if not isinstance(cfg, dict):
        return "Config must be a JSON object", False

    # Resolve the preset (if named) OR fall through with explicit fields.
    preset_name = (cfg.get("preset") or "").strip()
    repo_id = (cfg.get("repo_id") or "").strip()
    cmd = (cfg.get("cmd") or "").strip()
    host = (cfg.get("host") or cfg.get("remote_host") or "").strip()
    try:
        end_after_min = int(cfg.get("end_after_min") or 0)
    except Exception:
        end_after_min = 0
    set_default = bool(cfg.get("set_default", True))

    state_path = Path(COOKBOOK_STATE_FILE)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}

    # Preset lookup. Try three matching strategies in order so the
    # schedule still works even when the user's preset is named
    # differently from the model's short name:
    #
    #   1. Exact preset.name == preset_name (case-insensitive)
    #   2. preset.model / preset.modelId == repo_id  (caller knows the repo)
    #   3. preset.model's short name (after final /) == preset_name
    #
    # Without #2 and #3, scheduling "Qwen3.5-397B-A17B-AWQ" failed when
    # the saved preset was named "vllm-qwen-397b" or had the model field
    # populated with the full HF repo path. Either should resolve.
    def _short(name: str) -> str:
        return (name or "").rsplit("/", 1)[-1].lower()

    if not cmd or not repo_id:
        presets = state.get("presets") or []
        chosen = None
        # Strategy 1: exact name match.
        if preset_name:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and (p.get("name") or "").lower() == preset_name.lower()),
                None,
            )
        # Strategy 2: repo_id matches the preset's model field.
        if chosen is None and repo_id:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and (p.get("model") or p.get("modelId") or "").lower() == repo_id.lower()),
                None,
            )
        # Strategy 3: model's short name matches the preset_name.
        if chosen is None and preset_name:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and _short(p.get("model") or p.get("modelId") or "") == preset_name.lower()),
                None,
            )
        if chosen is not None:
            repo_id = repo_id or chosen.get("model") or chosen.get("modelId") or ""
            cmd = cmd or (chosen.get("cmd") or "").strip()
            host = host or chosen.get("host") or chosen.get("remoteHost") or ""
    if not repo_id or not cmd or cmd.startswith("(adopted"):
        # Surface what we tried so the user can name their preset to match.
        preset_names = [(p.get("name") or "") for p in (state.get("presets") or []) if isinstance(p, dict)]
        hint = f" Saved presets: {preset_names!r}" if preset_names else ""
        return (f"No launchable config for {preset_name!r} (repo_id={repo_id!r}). "
                f"Check Cookbook → Presets has a real cmd, not 'adopted'.{hint}", False)

    # Resolve env_prefix etc. from the host's saved cookbook server entry,
    # matching the chat agent's serve_model path.
    body = {"repo_id": repo_id, "cmd": cmd}
    if host:
        body["remote_host"] = host
    env = (state.get("env") or {})
    srv = next(
        (s for s in (env.get("servers") or [])
         if isinstance(s, dict) and (s.get("host") == host or s.get("name") == host)),
        {},
    )
    if srv.get("env") == "venv" and srv.get("envPath"):
        body["env_prefix"] = f"source {srv['envPath']}/bin/activate"
    elif srv.get("env") == "conda" and srv.get("envPath"):
        body["env_prefix"] = f"conda activate {srv['envPath']}"
    if srv.get("hfToken"): body["hf_token"] = srv["hfToken"]
    if srv.get("port"): body["ssh_port"] = str(srv["port"])
    if srv.get("platform"): body["platform"] = srv["platform"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{internal_api_base()}/api/model/serve",
                                  json=body, headers=headers)
            data = r.json() if r.content else {}
    except Exception as e:
        return f"Launch HTTP failed: {e}", False
    if not data.get("ok"):
        return f"Launch rejected: {data.get('error') or data.get('detail') or 'unknown'}", False

    sid = data.get("session_id") or ""
    endpoint_id = data.get("endpoint_id") or ""
    # Scheduled serves are usually meant to become the active local model for
    # chat/tools while their time window is open. Persist both endpoint and
    # model so task/utility/default resolution does not keep routing to a stale
    # API fallback. Allow explicit opt-out with {"set_default": false}.
    if endpoint_id and set_default:
        try:
            selected_model = repo_id
            try:
                from core.database import SessionLocal as _SL, ModelEndpoint as _ME
                _db = _SL()
                try:
                    _ep = _db.query(_ME).filter(_ME.id == endpoint_id).first()
                    if _ep and _ep.cached_models:
                        _models = json.loads(_ep.cached_models or "[]")
                        if isinstance(_models, list) and _models:
                            selected_model = str(_models[0])
                finally:
                    _db.close()
            except Exception:
                pass
            from src.settings import load_settings as _load_settings, save_settings as _save_settings
            _settings = _load_settings()
            _settings["default_endpoint_id"] = endpoint_id
            _settings["default_model"] = selected_model
            # Keep background tasks aligned unless the user explicitly chose a
            # separate task model.
            if not (_settings.get("task_endpoint_id") or "").strip():
                _settings["task_endpoint_id"] = endpoint_id
                _settings["task_model"] = selected_model
            if not (_settings.get("utility_endpoint_id") or "").strip():
                _settings["utility_endpoint_id"] = endpoint_id
                _settings["utility_model"] = selected_model
            _save_settings(_settings)
            if owner:
                from routes.prefs_routes import _load_for_user, _save_for_user
                _prefs = _load_for_user(owner)
                _prefs["default_endpoint_id"] = endpoint_id
                _prefs["default_model"] = selected_model
                if not (_prefs.get("utility_endpoint_id") or "").strip():
                    _prefs["utility_endpoint_id"] = endpoint_id
                    _prefs["utility_model"] = selected_model
                _save_for_user(owner, _prefs)
        except Exception as e:
            logger.warning(f"cookbook_serve: default endpoint update failed: {e}")
    # Register the new task in cookbook_state.json + stamp it with our
    # scheduler-owner markers. /api/model/serve spawns the tmux session
    # but leaves the state-write to the UI — when a scheduled action
    # launches a serve from server-side, NOBODY writes the task into
    # state, so the Cookbook tab never shows it. We do the write here.
    if sid:
        try:
            # Re-read fresh (the route may have updated state already).
            try:
                fresh = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                fresh = {}
            if not isinstance(fresh, dict):
                fresh = {}
            tasks = fresh.get("tasks") if isinstance(fresh.get("tasks"), list) else []
            existing = next(
                (t for t in tasks if isinstance(t, dict) and t.get("sessionId") == sid),
                None,
            )
            if existing is None:
                display_name = repo_id.split("/")[-1] if "/" in repo_id else repo_id
                ssh_port = str(srv.get("port") or cfg.get("ssh_port") or "")
                platform = str(srv.get("platform") or cfg.get("platform") or "linux")
                placeholder = (
                    f"Launched by scheduled task {task_name!r} — waiting for tmux output…\n"
                    f"  session: {sid}\n"
                    f"  target:  {host or 'local'}\n"
                    f"  cmd:     {cmd[:200]}{'…' if len(cmd) > 200 else ''}"
                )
                existing = {
                    "id": sid,
                    "sessionId": sid,
                    "name": display_name,
                    "modelId": repo_id,
                    "type": "serve",
                    "status": "running",
                    "output": placeholder,
                    "ts": int(_time.time() * 1000),
                    "payload": {"repo_id": repo_id, "remote_host": host or "", "_cmd": cmd},
                    "remoteHost": host or "",
                    "sshPort": ssh_port or "",
                    "platform": platform or "linux",
                    "_serveReady": False,
                    "_endpointAdded": bool(endpoint_id),
                }
                tasks.append(existing)
            # Stamp ownership + end-at on the task entry.
            existing["_scheduledByTask"] = task_name or ""
            existing["_scheduledByOwner"] = owner or ""
            if endpoint_id:
                existing["_endpointId"] = endpoint_id
                existing["endpointId"] = endpoint_id
                existing["_endpointAdded"] = True
            if end_after_min > 0:
                existing["_scheduledStopAtMs"] = int(_time.time() * 1000) + end_after_min * 60 * 1000
            fresh["tasks"] = tasks
            atomic_write_json(state_path, fresh)
        except Exception as e:
            logger.warning(f"cookbook_serve: state register/stamp failed: {e}")
    # Don't try to render absolute clock time in the message — the
    # server runs in UTC (Docker default), the user reads it as local,
    # and the offset depends on the user's TZ which the action doesn't
    # have a reliable handle on. The Tasks UI already shows the RUN
    # timestamp in the user's local time right above this message, so
    # "stops 8 min after that" gives the user everything they need.
    if end_after_min:
        return (
            f"Launched {repo_id} (session {sid}); stops {end_after_min} min after this ran",
            True,
        )
    return f"Launched {repo_id} (session {sid})", True
