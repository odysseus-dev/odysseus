from dataclasses import replace
import json
import os
import time

import pytest

from src import bg_jobs, containment, process_ownership
from src.agent_runtime import process_resources as resources
from src.agent_runtime.authority import RequestAuthority, OperationGrant, ExactOperation, restore_background_authority
from src.agent_runtime.resources import NativeBackendResource, ResourceIdentityError, BackgroundJobResource, FilesystemRoot, FilesystemResource
from src.process_lifecycle import ProcessIdentity
from tests.process_resource_helpers import seed_linkage, launch_authority


@pytest.fixture
def store(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "private"
    monkeypatch.setattr(resources, "_LAUNCH_DIR", private / "launches")
    monkeypatch.setattr(bg_jobs, "_STORE", private / "jobs.json")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", private / "jobs")
    monkeypatch.setattr(containment, "_store_path", lambda: private / "receipts.json")
    monkeypatch.setattr(process_ownership, "verify", lambda *a: process_ownership.OWNED)
    monkeypatch.setattr(ProcessIdentity, "exited", lambda self: False)
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)
    return workspace


def seed(workspace, job_id="job", status="running"):
    bg_jobs._JOBS_DIR.mkdir(parents=True, exist_ok=True)
    record = {"id": job_id, "session_id": "thread", "command": "printf output", "pid": 4321,
              "status": status, "started_at": time.time(), "max_runtime_s": 3600,
              "exit_path": str(bg_jobs._JOBS_DIR / (job_id + ".exit")),
              "result_path": str(bg_jobs._JOBS_DIR / (job_id + ".result.json")),
              "log_path": str(bg_jobs._JOBS_DIR / (job_id + ".log"))}
    resource = seed_linkage(record, workspace, owner="alice", request_id="origin")
    jobs = bg_jobs._load()
    jobs[job_id] = record
    bg_jobs._save(jobs)
    return resource, record


@pytest.mark.parametrize("field,value", [("job_id", "sibling"), ("generation", "f" * 32), ("containment_id", "other-receipt"),
    ("owner", "bob"), ("request_id", "other-request"), ("thread_id", "other-thread")])
def test_job_substitution_fails_closed(store, field, value):
    resource, _ = seed(store)
    changed = resource.to_dict()
    changed[field] = value
    for process in changed["processes"]:
        if field in process:
            process[field] = value
    expected = BackgroundJobResource.from_dict(changed)
    with pytest.raises((ResourceIdentityError, OSError)):
        resources.validate_job(expected)


@pytest.mark.parametrize("field,value", [("role", "leader"), ("namespace", "external:ssh"), ("identity", {"pid": 4321, "start_token": "replacement", "pgid": 4321})])
def test_role_producer_and_process_replacement_fail(store, field, value):
    resource, _ = seed(store)
    changed = resource.to_dict()
    changed["processes"][0][field] = value
    with pytest.raises((ValueError, OSError)):
        resources.validate_job(BackgroundJobResource.from_dict(changed))


def test_completed_history_does_not_target_reused_process(store, monkeypatch):
    resource, rec = seed(store, status="done")
    with open(rec["log_path"], "w") as log:
        log.write("historical output")
    monkeypatch.setattr(process_ownership, "verify", lambda *a: process_ownership.FOREIGN)
    monkeypatch.setattr(bg_jobs, "_kill", lambda *a, **k: pytest.fail("historical process targeted"))
    assert bg_jobs.get("job", expected=resource)["output"] == "historical output"
    assert bg_jobs.kill("job", expected=resource)["status"] == "done"


def test_same_id_new_generation_does_not_inherit_authority(store):
    old, _ = seed(store)
    seed(store)  # Same store key, new trusted launch generation.
    with pytest.raises(ResourceIdentityError):
        bg_jobs.kill("job", expected=old)
    with pytest.raises(ResourceIdentityError):
        bg_jobs.get("job", expected=old)


def test_receipt_substitution_is_revalidated_before_mutation(store, monkeypatch):
    resource, _ = seed(store)
    receipts = containment._load_records()
    receipts[resource.containment_id]["launch_generation"] = "replacement"
    from core.atomic_io import atomic_write_json
    atomic_write_json(containment._store_path(), receipts)
    monkeypatch.setattr(bg_jobs, "_kill_record", lambda *a: pytest.fail("replaced receipt used"))
    with pytest.raises(ResourceIdentityError):
        bg_jobs.kill("job", expected=resource)


