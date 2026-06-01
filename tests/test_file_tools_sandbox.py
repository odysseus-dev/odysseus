"""Security tests for the agent file tools (read_file / write_file / edit_file
/ glob / grep) added in the file-tools-parity PR.

These prove the filesystem-confinement boundary the tools enforce:

  1. edit_file / glob / grep (and read/write) cannot touch paths outside the
     workspace root.
  2. absolute paths, ``..`` traversal, symlinks, and hidden/system paths behave
     safely.
  3. edit_file never silently overwrites when old_string is ambiguous.
  4. create-new-file is limited to inside the root.
  5. the local-model fenced parse path and the hosted tool_calls path enforce
     the *same* constraints (they converge on one executor).

The tools are async; we drive them with ``asyncio.run`` so the suite needs no
pytest-asyncio plugin. The workspace root is set per-test via the
``ODYSSEUS_AGENT_FS_ROOT`` env var, which ``_agent_fs_root()`` reads on every
call.
"""

import asyncio
import os
import sys
from unittest import mock as _mock

import pytest

# Other test modules (e.g. test_agent_loop.py) globally replace src.agent_tools
# and friends with MagicMocks via sys.modules and never restore them. Drop any
# such stand-ins so this suite imports the REAL implementations regardless of
# pytest collection order — otherwise function_call_to_tool_block would return a
# mock ToolBlock and the call-path-convergence checks would be meaningless.
for _name in ("src.tool_execution", "src.tool_schemas", "src.agent_tools"):
    if isinstance(sys.modules.get(_name), _mock.Mock):
        del sys.modules[_name]

# agent_tools must be imported before tool_schemas to break a known
# tool_schemas <-> agent_tools circular import.
import src.agent_tools  # noqa: F401,E402
from src.tool_schemas import function_call_to_tool_block  # noqa: E402
from src.tool_execution import (
    _direct_fallback,
    _resolve_in_root,
    _agent_fs_root,
    _within_root,
)


def run_tool(tool: str, content: str) -> dict:
    """Execute a file tool through the shared executor and return its result."""
    return asyncio.run(_direct_fallback(tool, content))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A confined workspace root with one in-root file, plus an out-of-root
    sibling directory holding a secret the agent must never reach."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "inside.txt").write_text("hello inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_AGENT_FS_ROOT", str(root))
    # realpath so comparisons match what the tools compute (tmp dirs can be
    # symlinked, e.g. /var -> /private/var on macOS).
    return {
        "root": os.path.realpath(str(root)),
        "outside": os.path.realpath(str(outside)),
        "secret": os.path.realpath(str(secret)),
    }


# ---------------------------------------------------------------------------
# 1 + 2. Path resolution: the containment primitive every tool shares
# ---------------------------------------------------------------------------

class TestResolveInRoot:
    def test_relative_in_root_allowed(self, workspace):
        resolved, err = _resolve_in_root("inside.txt")
        assert err is None
        assert _within_root(workspace["root"], resolved)

    def test_nested_relative_allowed(self, workspace):
        resolved, err = _resolve_in_root("a/b/c.txt")
        assert err is None and _within_root(workspace["root"], resolved)

    def test_absolute_in_root_allowed(self, workspace):
        resolved, err = _resolve_in_root(os.path.join(workspace["root"], "inside.txt"))
        assert err is None and _within_root(workspace["root"], resolved)

    def test_hidden_dotfile_in_root_allowed(self, workspace):
        # Hidden files inside the workspace are part of it — contained, not blocked.
        resolved, err = _resolve_in_root(".env")
        assert err is None and _within_root(workspace["root"], resolved)

    def test_parent_traversal_rejected(self, workspace):
        resolved, err = _resolve_in_root("../outside/secret.txt")
        assert resolved is None and err and "outside the agent workspace root" in err

    def test_deep_traversal_rejected(self, workspace):
        resolved, err = _resolve_in_root("a/b/../../../outside/secret.txt")
        assert resolved is None and err

    def test_absolute_outside_rejected(self, workspace):
        resolved, err = _resolve_in_root(workspace["secret"])
        assert resolved is None and err

    def test_system_path_rejected(self, workspace):
        target = r"C:\Windows\System32\drivers\etc\hosts" if os.name == "nt" else "/etc/passwd"
        resolved, err = _resolve_in_root(target)
        assert resolved is None and err

    def test_sibling_prefix_not_a_false_positive(self, workspace, tmp_path):
        # A directory whose name merely starts with the root path must NOT count
        # as inside it (e.g. /work vs /work-evil).
        sibling = str(tmp_path / "work-evil") + os.sep + "x.txt"
        resolved, err = _resolve_in_root(sibling)
        assert resolved is None and err

    def test_empty_path_rejected(self, workspace):
        resolved, err = _resolve_in_root("   ")
        assert resolved is None and err == "path required"

    def test_default_root_is_cwd(self, monkeypatch):
        # With no override, confinement defaults to the process working dir —
        # an explicit boundary, not unrestricted access.
        monkeypatch.delenv("ODYSSEUS_AGENT_FS_ROOT", raising=False)
        assert _agent_fs_root() == os.path.realpath(os.getcwd())


