"""Native execution must use the shared boundary and report actual teardown."""
import asyncio
import os
import sys

import pytest

from src import containment, tool_execution
from src.agent_tools import subprocess_tools


@pytest.fixture(autouse=True)
def native_boundary(tmp_path, monkeypatch):
    from src.agent_runtime import process_resources
    from tests.process_resource_helpers import authorized_handler
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(workspace))
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "private" / "grants.json")
    monkeypatch.setattr(process_resources, "_LAUNCH_DIR", tmp_path / "private" / "launches")
    for cls in (subprocess_tools.BashTool, subprocess_tools.PythonTool):
        original = cls.execute
        async def execute(self, content, ctx, _original=original):
            return await authorized_handler(_original.__get__(self), workspace)(content, ctx)
        monkeypatch.setattr(cls, "execute", execute)
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)
    monkeypatch.setattr(containment, "MECHANISMS", tuple(
        m for m in containment.MECHANISMS if m.name == "process_group"
    ))
    return workspace


@pytest.mark.skipif(os.name == "nt", reason="real POSIX group teardown")
async def test_native_bash_owns_and_releases_its_child(native_boundary):
    result = await subprocess_tools.BashTool().execute(
        "if read answer; then echo unexpected; else printf '%s' \"$ODY_TEST_ENV\"; fi",
        {"subproc_env": {"PATH": "/usr/bin:/bin", "ODY_TEST_ENV": "captured"}},
    )
    assert result["output"] == "captured"
    assert result["exit_code"] == 0
    assert result["teardown"]["dead"] is True
    assert result["containment"]["enforced"] == ["wall_clock"]
    assert result["containment"]["unenforced_required"] == ["filesystem", "process_tree"]
    assert result["containment"]["network"] == "inherit"
    assert containment.active_grants() == []


async def test_native_bash_refuses_before_spawn_when_required_boundary_missing(monkeypatch):
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)
    async def forbidden(*args, **kwargs):
        pytest.fail("refused command reached spawn")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    result = await subprocess_tools.BashTool().execute("echo hello", {})
    assert result["containment"]["executed"] is False
    assert result["containment"]["unenforced_required"] == ["filesystem", "process_tree"]


async def test_failed_spawn_releases_unstarted_grant(native_boundary, monkeypatch):
    async def fail(*args, **kwargs):
        raise OSError("spawn failed")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    result = await subprocess_tools.BashTool().execute("echo hello", {})
    assert result["exit_code"] == 1
    assert containment.active_grants() == []


@pytest.mark.skipif(os.name == "nt", reason="real POSIX process")
async def test_long_line_is_drained_and_truncation_reported(native_boundary):
    spec = containment.ContainmentSpec(
        workspace=str(native_boundary), env=dict(os.environ), wall_clock_s=5,
        required=frozenset({containment.PROCESS_TREE, containment.WALL_CLOCK}),
        max_output_bytes=100,
    )
    result = await containment.run(containment.acquire(spec, owner="long-line"),
        [sys.executable, "-c", "print('x' * 200000)"], argv=True)
    assert result.exit_code == 0
    assert result.stdout == "x" * 100
    assert result.output_truncated is True
    assert result.release.dead is True


@pytest.mark.skipif(os.name == "nt", reason="real POSIX process")
async def test_output_exactly_at_cap_is_complete(native_boundary):
    spec = containment.ContainmentSpec(
        workspace=str(native_boundary), env=dict(os.environ), wall_clock_s=5,
        required=frozenset({containment.PROCESS_TREE, containment.WALL_CLOCK}), max_output_bytes=100,
    )
    result = await containment.run(containment.acquire(spec, owner="exact-cap"),
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100)"], argv=True)
    assert len(result.stdout) == 100
    assert result.output_truncated is False


def test_permission_denied_is_not_verified_death(monkeypatch):
    from core import platform_compat
    def denied(*args):
        raise PermissionError("EPERM")
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    monkeypatch.setattr(os, "kill", denied)
    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(containment, "_own_pgid", lambda: 1)
    assert platform_compat.pid_alive(987654) is True
    assert containment._group_present(987654) is True


