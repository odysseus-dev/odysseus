"""Safe helpers for executing PowerShell on a remote Windows OpenSSH host."""

from __future__ import annotations

import base64


def encode_powershell(script: str) -> str:
    """Encode a PowerShell script for ``-EncodedCommand`` (UTF-16LE)."""
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def ssh_base(host: str, port: str | None = None, *, connect_timeout: int | None = None) -> list[str]:
    argv = ["ssh"]
    if connect_timeout is not None:
        argv.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if port and str(port) != "22":
        argv.extend(["-p", str(port)])
    argv.append(host)
    return argv


def powershell_ssh_argv(
    host: str,
    script: str,
    port: str | None = None,
    *,
    connect_timeout: int | None = None,
) -> list[str]:
    return ssh_base(host, port, connect_timeout=connect_timeout) + [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encode_powershell(script),
    ]


def scp_argv(source: str, host: str, destination: str, port: str | None = None) -> list[str]:
    argv = ["scp", "-O", "-q"]
    if port and str(port) != "22":
        argv.extend(["-P", str(port)])
    argv.extend([source, f"{host}:{destination}"])
    return argv


def background_powershell_launch_script(runner_name: str) -> str:
    """Build a WMI launch script that survives Windows OpenSSH job cleanup."""
    safe_name = runner_name.replace("'", "''")
    child_script = f"& (Join-Path $HOME '{safe_name}')"
    child_encoded = encode_powershell(child_script)
    return (
        f"$cmd = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {child_encoded}'; "
        "$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$cmd}; "
        "if ($result.ReturnValue -ne 0) { throw ('Win32_Process.Create failed: ' + $result.ReturnValue) }; "
        "Write-Output $result.ProcessId"
    )
