"""Durable Wave 4 effect log: pre-invocation claims, append-only replay."""
from __future__ import annotations

import json
import os

import pytest

from src.agent_runtime import effects as fx
from src.agent_runtime.effect_log import EffectLog, EffectPersistenceError
from src.agent_runtime.resources import FilesystemResource, FilesystemRoot, OwnedResource


RUN = "a" * 32


@pytest.fixture
def target(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root = FilesystemRoot.seal(str(workspace))
    return fx.resource_ref(FilesystemResource.resolve(root, str(workspace / "a.txt"), allow_missing=True), "destination")


def claim(log, target, effect_id="e1", action_id="act-1"):
    return log.claim(effect_id=effect_id, action_id=action_id, operation=fx.OperationRef("write_file", "", "0" * 64),
                     impact_scope=(target,), obligations=(fx.Postcondition(target, fx.Predicate.EXISTS),))


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_claim_is_fsynced_to_disk_before_returning(tmp_path, target, monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    log = EffectLog(RUN, directory=tmp_path / "fx")
    made = claim(log, target)
    assert synced, "claim must be fsynced before the caller can invoke a backend"
    on_disk = records(log.path)
    assert [r["type"] for r in on_disk] == ["claim"]
    assert fx.EffectClaim.from_dict(on_disk[0]["record"]) == made
    assert oct(log.path.stat().st_mode & 0o777) == "0o600"


def test_claim_persistence_failure_raises_and_records_nothing(tmp_path, target):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    log = EffectLog(RUN, directory=blocker)
    with pytest.raises(EffectPersistenceError):
        claim(log, target)
    assert log.history().claims == ()


def test_non_claim_failure_degrades_without_losing_in_memory_truth(tmp_path, target, monkeypatch):
    log = EffectLog(RUN, directory=tmp_path / "fx")
    made = claim(log, target)
    monkeypatch.setattr(log, "_write", lambda kind, record: (_ for _ in ()).throw(OSError("disk full")))
    log.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.REPORTED_SUCCESS, impact=fx.Impact.POSSIBLE)
    assert log.degraded
    assert fx.assess(made, log.history()).execution is fx.ExecutionOutcome.REPORTED_SUCCESS
    # Replay only sees the durable claim: it stays unknown, never success.
    reloaded = EffectLog.load(RUN, directory=tmp_path / "fx")
    assert fx.assess(made, reloaded.history()).verdict is fx.EffectVerdict.PENDING


def test_replay_after_restart_marks_unsettled_claims_interrupted(tmp_path, target):
    directory = tmp_path / "fx"
    log = EffectLog(RUN, directory=directory)
    settled = claim(log, target, "e1", "a1")
    log.outcome(effect_id="e1", execution=fx.ExecutionOutcome.REPORTED_SUCCESS, impact=fx.Impact.POSSIBLE,
                execution_id="a1:x")
    log.observe(observation_id="o1", resource=target, mechanism=fx.ObservationMechanism.FILESYSTEM_READ,
                coverage=fx.Coverage.COMPLETE, source_action_id="r1", exists=True)
    pending = claim(log, target, "e2", "a2")
    del log  # process "crashes" before e2 settles

    reloaded = EffectLog.load(RUN, directory=directory)
    assert fx.assess(settled, reloaded.history()).verdict is fx.EffectVerdict.UNVERIFIED  # e2 made o1 stale
    appended = reloaded.recover_interrupted()
    assert [(o.effect_id, o.execution, o.impact, o.replayed) for o in appended] == [
        ("e2", fx.ExecutionOutcome.INTERRUPTED, fx.Impact.POSSIBLE, True)]
    assessment = fx.assess(pending, reloaded.history())
    assert assessment.verdict is fx.EffectVerdict.UNVERIFIED and assessment.unresolved_impact
    # Recovery is append-only and idempotent across another restart.
    again = EffectLog.load(RUN, directory=directory)
    assert again.recover_interrupted() == ()
    assert [r["type"] for r in records(again.path)] == ["claim", "outcome", "observation", "claim", "outcome"]


def test_running_background_claim_is_not_converted_by_replay(tmp_path, target):
    log = EffectLog(RUN, directory=tmp_path / "fx")
    made = claim(log, target)
    log.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.RUNNING, impact=fx.Impact.POSSIBLE)
    reloaded = EffectLog.load(RUN, directory=tmp_path / "fx")
    assert reloaded.recover_interrupted() == ()
    assert fx.assess(made, reloaded.history()).verdict is fx.EffectVerdict.PENDING