def test_result_publication_cannot_overwrite_authoritative_fields(store):
    resource, rec = seed(store)
    report = {"resource_identity": resource.to_dict(), "containment": {"id": resource.containment_id},
              "owner": "bob", "pid": 9999, "start_token": "replacement", "id": "other",
              "launch_resource": {}, "session_id": "other", "containment_id": "fake"}
    from pathlib import Path
    Path(rec["result_path"]).write_text(json.dumps(report))
    Path(rec["exit_path"]).write_text("0")
    final = bg_jobs.refresh("job")["job"]
    assert resources.job_from_record(final) == resource
    assert final["pid"] == rec["pid"] and final["session_id"] == "thread"


def test_resolution_and_lookup_do_not_reap_unrelated_jobs(store, monkeypatch):
    resource, _ = seed(store, status="done")
    sibling, rec = seed(store, "sibling")
    jobs = bg_jobs._load()
    jobs["sibling"]["started_at"] = 0
    bg_jobs._save(jobs)
    monkeypatch.setattr(bg_jobs, "_kill_record", lambda *a: pytest.fail("unrelated job reaped"))
    authority = RequestAuthority("lookup", "alice", "thread", "", (OperationGrant("manage_bg_jobs"),))
    bound = resources.resolve_process_operation(authority, ExactOperation.normalize("manage_bg_jobs", '{"action":"output","job_id":"job"}'), NativeBackendResource("manage_bg_jobs"))
    assert bound.jobs == (resource,)
    bg_jobs.get("job", expected=resource)
    assert bg_jobs.peek("sibling")["status"] == "running"


def test_child_cannot_target_sibling_or_replaced_job(store):
    first, _ = seed(store, "first")
    second, _ = seed(store, "second")
    parent = RequestAuthority("parent", "alice", "thread", "", (OperationGrant("manage_bg_jobs"),), job_resources=(first,))
    child = replace(parent, job_resources=(second,))
    inherited = parent.intersect(child)
    assert inherited.job_resources == ()
    with pytest.raises(ResourceIdentityError):
        resources.resolve_process_operation(inherited, ExactOperation.normalize("manage_bg_jobs", '{"action":"kill","job_id":"second"}'), NativeBackendResource("manage_bg_jobs"))
    seed(store, "first")
    assert parent.intersect(child).job_resources == ()


@pytest.mark.parametrize("field,value", [("generation", "f" * 32), ("owner", "bob"), ("request_id", "other"), ("thread_id", "other")])
def test_continuation_sidecar_mismatch_fails_closed(store, field, value):
    resource, _ = seed(store, status="done")
    sidecar = bg_jobs._JOBS_DIR / "job.authority.json"
    data = json.loads(sidecar.read_text())
    data["job"][field] = value
    sidecar.write_text(json.dumps(data))
    assert restore_background_authority("job", owner="alice", session_id="thread").grants == ()


def test_matching_continuation_preserves_original_authority(store):
    seed(store, status="done")
    authority = restore_background_authority("job", owner="alice", session_id="thread")
    assert authority.request_id == "origin" and authority.inherited
    assert authority.permits(ExactOperation.normalize("bash", "printf output"))
    assert restore_background_authority("job", owner="bob", session_id="thread").grants == ()


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
@pytest.mark.parametrize("state", ["launch", "job_store", "sidecar", "receipt"])
def test_launch_and_job_control_files_are_protected(store, tmp_path, alias, state):
    resource, _ = seed(store)
    control = {"launch": resources.launch_path(resource.generation), "job_store": bg_jobs._STORE,
               "sidecar": bg_jobs._JOBS_DIR / "job.authority.json", "receipt": containment._store_path()}[state]
    target = control
    if alias == "symlink":
        target = store / "alias"
        target.symlink_to(control)
    elif alias == "hardlink":
        target = store / "alias"
        try:
            os.link(control, target)
        except OSError as e:
            pytest.skip(f"hardlinks unavailable: {e}")
    root = FilesystemRoot.seal(tmp_path)
    with pytest.raises(ValueError):
        FilesystemResource.resolve(root, str(target))
    with pytest.raises(ResourceIdentityError):
        resources.guard_launch_workspace(root)
    if alias != "direct":
        with pytest.raises(ResourceIdentityError):
            resources.guard_launch_workspace(FilesystemRoot.seal(store))


