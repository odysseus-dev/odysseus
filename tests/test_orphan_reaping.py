"""Teardown across a restart: the pid in a store is a claim, not a handle.

Three stores here outlive the process that wrote them, deliberately — a restart
is supposed to keep a background job and its result. The consequence nobody had
closed is that the recorded pid is reassignable, so a teardown driven off an old
record can land on a process the kernel has since given to somebody else. That
was ODY-86's shape.

Covered:

* :func:`src.containment.release` gating a grant it recovered from the store,
  and *not* gating one whose process the caller is holding.
* :func:`src.containment.reap_record`, the entry point for a reaper that has a
  row and no grant object.
* :mod:`src.process_reaper`, which gives the two stores opposite treatment —
  orphaned grants are torn down, detached jobs are only corrected.
* :func:`src.bg_jobs.disown_unverified`.

The verdicts come from :mod:`src.process_ownership`, substituted here so each
case is driven exactly; that module's own tests pin it against real processes.
"""

import json

import pytest

from src import bg_jobs, containment, process_ownership, process_reaper


@pytest.fixture
def grant_store(tmp_path, monkeypatch):
    """Redirect the containment grant store. Returns a reader for it."""
    path = tmp_path / "containment_grants.json"
    monkeypatch.setattr(containment, "_store_path", lambda: path)

    def _read():
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    return _read


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    """Redirect the background-job store and its spool directory."""
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "bg_jobs.json")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "bg_jobs")
    (tmp_path / "bg_jobs").mkdir()


def verdicts(monkeypatch, mapping, default=process_ownership.OWNED):
    """Pin verify()'s answer per pid."""
    monkeypatch.setattr(
        process_ownership, "verify",
        lambda pid, _token: mapping.get(int(pid or 0), default),
    )


def seed_grant(pid=4242, pgid=4242, token="token:4242", **extra):
    """Write a grant record the way a previous run would have left it."""
    record = {
        "id": "grant-1", "owner": "session-7", "mechanism": "process_group",
        "mode": containment.CONTAINMENT_MODE, "workspace": "/tmp",
        "enforced": ["filesystem", "process_tree", "wall_clock"],
        "degraded": [], "unenforced_required": [],
        "required": ["filesystem", "process_tree", "wall_clock"],
        "wall_clock_s": 60, "max_memory_bytes": None, "max_processes": None,
        "network": "inherit", "external": False, "pid": pid, "pgid": pgid,
        "start_token": token, "acquired_at": 0.0, "released_at": None, "release": None,
    }
    record.update(extra)
    containment._save_records({record["id"]: record})
    return record


def seed_job(pid=4242, token="token:4242", status="running", **extra):
    record = {
        "id": "job-1", "session_id": "chat-1", "command": "sleep 300",
        "status": status, "pid": pid, "start_token": token, "started_at": 0.0,
        "ended_at": None, "exit_code": None, "max_runtime_s": 3600,
        "followed_up": False, "log_path": "", "exit_path": "",
    }
    record.update(extra)
    bg_jobs._save({record["id"]: record})
    return record


# ── The gate inside release() ───────────────────────────────────────────────
def test_a_recovered_grant_naming_a_recycled_pid_is_not_signalled(
    grant_store, monkeypatch
):
    """The headline case. The pid is live, and it is not ours."""
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.FOREIGN})

    def _no_signals(*_args, **_kwargs):
        raise AssertionError("a foreign pid must never be signalled")

    monkeypatch.setattr(containment, "_signal_tree", _no_signals)

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.ownership == process_ownership.FOREIGN
    assert outcome.dead is False
    # Not listed as a survivor of *our* grant either: naming a stranger's pid
    # there invites the next reaper to kill it.
    assert outcome.survivors == ()


