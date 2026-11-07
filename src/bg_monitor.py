"""Always-on monitor that auto-continues the agent when a background job
(see src/bg_jobs.py) finishes.

Reliability is the whole point: completion → agent re-invocation must never
silently no-op. The monitor drains `bg_jobs.pending_followups()` every tick and
only calls `mark_followed_up()` AFTER the agent run succeeds — so a transient
failure is simply retried on the next tick. A timed-out/dead job still produces
a follow-up ("the job failed/timed out"), so the user always hears back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum, auto

from src import bg_jobs
from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)

_monitor_task = None
POLL_INTERVAL_S = 5
# The follow-up agent run is allowed a few rounds to actually continue the task
# (e.g. after `pip install` finishes, run the transcription).
_FOLLOWUP_MAX_ROUNDS = 12


class FollowupResult(Enum):
    RETRYABLE_LATER = auto()
    COMPLETED = auto()
    TERMINAL_UNFOLLOWABLE = auto()


def _background_result_message(rec):
    inject = (
        f"[Background job {rec['id']} finished]\n\n"
        f"{bg_jobs.result_text(rec)}\n\n"
        "Continue the task using this output. Don't repeat work that's already done. "
        "If the task is now complete, give the user the final result."
    )
    return untrusted_context_message("background job output", inject)


def _settle_launch_effect(resource, rec):
    """Record the exact job's settlement against its durable launch claim.

    Uses only the Wave 3-validated job identity and typed lifecycle facts from
    the server-owned record. Settlement is execution evidence; the delivered
    output remains attributed content and verifies nothing. Best-effort: a
    failure leaves the claim running/unknown and never blocks the follow-up.
    """
    try:
        from src.agent_runtime.effect_adapters import settle_background_job
        from src.agent_tools.bg_job_tools import job_lifecycle_facts
        settle_background_job(resource, job_lifecycle_facts(rec))
    except Exception as error:  # noqa: BLE001
        logger.warning("bg-followup: effect settlement for %s was not recorded: %s", rec.get("id"), error)


async def _drain_agent(sess, messages, request_authority=None):
    """Run the agent loop headless against a session. Returns
    (final_prose, tool_events) — tool_events in the same shape the live chat
    saves, so the frontend rebuilds them as standard agent-thread tool cards."""
    from src.agent_loop import stream_agent_loop
    from src.agent_runtime.authority import RequestAuthority
    full = ""
    final_replaced = False
    tool_events = []
    round_num = 1
    async for chunk in stream_agent_loop(
        sess.endpoint_url, sess.model, messages,
        headers=getattr(sess, "headers", None),
        context_length=getattr(sess, "context_length", 0) or 0,
        session_id=sess.id,
        max_rounds=_FOLLOWUP_MAX_ROUNDS,
        owner=getattr(sess, "owner", None),
        workspace=request_authority.workspace or None if request_authority is not None else None,
        request_authority=(request_authority or RequestAuthority.empty(
            owner=getattr(sess, "owner", None), session_id=sess.id)),
    ):
        if not chunk.startswith("data: "):
            continue
        body = chunk[6:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            d = json.loads(body)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        if "delta" in d:
            delta = d.get("delta")
            if isinstance(delta, str):
                if d.get("thinking"):
                    continue
                if final_replaced:
                    # A later answer supersedes the replacement, as the
                    # completion gate treats it.
                    full = ""
                    final_replaced = False
                full += delta
        elif d.get("type") == "final_response":
            # The completion gate may present its sanitized answer as one
            # replacement instead of deltas.
            full = str(d.get("content") or "")
            final_replaced = True
        elif d.get("type") == "agent_step":
            round_num = d.get("round", round_num)
        elif d.get("type") == "tool_output":
            # Mirror the live chat's tool_event shape (chat_routes / chatRenderer).
            tool_event = {
                "round": round_num,
                "tool": d.get("tool"),
                "command": d.get("command"),
                "output": d.get("output"),
                "exit_code": d.get("exit_code"),
            }
            if isinstance(d.get("ask_user"), dict):
                # Preserve exact-approval cards from a tainted background-job
                # continuation so the user can authorize the sealed action on
                # the next foreground turn instead of losing it headlessly.
                tool_event["ask_user"] = d["ask_user"]
            tool_events.append(tool_event)
    return full, tool_events


async def _run_followup(rec: dict) -> FollowupResult:
    """Continue only an exactly linked result; distinguish retry from terminal."""
    from src.ai_interaction import get_session_manager
    from core.models import ChatMessage

    sm = get_session_manager()
    if not sm:
        return FollowupResult.RETRYABLE_LATER
    sess = sm.get_session(rec["session_id"])
    if not sess:
        # Session was deleted — nothing to continue. Consider it handled so we
        # don't retry forever.
        logger.info("bg-followup: session %s gone for job %s — skipping", rec.get("session_id"), rec.get("id"))
        # The job is retired without a continuation, then pruned with its
        # publication. Settle its launch effect first so it is not left RUNNING.
        from src.agent_runtime.process_resources import job_from_record, validate_job
        try:
            resource = job_from_record(rec)
            validate_job(resource)
        except (ValueError, TypeError, OSError, RuntimeError):
            pass  # no validated linkage: nothing may be settled
        else:
            _settle_launch_effect(resource, rec)
        return FollowupResult.TERMINAL_UNFOLLOWABLE

    # Don't write into a session that's mid-stream. The followup appends to
    # history + save_sessions(); a concurrent live turn does the same, and with
    # no per-session lock the two interleave (reordered/clobbered messages).
    # Defer — return False so we retry on the next tick once the turn finishes.
    try:
        from src import agent_runs
        if agent_runs.is_active(sess.id):
            logger.info("bg-followup: session %s busy (live turn) — deferring job %s", sess.id, rec.get("id"))
            return FollowupResult.RETRYABLE_LATER
    except Exception:
        pass

    from src.agent_runtime.authority import restore_background_authority
    from src.settings import get_setting
    authority = restore_background_authority(
        rec["id"], owner=getattr(sess, "owner", None), session_id=sess.id)
    # A result can trigger a continuation only through the immutable producer
    # linkage, never merely because it names an existing chat.
    from src.agent_runtime.process_resources import job_from_record, validate_job
    try:
        resource = job_from_record(rec)
        validate_job(resource)
        _settle_launch_effect(resource, rec)
        if not authority.grants or (resource.owner, resource.thread_id, resource.request_id) != (
                str(getattr(sess, "owner", None) or "").strip().casefold(), sess.id, authority.request_id):
            return FollowupResult.TERMINAL_UNFOLLOWABLE
    except (ValueError, TypeError, OSError, RuntimeError):
        return FollowupResult.TERMINAL_UNFOLLOWABLE
    context = sess.get_context_messages()
    context.append(_background_result_message(rec))
    authority = authority.restrict(disabled_tools=get_setting("disabled_tools", []) or ())
    full, tool_events = await _drain_agent(sess, context, request_authority=authority)
    # An awaited continuation must not deliver a result after its immutable
    # linkage disappears or is replaced. This check grants no new authority.
    try:
        validate_job(resource)
    except (ValueError, TypeError, OSError, RuntimeError):
        return FollowupResult.TERMINAL_UNFOLLOWABLE

    # Persist ONLY the assistant continuation so it renders as a normal agent
    # turn — a standard chat bubble plus `tool_events` that the frontend
    # rebuilds into the usual agent-thread tool cards (chatRenderer:1494). The
    # trigger isn't saved as its own message (it'd be an out-of-place bubble);
    # the raw job output is stashed in metadata for traceability instead.
    sm.add_message(sess.id, ChatMessage(
        "assistant", full,
        metadata={
            "tool_events": tool_events,
            "model": sess.model,
            "bg_job_id": rec["id"],
            "bg_result": bg_jobs.result_text(rec)[:4000],
        },
    ))
    sm.save_sessions()
    logger.info("bg-followup: auto-continued session %s for job %s (%d chars, %d tools)",
                sess.id, rec["id"], len(full), len(tool_events))
    return FollowupResult.COMPLETED


async def _process_followup(rec):
    outcome = await _run_followup(rec)
    if outcome is FollowupResult.COMPLETED:
        from src.agent_runtime.process_resources import job_from_record
        bg_jobs.mark_followed_up(rec["id"], expected=job_from_record(rec))
    elif outcome is FollowupResult.TERMINAL_UNFOLLOWABLE:
        if not bg_jobs.mark_unfollowable(rec["id"], expected_record=rec):
            return FollowupResult.RETRYABLE_LATER
        logger.warning("bg-followup: job %s has no valid continuation linkage; retired from pending", rec.get("id"))
    return outcome


async def _loop():
    while True:
        try:
            for rec in bg_jobs.pending_followups():
                try:
                    await _process_followup(rec)
                except Exception as e:
                    # Idempotent: leave followed_up=False so the next tick retries.
                    logger.warning("bg-followup failed for %s (will retry): %s", rec.get("id"), e)
        except Exception as e:
            logger.warning("bg-monitor tick error: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)


def start_bg_monitor():
    """Idempotent — start the always-on background-job monitor."""
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        return _monitor_task
    _monitor_task = asyncio.create_task(_loop())
    logger.info("Background-job monitor started (poll %ds)", POLL_INTERVAL_S)
    return _monitor_task
