"""Workspace confinement.

The agent's per-turn workspace is a single context-local binding set in
execute_tool_block. The shared path resolvers (_resolve_tool_path /
_resolve_search_root) and the subprocess cwd helper (agent_cwd) read it, so
confinement is enforced in ONE place: a tool that uses the shared helpers is
confined automatically and a new tool cannot accidentally bypass it.

Covers: the resolver helper, the central binding (the safety net), end-to-end
confinement of read/write/edit/grep/ls + subprocess cwd via execute_tool_block,
the get_workspace tool, no-leak across calls, and the admin-gated browse route.
"""
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from src.tool_execution import (
    _AGENT_WORKDIR,
    _active_workspace,
    _resolve_search_root,
    _resolve_tool_path,
    _resolve_tool_path_in_workspace,
    agent_cwd,
    execute_tool_block,
    get_active_workspace,
)


def _block(tool, content=""):
    return SimpleNamespace(tool_type=tool, content=content)


@pytest.fixture
def ws():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("x")
    return d


@pytest.fixture
def admin(monkeypatch):
    """Pass the public-tool gate so file tools dispatch in tests."""
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)
    monkeypatch.setitem(
        execute_tool_block.__globals__,
        "owner_is_admin_or_single_user",
        lambda owner: True,
    )


# ── the resolver helper ────────────────────────────────────────────────

def test_resolver_confines(ws):
    real = os.path.realpath(os.path.join(ws, "a.txt"))
    assert _resolve_tool_path_in_workspace(ws, "a.txt") == real          # relative
    assert _resolve_tool_path_in_workspace(ws, os.path.join(ws, "a.txt")) == real  # abs inside
    outside = tempfile.mkdtemp()
    with pytest.raises(ValueError):                                       # abs outside
        _resolve_tool_path_in_workspace(ws, os.path.join(outside, "x.txt"))
    with pytest.raises(ValueError):                                       # parent escape
        _resolve_tool_path_in_workspace(ws, os.path.join("..", "..", "escape.txt"))


def test_resolver_blocks_sensitive_inside_workspace(ws):
    os.makedirs(os.path.join(ws, ".ssh"), exist_ok=True)
    with pytest.raises(ValueError):
        _resolve_tool_path_in_workspace(ws, ".ssh/authorized_keys")


# ── the central binding: the safety net ─────────────────────────────────

def test_active_binding_confines_shared_resolvers(ws):
    """ANY tool resolving paths through the shared helpers is confined while the
    binding is active, without doing anything workspace-specific itself. This is
    what stops a newly added tool from accidentally ignoring the workspace."""
    token = _active_workspace.set(ws)
    try:
        assert get_active_workspace() == ws
        assert agent_cwd() == ws
        assert _resolve_tool_path("a.txt") == os.path.realpath(os.path.join(ws, "a.txt"))
        with pytest.raises(ValueError):          # normally-allowed root, now outside ws
            _resolve_tool_path("/tmp/whatever.txt")
        assert _resolve_search_root("") == os.path.realpath(ws)
    finally:
        _active_workspace.reset(token)


def test_no_binding_uses_default_roots():
    assert get_active_workspace() is None
    assert agent_cwd() == _AGENT_WORKDIR
    with pytest.raises(ValueError):
        _resolve_tool_path("/etc/hosts")


# ── end-to-end via execute_tool_block (sets + resets the binding) ───────

@pytest.mark.asyncio
async def test_read_write_edit_confined_e2e(ws, admin):
    _, r = await execute_tool_block(_block("write_file", "note.txt\nhello"), owner="a", workspace=ws)
    assert r["exit_code"] == 0 and os.path.isfile(os.path.join(ws, "note.txt"))
    _, r = await execute_tool_block(_block("read_file", "note.txt"), owner="a", workspace=ws)
    assert r["exit_code"] == 0 and r["output"] == "hello"

    with open(os.path.join(ws, "f.txt"), "w") as f:
        f.write("foo bar")
    _, r = await execute_tool_block(
        _block("edit_file", json.dumps({"path": "f.txt", "old_string": "foo", "new_string": "baz"})),
        owner="a", workspace=ws,
    )
    assert r["exit_code"] == 0
    with open(os.path.join(ws, "f.txt")) as f:
        assert f.read() == "baz bar"

    # outside the workspace is rejected, and nothing is created
    outside = tempfile.mkdtemp()
    of = os.path.join(outside, "secret.txt")
    with open(of, "w") as f:
        f.write("nope")
    _, r = await execute_tool_block(_block("read_file", of), owner="a", workspace=ws)
    assert r["exit_code"] == 1 and "outside the workspace" in r["error"]
    escape = os.path.join(outside, "_esc.txt")
    _, r = await execute_tool_block(_block("write_file", f"{escape}\nx"), owner="a", workspace=ws)
    assert r["exit_code"] == 1 and "outside the workspace" in r["error"]
    assert not os.path.exists(escape)