@pytest.mark.skipif(os.name == "nt", reason="real POSIX process")
async def test_blocked_stdin_is_inside_wall_clock(native_boundary):
    spec = containment.ContainmentSpec(
        workspace=str(native_boundary), env=dict(os.environ), wall_clock_s=1,
        required=frozenset({containment.PROCESS_TREE, containment.WALL_CLOCK}),
    )
    result = await asyncio.wait_for(containment.run(
        containment.acquire(spec, owner="blocked-stdin"), "sleep 60", stdin=b"x" * 2000000,
    ), timeout=8)
    assert result.timed_out is True
    assert result.release.dead is True


@pytest.mark.parametrize("source", ["print(1 + 1)", "import os; print(os.getcwd())",
                                   "exec('print(2)')", "print('/workspace')"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace argv; Windows refusal tested separately")
async def test_python_namespace_is_independent_of_content(source, native_boundary, monkeypatch):
    from tests.containment_helpers import capture_owned_spawn
    captured = capture_owned_spawn(monkeypatch, native_boundary)
    monkeypatch.setattr(containment, "MECHANISMS", (containment.Mechanism(
        "bubblewrap", 30, lambda: True, lambda spec: containment.DEFAULT_REQUIRED,
    ),))
    original_which = containment.shutil.which
    monkeypatch.setattr(containment.shutil, "which", lambda name:
                        "/usr/bin/bwrap" if name == "bwrap" else original_which(name))
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)
    result = await subprocess_tools.PythonTool().execute(source, {})
    assert os.path.basename(captured["argv"][0]) == "bwrap"
    assert "--bind" in captured["argv"]
    assert result["containment"]["enforced"] == sorted(containment.DEFAULT_REQUIRED)
    assert "-I" in captured["argv"]


async def test_ordinary_python_cannot_bypass_unavailable_containment(monkeypatch):
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)
    async def forbidden(*args, **kwargs):
        pytest.fail("ordinary Python bypassed required containment")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    result = await subprocess_tools.PythonTool().execute("print(1 + 1)", {})
    assert result["containment"]["executed"] is False


async def test_python_final_expression_and_opt_in_imports(native_boundary):
    package = native_boundary / "packages"
    package.mkdir()
    (package / "demo.py").write_text("value = 42\n")
    result = await subprocess_tools.PythonTool().execute("import demo; demo.value", {
        "subproc_env": {**os.environ, "ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES": str(package)},
    })
    assert result["output"] == "42"
    assert result["teardown"]["dead"] is True


async def test_capture_preserves_multibyte_text_across_chunks(native_boundary):
    spec = containment.ContainmentSpec(
        workspace=str(native_boundary), env=dict(os.environ), wall_clock_s=5,
        required=frozenset({containment.PROCESS_TREE, containment.WALL_CLOCK}), max_output_bytes=200000,
    )
    result = await containment.run(containment.acquire(spec, owner="unicode"),
        [sys.executable, "-c", "import sys; sys.stdout.write('€' * 30000)"], argv=True)
    assert result.stdout == "€" * 30000
    assert result.output_truncated is False


async def test_chat_bash_captures_more_than_2000_lines(native_boundary):
    result = await subprocess_tools.BashTool().execute(
        "printf 'START\\n'; for i in $(seq 1 3000); do printf 'x\\n'; done; printf 'END\\n'",
        {"session_id": "large-output-chat"},
    )
    assert result["output"].startswith("START\n")
    assert result["output"].endswith("\nEND")
    assert len(result["output"].splitlines()) == 3002
    assert result["output_truncated"] is False
    assert result["teardown"]["dead"] is True


async def test_chat_bash_reports_capture_limit_as_incomplete(native_boundary):
    result = await subprocess_tools.BashTool().execute(
        "for i in $(seq 1 12000); do printf 'x\\n'; done", {"session_id": "capped-chat"},
    )
    assert result["exit_code"] == 0
    assert result["output_truncated"] is True
    assert "truncated" in result["output"]
    assert result["teardown"]["dead"] is True


async def test_repeated_chat_calls_refresh_environment(native_boundary):
    first = await subprocess_tools.BashTool().execute('printf "%s" "$VALUE"', {
        "session_id": "same-chat", "subproc_env": {"PATH": "/usr/bin:/bin", "VALUE": "first"},
    })
    second = await subprocess_tools.BashTool().execute('printf "%s" "$VALUE"', {
        "session_id": "same-chat", "subproc_env": {"PATH": "/usr/bin:/bin", "VALUE": "second"},
    })
    assert first["output"] == "first"
    assert second["output"] == "second"
    assert first["containment"]["id"] != second["containment"]["id"]