# ---------------------------------------------------------------------------
# 1. Each tool refuses to operate outside the root
# ---------------------------------------------------------------------------

class TestToolsConfined:
    def test_read_file_outside_rejected(self, workspace):
        res = run_tool("read_file", workspace["secret"])
        assert res["exit_code"] == 1 and "outside the agent workspace root" in res["error"]
        assert "TOP SECRET" not in res.get("output", "")

    def test_read_file_traversal_rejected(self, workspace):
        res = run_tool("read_file", "../outside/secret.txt")
        assert res["exit_code"] == 1 and "outside" in res["error"]

    def test_read_file_in_root_works(self, workspace):
        res = run_tool("read_file", "inside.txt")
        assert res["exit_code"] == 0 and "hello inside" in res["output"]

    def test_write_file_outside_rejected(self, workspace):
        target = os.path.join(workspace["outside"], "planted.txt")
        res = run_tool("write_file", target + "\nmalicious")
        assert res["exit_code"] == 1 and "outside" in res["error"]
        assert not os.path.exists(target)

    def test_write_file_in_root_works(self, workspace):
        res = run_tool("write_file", "note.txt\nbody")
        assert res["exit_code"] == 0
        assert (os.path.join(workspace["root"], "note.txt"))
        assert os.path.exists(os.path.join(workspace["root"], "note.txt"))

    def test_edit_file_outside_rejected(self, workspace):
        content = workspace["secret"] + "\n<<<<<<< OLD\nTOP SECRET\n=======\nHACKED\n>>>>>>> NEW"
        res = run_tool("edit_file", content)
        assert res["exit_code"] == 1 and "outside" in res["error"]
        assert open(workspace["secret"], encoding="utf-8").read() == "TOP SECRET\n"

    def test_glob_explicit_path_outside_rejected(self, workspace):
        res = run_tool("glob", "*.txt\n" + workspace["outside"])
        assert res["exit_code"] == 1 and "outside" in res["error"]

    def test_glob_default_root_excludes_outside(self, workspace):
        res = run_tool("glob", "**/*.txt")
        assert res["exit_code"] == 0
        assert "inside.txt" in res["output"]
        assert "secret.txt" not in res["output"]

    def test_grep_explicit_path_outside_rejected(self, workspace):
        res = run_tool("grep", '{"pattern": "SECRET", "path": "%s"}' % workspace["outside"].replace("\\", "\\\\"))
        assert res["exit_code"] == 1 and "outside" in res["error"]

    def test_grep_default_root_excludes_outside(self, workspace):
        # The secret string lives only outside the root, so a default-root grep
        # must not surface it.
        res = run_tool("grep", '{"pattern": "SECRET", "output_mode": "content"}')
        assert res["exit_code"] == 0
        assert "TOP SECRET" not in res["output"]


# ---------------------------------------------------------------------------
# 3. edit_file ambiguity — never silently overwrite
# ---------------------------------------------------------------------------

class TestEditAmbiguity:
    def test_ambiguous_old_string_refused_and_file_unchanged(self, workspace):
        p = os.path.join(workspace["root"], "dup.txt")
        original = "x = 1\ny = 1\nz = 2\n"  # "= 1" appears twice
        open(p, "w", encoding="utf-8").write(original)
        res = run_tool("edit_file", "dup.txt\n<<<<<<< OLD\n= 1\n=======\n= 9\n>>>>>>> NEW")
        assert res["exit_code"] == 1
        assert "not unique" in res["error"] or "matches" in res["error"]
        assert open(p, encoding="utf-8").read() == original  # untouched

    def test_replace_all_makes_ambiguous_edit_explicit(self, workspace):
        p = os.path.join(workspace["root"], "dup2.txt")
        open(p, "w", encoding="utf-8").write("a\na\n")
        res = run_tool("edit_file", "dup2.txt replace_all\n<<<<<<< OLD\na\n=======\nb\n>>>>>>> NEW")
        assert res["exit_code"] == 0
        assert open(p, encoding="utf-8").read() == "b\nb\n"

    def test_missing_old_string_refused(self, workspace):
        open(os.path.join(workspace["root"], "f.txt"), "w", encoding="utf-8").write("real content\n")
        res = run_tool("edit_file", "f.txt\n<<<<<<< OLD\nNOT PRESENT\n=======\nX\n>>>>>>> NEW")
        assert res["exit_code"] == 1 and "not found" in res["error"]


