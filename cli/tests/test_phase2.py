"""Unit tests for Phase 2: sandbox containment, diff preview, sessions, repo map."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odysseus_cli import sandbox, session  # noqa: E402


# ── Sandbox: path containment ──────────────────────────────────────────────
def test_resolve_in_root_allows_inside(tmp_path):
    assert sandbox.resolve_in_root("src/app.py", tmp_path) == (tmp_path / "src/app.py").resolve()
    assert sandbox.resolve_in_root("./notes.md", tmp_path) == (tmp_path / "notes.md").resolve()


def test_resolve_in_root_blocks_escape(tmp_path):
    assert sandbox.resolve_in_root("../secret.txt", tmp_path) is None
    assert sandbox.resolve_in_root("/etc/passwd", tmp_path) is None
    assert sandbox.resolve_in_root("src/../../escape", tmp_path) is None


def test_tool_path_only_for_file_tools():
    assert sandbox.tool_path("read_file", "src/a.py\n") == "src/a.py"
    assert sandbox.tool_path("write_file", "out.txt\nhello") == "out.txt"
    assert sandbox.tool_path("bash", "ls -la") is None


# ── Sandbox: diff preview ──────────────────────────────────────────────────
def test_split_write_separates_path_and_content():
    path, content = sandbox.split_write("file.txt\nline1\nline2")
    assert path == "file.txt"
    assert content == "line1\nline2"


def test_unified_diff_new_file(tmp_path):
    target = tmp_path / "new.txt"
    diff = sandbox.unified_diff_for_write(target, "hello\nworld")
    body = "\n".join(diff)
    assert "+hello" in body and "+world" in body


def test_unified_diff_modified_file(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("a\nb\nc\n")
    diff = sandbox.unified_diff_for_write(target, "a\nB\nc\n")
    body = "\n".join(diff)
    assert "-b" in body and "+B" in body


# ── Sessions: save / resume ────────────────────────────────────────────────
def test_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    path = session.save(msgs, "llama3.2:3b", tmp_path)
    assert path.is_file()
    loaded = session.load(path)
    assert loaded["messages"] == msgs
    assert loaded["model"] == "llama3.2:3b"


def test_latest_for_root_picks_same_project(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir(); proj_b.mkdir()
    session.save([{"role": "user", "content": "in a"}], "m", proj_a)
    session.save([{"role": "user", "content": "in b"}], "m", proj_b)
    latest_a = session.latest_for_root(proj_a)
    assert latest_a is not None
    assert session.load(latest_a)["messages"][0]["content"] == "in a"


# ── Repo map ───────────────────────────────────────────────────────────────
def test_repo_map_skips_noise(tmp_path):
    from odysseus_cli.agent import build_repo_map
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    out = build_repo_map(tmp_path)
    assert "app.py" in out
    assert "junk.js" not in out  # node_modules is skipped
