# _email.py -- chunked from builtin_actions.py
import json
import logging
from typing import Tuple

from src.builtin_actions._shared import TaskNoop

logger = logging.getLogger(__name__)

def _result_has_work(result: str | None) -> bool:
    """Heuristic: did the email pass actually process anything?

    `_run_auto_summarize_once` returns strings like 'Processed 0 emails',
    'No new emails to summarize', 'Tagged 0 / Moved 0', etc. when nothing
    was done. Used to decide whether to record the run or noop it.
    """
    if not isinstance(result, str) or not result:
        return False
    low = result.lower()
    if "processed 0" in low or "no new" in low or "nothing to" in low:
        return False
    # "Tagged 0 / Moved 0" or similar zero-count summaries
    if low.count(" 0") >= 2 and ("tagged" in low or "moved" in low or "drafted" in low):
        return False
    return True



def _result_is_config_error(result: str | None) -> bool:
    if not isinstance(result, str):
        return False
    low = result.lower()
    return (
        "no model configured" in low
        or "no model endpoint configured" in low
        or "no llm endpoint available" in low
    )



def _email_task_account_id(kwargs) -> str | None:
    prompt = (kwargs.get("prompt") or "").strip()
    if not prompt:
        return None
    try:
        data = json.loads(prompt)
        if isinstance(data, dict):
            val = data.get("account_id") or data.get("email_account_id")
            return str(val).strip() or None
    except Exception:
        pass
    for line in prompt.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip().lower() in {"account_id", "email_account_id"}:
            return val.strip() or None
    return None