# ---------------------------------------------------------------------------
# 4. create-new-file limited to safe locations
# ---------------------------------------------------------------------------

class TestCreateNewFile:
    def test_create_inside_root_ok(self, workspace):
        res = run_tool("edit_file", "new/created.txt\n<<<<<<< OLD\n=======\nfresh\n>>>>>>> NEW")
        assert res["exit_code"] == 0
        created = os.path.join(workspace["root"], "new", "created.txt")
        assert os.path.exists(created) and open(created, encoding="utf-8").read() == "fresh"

    def test_create_outside_root_rejected(self, workspace):
        target = os.path.join(workspace["outside"], "created.txt")
        res = run_tool("edit_file", target + "\n<<<<<<< OLD\n=======\nfresh\n>>>>>>> NEW")
        assert res["exit_code"] == 1 and "outside" in res["error"]
        assert not os.path.exists(target)

    def test_create_does_not_clobber_existing_nonempty(self, workspace):
        # Empty old_string on an existing non-empty file must refuse, not wipe it.
        res = run_tool("edit_file", "inside.txt\n<<<<<<< OLD\n=======\nWIPED\n>>>>>>> NEW")
        assert res["exit_code"] == 1
        assert open(os.path.join(workspace["root"], "inside.txt"), encoding="utf-8").read() == "hello inside\n"


# ---------------------------------------------------------------------------
# 2. Symlink escapes (skipped where the OS won't let us create symlinks)
# ---------------------------------------------------------------------------

class TestSymlinkEscape:
    def _make_symlink(self, link, target):
        try:
            os.symlink(target, link)
            return True
        except (OSError, NotImplementedError, AttributeError):
            return False

    def test_symlinked_file_to_outside_rejected(self, workspace):
        link = os.path.join(workspace["root"], "link.txt")
        if not self._make_symlink(link, workspace["secret"]):
            pytest.skip("symlinks not permitted on this platform/user")
        res = run_tool("read_file", "link.txt")
        assert res["exit_code"] == 1 and "outside" in res["error"]
        assert "TOP SECRET" not in res.get("output", "")

    def test_create_through_symlinked_dir_rejected(self, workspace):
        link_dir = os.path.join(workspace["root"], "outlink")
        if not self._make_symlink(link_dir, workspace["outside"]):
            pytest.skip("symlinks not permitted on this platform/user")
        target = "outlink/planted.txt"
        res = run_tool("edit_file", target + "\n<<<<<<< OLD\n=======\nx\n>>>>>>> NEW")
        assert res["exit_code"] == 1 and "outside" in res["error"]
        assert not os.path.exists(os.path.join(workspace["outside"], "planted.txt"))


# ---------------------------------------------------------------------------
# 5. Both call paths enforce the same constraint
# ---------------------------------------------------------------------------

class TestBothCallPathsEnforce:
    """The hosted tool_calls path serializes args into the exact fenced string
    the local-model parser produces; both are executed by _direct_fallback, so
    the boundary cannot be bypassed by switching call style."""

    def test_edit_file_hosted_and_fenced_converge_and_both_rejected(self, workspace):
        # Hosted path: structured args -> ToolBlock content
        block = function_call_to_tool_block(
            "edit_file",
            {"path": "../outside/secret.txt", "old_string": "TOP SECRET", "new_string": "HACKED"},
        )
        # Local-model path: the same fenced format a small model would emit
        fenced = "../outside/secret.txt\n<<<<<<< OLD\nTOP SECRET\n=======\nHACKED\n>>>>>>> NEW"
        assert block.content == fenced  # the two paths produce identical input

        res_hosted = run_tool("edit_file", block.content)
        res_fenced = run_tool("edit_file", fenced)
        for res in (res_hosted, res_fenced):
            assert res["exit_code"] == 1 and "outside" in res["error"]
        assert open(workspace["secret"], encoding="utf-8").read() == "TOP SECRET\n"

    def test_glob_hosted_and_fenced_both_rejected(self, workspace):
        block = function_call_to_tool_block("glob", {"pattern": "*.txt", "path": workspace["outside"]})
        res_hosted = run_tool("glob", block.content)
        res_fenced = run_tool("glob", "*.txt\n" + workspace["outside"])
        assert res_hosted["exit_code"] == 1 and "outside" in res_hosted["error"]
        assert res_fenced["exit_code"] == 1 and "outside" in res_fenced["error"]

    def test_read_file_hosted_and_fenced_both_rejected(self, workspace):
        block = function_call_to_tool_block("read_file", {"path": workspace["secret"]})
        res_hosted = run_tool("read_file", block.content)
        res_fenced = run_tool("read_file", workspace["secret"])
        assert res_hosted["exit_code"] == 1 and "outside" in res_hosted["error"]
        assert res_fenced["exit_code"] == 1 and "outside" in res_fenced["error"]