def test_an_unverifiable_grant_is_not_signalled_and_stays_active(
    grant_store, monkeypatch
):
    """An inspection mechanism this host does not have is a containment failure.

    Reported as an undead tree and left in the store, so the orphan stays
    visible in ``active_grants()`` rather than being written off as handled.
    """
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.UNVERIFIABLE})
    monkeypatch.setattr(
        containment, "_signal_tree",
        lambda *_a, **_k: pytest.fail("an unidentified pid must never be signalled"),
    )

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.ownership == process_ownership.UNVERIFIABLE
    assert outcome.dead is False
    assert containment.active_grants(), "the orphan must remain visible"


def test_a_recovered_grant_whose_process_is_gone_is_released_clean(
    grant_store, monkeypatch
):
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.GONE})
    monkeypatch.setattr(containment, "_group_present", lambda _pgid: False)

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.dead is True
    assert outcome.ownership == process_ownership.GONE
    assert containment.active_grants() == []


def test_a_gone_leader_with_a_live_group_is_reported_not_killed(
    grant_store, monkeypatch
):
    """Children outlive the leader, but with the leader gone nothing proves the
    group is still ours — and a recycled group id would mean killpg hits
    strangers. The orphan is reported instead of guessed at."""
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.GONE})
    monkeypatch.setattr(containment, "_group_present", lambda _pgid: True)
    monkeypatch.setattr(
        containment, "_signal_tree",
        lambda *_a, **_k: pytest.fail("an unprovable group must not be signalled"),
    )

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.dead is False
    assert outcome.survivors == (4242,)


def test_a_verified_grant_is_torn_down_normally(grant_store, monkeypatch):
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.OWNED})
    monkeypatch.setattr(containment, "_pgid_of", lambda pid: 4242)
    signals = []
    monkeypatch.setattr(
        containment, "_signal_tree",
        lambda pid, pgid, sig: signals.append((pid, pgid, sig)),
    )
    # Dead on the first probe, so the teardown does not wait out its grace.
    monkeypatch.setattr(containment, "_tree_gone", lambda *_a, **_k: True)

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.dead is True
    assert outcome.ownership == ""


def test_verified_leader_does_not_authorize_a_different_group(grant_store, monkeypatch):
    seed_grant(pgid=9999)
    verdicts(monkeypatch, {4242: process_ownership.OWNED})
    monkeypatch.setattr(containment, "_pgid_of", lambda pid: 4242)
    monkeypatch.setattr(containment, "_signal_tree", lambda *args: pytest.fail("foreign group signalled"))
    outcome = containment.reap_record(grant_store()["grant-1"])
    assert outcome.dead is False
    assert outcome.ownership == process_ownership.UNVERIFIABLE


def test_reaper_retains_a_group_after_its_leader_dies(grant_store, monkeypatch):
    from src import process_reaper
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.GONE})
    monkeypatch.setattr(containment, "_group_present", lambda pgid: True)
    monkeypatch.setattr(containment, "_signal_tree", lambda *args: pytest.fail("unidentified group signalled"))
    report = process_reaper.reap_containment_grants()
    assert report["failed"] == 1
    assert report["already_gone"] == 0
    assert "grant-1" in grant_store()


def test_an_in_process_grant_is_not_subjected_to_the_gate(monkeypatch, tmp_path):
    """A grant carrying its own pid belongs to the caller holding it.

    The caller launched the child, so there is no identity question — and
    demanding a token here would refuse teardown of a perfectly ordinary tool
    call on a host with no inspection mechanism.
    """
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(
        process_ownership, "verify",
        lambda *_a, **_k: pytest.fail("an in-process teardown must not consult ownership"),
    )
    spec = containment.ContainmentSpec(
        workspace=str(tmp_path), env={}, wall_clock_s=5,
    )
    grant = containment.ContainmentGrant(
        id="live-1", mechanism="process_group", workspace=str(tmp_path),
        enforced=containment.DEFAULT_REQUIRED, degraded=(), unenforced_required=(),
        owner="session-7", mode=containment.CONTAINMENT_MODE, spec=spec,
        pid=4242, pgid=4242,
    )
    monkeypatch.setattr(containment, "_tree_gone", lambda *_a, **_k: True)
    monkeypatch.setattr(containment, "_group_present", lambda _pgid: False)

    outcome = containment.release(grant)

    assert outcome.dead is True
    assert outcome.ownership == ""