@pytest.mark.asyncio
async def test_grep_and_ls_confined_e2e(ws, admin):
    with open(os.path.join(ws, "doc.txt"), "w") as f:
        f.write("hello workspace\n")
    _, r = await execute_tool_block(_block("grep", json.dumps({"pattern": "hello"})), owner="a", workspace=ws)
    assert r["exit_code"] == 0 and "doc.txt" in r["output"]
    outside = tempfile.mkdtemp()
    _, r = await execute_tool_block(_block("grep", json.dumps({"pattern": "x", "path": outside})), owner="a", workspace=ws)
    assert r["exit_code"] == 1 and "outside the workspace" in r["error"]
    _, r = await execute_tool_block(_block("ls", ""), owner="a", workspace=ws)
    assert r["exit_code"] == 0 and "doc.txt" in r["output"]
    _, r = await execute_tool_block(_block("ls", outside), owner="a", workspace=ws)
    assert r["exit_code"] == 1 and "outside the workspace" in r["error"]


@pytest.mark.asyncio
async def test_workspace_bash_file_mutations_are_blocked(ws, admin):
    commands = [
        "printf 'x' > note.txt",
        "printf 'x' >> note.txt",
        "cat <<'EOF' > note.txt\nx\nEOF",
        "printf 'x' | tee note.txt",
        "cp a.txt b.txt",
        "touch note.txt",
        "sed -i 's/x/y/' a.txt",
    ]

    for command in commands:
        desc, r = await execute_tool_block(_block("bash", command), owner="a", workspace=ws)
        assert desc == "bash: BLOCKED"
        assert r["exit_code"] == 1
        assert "write_file" in r["error"]

    assert not os.path.exists(os.path.join(ws, "note.txt"))
    assert not os.path.exists(os.path.join(ws, "b.txt"))


@pytest.mark.asyncio
async def test_workspace_background_bash_file_mutation_is_blocked(ws, admin):
    desc, r = await execute_tool_block(
        _block("bash", "#!bg\nprintf 'x' > note.txt"),
        session_id="s1",
        owner="a",
        workspace=ws,
    )

    assert desc == "bash: BLOCKED"
    assert r["exit_code"] == 1
    assert "write_file" in r["error"]
    assert not os.path.exists(os.path.join(ws, "note.txt"))


@pytest.mark.asyncio
async def test_workspace_bash_read_only_diagnostics_remain_allowed(ws, admin):
    desc, r = await execute_tool_block(_block("bash", "echo OK 2>/dev/null"), owner="a", workspace=ws)

    assert desc != "bash: BLOCKED"
    assert r["exit_code"] == 0
    assert "OK" in r["output"]


@pytest.mark.asyncio
async def test_subprocess_cwd_is_workspace_e2e(ws, admin):
    """python tool runs with cwd = workspace (OS-agnostic probe)."""
    _, r = await execute_tool_block(_block("python", "import os; print(os.getcwd())"), owner="a", workspace=ws)
    assert r["exit_code"] == 0
    assert os.path.realpath(r["output"].strip()) == os.path.realpath(ws)


# ── get_workspace tool ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_workspace_tool(ws, admin):
    _, r = await execute_tool_block(_block("get_workspace", ""), owner="a", workspace=ws)
    assert r["exit_code"] == 0 and r["output"].startswith(ws) and "not sandboxed" in r["output"]
    _, r = await execute_tool_block(_block("get_workspace", ""), owner="a")  # none active
    assert r["exit_code"] == 0 and "No workspace" in r["output"]


# ── no leak across calls ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_binding_does_not_leak(ws, admin):
    await execute_tool_block(_block("ls", ""), owner="a", workspace=ws)
    assert get_active_workspace() is None


# ── tool selection: an active workspace is the file-work signal ─────────
# A vague ("low-signal") message like "look at the local project" matches no
# domain keywords, so retrieval is normally skipped. When a workspace is set it
# must still surface the file tools, otherwise the agent says it has no file
# access (the bug this guards against).

