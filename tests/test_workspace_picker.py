"""Workspace picker selection, persistence, and managed-folder behavior."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.prefs_routes as prefs_routes
import routes.workspace_routes as workspace_routes
import src.constants as constants


def _request(*, site="same-origin"):
    return SimpleNamespace(headers={"sec-fetch-site": site})


def _endpoint(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


@pytest.fixture
def workspace_api(monkeypatch, tmp_path):
    managed = tmp_path / "runtime" / "data" / "agent_workspace"
    prefs_file = tmp_path / "runtime" / "data" / "user_prefs.json"
    monkeypatch.setattr(workspace_routes, "AGENT_WORKSPACE_DIR", str(managed))
    monkeypatch.setattr(constants, "AGENT_WORKSPACE_DIR", str(managed))
    monkeypatch.setattr(constants, "DATA_DIR", str(managed.parent))
    monkeypatch.setattr(constants, "LOGS_DIR", str(managed.parent / "logs"))
    monkeypatch.setattr(constants, "MAIL_ATTACHMENTS_DIR", str(managed.parent / "mail"))
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(workspace_routes, "get_current_user", lambda _request: "admin")
    monkeypatch.setattr(
        workspace_routes,
        "owner_is_admin_or_single_user",
        lambda owner: owner == "admin",
    )
    router = workspace_routes.setup_workspace_routes()
    return {
        "managed": managed,
        "browse": _endpoint(router, "/api/workspace/browse", "GET"),
        "get": _endpoint(router, "/api/workspace/selection", "GET"),
        "select": _endpoint(router, "/api/workspace/selection", "POST"),
        "clear": _endpoint(router, "/api/workspace/selection", "DELETE"),
        "create": _endpoint(router, "/api/workspace/folders", "POST"),
    }


def test_default_workspace_is_created_and_browsed(workspace_api):
    managed = workspace_api["managed"]
    assert not managed.exists()

    out = workspace_api["browse"](request=_request(), path="")

    assert managed.is_dir()
    assert out["path"] == os.path.realpath(managed)
    assert out["default_path"] == os.path.realpath(managed)
    assert out["selectable"] is True
    assert out["can_create_folder"] is True

    selected = workspace_api["get"](request=_request())
    assert selected["path"] == ""
    assert selected["migration_allowed"] is True


def test_application_data_folder_is_visibly_unselectable(workspace_api):
    private = workspace_api["managed"].parent / "private"
    private.mkdir(parents=True)

    out = workspace_api["browse"](request=_request(), path=str(private))

    assert out["selectable"] is False
    assert "application data" in out["selectable_reason"].lower()


def test_browse_invalid_path_reports_missing_instead_of_falling_back(workspace_api):
    missing = workspace_api["managed"] / "missing"

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["browse"](request=_request(), path=str(missing))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Folder does not exist."


def test_invalid_path_reports_validation_error(workspace_api):
    with pytest.raises(HTTPException) as exc_info:
        workspace_api["browse"](request=_request(), path="bad\x00path")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Folder path is invalid."

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["select"](
            request=_request(),
            body=workspace_routes.WorkspaceSelection(path="bad\x00path"),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Folder path is invalid."


def test_default_workspace_failure_does_not_expose_internal_error(
    workspace_api, monkeypatch
):
    def fail_mkdir(*_args, **_kwargs):
        raise OSError("private deployment path and stack detail")

    monkeypatch.setattr(workspace_routes.Path, "mkdir", fail_mkdir)

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["browse"](request=_request(), path="")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Default workspace is unavailable."


def test_sandbox_policy_failure_does_not_expose_internal_error(
    workspace_api, monkeypatch
):
    import src.execution_sandbox as execution_sandbox

    project = workspace_api["managed"] / "project"
    project.mkdir(parents=True)

    def fail_policy(_path):
        raise RuntimeError("private sandbox configuration and stack detail")

    monkeypatch.setattr(
        execution_sandbox,
        "validate_sandbox_workspace_path",
        fail_policy,
    )

    out = workspace_api["browse"](request=_request(), path=str(project))

    assert out["selectable"] is False
    assert out["selectable_reason"] == "Workspace sandbox policy is unavailable."


def test_typed_existing_folder_is_selected_and_persisted(workspace_api):
    project = workspace_api["managed"] / "project"
    project.mkdir(parents=True)

    out = workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(project)),
    )
    selected = workspace_api["get"](request=_request())

    assert out["ok"] is True
    assert out["path"] == os.path.realpath(project)
    assert out["browse"]["path"] == os.path.realpath(project)
    assert selected["path"] == os.path.realpath(project)
    stored = json.loads(Path(prefs_routes.PREFS_FILE).read_text(encoding="utf-8"))
    assert stored["_users"]["admin"]["agent_workspace"] == os.path.realpath(project)


def test_selection_is_not_persisted_when_folder_refresh_fails(workspace_api, monkeypatch):
    original = workspace_api["managed"] / "original"
    unreadable = workspace_api["managed"] / "unreadable"
    original.mkdir(parents=True)
    unreadable.mkdir()
    workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(original)),
    )
    real_browse_payload = workspace_routes._browse_payload

    def fail_unreadable(target):
        if os.path.realpath(target) == os.path.realpath(unreadable):
            raise HTTPException(status_code=403, detail="Folder cannot be read.")
        return real_browse_payload(target)

    monkeypatch.setattr(workspace_routes, "_browse_payload", fail_unreadable)

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["select"](
            request=_request(),
            body=workspace_routes.WorkspaceSelection(path=str(unreadable)),
        )

    assert exc_info.value.status_code == 403
    assert workspace_api["get"](request=_request())["path"] == os.path.realpath(original)


def test_missing_managed_folder_offers_then_creates_and_selects(workspace_api):
    project = workspace_api["managed"] / "new" / "project"

    missing = workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(project)),
    )
    payload = json.loads(missing.body)
    assert missing.status_code == 404
    assert payload == {
        "ok": False,
        "error": "Folder does not exist.",
        "code": "folder_missing",
        "path": os.path.realpath(project),
        "can_create": True,
    }

    created = workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(project), create=True),
    )
    assert project.is_dir()
    assert created["path"] == os.path.realpath(project)
    assert workspace_api["get"](request=_request())["path"] == os.path.realpath(project)


def test_missing_sensitive_folder_is_not_offered_for_creation(workspace_api):
    target = workspace_api["managed"] / "project" / ".codex" / "state"

    missing = workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(target)),
    )

    payload = json.loads(missing.body)
    assert payload["code"] == "folder_missing"
    assert payload["can_create"] is False

    with pytest.raises(HTTPException):
        workspace_api["select"](
            request=_request(),
            body=workspace_routes.WorkspaceSelection(path=str(target), create=True),
        )
    assert not target.exists()


def test_missing_folder_outside_managed_root_cannot_be_created(workspace_api, tmp_path):
    outside = tmp_path / "outside" / "project"

    missing = workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(outside)),
    )
    assert json.loads(missing.body)["can_create"] is False

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["select"](
            request=_request(),
            body=workspace_routes.WorkspaceSelection(path=str(outside), create=True),
        )
    assert exc_info.value.status_code == 400
    assert "default workspace" in exc_info.value.detail
    assert not outside.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow creation regression")
def test_missing_folder_creation_rejects_symlink_swap(workspace_api, tmp_path, monkeypatch):
    managed = workspace_api["managed"]
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace_api["browse"](request=_request(), path="")
    target = managed / "swapped" / "project"

    original_target = workspace_routes._creation_target

    def swap_after_validation(raw_path):
        projected, can_create = original_target(raw_path)
        os.symlink(outside, managed / "swapped", target_is_directory=True)
        return projected, can_create

    monkeypatch.setattr(workspace_routes, "_creation_target", swap_after_validation)

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["select"](
            request=_request(),
            body=workspace_routes.WorkspaceSelection(path=str(target), create=True),
        )

    assert exc_info.value.status_code == 400
    assert not (outside / "project").exists()
    assert workspace_api["get"](request=_request())["path"] == ""


def test_missing_folder_creation_fails_closed_without_no_follow_support(
    workspace_api, monkeypatch
):
    managed = workspace_api["managed"]
    workspace_api["browse"](request=_request(), path="")
    target = managed / "new" / "project"

    monkeypatch.setattr(workspace_routes.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(workspace_routes.os, "O_DIRECTORY", 0, raising=False)

    with pytest.raises(RuntimeError, match="no-follow"):
        workspace_routes._create_managed_path(str(target))

    assert not target.exists()


def test_new_folder_button_api_creates_and_refreshes_into_folder(workspace_api):
    managed = workspace_api["managed"]
    workspace_api["browse"](request=_request(), path="")

    out = workspace_api["create"](
        request=_request(),
        body=workspace_routes.WorkspaceFolderCreate(
            parent=str(managed),
            name="repo-one",
        ),
    )

    created = managed / "repo-one"
    assert created.is_dir()
    assert out["path"] == os.path.realpath(created)
    assert out["browse"]["path"] == os.path.realpath(created)


@pytest.mark.parametrize("name", ["", ".hidden", "..", "a/b", "a\\b"])
def test_new_folder_rejects_unsafe_names(workspace_api, name):
    workspace_api["browse"](request=_request(), path="")
    with pytest.raises(HTTPException) as exc_info:
        workspace_api["create"](
            request=_request(),
            body=workspace_routes.WorkspaceFolderCreate(
                parent=str(workspace_api["managed"]),
                name=name,
            ),
        )
    assert exc_info.value.status_code == 400


def test_clear_removes_server_owned_selection(workspace_api):
    project = workspace_api["managed"] / "project"
    project.mkdir(parents=True)
    workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(project)),
    )

    out = workspace_api["clear"](request=_request())

    assert out["ok"] is True
    selected = workspace_api["get"](request=_request())
    assert selected["path"] == ""
    assert selected["migration_allowed"] is False
    stored = json.loads(Path(prefs_routes.PREFS_FILE).read_text(encoding="utf-8"))
    assert stored["_users"]["admin"]["agent_workspace"] == ""


def test_clear_does_not_commit_tombstone_before_default_workspace_ready(
    workspace_api, monkeypatch
):
    project = workspace_api["managed"] / "project"
    project.mkdir(parents=True)
    workspace_api["select"](
        request=_request(),
        body=workspace_routes.WorkspaceSelection(path=str(project)),
    )

    def fail_default_workspace():
        raise RuntimeError("default workspace unavailable")

    monkeypatch.setattr(workspace_routes, "ensure_default_workspace", fail_default_workspace)

    with pytest.raises(RuntimeError, match="default workspace unavailable"):
        workspace_api["clear"](request=_request())

    stored = json.loads(Path(prefs_routes.PREFS_FILE).read_text(encoding="utf-8"))
    assert stored["_users"]["admin"]["agent_workspace"] == os.path.realpath(project)


def test_workspace_mutations_reject_cross_site_requests(workspace_api):
    project = workspace_api["managed"] / "project"
    project.mkdir(parents=True)

    with pytest.raises(HTTPException) as exc_info:
        workspace_api["select"](
            request=_request(site="cross-site"),
            body=workspace_routes.WorkspaceSelection(path=str(project)),
        )

    assert exc_info.value.status_code == 403


def test_picker_source_uses_typed_value_and_visible_creation_controls():
    source = open("static/js/workspace.js", encoding="utf-8").read()

    assert "const path = input ? input.value.trim() : '';" in source
    assert "#workspace-use').addEventListener('click', () => _useTypedPath(false))" in source
    assert "'/api/workspace/selection'" in source
    assert "Create and use folder" in source
    assert 'id="workspace-new-folder"' in source
    assert 'id="workspace-selection-status"' in source
    assert "not sandboxed" not in source
    assert "cached && state.migration_allowed" in source
    assert "export function whenWorkspaceReady()" in source
    assert "return _syncServerWorkspace();" in source
    assert "_workspaceReadyPromise" not in source

    chat_source = open("static/js/chat.js", encoding="utf-8").read()
    assert "await workspaceModule.whenWorkspaceReady();" in chat_source
    assert "const _ws = workspaceModule.getWorkspace();" in chat_source