def test_the_release_block_names_the_ownership_verdict(grant_store, monkeypatch):
    """The verdict reaches the record, so "why is this still here" is answerable."""
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.FOREIGN})

    outcome = containment.reap_record(grant_store()["grant-1"])

    assert outcome.to_dict()["ownership"] == process_ownership.FOREIGN
    assert grant_store()["grant-1"]["release"]["ownership"] == process_ownership.FOREIGN


# ── The reaper ──────────────────────────────────────────────────────────────
def test_the_reaper_drops_a_foreign_grant_without_signalling_it(
    grant_store, job_store, monkeypatch
):
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.FOREIGN})
    monkeypatch.setattr(
        containment, "_signal_tree",
        lambda *_a, **_k: pytest.fail("the reaper must not signal a foreign pid"),
    )

    report = process_reaper.reap_containment_grants()

    assert report["foreign"] == 1
    # Dropped rather than retried: the only thing left to do with a record
    # about someone else's process is stop believing it.
    assert containment.active_grants() == []


def test_the_reaper_keeps_an_unverifiable_grant_visible(
    grant_store, job_store, monkeypatch
):
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.UNVERIFIABLE})

    report = process_reaper.reap_containment_grants()

    assert report["unverifiable"] == 1
    assert len(containment.active_grants()) == 1


def test_the_reaper_tears_down_a_verified_orphan(grant_store, job_store, monkeypatch):
    """A live process under an abandoned grant has no caller left. It goes."""
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.OWNED})
    torn_down = []

    def _reap(record, **_kwargs):
        torn_down.append(record["id"])
        return containment.ReleaseOutcome(dead=True, escalated=True, mechanism="process_group")

    monkeypatch.setattr(containment, "reap_record", _reap)

    report = process_reaper.reap_containment_grants()

    assert torn_down == ["grant-1"]
    assert report["torn_down"] == 1
    assert containment.active_grants() == []


def test_the_reaper_keeps_a_grant_that_survived_its_teardown(
    grant_store, job_store, monkeypatch
):
    seed_grant()
    verdicts(monkeypatch, {4242: process_ownership.OWNED})
    monkeypatch.setattr(
        containment, "reap_record",
        lambda record, **_k: containment.ReleaseOutcome(
            dead=False, escalated=True, survivors=(4242,), mechanism="process_group",
        ),
    )

    report = process_reaper.reap_containment_grants()

    assert report["failed"] == 1
    assert len(containment.active_grants()) == 1


def test_the_reaper_forgets_an_external_grant_without_inspecting_anything(
    grant_store, job_store, monkeypatch
):
    """Nothing local ever ran, so there is nothing local to reap."""
    seed_grant(external=True)
    monkeypatch.setattr(
        process_ownership, "verify",
        lambda *_a, **_k: pytest.fail("an external grant has no local pid to verify"),
    )

    report = process_reaper.reap_containment_grants()

    assert report["already_gone"] == 1
    assert containment.active_grants() == []


def test_the_reaper_survives_an_unreadable_store(monkeypatch, job_store):
    monkeypatch.setattr(
        containment, "active_grants",
        lambda: (_ for _ in ()).throw(RuntimeError("store on fire")),
    )

    assert process_reaper.reap_containment_grants()["seen"] == 0