def test_torn_final_write_is_ignored_but_corruption_fails_closed(tmp_path, target):
    directory = tmp_path / "fx"
    log = EffectLog(RUN, directory=directory)
    claim(log, target)
    with open(log.path, "ab") as stream:
        stream.write(b'{"v":1,"type":"outcome","rec')  # crash mid-append
    assert len(EffectLog.load(RUN, directory=directory).history().claims) == 1
    with open(log.path, "ab") as stream:
        stream.write(b'\n{"v":1,"type":"outcome","record":{"forged":true}}\n')
    with pytest.raises(EffectPersistenceError):
        EffectLog.load(RUN, directory=directory)


def test_forged_success_record_cannot_be_replayed_into_verification(tmp_path, target):
    directory = tmp_path / "fx"
    log = EffectLog(RUN, directory=directory)
    made = claim(log, target)
    forged = {"v": 1, "type": "outcome", "record": {**fx.EffectOutcome(
        made.effect_id, 2, fx.ExecutionOutcome.FAILED, fx.Impact.POSSIBLE).to_dict(), "execution": "verified"}}
    with open(log.path, "a") as stream:
        stream.write(json.dumps(forged) + "\n")
    with pytest.raises(EffectPersistenceError):
        EffectLog.load(RUN, directory=directory)


def test_hardlinked_log_is_refused(tmp_path, target):
    directory = tmp_path / "fx"
    log = EffectLog(RUN, directory=directory)
    claim(log, target)
    os.link(log.path, tmp_path / "alias.jsonl")
    with pytest.raises(EffectPersistenceError):
        EffectLog.load(RUN, directory=directory)
    with pytest.raises(EffectPersistenceError):
        claim(log, target, "e2", "a2")


def test_read_only_runs_write_no_file(tmp_path, target):
    log = EffectLog(RUN, directory=tmp_path / "fx")
    log.observe(observation_id="o1", resource=target, mechanism=fx.ObservationMechanism.FILESYSTEM_READ,
                coverage=fx.Coverage.PARTIAL, source_action_id="r1", exists=True)
    assert not log.path.exists() and len(log.history().observations) == 1


def test_run_identifier_must_be_server_generated(tmp_path):
    for forged in ("../escape", "", "A" * 32, "a" * 31):
        with pytest.raises(ValueError):
            EffectLog(forged, directory=tmp_path)


def test_effect_store_is_server_control_state(tmp_path):
    from src.agent_runtime import effect_log
    store = effect_log.effects_dir()
    store.mkdir(parents=True, exist_ok=True)
    root = FilesystemRoot.seal(str(store.parent))
    with pytest.raises(ValueError, match="sensitive"):
        FilesystemResource.resolve(root, str(store / ("b" * 32 + ".jsonl")), allow_missing=True)


def test_owned_revision_scope_round_trips(tmp_path):
    record = fx.resource_ref(OwnedResource("notes", "u", "t", "notes", "n1", "rev-1"), "record")
    log = EffectLog(RUN, directory=tmp_path / "fx")
    log.claim(effect_id="e1", action_id="a1", operation=fx.OperationRef("manage_notes", "", "0" * 64),
              impact_scope=(record,))
    assert EffectLog.load(RUN, directory=tmp_path / "fx").history().claims[0].impact_scope == (record,)


# -- crash durability ----------------------------------------------------------

