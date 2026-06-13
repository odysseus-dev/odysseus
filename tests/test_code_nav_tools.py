"""Tests for the code-navigation tools (grep, glob, ls) + read_file line range."""
import os
import shutil
import asyncio
import tempfile
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_code_nav.db")

from src.tool_execution import _direct_fallback


def _run(tool, content):
    return asyncio.run(_direct_fallback(tool, content))


@pytest.fixture
def repo():
    # Built under /tmp, which is on the default tool-path allowlist.
    root = tempfile.mkdtemp(dir="/tmp", prefix="codenav_")
    try:
        with open(os.path.join(root, "a.py"), "w") as f:
            f.write("import os\n# needle here\nprint('x')\n")
        os.mkdir(os.path.join(root, "sub"))
        with open(os.path.join(root, "sub", "b.txt"), "w") as f:
            f.write("nothing\nNEEDLE upper\n")
        os.mkdir(os.path.join(root, "node_modules"))
        with open(os.path.join(root, "node_modules", "dep.py"), "w") as f:
            f.write("needle in dep\n")
        g = os.path.join(root, ".git")
        os.mkdir(g)
        with open(os.path.join(g, "config"), "w") as f:
            f.write("needle in git\n")
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── grep ──────────────────────────────────────────────────────────────────

def test_grep_finds_match(repo):
    r = _run("grep", f'{{"pattern": "needle", "path": "{repo}"}}')
    assert r["exit_code"] == 0
    assert "a.py:2:" in r["output"]


def test_grep_skips_junk_dirs(repo):
    r = _run("grep", f'{{"pattern": "needle", "path": "{repo}"}}')
    assert "node_modules" not in r["output"]
    assert ".git/config" not in r["output"]


def test_grep_ignore_case(repo):
    r = _run("grep", f'{{"pattern": "needle", "ignore_case": true, "path": "{repo}"}}')
    assert "b.txt:2:" in r["output"]


def test_grep_glob_filter(repo):
    r = _run("grep", f'{{"pattern": "needle", "ignore_case": true, "glob": "*.py", "path": "{repo}"}}')
    assert "a.py" in r["output"]
    assert "b.txt" not in r["output"]


def test_grep_no_match(repo):
    r = _run("grep", f'{{"pattern": "zzzznotfound", "path": "{repo}"}}')
    assert r["exit_code"] == 0
    assert "No matches" in r["output"]


def test_grep_requires_pattern(repo):
    r = _run("grep", "{}")
    assert r["exit_code"] == 1
    assert "pattern is required" in r["error"]


def test_grep_path_outside_roots_rejected(repo):
    r = _run("grep", '{"pattern": "x", "path": "/etc"}')
    assert r["exit_code"] == 1
    assert "outside the allowed roots" in r["error"]


def test_grep_python_fallback_when_no_rg(repo, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = _run("grep", f'{{"pattern": "needle", "path": "{repo}"}}')
    assert r["exit_code"] == 0
    assert "a.py:2:" in r["output"]
    assert "node_modules" not in r["output"]
    assert ".git/config" not in r["output"]


# ── glob ──────────────────────────────────────────────────────────────────

def test_glob_py(repo):
    r = _run("glob", f'{{"pattern": "*.py", "path": "{repo}"}}')
    assert r["exit_code"] == 0
    assert "a.py" in r["output"]


def test_glob_recursive_skips_junk(repo):
    r = _run("glob", f'{{"pattern": "**/*.py", "path": "{repo}"}}')
    assert "a.py" in r["output"]
    assert "node_modules" not in r["output"]


def test_glob_requires_pattern(repo):
    r = _run("glob", "{}")
    assert r["exit_code"] == 1


# ── ls ────────────────────────────────────────────────────────────────────

def test_ls_lists_entries(repo):
    r = _run("ls", f'{{"path": "{repo}"}}')
    assert r["exit_code"] == 0
    assert "a.py" in r["output"]
    assert "sub/" in r["output"]
    assert ".git" not in r["output"]  # hidden skipped


def test_ls_path_outside_rejected(repo):
    r = _run("ls", '{"path": "/etc"}')
    assert r["exit_code"] == 1
    assert "outside the allowed roots" in r["error"]


# ── read_file line range ───────────────────────────────────────────────────

def test_read_file_offset_limit(repo):
    p = os.path.join(repo, "lines.txt")
    with open(p, "w") as f:
        f.write("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    r = _run("read_file", f'{{"path": "{p}", "offset": 3, "limit": 2}}')
    assert r["exit_code"] == 0
    assert r["output"] == "line3\nline4\n"


def test_read_file_plain_path_backcompat(repo):
    r = _run("read_file", os.path.join(repo, "a.py"))
    assert r["exit_code"] == 0
    assert "needle" in r["output"]