def _captured_agent_request(monkeypatch, *, workspace, prompt="look at the local project"):
    import asyncio
    import src.agent_loop as al

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    # Isolate the selection logic from owner gating (tested separately).
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    captured = {}

    async def _fake_stream(_candidates, messages, **kwargs):
        captured["tools"] = kwargs.get("tools")
        captured["messages"] = messages
        yield "data: " + json.dumps({"delta": "ok"}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    async def _run():
        gen = al.stream_agent_loop(
            "https://api.openai.com/v1", "gpt-test",
            [{"role": "user", "content": prompt}],
            max_rounds=1, relevant_tools=None, owner="admin", workspace=workspace,
        )
        captured["chunks"] = [c async for c in gen]

    asyncio.run(_run())
    return captured


def _sent_tool_names(monkeypatch, *, workspace, prompt="look at the local project"):
    captured = _captured_agent_request(monkeypatch, workspace=workspace, prompt=prompt)
    schemas = captured["tools"] or []
    return {t["function"]["name"] for t in schemas if isinstance(t, dict) and "function" in t}


def _events_from_chunks(chunks):
    events = []
    for chunk in chunks:
        for line in str(chunk).splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                continue
            events.append(json.loads(payload))
    return events


def test_low_signal_with_workspace_surfaces_readonly_file_tools(monkeypatch):
    names = _sent_tool_names(monkeypatch, workspace="/tmp")
    # read-only nav tools surface so the agent can explore
    assert "read_file" in names
    assert "get_workspace" in names
    assert "grep" in names
    # write/shell tools do NOT surface on a vague message
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "bash" not in names
    assert "python" not in names


def test_low_signal_without_workspace_excludes_file_tools(monkeypatch):
    names = _sent_tool_names(monkeypatch, workspace=None)
    assert "read_file" not in names
    assert "get_workspace" not in names


def test_workspace_copy_request_surfaces_write_file_tools(monkeypatch):
    names = _sent_tool_names(
        monkeypatch,
        workspace="/tmp",
        prompt="Copy README.txt to README_copy.txt and add a final line",
    )

    assert "get_workspace" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names


def test_workspace_readme_append_request_surfaces_write_file_tools(monkeypatch):
    names = _sent_tool_names(
        monkeypatch,
        workspace="/tmp",
        prompt="Append 'This is a test' to the README",
    )

    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names


def test_workspace_contract_prompt_is_injected(monkeypatch):
    captured = _captured_agent_request(monkeypatch, workspace="/tmp/project")
    messages = captured["messages"]
    contract = next(
        (m for m in messages if "ACTIVE WORKSPACE CONTRACT" in (m.get("content") or "")),
        None,
    )
    assert contract is not None
    assert contract["role"] == "system"
    assert contract.get("_protected") is None  # stripped before provider call
    assert "/tmp/project" in contract["content"]
    assert "write_file" in contract["content"]
    assert "verify the artifact" in contract["content"]
    assert "Do not say you lack permission" in contract["content"]


def test_workspace_contract_includes_configured_label(monkeypatch):
    import src.agent_loop as al

    monkeypatch.setenv("ODYSSEUS_DEFAULT_WORKSPACE", "/workspace")
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_LABEL", r"D:\Odysseus_Workspace")
    captured = _captured_agent_request(monkeypatch, workspace="/workspace")
    messages = captured["messages"]
    contract = next(
        (m for m in messages if "ACTIVE WORKSPACE CONTRACT" in (m.get("content") or "")),
        None,
    )

    assert contract is not None
    assert r"D:\Odysseus_Workspace (mounted as /workspace)" in contract["content"]
    assert "same workspace" in contract["content"]
    assert al._workspace_display_label("/workspace") == r"D:\Odysseus_Workspace (mounted as /workspace)"


def test_agent_limits_include_workspace_label(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DEFAULT_WORKSPACE", "/workspace")
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_LABEL", r"D:\Odysseus_Workspace")
    captured = _captured_agent_request(monkeypatch, workspace="/workspace")
    metrics = next(
        e["data"] for e in _events_from_chunks(captured["chunks"])
        if e.get("type") == "metrics"
    )

    limits = metrics["agent_limits"]
    assert limits["workspace_bound"] is True
    assert limits["workspace_path"] == "/workspace"
    assert limits["workspace_label"] == r"D:\Odysseus_Workspace (mounted as /workspace)"


def test_final_answer_contract_prompt_is_injected(monkeypatch):
    captured = _captured_agent_request(monkeypatch, workspace=None)
    messages = captured["messages"]
    contract = next(
        (m for m in messages if "FINAL ANSWER CONTRACT" in (m.get("content") or "")),
        None,
    )

    assert contract is not None
    assert contract["role"] == "system"
    assert contract.get("_protected") is None
    assert "Keep the final answer concise" in contract["content"]
    assert "Do not replay tool commands" in contract["content"]


# ── browse route is admin-gated ─────────────────────────────────────────

def test_browse_is_admin_gated(monkeypatch):
    from fastapi import HTTPException
    import routes.workspace_routes as wr

    router = wr.setup_workspace_routes()
    browse = next(r.endpoint for r in router.routes if r.path == "/api/workspace/browse")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "bob")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)
    with pytest.raises(HTTPException) as ei:
        browse(request=object(), path="/")
    assert ei.value.status_code == 403

    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    out = browse(request=object(), path=os.path.expanduser("~"))
    assert "dirs" in out and "path" in out
    assert all("name" in d and "path" in d for d in out["dirs"])


