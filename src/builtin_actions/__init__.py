"""builtin_actions package -- split from builtin_actions.py"""

"""
builtin_actions.py

Registry of built-in automation actions that can be executed by the task
scheduler without needing an LLM call.
"""

import logging
import os
import json
from datetime import datetime
from typing import Tuple

from src.auth_helpers import owner_filter
from core.platform_compat import IS_WINDOWS, find_bash
from core.constants import internal_api_base
from src.constants import DATA_DIR, DEEP_RESEARCH_DIR, TIDY_CALENDAR_STATE_FILE, EMAIL_URGENCY_CACHE_DIR, COOKBOOK_STATE_FILE
from src.interactive_gate import wait_for_interactive_quiet
from src.builtin_actions._shared import TaskDeferred, TaskNoop

logger = logging.getLogger(__name__)


# ── Re-exported action functions ──
from src.builtin_actions._housekeeping import (
    action_tidy_sessions,
    action_tidy_documents,
    action_consolidate_memory,
    action_tidy_research,
    action_tidy_calendar,
)
from src.builtin_actions._shell import (
    _run_subprocess,
    action_ssh_command,
    action_run_script,
    action_run_local,
)
from src.builtin_actions._email import (
    _result_has_work,
    _result_is_config_error,
    _email_task_account_id,
    action_summarize_emails,
    action_draft_email_replies,
    action_email_auto_translate,
)
from src.builtin_actions._events import (
    _HEURISTIC_CRITICAL,
    _HEURISTIC_HIGH,
    _HEURISTIC_TYPES,
    _SIG_SKIP_PREFIXES,
    _TYPE_COLORS,
    _classify_event_heuristic,
    _memory_context_lines,
    action_classify_events,
    action_ping_events,
    action_extract_email_events,
    action_learn_sender_signatures,
)
from src.builtin_actions._urgency import action_check_email_urgency
from src.builtin_actions._misc import (
    action_daily_brief,
    action_test_skills,
    action_audit_skills,
    action_ping_notes,
    action_cookbook_serve,
)

BUILTIN_ACTIONS = {
    "tidy_sessions": action_tidy_sessions,
    "tidy_documents": action_tidy_documents,
    "consolidate_memory": action_consolidate_memory,
    "tidy_research": action_tidy_research,
    "summarize_emails": action_summarize_emails,
    "draft_email_replies": action_draft_email_replies,
    "email_auto_translate": action_email_auto_translate,
    "extract_email_events": action_extract_email_events,
    "classify_events": action_classify_events,
    # ping_events removed from the user-facing registry. Calendar reminders
    # are represented as Notes, so note pings are the single dispatch path.
    "daily_brief": action_daily_brief,
    "learn_sender_signatures": action_learn_sender_signatures,
    "ssh_command": action_ssh_command,
    "run_script": action_run_script,
    "run_local": action_run_local,
    "test_skills": action_test_skills,
    "audit_skills": action_audit_skills,
    "check_email_urgency": action_check_email_urgency,
    "cookbook_serve": action_cookbook_serve,
    # ping_notes removed from the registry — runs only inside `_note_pings_loop`.
}

# Descriptions for the UI/API
BUILTIN_ACTION_INFO = {
    "tidy_sessions": "Clean up empty chat sessions and auto-sort into folders",
    "tidy_documents": "Remove junk/empty documents",
    "consolidate_memory": "Remove duplicate memories",
    "tidy_research": "Remove orphaned research files (sessions that were deleted)",
    "summarize_emails": "Pre-generate AI summaries for new inbox emails",
    "draft_email_replies": "Pre-draft AI reply suggestions for new inbox emails",
    "email_auto_translate": "Detect foreign-language emails and cache translated text for the email reader",
    "extract_email_events": "Scan emails for booking/meeting confirmations and auto-add to calendar",
    "classify_events": "Tag upcoming events with importance (low/normal/high/critical) and type (work/health/travel/etc.); colors them too",
    "daily_brief": "Build a morning digest: today's calendar, unread email count + top senders, active todos",
    "learn_sender_signatures": "LLM learns each sender's signature from 3+ of their recent emails; cached per address so future renders fold sigs reliably without heuristics",
    "ssh_command": "Run a shell command on a local or remote host",
    "run_script": "Run a script locally or on ODYSSEUS_SCRIPT_HOST",
    "test_skills": "Run the per-skill Test on every skill: agent run + LLM judge → records verdict on the skill (pass/needs_work/fail/inconclusive). Advisory only — never rewrites or demotes anything.",
    "audit_skills": "Audit unaudited skills after enough new skills are added: test, narrow metadata, self-edit/retry, optional teacher rewrite, tag duplicates/trivial skills, and publish/draft using the auto-approve threshold.",
    "check_email_urgency": "Scan unread emails hourly, tag urgent/reply-soon/newsletter/marketing/spam, and send a reminder when a new email needs a fast reply.",
}
