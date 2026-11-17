from dataclasses import replace
import json
import signal

import pytest

from src import process_ownership
from src.process_lifecycle import ProcessIdentity, signal_identity
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority
from src.agent_runtime.resources import ProcessResource, NativeBackendResource, FilesystemRoot, ProcessLaunchScope, ResourceIdentityError
from src.agent_runtime.process_resources import resolve_process_operation
from src.containment import DEFAULT_REQUIRED


def process():
    return ProcessResource("native:containment", "alice", "request", "thread", ProcessIdentity(4321, "boot:start", 4321), "leader", "job", "receipt")


@pytest.mark.parametrize("verdict", [process_ownership.FOREIGN, process_ownership.GONE, process_ownership.UNVERIFIABLE])
def test_stale_reused_or_unverifiable_identity_cannot_be_admitted(monkeypatch, verdict):
    monkeypatch.setattr(process_ownership, "verify", lambda *a: verdict)
    with pytest.raises(ResourceIdentityError):
        process().validate()


@pytest.mark.parametrize("field,value", [("pid", 0), ("pid", "4321"), ("pid", True), ("pgid", "4321"), ("start_token", None), ("start_token", ""), ("start_token", {})])
def test_malformed_lifecycle_observations_fail_closed(field, value):
    record = process().to_dict()
    record["identity"][field] = value
    with pytest.raises((ValueError, TypeError)):
        ProcessResource.from_dict(record)


def test_no_duplicate_lifecycle_fields_and_strict_restore():
    resource = process()
    record = resource.to_dict()
    assert ProcessResource.from_dict(record) == resource
    assert "pid" not in record and "start_token" not in record
    record["identity"]["incarnation"] = "invented"
    with pytest.raises(ValueError):
        ProcessResource.from_dict(record)


def test_incarnation_is_not_application_ownership(monkeypatch):
    monkeypatch.setattr(process_ownership, "verify", lambda *a: process_ownership.OWNED)
    monkeypatch.setattr(ProcessIdentity, "exited", lambda self: False)
    resource = process()
    resource.validate()
    for field in ("namespace", "owner", "request_id", "thread_id", "role", "job_id", "containment_id"):
        if field in {"namespace", "role"}:
            with pytest.raises(ValueError):
                replace(resource, **{field: "supervisor" if field == "role" else "external:ssh"})
            continue
        changed = replace(resource, **{field: "supervisor" if field == "role" else "other"})
        assert changed != resource
    with pytest.raises(ValueError):
        RequestAuthority("request", "bob", "thread", "", process_resources=(resource,))
    with pytest.raises(ValueError):
        RequestAuthority("request", "alice", "other-thread", "", process_resources=(resource,))


def test_pid_reuse_at_signal_boundary_uses_wave5b_engine(monkeypatch):
    verdicts = iter([process_ownership.OWNED, process_ownership.OWNED, process_ownership.FOREIGN])
    monkeypatch.setattr(process_ownership, "verify", lambda *a: next(verdicts))
    monkeypatch.setattr("src.process_lifecycle.is_zombie", lambda pid: False)
    monkeypatch.setattr("os.kill", lambda *a: pytest.fail("reused PID signalled"))
    target = process()
    target.validate()
    assert signal_identity(target.identity, signal.SIGTERM) is False


def test_child_cannot_renew_replaced_parent_process(monkeypatch):
    old = process()
    fresh = replace(old, identity=replace(old.identity, start_token="boot:replacement"))
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: process_ownership.FOREIGN if token == "boot:start" else process_ownership.OWNED)
    parent = RequestAuthority("parent", "alice", "thread", "", process_resources=(old,))
    child = replace(parent, request_id="child", process_resources=(fresh,))
    result = parent.intersect(child)
    assert result.process_resources == ()


def test_legacy_authority_cannot_reconstruct_creation_scope(tmp_path):
    authority = RequestAuthority("request", "alice", "thread", str(tmp_path), (OperationGrant("bash"),))
    snapshot = authority.to_dict()
    snapshot["version"] = 3
    for field in ("launch_scopes", "process_resources", "job_resources"):
        snapshot.pop(field)
    restored = RequestAuthority.from_dict(snapshot)
    assert restored.launch_scopes == restored.process_resources == restored.job_resources == ()
    with pytest.raises(ResourceIdentityError):
        resolve_process_operation(restored, ExactOperation.normalize("bash", "pwd"), NativeBackendResource("bash"))


def test_launch_is_server_generation_exact_operation_and_credential_free(tmp_path):
    authority = RequestAuthority("request", "alice", "thread", str(tmp_path), (OperationGrant("bash"),))
    operation = ExactOperation.normalize("bash", "printf secret-token")
    bound = resolve_process_operation(authority, operation, NativeBackendResource("bash"))
    assert "secret-token" not in json.dumps(bound.to_dict())
    assert len(bound.launch.generation) == 32
    assert bound.launch.scope.root == authority.resource_roots[0]
    with pytest.raises(ResourceIdentityError):
        resolve_process_operation(authority, ExactOperation.normalize("bash", "pwd"), NativeBackendResource("bash"), approved=bound, exact_admission=True)


def test_child_launch_scope_can_narrow_but_cannot_broaden(tmp_path):
    sub = tmp_path / "child"
    sub.mkdir()
    parent = RequestAuthority("request", "alice", "thread", str(tmp_path), (OperationGrant("bash"),))
    smaller = ProcessLaunchScope(NativeBackendResource("bash"), FilesystemRoot.seal(sub, owner="alice"), DEFAULT_REQUIRED)
    child = replace(parent, launch_scopes=(smaller,))
    assert parent.intersect(child).launch_scopes == (smaller,)
    assert child.intersect(parent).launch_scopes == ()


def test_child_launch_cannot_refresh_a_replaced_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    parent = RequestAuthority("request", "alice", "thread", str(root), (OperationGrant("bash"),))
    root.rename(tmp_path / "retired")
    root.mkdir()
    child = RequestAuthority("child", "alice", "thread", str(root), (OperationGrant("bash"),))
    with pytest.raises(ResourceIdentityError):
        parent.intersect(child)
