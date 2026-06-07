import base64
from pathlib import Path

from src.windows_remote import (
    background_powershell_launch_script,
    encode_powershell,
    powershell_ssh_argv,
    scp_argv,
)
from routes.cookbook_helpers import _validate_local_dir


def _decode(encoded: str) -> str:
    return base64.b64decode(encoded).decode("utf-16le")


def test_powershell_ssh_argv_preserves_multiline_script():
    script = '$x = "quoted"\nif ($x) { Write-Output $x }'
    argv = powershell_ssh_argv("user@windows", script, "2222", connect_timeout=6)

    assert argv[:6] == ["ssh", "-o", "ConnectTimeout=6", "-p", "2222", "user@windows"]
    assert argv[6:10] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert _decode(argv[-1]) == script
    assert encode_powershell(script) == argv[-1]


def test_scp_uses_uppercase_port_and_legacy_protocol():
    assert scp_argv("runner.ps1", "user@windows", ".runner.ps1", "2222") == [
        "scp", "-O", "-q", "-P", "2222", "runner.ps1", "user@windows:.runner.ps1"
    ]


def test_background_launch_uses_wmi_and_encoded_child_command():
    script = background_powershell_launch_script(".cookbook-test_run.ps1")
    assert "Invoke-CimMethod -ClassName Win32_Process" in script
    assert "Start-Process" not in script
    encoded = script.split("-EncodedCommand ", 1)[1].split("'", 1)[0]
    assert _decode(encoded) == "& (Join-Path $HOME '.cookbook-test_run.ps1')"


def test_windows_download_path_accepts_spaces():
    assert _validate_local_dir(r"E:\Odysseus Models\huggingface") == r"E:\Odysseus Models\huggingface"


def test_windows_download_path_rejects_shell_metacharacters():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _validate_local_dir("E:\\Odysseus;Remove-Item C:\\")


def test_cookbook_remote_windows_runner_regressions_are_fixed():
    source = (Path(__file__).parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")
    assert "ps_lines.append('try {{')" not in source
    assert 'powershell -Command \\"{launch_ps}\\"' not in source
    assert "background_powershell_launch_script(remote_runner)" in source
    assert 'errors="replace"' in source
    assert "if is_alive or remote_win_task:" in source