def synced(monkeypatch, events):
    """Record the path each real fsync makes durable, in call order."""
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (events.append(os.readlink(f"/proc/self/fd/{fd}")),
                                                 real_fsync(fd))[1])


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc fd paths")
def test_created_directories_are_synced_before_the_claim_returns(tmp_path, target, monkeypatch):
    events = []
    synced(monkeypatch, events)
    log = EffectLog(RUN, directory=tmp_path / "new" / "fx")
    claim(log, target)
    # Each newly created directory entry, then the record, then the log's entry.
    assert events == [str(tmp_path), str(tmp_path / "new"), str(log.path), str(tmp_path / "new" / "fx")]
    events.clear()
    claim(log, target, "e2", "a2")
    assert events == [str(log.path)], "later appends need only the record fsync"


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc fd paths")
def test_launch_index_is_synced_written_replaced_then_directory_synced(tmp_path, target, monkeypatch):
    log = EffectLog(RUN, directory=tmp_path / "fx")
    claim(log, target)
    events = []
    synced(monkeypatch, events)
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    log.index_launch("d" * 32, "e1")
    assert events == [str(tmp_path / "fx" / ("launch-" + "d" * 32 + ".tmp")), "replace", str(tmp_path / "fx")]
    assert EffectLog.launch_owner("d" * 32, directory=tmp_path / "fx") == (RUN, "e1")


def test_torn_tail_from_a_crashed_writer_is_repaired_before_the_next_append(tmp_path, target):
    directory = tmp_path / "fx"
    claim(EffectLog(RUN, directory=directory), target)
    with open(directory / f"{RUN}.jsonl", "ab") as stream:
        stream.write(b'{"v":1,"type":"claim","rec')  # crash mid-append
    survivor = EffectLog.load(RUN, directory=directory)
    claim(survivor, target, "e2", "a2")
    reloaded = EffectLog.load(RUN, directory=directory)
    assert [c.effect_id for c in reloaded.history().claims] == ["e1", "e2"]
    assert all(line.startswith("{") for line in (directory / f"{RUN}.jsonl").read_text().splitlines())


# -- concurrent writers --------------------------------------------------------

def test_independent_logs_allocate_from_the_durable_tail(tmp_path, target):
    directory = tmp_path / "fx"
    first, second = EffectLog(RUN, directory=directory), EffectLog(RUN, directory=directory)
    claim(first, target, "e1", "a1")
    claim(second, target, "e2", "a2")  # second never saw e1 in memory
    claim(first, target, "e3", "a3")
    positions = [r.sequence for r in (*EffectLog.load(RUN, directory=directory).history().claims,)]
    assert positions == [1, 2, 3]
    assert [c.effect_id for c in first.history().claims] == ["e1", "e2", "e3"]


def test_threads_with_separate_logs_never_duplicate_positions(tmp_path, target):
    import threading
    directory = tmp_path / "fx"
    logs = [EffectLog(RUN, directory=directory) for _ in range(4)]
    barrier = threading.Barrier(len(logs))

    def append(index, log):
        barrier.wait()
        for n in range(25):
            claim(log, target, f"e{index}-{n}", f"a{index}-{n}")

    threads = [threading.Thread(target=append, args=(i, log)) for i, log in enumerate(logs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    history = EffectLog.load(RUN, directory=directory).history()
    assert sorted(c.sequence for c in history.claims) == list(range(1, 101))


_WRITER = """
import sys
from pathlib import Path
from src.agent_runtime import effects as fx
from src.agent_runtime.effect_log import EffectLog
from src.agent_runtime.resources import FilesystemResource, FilesystemRoot
directory, workspace, index = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
root = FilesystemRoot.seal(workspace)
target = fx.resource_ref(FilesystemResource.resolve(root, workspace + "/a.txt", allow_missing=True), "destination")
log = EffectLog(sys.argv[4], directory=directory)
for n in range(40):
    log.claim(effect_id=f"p{index}-{n}", action_id=f"a{index}-{n}",
              operation=fx.OperationRef("write_file", "", "0" * 64), impact_scope=(target,))
"""


def test_independent_processes_never_duplicate_positions(tmp_path, target):
    import subprocess
    import sys
    from pathlib import Path
    directory = tmp_path / "fx"
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "ODYSSEUS_DATA_DIR": str(tmp_path / "data"), "PYTHONPATH": str(root)}
    writers = [subprocess.Popen([sys.executable, "-c", _WRITER, str(directory), str(tmp_path / "ws"), str(i), RUN],
                                cwd=root, env=env) for i in range(4)]
    assert [writer.wait(timeout=120) for writer in writers] == [0, 0, 0, 0]
    history = EffectLog.load(RUN, directory=directory).history()
    assert sorted(c.sequence for c in history.claims) == list(range(1, 161))


def test_a_settled_effect_is_never_settled_again_by_another_writer(tmp_path, target):
    directory = tmp_path / "fx"
    owner = EffectLog(RUN, directory=directory)
    made = claim(owner, target)
    owner.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.RUNNING, impact=fx.Impact.POSSIBLE)
    other = EffectLog.load(RUN, directory=directory)  # also sees RUNNING
    assert owner.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.REPORTED_SUCCESS,
                         impact=fx.Impact.POSSIBLE) is not None
    assert other.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.FAILED,
                         impact=fx.Impact.POSSIBLE) is None
    reloaded = EffectLog.load(RUN, directory=directory)
    assert reloaded.history().latest_outcome(made.effect_id).execution is fx.ExecutionOutcome.REPORTED_SUCCESS
    assert other.history().latest_outcome(made.effect_id).execution is fx.ExecutionOutcome.REPORTED_SUCCESS


