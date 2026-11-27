import asyncio
import os
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "static/js/cookbookServe.js"


def test_local_windows_delete_is_routed_through_powershell():
    """/api/shell/exec runs bare commands through Git Bash on Windows, which
    has no Remove-Item; the local delete must start with `powershell`."""
    source = SOURCE.read_text(encoding="utf-8")

    windows_branch = source.index("const _psSingleQuote")
    wrap = source.index("cmd = `powershell -NoProfile -Command \"${cmd}\"`;", windows_branch)
    assert wrap < source.index("const unixTarget", windows_branch)


def test_delete_checks_shell_exit_code_before_removing_row():
    source = SOURCE.read_text(encoding="utf-8")

    fetch = source.index("const res = await _fetchCookbookWithTimeout('/api/shell/exec'")
    check = source.index("out.exit_code !== 0", fetch)
    remove_row = source.index("_cachedAllModels = _cachedAllModels.filter", fetch)
    assert check < remove_row
    assert "uiModule.showError('Delete failed: '" in source


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows shell route")
def test_powershell_delete_command_removes_dir_via_shell_route(tmp_path):
    from routes.shell_routes import _exec_shell

    target = tmp_path / "models--org--repo"
    (target / "blobs").mkdir(parents=True)
    (target / "blobs" / "model.gguf").write_bytes(b"x")

    inner = f"Remove-Item -Recurse -Force '{target}' -ErrorAction SilentlyContinue"
    bare = asyncio.run(_exec_shell(inner))
    assert target.exists() and (bare["exit_code"] != 0 or bare["stderr"])

    result = asyncio.run(_exec_shell(f'powershell -NoProfile -Command "{inner}"'))
    assert result["exit_code"] == 0, result
    assert not target.exists()
