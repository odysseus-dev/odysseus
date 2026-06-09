"""Workspace confinement: file tools are hard-bounded to the workspace folder
(layered on upstream's sensitive-path policy); bash runs with cwd there."""
import os
import tempfile

import pytest

from src.tool_execution import _resolve_tool_path_in_workspace, _direct_fallback


def test_workspace_resolver_confines():
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "a.txt"), "w").write("x")
    real = os.path.realpath(os.path.join(ws, "a.txt"))
    # relative path resolves under the workspace
    assert _resolve_tool_path_in_workspace(ws, "a.txt") == real
    # absolute path inside the workspace is allowed
    assert _resolve_tool_path_in_workspace(ws, os.path.join(ws, "a.txt")) == real
    # absolute path outside is rejected (sibling temp dir, portable across OSes)
    outside = tempfile.mkdtemp()
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, os.path.join(outside, "x.txt"))
    # parent-escape is rejected
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, os.path.join("..", "..", "escape.txt"))


def test_workspace_resolver_blocks_sensitive():
    """Upstream's sensitive-file deny list still applies inside the workspace."""
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, ".ssh"), exist_ok=True)
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, ".ssh/authorized_keys")


@pytest.mark.asyncio
async def test_read_write_confined_in_workspace():
    ws = tempfile.mkdtemp()
    # Write inside the workspace (relative path) succeeds.
    res = await _direct_fallback("write_file", "note.txt\nhello", workspace=ws)
    assert res["exit_code"] == 0
    assert os.path.isfile(os.path.join(ws, "note.txt"))
    # Read it back.
    res = await _direct_fallback("read_file", "note.txt", workspace=ws)
    assert res["exit_code"] == 0 and res["output"] == "hello"
    # Reading outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    outside_file = os.path.join(outside, "secret.txt")
    open(outside_file, "w").write("nope")
    res = await _direct_fallback("read_file", outside_file, workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    # Writing outside is rejected (file must not be created).
    escape = os.path.join(outside, "_ws_escape.txt")
    res = await _direct_fallback("write_file", f"{escape}\nx", workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    assert not os.path.exists(escape)


def test_browse_is_admin_gated(monkeypatch):
    """The directory-browser endpoint must refuse non-admin callers."""
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    router = wr.setup_workspace_routes()
    browse = next(r.endpoint for r in router.routes if r.path == "/api/workspace/browse")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "bob")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)
    with pytest.raises(HTTPException) as ei:
        browse(request=object(), path="/")
    assert ei.value.status_code == 403

    # Admin / single-user is allowed.
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    out = browse(request=object(), path=os.path.expanduser("~"))
    assert "dirs" in out and "path" in out
    assert all("name" in d and "path" in d for d in out["dirs"])


@pytest.mark.asyncio
async def test_subprocess_runs_with_workspace_cwd():
    """bash/python subprocesses run with cwd set to the workspace. Use the
    python tool for an OS-agnostic cwd probe (Windows cmd has no `pwd`)."""
    ws = tempfile.mkdtemp()
    res = await _direct_fallback("python", "import os; print(os.getcwd())", workspace=ws)
    assert res["exit_code"] == 0
    assert os.path.realpath(res["output"].strip()) == os.path.realpath(ws)


# --- Tools that landed after this PR, now wired into the workspace -----------

@pytest.mark.asyncio
async def test_edit_file_confined_in_workspace():
    import json
    from src.tool_execution import _do_edit_file
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "f.txt"), "w").write("foo bar")
    # Edit inside the workspace succeeds.
    res = await _do_edit_file(json.dumps(
        {"path": "f.txt", "old_string": "foo", "new_string": "baz"}), workspace=ws)
    assert res["exit_code"] == 0
    assert open(os.path.join(ws, "f.txt")).read() == "baz bar"
    # Editing outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    outside_file = os.path.join(outside, "f.txt")
    open(outside_file, "w").write("a")
    res = await _do_edit_file(json.dumps(
        {"path": outside_file, "old_string": "a", "new_string": "b"}), workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]


@pytest.mark.asyncio
async def test_grep_and_ls_confined_in_workspace():
    import json
    ws = tempfile.mkdtemp()
    open(os.path.join(ws, "doc.txt"), "w").write("hello workspace\n")
    # grep with no path searches the workspace root and finds the match.
    res = await _direct_fallback("grep", json.dumps({"pattern": "hello"}), workspace=ws)
    assert res["exit_code"] == 0 and "doc.txt" in res["output"]
    # grep pointed outside the workspace is rejected (sibling temp dir, portable).
    outside = tempfile.mkdtemp()
    res = await _direct_fallback("grep", json.dumps({"pattern": "x", "path": outside}), workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
    # ls of the workspace lists its files; ls outside is rejected.
    res = await _direct_fallback("ls", "", workspace=ws)
    assert res["exit_code"] == 0 and "doc.txt" in res["output"]
    res = await _direct_fallback("ls", outside, workspace=ws)
    assert res["exit_code"] == 1 and "outside the workspace" in res["error"]