def test_recovery_leaves_a_claim_another_writer_settled(tmp_path, target):
    directory = tmp_path / "fx"
    live = EffectLog(RUN, directory=directory)
    made = claim(live, target)
    stale = EffectLog.load(RUN, directory=directory)  # sees the claim unsettled
    live.outcome(effect_id=made.effect_id, execution=fx.ExecutionOutcome.REPORTED_SUCCESS, impact=fx.Impact.POSSIBLE)
    assert stale.recover_interrupted() == ()
    assert [r["type"] for r in records(live.path)] == ["claim", "outcome"]


def test_concurrent_effect_log_open_returns_same_instance(tmp_path):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: EffectLog.open(RUN, directory=tmp_path / "fx"), range(16)))
    assert all(instance is instances[0] for instance in instances)


# -- control-plane protection ----------------------------------------------------

def test_hardlinked_effect_state_is_control_plane_without_scanning_the_store(tmp_path, monkeypatch):
    from pathlib import Path
    from src.agent_runtime import effect_log, resources
    store = tmp_path / "effects"
    store.mkdir()
    monkeypatch.setattr(effect_log, "EFFECTS_DIR", str(store))
    for n in range(50):
        (store / f"{n:032x}.jsonl").write_text("{}\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ordinary = workspace / "notes.txt"
    ordinary.write_text("x")
    listed, globbed = [], []
    real_scandir, real_rglob = os.scandir, Path.rglob
    monkeypatch.setattr(resources.os, "scandir", lambda path: (listed.append(str(path)), real_scandir(path))[1])
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: (globbed.append(str(self)), real_rglob(self, pattern))[1])
    assert resources._control_plane_path(str(ordinary)) is False
    assert str(store) not in listed and str(store) not in globbed
    alias = workspace / "sneaky.jsonl"
    os.link(store / f"{7:032x}.jsonl", alias)
    assert resources._control_plane_path(str(alias)) is True
    # Wave 3's scan-local snapshot form: the store is a prefix, not inventory.
    snapshot = resources._control_plane_snapshot()
    assert str(store) in snapshot[0]
    assert resources._control_plane_path(str(store / "new.jsonl"), snapshot=snapshot) is True
    listed.clear()
    assert resources._control_plane_path(str(ordinary), snapshot=snapshot) is False
    assert str(store) not in listed
    assert resources._control_plane_path(str(alias), snapshot=snapshot) is True
    assert str(store) not in globbed, "the effect store is listed one level, never recursively inventoried"
    # Multi-linked regular files are treated as control-plane resources to prevent hardlink race/escape behavior.
    elsewhere = tmp_path / "other.txt"
    elsewhere.write_text("y")
    os.link(elsewhere, workspace / "pnpm-style.txt")
    assert resources._control_plane_path(str(workspace / "pnpm-style.txt")) is True
