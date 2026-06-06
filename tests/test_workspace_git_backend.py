import os
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _git(repo, *args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.test")
    _git(path, "config", "user.name", "Workspace Tests")
    return path


def _client(monkeypatch, *, admin=True):
    import routes.workspace_routes as workspace_routes
    import routes.workspace_git_routes as workspace_git_routes

    monkeypatch.setattr(workspace_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(workspace_routes, "owner_is_admin_or_single_user", lambda owner: admin)
    monkeypatch.setattr(workspace_git_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(workspace_git_routes, "owner_is_admin_or_single_user", lambda owner: admin)

    app = FastAPI()
    app.include_router(workspace_routes.setup_workspace_routes())
    app.include_router(workspace_git_routes.setup_workspace_git_routes())
    return TestClient(app)


def test_git_routes_reject_non_admin_owner(monkeypatch, tmp_path):
    client = _client(monkeypatch, admin=False)

    response = client.get("/api/workspace/git/status", params={"workspace": str(tmp_path)})

    assert response.status_code == 403
    assert response.json()["code"] == "not_authorized"


def test_all_workspace_file_and_git_endpoints_reject_non_admin_owner(monkeypatch, tmp_path):
    client = _client(monkeypatch, admin=False)
    get_endpoints = [
        ("/api/workspace/files", {"workspace": str(tmp_path)}),
        ("/api/workspace/file", {"workspace": str(tmp_path), "path": "x"}),
        ("/api/workspace/git/status", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/diff", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/branches", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/history", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/blame", {"workspace": str(tmp_path), "path": "x"}),
        ("/api/workspace/git/conflicts", {"workspace": str(tmp_path)}),
    ]
    post_endpoints = [
        ("/api/workspace/file/save", {"workspace": str(tmp_path), "path": "x", "content": ""}),
        ("/api/workspace/git/stage", {"workspace": str(tmp_path), "paths": ["x"]}),
        ("/api/workspace/git/unstage", {"workspace": str(tmp_path), "paths": ["x"]}),
        ("/api/workspace/git/discard", {"workspace": str(tmp_path), "paths": ["x"]}),
        ("/api/workspace/git/hunks/stage", {"workspace": str(tmp_path), "path": "x", "hunkId": "h"}),
        ("/api/workspace/git/hunks/unstage", {"workspace": str(tmp_path), "path": "x", "hunkId": "h"}),
        ("/api/workspace/git/commit", {"workspace": str(tmp_path), "message": "m"}),
        ("/api/workspace/git/commit-selected", {"workspace": str(tmp_path), "paths": ["x"], "message": "m"}),
        ("/api/workspace/git/checkout", {"workspace": str(tmp_path), "branch": "main"}),
        ("/api/workspace/git/checkout-stash", {"workspace": str(tmp_path), "branch": "main"}),
        ("/api/workspace/git/branch/create", {"workspace": str(tmp_path), "branch": "feature"}),
        ("/api/workspace/git/fetch", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/pull", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/push", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/init", {"workspace": str(tmp_path)}),
        ("/api/workspace/git/clone", {"workspace": str(tmp_path), "url": str(tmp_path), "name": "x"}),
        ("/api/workspace/git/conflict/resolve", {"workspace": str(tmp_path), "path": "x", "content": ""}),
    ]

    for path, params in get_endpoints:
        response = client.get(path, params=params)
        assert response.status_code == 403, path
        assert response.json()["code"] == "not_authorized"
    for path, body in post_endpoints:
        response = client.post(path, json=body)
        assert response.status_code == 403, path
        assert response.json()["code"] == "not_authorized"


def test_git_routes_reject_missing_or_invalid_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch)

    missing = client.get("/api/workspace/git/status")
    invalid = client.get("/api/workspace/git/status", params={"workspace": str(tmp_path / "missing")})

    assert missing.status_code == 400
    assert missing.json()["code"] == "invalid_workspace"
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_workspace"


def test_git_status_rejects_non_git_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch)

    response = client.get("/api/workspace/git/status", params={"workspace": str(tmp_path)})

    assert response.status_code == 400
    assert response.json()["code"] == "not_git_repo"


def test_workspace_file_list_read_save_and_rejections(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    (tmp_path / "b-dir").mkdir()
    (tmp_path / "a-dir").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "A.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"a\x00b")
    (tmp_path / ".ssh").mkdir()

    listed = client.get("/api/workspace/files", params={"workspace": str(tmp_path), "path": ""})
    assert listed.status_code == 200
    body = listed.json()
    assert [d["name"] for d in body["dirs"]] == ["a-dir", "b-dir"]
    assert [f["name"] for f in body["files"]] == ["A.txt", "bin.dat", "z.txt"]

    read_text = client.get("/api/workspace/file", params={"workspace": str(tmp_path), "path": "A.txt"})
    assert read_text.json()["content"] == "hello"
    assert read_text.json()["binary"] is False
    assert read_text.json()["editable"] is True

    read_binary = client.get("/api/workspace/file", params={"workspace": str(tmp_path), "path": "bin.dat"})
    assert read_binary.json()["binary"] is True
    assert read_binary.json()["editable"] is False
    assert "content" not in read_binary.json()

    saved = client.post(
        "/api/workspace/file/save",
        json={"workspace": str(tmp_path), "path": "A.txt", "content": "updated"},
    )
    assert saved.status_code == 200
    assert (tmp_path / "A.txt").read_text(encoding="utf-8") == "updated"

    traversal = client.get("/api/workspace/file", params={"workspace": str(tmp_path), "path": "../x"})
    sensitive = client.post(
        "/api/workspace/file/save",
        json={"workspace": str(tmp_path), "path": ".ssh/authorized_keys", "content": "x"},
    )
    new_parent = client.post(
        "/api/workspace/file/save",
        json={"workspace": str(tmp_path), "path": "new/child.txt", "content": "x"},
    )
    assert traversal.status_code == 400
    assert traversal.json()["code"] == "outside_workspace"
    assert sensitive.status_code == 400
    assert sensitive.json()["code"] == "outside_workspace"
    assert new_parent.status_code == 400
    assert new_parent.json()["code"] == "outside_workspace"


def test_workspace_file_apis_reject_git_metadata_and_malformed_body(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "safe.txt").write_text("safe\n", encoding="utf-8")

    read_git = client.get("/api/workspace/file", params={"workspace": str(repo), "path": ".git/config"})
    write_git = client.post(
        "/api/workspace/file/save",
        json={"workspace": str(repo), "path": ".git/hooks/pre-commit", "content": "#!/bin/sh\nexit 1\n"},
    )
    missing_content = client.post(
        "/api/workspace/file/save",
        json={"workspace": str(repo), "path": "safe.txt"},
    )

    assert read_git.status_code == 400
    assert read_git.json()["code"] == "outside_workspace"
    assert write_git.status_code == 400
    assert write_git.json()["code"] == "outside_workspace"
    assert missing_content.status_code == 400
    assert missing_content.json()["code"] == "invalid_request"
    assert (repo / "safe.txt").read_text(encoding="utf-8") == "safe\n"


def test_git_status_and_diff_cover_common_file_states(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "delete-me.txt").write_text("gone\n", encoding="utf-8")
    (repo / "old-name.txt").write_text("rename\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "tracked.txt").write_text("base\nchanged\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    (repo / "delete-me.txt").unlink()
    _git(repo, "mv", "old-name.txt", "new-name.txt")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    _git(repo, "add", "new.txt", "new-name.txt")

    status = client.get("/api/workspace/git/status", params={"workspace": str(repo)}).json()
    by_path = {item["path"]: item for item in status["files"]}
    assert by_path["tracked.txt"]["worktree"] == "modified"
    assert by_path["new.txt"]["index"] == "added"
    assert by_path["delete-me.txt"]["worktree"] == "deleted"
    assert by_path["new-name.txt"]["index"] == "renamed"
    assert "ignored.txt" not in by_path

    diff = client.get(
        "/api/workspace/git/diff",
        params={"workspace": str(repo), "path": "tracked.txt"},
    ).json()
    assert diff["files"][0]["path"] == "tracked.txt"
    assert diff["files"][0]["hunks"]
    assert "+changed" in diff["patch"]


def test_git_status_and_diff_are_bounded_to_selected_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    workspace = repo / "workspace"
    workspace.mkdir()
    (workspace / "visible.txt").write_text("base\n", encoding="utf-8")
    (repo / "secret.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (workspace / "visible.txt").write_text("base\nvisible\n", encoding="utf-8")
    (repo / "secret.txt").write_text("base\nsecret\n", encoding="utf-8")

    status = client.get("/api/workspace/git/status", params={"workspace": str(workspace)}).json()
    assert [item["path"] for item in status["files"]] == ["visible.txt"]

    diff = client.get("/api/workspace/git/diff", params={"workspace": str(workspace)}).json()
    assert "visible" in diff["patch"]
    assert "secret" not in diff["patch"]
    assert diff["files"][0]["path"] == "visible.txt"


def test_git_mutation_responses_use_workspace_relative_paths(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    workspace = repo / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    (workspace / "file.txt").write_text("changed\n", encoding="utf-8")

    staged = client.post("/api/workspace/git/stage", json={"workspace": str(workspace), "paths": ["file.txt"]})
    selected = client.post(
        "/api/workspace/git/commit-selected",
        json={"workspace": str(workspace), "paths": ["file.txt"], "message": "selected"},
    )
    blame = client.get("/api/workspace/git/blame", params={"workspace": str(workspace), "path": "file.txt"})

    assert staged.status_code == 200
    assert staged.json()["paths"] == ["file.txt"]
    assert selected.status_code == 200
    assert selected.json()["paths"] == ["file.txt"]
    assert blame.status_code == 200
    assert blame.json()["path"] == "file.txt"


def test_git_rejects_absolute_blank_and_root_pathspecs(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")

    absolute = client.post("/api/workspace/git/stage", json={"workspace": str(repo), "paths": [str(repo / "file.txt")]})
    blank = client.post("/api/workspace/git/stage", json={"workspace": str(repo), "paths": [""]})
    root = client.post("/api/workspace/git/discard", json={"workspace": str(repo), "paths": ["."]})

    assert absolute.status_code == 400
    assert absolute.json()["code"] == "outside_workspace"
    assert blank.status_code == 400
    assert blank.json()["code"] == "outside_workspace"
    assert root.status_code == 400
    assert root.json()["code"] == "outside_workspace"


def test_git_stage_unstage_discard_and_hunk_operations(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")

    (repo / "file.txt").write_text("ONE\ntwo\nTHREE\n", encoding="utf-8")
    diff = client.get(
        "/api/workspace/git/diff",
        params={"workspace": str(repo), "path": "file.txt"},
    ).json()
    hunk_id = diff["files"][0]["hunks"][0]["id"]

    staged_hunk = client.post(
        "/api/workspace/git/hunks/stage",
        json={"workspace": str(repo), "path": "file.txt", "hunkId": hunk_id},
    )
    assert staged_hunk.status_code == 200
    assert client.get("/api/workspace/git/diff", params={"workspace": str(repo), "staged": True}).json()["files"]

    unstaged_hunk = client.post(
        "/api/workspace/git/hunks/unstage",
        json={"workspace": str(repo), "path": "file.txt", "hunkId": hunk_id},
    )
    assert unstaged_hunk.status_code == 200

    assert client.post("/api/workspace/git/stage", json={"workspace": str(repo), "paths": ["file.txt"]}).status_code == 200
    assert client.post("/api/workspace/git/unstage", json={"workspace": str(repo), "paths": ["file.txt"]}).status_code == 200
    assert client.post("/api/workspace/git/discard", json={"workspace": str(repo), "paths": ["file.txt"]}).status_code == 200
    assert (repo / "file.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\n"

    traversal = client.post("/api/workspace/git/stage", json={"workspace": str(repo), "paths": ["../escape"]})
    assert traversal.status_code == 400
    assert traversal.json()["code"] == "outside_workspace"


def test_git_stage_rejects_clean_filter_config(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "config", "filter.bad.clean", "sh -c 'echo pwned > should-not-run'")
    _git(repo, "config", "filter.bad.smudge", "cat")
    (repo / ".gitattributes").write_text("*.txt filter=bad\n", encoding="utf-8")
    (repo / "file.txt").write_text("content\n", encoding="utf-8")

    response = client.post("/api/workspace/git/stage", json={"workspace": str(repo), "paths": ["file.txt"]})

    assert response.status_code == 400
    assert response.json()["code"] == "git_failed"
    assert "unsafe git config" in response.json()["error"]
    assert not (repo / "should-not-run").exists()


def test_git_discard_removes_staged_additions_and_restores_staged_deletions(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "delete-me.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "delete-me.txt")
    _git(repo, "commit", "-m", "base")

    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    add_response = client.post("/api/workspace/git/discard", json={"workspace": str(repo), "paths": ["new.txt"]})
    assert add_response.status_code == 200
    assert not (repo / "new.txt").exists()
    assert "new.txt" not in _git(repo, "diff", "--cached", "--name-only").stdout

    (repo / "delete-me.txt").unlink()
    _git(repo, "add", "delete-me.txt")
    delete_response = client.post("/api/workspace/git/discard", json={"workspace": str(repo), "paths": ["delete-me.txt"]})
    assert delete_response.status_code == 200
    assert (repo / "delete-me.txt").read_text(encoding="utf-8") == "base\n"
    assert "delete-me.txt" not in _git(repo, "diff", "--cached", "--name-only").stdout


def test_git_discard_reports_tracked_restore_failure(monkeypatch, tmp_path):
    import subprocess
    import src.workspace_git as workspace_git

    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")

    real_git_run = workspace_git.git_run

    def failing_restore(repo_root, args, *, check=True, env=None):
        if args[:2] == ["restore", "--worktree"]:
            return subprocess.CompletedProcess(["git", *args], 1, "", "restore failed")
        return real_git_run(repo_root, args, check=check, env=env)

    monkeypatch.setattr(workspace_git, "git_run", failing_restore)

    response = client.post("/api/workspace/git/discard", json={"workspace": str(repo), "paths": ["file.txt"]})

    assert response.status_code == 400
    assert response.json()["code"] == "git_failed"


def test_git_commit_branches_and_local_remote_actions(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "push", "-u", "origin", "master")

    (repo / "file.txt").write_text("base\nnext\n", encoding="utf-8")
    committed = client.post("/api/workspace/git/commit-selected", json={
        "workspace": str(repo),
        "paths": ["file.txt"],
        "message": "selected",
    })
    assert committed.status_code == 200
    assert committed.json()["commit"]

    branches = client.get("/api/workspace/git/branches", params={"workspace": str(repo)}).json()
    assert branches["current"]
    assert any(branch["name"] == "master" for branch in branches["local"])

    _git(repo, "checkout", "-b", "feature")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    blocked = client.post("/api/workspace/git/checkout", json={"workspace": str(repo), "branch": "master"})
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "dirty_worktree"
    stashed = client.post("/api/workspace/git/checkout-stash", json={"workspace": str(repo), "branch": "master"})
    assert stashed.status_code == 200

    assert client.post("/api/workspace/git/fetch", json={"workspace": str(repo)}).status_code == 200
    assert client.post("/api/workspace/git/pull", json={"workspace": str(repo)}).status_code == 200
    assert client.post("/api/workspace/git/push", json={"workspace": str(repo)}).status_code == 200


def test_git_create_branch(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")

    created = client.post("/api/workspace/git/branch/create", json={"workspace": str(repo), "branch": "feature-x"})
    assert created.status_code == 200
    assert created.json()["branch"] == "feature-x"
    assert _git(repo, "branch", "--show-current").stdout.strip() == "feature-x"

    # Creating an existing branch is rejected.
    dup = client.post("/api/workspace/git/branch/create", json={"workspace": str(repo), "branch": "feature-x"})
    assert dup.status_code == 400
    assert dup.json()["code"] == "git_failed"

    # Invalid names are rejected before reaching the worktree.
    for bad in ("bad name", "-x", "foo..bar"):
        resp = client.post("/api/workspace/git/branch/create", json={"workspace": str(repo), "branch": bad})
        assert resp.status_code == 400, bad


def test_git_create_branch_rejects_nested_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    workspace = repo / "sub"
    workspace.mkdir()
    (workspace / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    resp = client.post("/api/workspace/git/branch/create", json={"workspace": str(workspace), "branch": "feature"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "outside_workspace"


def test_git_commit_staged_changes(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")

    response = client.post("/api/workspace/git/commit", json={"workspace": str(repo), "message": "normal commit"})

    assert response.status_code == 200
    assert response.json()["commit"]
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines() == ["file.txt"]


def test_repo_wide_mutations_reject_nested_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    workspace = repo / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    for endpoint, body in [
        ("/api/workspace/git/checkout", {"workspace": str(workspace), "branch": "master"}),
        ("/api/workspace/git/checkout-stash", {"workspace": str(workspace), "branch": "master"}),
        ("/api/workspace/git/fetch", {"workspace": str(workspace)}),
        ("/api/workspace/git/pull", {"workspace": str(workspace)}),
        ("/api/workspace/git/push", {"workspace": str(workspace)}),
    ]:
        response = client.post(endpoint, json=body)
        assert response.status_code == 400, endpoint
        assert response.json()["code"] == "outside_workspace"


def test_git_status_and_branches_report_upstream_ahead_behind(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "push", "-u", "origin", "master")

    (repo / "ahead.txt").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "ahead.txt")
    _git(repo, "commit", "-m", "ahead")
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(remote), str(other))
    _git(other, "config", "user.email", "tests@example.test")
    _git(other, "config", "user.name", "Workspace Tests")
    (other / "behind.txt").write_text("behind\n", encoding="utf-8")
    _git(other, "add", "behind.txt")
    _git(other, "commit", "-m", "behind")
    _git(other, "push")
    _git(repo, "fetch")

    status = client.get("/api/workspace/git/status", params={"workspace": str(repo)}).json()
    assert status["upstream"] == "origin/master"
    assert status["ahead"] == 1
    assert status["behind"] == 1

    branches = client.get("/api/workspace/git/branches", params={"workspace": str(repo)}).json()
    master = next(branch for branch in branches["local"] if branch["name"] == "master")
    assert master["upstream"] == "origin/master"
    assert master["ahead"] == 1
    assert master["behind"] == 1
    assert any(branch["name"] == "origin/master" for branch in branches["remote"])


def test_selected_commit_preserves_unrelated_staged_changes(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "selected.txt").write_text("base\n", encoding="utf-8")
    (repo / "staged.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "selected.txt").write_text("selected\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")

    response = client.post("/api/workspace/git/commit-selected", json={
        "workspace": str(repo),
        "paths": ["selected.txt"],
        "message": "selected only",
    })

    assert response.status_code == 200
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert committed == ["selected.txt"]
    assert "staged.txt" in _git(repo, "diff", "--cached", "--name-only").stdout.splitlines()


def test_selected_commit_failure_does_not_stage_selected_paths(monkeypatch, tmp_path):
    import src.workspace_git as workspace_git

    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "selected.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "selected.txt")
    _git(repo, "commit", "-m", "base")
    real_git_run = workspace_git.git_run

    def failing_commit(repo_root, args, *, check=True, env=None):
        if args and args[0] == "commit":
            raise workspace_git.GitWorkspaceError("git_failed", "simulated commit failure")
        return real_git_run(repo_root, args, check=check, env=env)

    monkeypatch.setattr(workspace_git, "git_run", failing_commit)

    (repo / "selected.txt").write_text("changed\n", encoding="utf-8")
    response = client.post("/api/workspace/git/commit-selected", json={
        "workspace": str(repo),
        "paths": ["selected.txt"],
        "message": "blocked",
    })

    assert response.status_code == 400
    assert response.json()["code"] == "git_failed"
    assert _git(repo, "diff", "--cached", "--name-only").stdout == ""


def test_initial_selected_commit(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "first.txt").write_text("first\n", encoding="utf-8")

    response = client.post("/api/workspace/git/commit-selected", json={
        "workspace": str(repo),
        "paths": ["first.txt"],
        "message": "initial selected",
    })

    assert response.status_code == 200
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines() == ["first.txt"]


def test_selected_commit_can_commit_deletions(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "deleted.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "deleted.txt")
    _git(repo, "commit", "-m", "base")
    (repo / "deleted.txt").unlink()

    response = client.post("/api/workspace/git/commit-selected", json={
        "workspace": str(repo),
        "paths": ["deleted.txt"],
        "message": "delete selected",
    })

    assert response.status_code == 200
    assert _git(repo, "show", "--name-status", "--format=", "HEAD").stdout.splitlines() == ["D\tdeleted.txt"]


def test_git_init_clone_history_blame_and_conflict_resolution(monkeypatch, tmp_path):
    client = _client(monkeypatch)

    init_target = tmp_path / "init-target"
    init_target.mkdir()
    init_response = client.post("/api/workspace/git/init", json={"workspace": str(init_target)})
    assert init_response.status_code == 200
    assert (init_target / ".git").is_dir()

    source = _init_repo(tmp_path / "source")
    (source / "file.txt").write_text("line one\nline two\n", encoding="utf-8")
    _git(source, "add", "file.txt")
    _git(source, "commit", "-m", "base")

    clone_parent = tmp_path / "clones"
    clone = client.post("/api/workspace/git/clone", json={
        "workspace": str(tmp_path),
        "url": str(source),
        "target": str(clone_parent),
        "name": "safe-clone",
    })
    assert clone.status_code == 200
    clone_path = clone.json()["path"]
    assert os.path.isdir(os.path.join(clone_path, ".git"))

    unsafe = client.post("/api/workspace/git/clone", json={
        "workspace": str(tmp_path),
        "url": str(source),
        "target": str(clone_parent),
        "name": "../bad",
    })
    assert unsafe.status_code == 400
    assert unsafe.json()["code"] == "outside_workspace"

    no_workspace_target = client.post("/api/workspace/git/clone", json={
        "url": str(source),
        "target": str(tmp_path / "anywhere"),
        "name": "clone",
    })
    assert no_workspace_target.status_code == 400
    assert no_workspace_target.json()["code"] == "invalid_workspace"

    history = client.get("/api/workspace/git/history", params={"workspace": clone_path}).json()
    assert history["commits"][0]["message"] == "base"
    file_history = client.get(
        "/api/workspace/git/history",
        params={"workspace": clone_path, "path": "file.txt"},
    ).json()
    assert file_history["commits"]
    blame = client.get("/api/workspace/git/blame", params={"workspace": clone_path, "path": "file.txt"}).json()
    assert blame["lines"][0]["text"] == "line one"

    conflict_repo = _init_repo(tmp_path / "conflict")
    (conflict_repo / "conflict.txt").write_text("base\n", encoding="utf-8")
    _git(conflict_repo, "add", "conflict.txt")
    _git(conflict_repo, "commit", "-m", "base")
    _git(conflict_repo, "checkout", "-b", "left")
    (conflict_repo / "conflict.txt").write_text("left\n", encoding="utf-8")
    _git(conflict_repo, "commit", "-am", "left")
    _git(conflict_repo, "checkout", "master")
    _git(conflict_repo, "checkout", "-b", "right")
    (conflict_repo / "conflict.txt").write_text("right\n", encoding="utf-8")
    _git(conflict_repo, "commit", "-am", "right")
    _git(conflict_repo, "merge", "left", check=False)

    discard_blocked = client.post(
        "/api/workspace/git/discard",
        json={"workspace": str(conflict_repo), "paths": ["conflict.txt"]},
    )
    assert discard_blocked.status_code == 400
    assert discard_blocked.json()["code"] == "merge_conflict"

    conflicts = client.get("/api/workspace/git/conflicts", params={"workspace": str(conflict_repo)}).json()
    assert conflicts["files"][0]["path"] == "conflict.txt"

    unresolved = client.post("/api/workspace/git/conflict/resolve", json={
        "workspace": str(conflict_repo),
        "path": "conflict.txt",
        "content": "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> other\n",
    })
    assert unresolved.status_code == 400
    assert unresolved.json()["code"] == "merge_conflict"
    resolved = client.post("/api/workspace/git/conflict/resolve", json={
        "workspace": str(conflict_repo),
        "path": "conflict.txt",
        "content": "resolved\n",
    })
    assert resolved.status_code == 200
    assert not client.get("/api/workspace/git/conflicts", params={"workspace": str(conflict_repo)}).json()["files"]


def test_clone_default_parent_and_binary_blame_rejection(monkeypatch, tmp_path):
    import src.workspace_git as workspace_git

    client = _client(monkeypatch)
    source = _init_repo(tmp_path / "source")
    (source / "file.txt").write_text("content\n", encoding="utf-8")
    _git(source, "add", "file.txt")
    _git(source, "commit", "-m", "base")

    default_parent = tmp_path / "default-clones"
    monkeypatch.setattr(workspace_git, "default_clone_parent", lambda: str(default_parent))
    clone = client.post("/api/workspace/git/clone", json={"url": str(source)})

    assert clone.status_code == 200
    assert clone.json()["path"] == os.path.join(str(default_parent), "source")
    assert os.path.isdir(os.path.join(clone.json()["path"], ".git"))

    (source / "bin.dat").write_bytes(b"a\x00b")
    _git(source, "add", "bin.dat")
    _git(source, "commit", "-m", "binary")
    rejected = client.get("/api/workspace/git/blame", params={"workspace": str(source), "path": "bin.dat"})
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "binary_file"


def test_default_clone_parent_uses_docker_or_home(monkeypatch):
    import src.workspace_git as workspace_git

    monkeypatch.setattr(workspace_git.os.path, "exists", lambda path: path == "/.dockerenv")
    assert workspace_git.default_clone_parent() == "/app/data/workspaces"

    monkeypatch.setattr(workspace_git.os.path, "exists", lambda path: False)
    monkeypatch.setattr(workspace_git.Path, "read_text", lambda self, errors=None: "0::/docker/abc")
    assert workspace_git.default_clone_parent() == "/app/data/workspaces"

    monkeypatch.setattr(workspace_git.Path, "read_text", lambda self, errors=None: "")
    assert workspace_git.default_clone_parent().endswith("odysseus-workspaces")


def test_missing_git_returns_stable_error(monkeypatch, tmp_path):
    import src.workspace_git as workspace_git

    client = _client(monkeypatch)
    _init_repo(tmp_path / "repo")
    monkeypatch.setattr(workspace_git.shutil, "which", lambda name: None)

    response = client.get("/api/workspace/git/status", params={"workspace": str(tmp_path / "repo")})

    assert response.status_code == 400
    assert response.json()["code"] == "missing_git"


def test_conflict_marker_detection_covers_diff3_and_is_anchored():
    import src.workspace_git as workspace_git

    detect = workspace_git._contains_conflict_markers

    # Standard merge-style markers (with and without labels).
    assert detect("a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> branch\nb\n")
    assert detect("<<<<<<<\nx\n=======\ny\n>>>>>>>\n")
    # diff3/zdiff3 base separator must be treated as a remaining marker so a
    # leaked base section cannot pass validation and get staged.
    assert detect("ours\n||||||| merged common ancestors\nbase\n=======\ntheirs\n")
    assert detect("clean\n||||||| base\nstuff\n")
    assert not detect("resolved\n")
    # Exact-7-char anchoring: an 8+ char underline / long rule / inline text is NOT
    # a conflict marker.
    assert not detect("Title\n========\n\nbody text\n")  # 8 '=' (reST underline)
    assert not detect("section\n====================\nbody\n")  # long rule
    assert not detect("the operator <<<<<<< is documented inline\n")  # mid-line


def test_resolve_conflict_rejects_leaked_diff3_base(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "base")

    # Content that still carries a diff3 base separator must be rejected, not staged.
    leaked = client.post(
        "/api/workspace/git/conflict/resolve",
        json={
            "workspace": str(repo),
            "path": "f.txt",
            "content": "ours\n||||||| merged common ancestors\nbase\n",
        },
    )
    assert leaked.status_code == 400
    assert leaked.json()["code"] == "merge_conflict"