def test_external_jobs_cannot_become_local_or_attest_containment(store):
    resource, _ = seed(store)
    external = resource.to_dict()
    external["namespace"] = "external:ssh"
    with pytest.raises(ValueError):
        BackgroundJobResource.from_dict(external)
    external = resource.to_dict()
    external["contained"] = True
    with pytest.raises(ValueError):
        BackgroundJobResource.from_dict(external)


@pytest.mark.parametrize("field,value", [("external", True), ("mechanism", "external_bridge"),
    ("supervisor_token", "reused"), ("supervisor_pid", 9876), ("owner", "bg:other")])
def test_receipt_cannot_replace_producer_or_claim_external_containment(store, field, value):
    resource, _ = seed(store, status="done")
    receipts = containment._load_records()
    receipts[resource.containment_id][field] = value
    from core.atomic_io import atomic_write_json
    atomic_write_json(containment._store_path(), receipts)
    with pytest.raises(ResourceIdentityError):
        bg_jobs.get("job", expected=resource)
    with pytest.raises(ResourceIdentityError):
        bg_jobs.mark_followed_up("job", expected=resource)


def test_target_lookup_does_not_wait_on_unrelated_live_handle(store, monkeypatch):
    resource, _ = seed(store, status="done")
    class OtherProcess:
        def poll(self):
            pytest.fail("Unrelated producer was reaped during lookup")
    monkeypatch.setattr(bg_jobs, "_LIVE_PROCS", {9876: OtherProcess()})
    bg_jobs.get("job", expected=resource)


@pytest.mark.parametrize("status", ["done", "running"])
@pytest.mark.parametrize("verdict", [process_ownership.FOREIGN, process_ownership.UNVERIFIABLE])
def test_historical_lookup_does_not_reap_reused_pid_handle(store, monkeypatch, status, verdict):
    resource, rec = seed(store, status=status)
    from pathlib import Path
    Path(rec["exit_path"]).write_text("0")
    Path(rec["result_path"]).write_text(json.dumps({
        "resource_identity": resource.to_dict(),
        "containment": {"id": resource.containment_id},
    }))
    monkeypatch.setattr(process_ownership, "verify", lambda *a: verdict)

    class ReplacementProcess:
        def poll(self):
            pytest.fail("Historical lookup reaped the replacement incarnation")

    replacement = ReplacementProcess()
    monkeypatch.setattr(bg_jobs, "_LIVE_PROCS", {rec["pid"]: replacement})
    assert bg_jobs.get("job", expected=resource)["status"] == "done"
    assert bg_jobs._LIVE_PROCS[rec["pid"]] is replacement


def test_service_refresh_still_reaps_finished_handles(store, monkeypatch):
    class FinishedProcess:
        def poll(self):
            return 0

    monkeypatch.setattr(bg_jobs, "_LIVE_PROCS", {4321: FinishedProcess()})
    bg_jobs.refresh()
    assert bg_jobs._LIVE_PROCS == {}


def test_completed_result_outlives_lifecycle_receipt_without_signalling(store, monkeypatch):
    resource, rec = seed(store, status="done")
    from pathlib import Path
    Path(rec["log_path"]).write_text("retained historical output")
    from core.atomic_io import atomic_write_json
    atomic_write_json(containment._store_path(), {})
    monkeypatch.setattr(bg_jobs, "_kill_record", lambda *a: pytest.fail("Historical resource was signalled"))
    assert bg_jobs.get("job", expected=resource)["output"] == "retained historical output"
    assert bg_jobs.kill("job", expected=resource)["status"] == "done"
    bg_jobs.mark_followed_up("job", expected=resource)
    jobs = bg_jobs._load()
    jobs["job"]["status"] = "running"
    bg_jobs._save(jobs)
    with pytest.raises(ResourceIdentityError):
        bg_jobs.kill("job", expected=resource)


@pytest.mark.parametrize("state", ["unknown_status", "malformed_sidecar", "missing_publication"])
def test_unresolved_or_malformed_authoritative_state_fails_closed(store, state):
    resource, _ = seed(store, status="done")
    if state == "unknown_status":
        jobs = bg_jobs._load()
        jobs["job"]["status"] = "unknown"
        bg_jobs._save(jobs)
    elif state == "malformed_sidecar":
        (bg_jobs._JOBS_DIR / "job.authority.json").write_text("[]")
    else:
        resources.launch_path(resource.generation).unlink()
    with pytest.raises(ResourceIdentityError):
        bg_jobs.get("job", expected=resource)