async def action_summarize_emails(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run one pass of email summary background processing."""
    try:
        from routes.email_pollers import _run_auto_summarize_once
        result = await _run_auto_summarize_once(
            do_summary=True,
            do_reply=False,
            account_id=_email_task_account_id(kwargs),
        )
        if _result_is_config_error(result):
            return result, False
        if not _result_has_work(result):
            raise TaskNoop(f"summarize: {result or 'no new emails'}")
        return result, True
    except Exception as e:
        logger.error(f"summarize_emails action failed: {e}")
        return str(e), False



async def action_draft_email_replies(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run one pass of AI reply drafting."""
    try:
        from routes.email_pollers import _run_auto_summarize_once
        result = await _run_auto_summarize_once(
            do_summary=False,
            do_reply=True,
            account_id=_email_task_account_id(kwargs),
            days_back=7,
            progress_cb=kwargs.get("progress_cb"),
        )
        if _result_is_config_error(result):
            return result, False
        if not _result_has_work(result):
            raise TaskNoop(f"draft replies: {result or 'no new emails'}")
        return result, True
    except Exception as e:
        logger.error(f"draft_email_replies action failed: {e}")
        return str(e), False



async def action_email_auto_translate(owner: str, **kwargs) -> Tuple[str, bool]:
    """Detect recent foreign-language emails and cache translated text.

    The reader still shows the original body; it simply checks this cache
    before calling the LLM on demand. Keep the scheduled pass deliberately
    small so translation never turns into a mailbox-wide background crawl.
    """
    try:
        import email as _email_mod
        import json as _json
        import re as _re
        import sqlite3 as _sql3
        from datetime import datetime as _dt, timedelta as _td

        from core.database import EmailAccount as _EA, SessionLocal as _SL
        from routes.email_helpers import (
            SCHEDULED_DB,
            _decode_header,
            _email_cache_owner_clause,
            _extract_reply,
            _extract_text,
            _imap_connect,
            email_translation_body_hash,
        )
        from src.settings import load_settings
        from src.task_endpoint import task_llm_call_async

        settings = load_settings()
        if not settings.get("email_auto_translate", False):
            raise TaskNoop("email auto-translate is disabled")

        target_language = (settings.get("email_translate_language") or "English").strip() or "English"
        account_id = _email_task_account_id(kwargs)
        days_back = 7
        max_process = 5
        try:
            data = _json.loads((kwargs.get("prompt") or "").strip() or "{}")
            if isinstance(data, dict):
                days_back = max(1, min(30, int(data.get("days_back") or days_back)))
                max_process = max(1, min(20, int(data.get("max_process") or max_process)))
        except Exception:
            pass

        db = _SL()
        try:
            from sqlalchemy import and_ as _and, or_ as _or
            q = db.query(_EA).filter(_EA.enabled == True)  # noqa: E712
            if owner:
                unowned = _or(_EA.owner == None, _EA.owner == "")  # noqa: E711
                same_mailbox = _or(_EA.imap_user == owner, _EA.from_address == owner)
                q = q.filter(_or(_EA.owner == owner, _and(unowned, same_mailbox)))
            if account_id:
                q = q.filter(_EA.id == account_id)
            accounts = q.all()
        finally:
            db.close()
        if not accounts:
            raise TaskNoop("no email accounts configured")

        def _cached(body_hash: str) -> bool:
            c = _sql3.connect(SCHEDULED_DB)
            try:
                owner_clause, owner_params = _email_cache_owner_clause(owner)
                row = c.execute(
                    f"SELECT 1 FROM email_translations "
                    f"WHERE body_hash = ? AND target_language = ? AND {owner_clause} LIMIT 1",
                    (body_hash, target_language, *owner_params),
                ).fetchone()
                return bool(row)
            finally:
                c.close()

        def _store(
            body_hash: str,
            *,
            uid: str,
            folder: str,
            subject: str,
            sender: str,
            translation: str,
            same_language: bool,
            model_used: str,
        ) -> None:
            c = _sql3.connect(SCHEDULED_DB)
            try:
                c.execute("""
                    INSERT OR REPLACE INTO email_translations
                    (body_hash, owner, target_language, uid, folder, subject, sender,
                     translation, same_language, model_used, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    body_hash, owner, target_language, uid, folder, subject, sender,
                    translation, 1 if same_language else 0, model_used, _dt.utcnow().isoformat(),
                ))
                c.commit()
            finally:
                c.close()

        async def _translate(body: str, subject: str, sender: str) -> tuple[str, bool]:
            content = await task_llm_call_async(
                [
                    {
                        "role": "system",
                        "content": (
                            "You translate emails faithfully. Preserve meaning, names, dates, money, addresses, "
                            "bullet structure, and tone. Do not summarize or answer the email. "
                            "Output only the translation between <<<TRANSLATION>>> and <<<END>>>. "
                            "If the email is already primarily in the target language, output exactly "
                            "<<<SAME_LANGUAGE>>>."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Target language: {target_language}\n\n"
                            f"From: {sender}\nSubject: {subject}\n\n{body[:16000]}\n\n"
                            "Translate the email unless it is already primarily in the target language.\n"
                            "Return only:\n<<<TRANSLATION>>>\ntranslated text\n<<<END>>>"
                        ),
                    },
                ],
                owner=owner,
                temperature=0.2,
                max_tokens=8192,
                timeout=180,
            )
            content = (content or "").strip()
            content = _extract_reply(content)
            if "<<<SAME_LANGUAGE>>>" in content:
                return "", True
            marker = _re.search(r"<<<TRANSLATION>>>\s*(.*?)\s*<<<END>>>", content, _re.S | _re.I)
            if marker:
                content = marker.group(1).strip()
            else:
                content = _re.sub(r"^\s*<<<TRANSLATION>>>\s*", "", content, flags=_re.I).strip()
                content = _re.sub(r"\s*<<<END>>>\s*$", "", content, flags=_re.I).strip()
            return content, False

        since = (_dt.utcnow() - _td(days=days_back)).strftime("%d-%b-%Y")
        examined = 0
        cached = 0
        translated = 0
        same_language = 0
        skipped = 0
        failures = 0
        processed = 0

        for acct in accounts:
            if processed >= max_process:
                break
            imap = None
            try:
                imap = _imap_connect(acct.id, owner=owner)
                imap.select("INBOX", readonly=True)
                status, data = imap.uid("SEARCH", None, f'(SINCE {since})')
                if status != "OK" or not data or not data[0]:
                    continue
                uids = list(reversed(data[0].split()))[:50]
                for uid_b in uids:
                    if processed >= max_process:
                        break
                    uid = uid_b.decode("utf-8", errors="ignore") if isinstance(uid_b, bytes) else str(uid_b)
                    status, msg_data = imap.uid("FETCH", uid, "(RFC822)")
                    if status != "OK" or not msg_data:
                        continue
                    raw = None
                    for part in msg_data:
                        if isinstance(part, tuple) and len(part) > 1:
                            raw = part[1]
                            break
                    if not raw:
                        continue
                    msg = _email_mod.message_from_bytes(raw)
                    subject = _decode_header(msg.get("Subject", ""))
                    sender = _decode_header(msg.get("From", ""))
                    body = (_extract_text(msg) or "").strip()
                    examined += 1
                    if len(body) < 80:
                        skipped += 1
                        continue
                    body_hash = email_translation_body_hash(body)
                    if _cached(body_hash):
                        cached += 1
                        continue
                    translation, is_same_language = await _translate(body, subject, sender)
                    if is_same_language:
                        _store(
                            body_hash,
                            uid=uid,
                            folder="INBOX",
                            subject=subject,
                            sender=sender,
                            translation="",
                            same_language=True,
                            model_used="background-task",
                        )
                        same_language += 1
                        processed += 1
                        continue
                    if not translation:
                        failures += 1
                        continue
                    _store(
                        body_hash,
                        uid=uid,
                        folder="INBOX",
                        subject=subject,
                        sender=sender,
                        translation=translation,
                        same_language=False,
                        model_used="background-task",
                    )
                    translated += 1
                    processed += 1
            except Exception as acct_e:
                failures += 1
                logger.warning(f"email_auto_translate account scan failed for {getattr(acct, 'id', '?')}: {acct_e}")
            finally:
                if imap:
                    try:
                        imap.logout()
                    except Exception:
                        pass

        if translated == 0 and same_language == 0:
            result = (
                f"no uncached foreign-language emails found "
                f"(examined {examined}, cached {cached}, skipped {skipped}, failures {failures})"
            )
            if failures:
                return f"Email Auto Translate failed: {result}", False
            raise TaskNoop(result)
        return (
            f"Email Auto Translate cached {translated} translation(s), marked {same_language} same-language "
            f"(examined {examined}, already cached {cached}, skipped {skipped}, failures {failures})",
            True,
        )
    except TaskNoop:
        raise
    except Exception as e:
        logger.error(f"email_auto_translate action failed: {e}")
        return str(e), False
