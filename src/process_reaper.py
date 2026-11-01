"""Startup reconciliation for processes a previous run left behind.

Two stores in this tree outlive the process that wrote them, on purpose:
``data/containment_grants.json`` so a restart can reap rather than orphan, and
``data/bg_jobs.json`` so a restart never loses a detached job or its result.
Until now nothing read either of them at startup. A crashed or restarted server
therefore left every grant permanently "active" and every background job
permanently "running", and the first thing to touch one of those records was a
teardown aimed at a pid that had been reassigned in the meantime.

This module runs once, during startup, before anything of this run exists. That
timing is what makes its rules safe: every record it sees was written by an
earlier run, so "I cannot identify this process" is information about a previous
run's child and not about one of ours.

The two stores get **opposite** treatment, which is the whole reason this is a
module and not a loop:

* A **containment grant** is tied to a tool call that no longer has a caller.
  A live process under an abandoned grant is by definition an orphan, so it is
  torn down.
* A **background job** is detached deliberately and is documented to survive a
  uvicorn restart. Killing one here would break the feature, so its record is
  only corrected, never reaped. What gets fixed is identity: a job whose pid now
  belongs to someone else is retired so that nothing later signals the stranger.

Fail closed in both: a signal requires a positive identity from
:mod:`src.process_ownership`, and every other verdict is recorded rather than
acted on. Containment that cannot identify its target is not containment, and
the honest failure is a visible orphan rather than a dead bystander.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from src import process_lifecycle, process_ownership

logger = logging.getLogger(__name__)


def reap_containment_grants() -> Dict[str, Any]:
    """Tear down or retire every grant a previous run left active.

    Per grant: a verified live process is torn down through
    :func:`src.containment.reap_record`; a grant whose process is gone is
    dropped; a grant naming a pid that is now someone else's is dropped
    *without a signal*, because the only thing left to do with it is stop
    believing it. A grant that cannot be verified at all is **kept**, so the
    orphan stays visible in ``active_grants()`` instead of being quietly
    written off as handled.
    """
    from src import containment

    report: Dict[str, Any] = {
        "seen": 0, "torn_down": 0, "already_gone": 0,
        "foreign": 0, "unverifiable": 0, "failed": 0,
    }
    try:
        records = containment.active_grants()
    except Exception:
        logger.warning("process_reaper: containment grant store unreadable", exc_info=True)
        return report

    for record in records:
        report["seen"] += 1
        grant_id = str(record.get("id") or "")
        if record.get("external"):
            # Nothing local ever ran, so there is nothing local to reap.
            containment.forget(grant_id)
            report["already_gone"] += 1
            continue
        # A grant's holder — the detached supervisor of a background job, or
        # the server process that acquired it — is an identity like any
        # other: a live pid in its slot proves nothing without its token.
        supervisor = process_lifecycle.ProcessIdentity.from_record(
            record, pid_key="supervisor_pid", token_key="supervisor_token")
        if record.get("lifetime") == "background" and supervisor and supervisor.owned():
            # Detached jobs deliberately survive a server restart. Their
            # supervisor owns the wall clock and teardown, independently.
            report["background_kept"] = report.get("background_kept", 0) + 1
            continue
        manager = process_lifecycle.ProcessIdentity.from_record(
            record, pid_key="manager_pid", token_key="manager_token")
        if record.get("lifetime") != "cleanup" and manager and manager.owned():
            report["manager_kept"] = report.get("manager_kept", 0) + 1
            continue
        verdict = process_ownership.verify_record(record)
        if verdict == process_ownership.GONE:
            if containment._group_present(record.get("pgid")):
                # Leader death does not prove tree death. Without a surviving
                # identity we cannot signal the group, so retain the evidence.
                report["failed"] += 1
                logger.error("process_reaper: grant %s leader is gone but group survives", grant_id)
                continue
            containment.forget(grant_id)
            report["already_gone"] += 1
            continue
        if verdict == process_ownership.FOREIGN:
            logger.warning(
                "process_reaper: grant %s named pid %s, which now belongs to a "
                "different process; dropping the record unsignalled",
                grant_id, record.get("pid"),
            )
            containment.forget(grant_id)
            report["foreign"] += 1
            continue
        if verdict == process_ownership.UNVERIFIABLE:
            logger.error(
                "process_reaper: grant %s (pid %s, owner %s) cannot be verified "
                "via %s; leaving it active and unsignalled — this is a "
                "containment failure, not a clean start",
                grant_id, record.get("pid"), record.get("owner"),
                process_ownership.inspection_mechanism(),
            )
            report["unverifiable"] += 1
            continue
        try:
            outcome = containment.reap_record(record)
        except Exception:
            logger.warning("process_reaper: tearing down grant %s failed", grant_id, exc_info=True)
            report["failed"] += 1
            continue
        if outcome.dead:
            containment.forget(grant_id)
            report["torn_down"] += 1
        else:
            logger.error(
                "process_reaper: grant %s survived teardown; survivors=%s",
                grant_id, list(outcome.survivors),
            )
            report["failed"] += 1
    return report


def reap_bg_jobs() -> Dict[str, Any]:
    """Correct the identity of background jobs a previous run launched.

    Deliberately kills nothing: a ``#!bg`` job is detached so that it outlives
    the request *and* the server, and the store exists so its result is still
    collected afterwards. The defect being closed is narrower — a record whose
    pid has been reassigned will be signalled by the max-runtime reaper an hour
    later, and that signal lands on whatever now holds the pid.
    """
    from src import bg_jobs

    try:
        return bg_jobs.disown_unverified()
    except Exception:
        logger.warning("process_reaper: background job store unreadable", exc_info=True)
        return {"seen": 0, "retired": 0, "kept": 0}


def reap_legacy_agent_tmux() -> Dict[str, Any]:
    """Retire this runtime's legacy agent shells; a name prefix is not ownership.

    Match the original clean Bash launcher and this runtime's HOME marker on
    every pane. Snapshot session/server identities and process start tokens
    before teardown; ambiguous sessions remain visible and unsignalled.
    """
    import os
    import re
    import shlex
    import shutil
    import subprocess
    import uuid
    from src import containment
    from src.constants import DATA_DIR

    report = {"seen": 0, "torn_down": 0, "unverifiable": 0, "failed": 0}
    tmux = shutil.which("tmux")
    if os.name == "nt" or not tmux:
        return report
    pattern = "#{session_id}\t#{session_name}\t#{session_created}\t#{pane_pid}\t#{pane_id}\t#{pid}\t#{pane_start_command}"
    def snapshot():
        result = subprocess.run([tmux, "list-panes", "-a", "-F", pattern],
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            if not result.stdout and any(message in result.stderr.lower() for message in ("no server", "no sessions", "error connecting")):
                return {}
            raise RuntimeError("tmux pane discovery failed")
        sessions = {}
        for line in result.stdout.splitlines():
            fields = line.split("\t", 6)
            if len(fields) != 7 or not fields[1].startswith("ody-agent-"):
                continue
            sessions.setdefault(fields[0], []).append(tuple(fields))
        return {key: sorted(rows) for key, rows in sessions.items()}

    def launcher_is_ours(command):
        try:
            argv = shlex.split(command)
        except ValueError:
            return False
        if not argv or argv.pop(0) != "env":
            return False
        env = {}
        while argv and "=" in argv[0]:
            key, value = argv.pop(0).split("=", 1)
            if key not in {"PATH", "VIRTUAL_ENV", "HOME", "TMPDIR", "TERM", "COLUMNS", "LINES"}:
                return False
            env[key] = value
        return argv == ["/bin/bash", "--noprofile", "--norc"] and env.get("HOME") == DATA_DIR

    try:
        sessions = snapshot()
        for session_id, panes in sessions.items():
            report["seen"] += 1
            if not re.fullmatch(r"\$\d+", session_id) or not all(launcher_is_ours(row[6]) for row in panes):
                report["unverifiable"] += 1
                continue
            server_pid = int(panes[0][5])
            server_token = process_ownership.start_token(server_pid)
            roots = [int(row[3]) for row in panes]
            table = process_ownership.process_table()
            if not all(pid in table and table[pid].ppid == server_pid and shlex.split(table[pid].command) == [
                "/bin/bash", "--noprofile", "--norc",
            ] for pid in roots):
                # A stale pane PID can now name a bystander. Its parent and
                # current launcher must still match the observed tmux server.
                report["unverifiable"] += 1
                continue
            # Membership and identity bound together: a descendant that changed
            # hands after the table was read is dropped, not recorded under a
            # stranger's token. One this host cannot identify keeps the whole
            # session visible and unsignalled.
            bound = process_lifecycle.bind_descendants(roots)
            targets = [seen.identity.pid for seen in bound]
            identities = {seen.identity.pid: seen.identity.start_token for seen in bound}
            if any(token is None for token in identities.values()):
                report["unverifiable"] += 1
                continue
            if snapshot().get(session_id) != panes or process_ownership.verify(server_pid, server_token) != process_ownership.OWNED or any(
                process_ownership.verify(pid, identities.get(pid)) != process_ownership.OWNED for pid in roots
            ):
                report["unverifiable"] += 1
                continue
            # Persist every positively identified tree before touching it. A
            # failed teardown then remains discoverable even if its pane dies.
            tracked = []
            for pid in reversed(targets):
                if process_ownership.verify(pid, identities[pid]) != process_ownership.OWNED:
                    continue
                spec = containment.ContainmentSpec(workspace=os.getcwd(), env={}, wall_clock_s=1,
                                                   required=frozenset())
                grant = containment.ContainmentGrant(
                    id=uuid.uuid4().hex[:12], mechanism="process_group", workspace=spec.workspace,
                    enforced=frozenset(), degraded=(containment.FILESYSTEM, containment.PROCESS_TREE), unenforced_required=(),
                    owner=f"legacy-tmux:{session_id}", mode=containment.MODE_ENFORCING,
                    spec=spec, pid=pid, pgid=containment._pgid_of(pid),
                )
                containment._write_record(grant)
                containment._update_record(grant.id, lifetime="cleanup", start_token=identities[pid])
                receipt = containment._load_records().get(grant.id, {})
                if receipt.get("start_token") != identities[pid] or receipt.get("lifetime") != "cleanup":
                    raise RuntimeError("legacy tmux cleanup receipt was not persisted")
                tracked.append((grant, identities[pid]))
            dead = True
            for grant, token in tracked:
                outcome = containment.release(grant, start_token=token, require_identity=True)
                dead = dead and outcome.dead
            remaining = snapshot().get(session_id)
            if remaining and dead:
                # Use the immutable tmux session id, not its reusable name.
                if remaining != panes or process_ownership.verify(server_pid, server_token) != process_ownership.OWNED:
                    dead = False
                else:
                    result = subprocess.run([tmux, "kill-session", "-t", session_id],
                                            capture_output=True, timeout=5)
                    dead = result.returncode == 0 and session_id not in snapshot()
            report["torn_down" if dead else "failed"] += 1
    except Exception:
        report["failed"] += 1
        logger.warning("process_reaper: legacy agent tmux cleanup failed", exc_info=True)
    if report["unverifiable"]:
        logger.warning("process_reaper: left %s legacy tmux sessions without positive ownership", report["unverifiable"])
    return report


def reap_orphans() -> Dict[str, Any]:
    """Run both reconciliations. Returns a report; raises nothing.

    Blocking: a teardown escalates SIGTERM → grace → SIGKILL and waits for the
    process to actually go. Call it off the event loop.
    """
    # Observe publication consumers before receipt recovery can forget a dead
    # manager's record. Publication retirement itself neither signals nor
    # asserts successful teardown; containment remains the recovery authority.
    from src.agent_runtime.process_resources import prune_foreground_publications
    try:
        publications_retired = prune_foreground_publications()
    except (OSError, ValueError, TypeError):
        publications_retired = 0
        logger.warning("process_reaper: foreground publication retirement failed", exc_info=True)
    report = {
        "mechanism": process_ownership.inspection_mechanism(),
        "grants": reap_containment_grants(),
        "bg_jobs": reap_bg_jobs(),
        "agent_tmux": reap_legacy_agent_tmux(),
        "foreground_publications_retired": publications_retired,
    }
    if report["mechanism"] == process_ownership.MECHANISM_NONE:
        logger.error(
            "process_reaper: this host offers no process inspection; no orphan "
            "from a previous run can be identified or reaped"
        )
    logger.info("process_reaper: startup reconciliation %s", report)
    return report


async def reap_orphans_at_startup() -> Dict[str, Any]:
    """:func:`reap_orphans` off the event loop, for an app startup task."""
    import asyncio

    return await asyncio.to_thread(reap_orphans)