# ── bind-time vetting of the workspace root ─────────────────────────────

def test_vet_workspace_accepts_normal_dir(ws):
    from src.tool_execution import vet_workspace
    assert vet_workspace(ws) == os.path.realpath(ws)


def test_vet_workspace_rejects_sensitive_root(tmp_path):
    # The resolver deny-lists sensitive paths inside the workspace, but the
    # empty-path search root is the workspace itself - a sensitive root must
    # be rejected before it is bound or `ls` with no path would list it.
    from src.tool_execution import vet_workspace
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    assert vet_workspace(str(ssh_dir)) is None


def test_vet_workspace_rejects_nondir_and_empty(ws):
    from src.tool_execution import vet_workspace
    assert vet_workspace(os.path.join(ws, "a.txt")) is None  # file, not dir
    assert vet_workspace("/nonexistent/path/xyz") is None
    assert vet_workspace("") is None
    assert vet_workspace("   ") is None


def test_vet_workspace_rejects_filesystem_root():
    # Binding / would make every absolute path "inside" the workspace,
    # collapsing confinement into host-wide file access.
    from src.tool_execution import vet_workspace
    assert vet_workspace("/") is None


def test_browse_marks_root_unselectable_and_vet_endpoint(monkeypatch):
    import routes.workspace_routes as wr

    router = wr.setup_workspace_routes()
    browse = next(r.endpoint for r in router.routes if r.path == "/api/workspace/browse")
    vet = next(r.endpoint for r in router.routes if r.path == "/api/workspace/vet")

    monkeypatch.setattr(wr, "get_current_user", lambda req: "admin")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)

    out = browse(request=object(), path="/")
    assert out["selectable"] is False
    out = browse(request=object(), path=os.path.expanduser("~"))
    assert out["selectable"] is True

    assert vet(request=object(), path="/") == {"ok": False, "path": None}
    home = os.path.realpath(os.path.expanduser("~"))
    assert vet(request=object(), path="~") == {"ok": True, "path": home}

    from fastapi import HTTPException
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)
    with pytest.raises(HTTPException) as ei:
        vet(request=object(), path="/tmp")
    assert ei.value.status_code == 403


# ── send-time privilege gate (no path oracle for non-admins) ────────────

def test_request_workspace_gate(ws, monkeypatch):
    """Non-admin chat callers must get a uniform drop with no vetting: the
    workspace_rejected signal would otherwise reveal which host paths exist."""
    import routes.chat_routes as cr

    monkeypatch.setattr(cr, "get_current_user", lambda req: "bob")
    vet_calls = []
    import src.tool_execution as te
    real_vet = te.vet_workspace
    monkeypatch.setattr(te, "vet_workspace", lambda p: vet_calls.append(p) or real_vet(p))

    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: False)
    # Valid and invalid paths are indistinguishable for a non-admin: both
    # drop silently, and the path never reaches the filesystem.
    assert cr._resolve_request_workspace(object(), ws) == ("", "")
    assert cr._resolve_request_workspace(object(), "/nonexistent/xyz") == ("", "")
    assert cr._resolve_request_workspace(object(), "") == ("", "")
    assert vet_calls == []

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True)
    assert cr._resolve_request_workspace(object(), ws) == (os.path.realpath(ws), "")
    assert cr._resolve_request_workspace(object(), "/nonexistent/xyz") == ("", "/nonexistent/xyz")


def test_request_workspace_defaults_to_mounted_workspace(monkeypatch, ws):
    """A missing workspace form value should still bind the standard Docker
    workspace mount when it is present and the caller is allowed to use it."""
    import routes.chat_routes as cr

    monkeypatch.setattr(cr, "get_current_user", lambda req: "admin")

    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(cr, "_DEFAULT_MOUNTED_WORKSPACE", "/workspace")
    monkeypatch.setattr(cr.os.path, "isdir", lambda path: path == "/workspace")

    import src.tool_execution as te
    monkeypatch.setattr(te, "vet_workspace", lambda path: ws if path == "/workspace" else None)

    assert cr._resolve_request_workspace(object(), "") == (ws, "")
    assert cr._resolve_request_workspace(object(), None) == (ws, "")


def test_request_workspace_default_is_not_rejected_when_missing(monkeypatch):
    """If the conventional mount is absent, omission just means no workspace;
    no workspace_rejected event should be emitted for an implicit default."""
    import routes.chat_routes as cr

    monkeypatch.setattr(cr, "get_current_user", lambda req: "admin")

    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(cr, "_DEFAULT_MOUNTED_WORKSPACE", "/workspace")
    monkeypatch.setattr(cr.os.path, "isdir", lambda path: False)

    assert cr._resolve_request_workspace(object(), "") == ("", "")