# ── Background jobs: corrected, never killed ────────────────────────────────
def test_a_job_whose_pid_was_reassigned_is_retired_unsignalled(
    job_store, monkeypatch
):
    """Left alone, refresh() would SIGKILL this pid at max-runtime.

    An hour after a restart, aimed at whatever now holds it.
    """
    seed_job()
    verdicts(monkeypatch, {4242: process_ownership.FOREIGN})
    monkeypatch.setattr(
        bg_jobs, "_kill",
        lambda *_a, **_k: pytest.fail("disowning a job must not signal anything"),
    )

    report = bg_jobs.disown_unverified()

    assert report == {"seen": 1, "retired": 1, "kept": 0}
    record = bg_jobs._load()["job-1"]
    assert record["status"] == "failed"
    assert record["ownership_lost"] == process_ownership.FOREIGN
    # The agent asked for this job and is still owed an answer.
    assert record["followed_up"] is False


def test_an_unverifiable_job_is_also_retired(job_store, monkeypatch):
    """Fail closed. Retiring loses a result, which is visible; keeping it leaves
    a pid this server will later signal without knowing what it points at."""
    seed_job(token=None)
    verdicts(monkeypatch, {4242: process_ownership.UNVERIFIABLE})

    assert bg_jobs.disown_unverified()["retired"] == 1


def test_a_job_that_is_still_ours_keeps_running(job_store, monkeypatch):
    """A detached job is documented to survive a restart. Killing it here would
    break the feature the store exists for."""
    seed_job()
    verdicts(monkeypatch, {4242: process_ownership.OWNED})

    report = bg_jobs.disown_unverified()

    assert report == {"seen": 1, "retired": 0, "kept": 1}
    assert bg_jobs._load()["job-1"]["status"] == "running"


def test_a_job_whose_process_is_gone_is_left_for_refresh(job_store, monkeypatch):
    """refresh() may still find an exit-code file the job wrote before it went,
    so retiring it here would discard a result that exists."""
    seed_job()
    verdicts(monkeypatch, {4242: process_ownership.GONE})

    assert bg_jobs.disown_unverified()["kept"] == 1
    assert bg_jobs._load()["job-1"]["status"] == "running"


def test_already_finished_jobs_are_not_reconsidered(job_store, monkeypatch):
    seed_job(status="done")
    verdicts(monkeypatch, {4242: process_ownership.FOREIGN})

    assert bg_jobs.disown_unverified() == {"seen": 0, "retired": 0, "kept": 0}


def test_a_launched_job_records_an_identity_next_to_its_pid(job_store, tmp_path, monkeypatch):
    """Without this the record is unverifiable forever and the reaper can only
    refuse — the token has to be captured at launch or not at all."""
    from tests.process_resource_helpers import launch
    from src.agent_runtime import process_resources
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(process_resources, "_LAUNCH_DIR", tmp_path / "private" / "launches")
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "private" / "grants.json")
    record = launch("true", "chat-1", cwd=str(workspace))

    assert "start_token" in record
    assert process_ownership.verify(record["pid"], record["start_token"]) in (
        process_ownership.OWNED, process_ownership.GONE,
    )


def test_an_abandoned_job_says_so_in_its_follow_up(job_store):
    """The agent is told the job was lost, not that it failed for its own reasons."""
    record = seed_job(status="failed", ownership_lost=process_ownership.FOREIGN)

    text = bg_jobs.result_text(record)

    assert "abandoned across a server restart" in text
    assert "neither waited on nor signalled" in text


def test_reap_orphans_reports_both_stores_and_the_mechanism(
    grant_store, job_store, monkeypatch
):
    seed_grant()
    seed_job()
    verdicts(monkeypatch, {4242: process_ownership.GONE})
    monkeypatch.setattr(process_reaper, "reap_legacy_agent_tmux", lambda: {"seen": 0})
    monkeypatch.setattr(containment, "_group_present", lambda _pgid: False)

    report = process_reaper.reap_orphans()

    assert report["mechanism"] == process_ownership.inspection_mechanism()
    assert report["grants"]["already_gone"] == 1
    assert report["bg_jobs"]["kept"] == 1
