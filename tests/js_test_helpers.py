import subprocess
from pathlib import Path

def run_node_script(
    args: list,
    input_str: str = None,
    cwd: Path = None,
    timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run node with specified arguments and input using UTF-8 encoding.
    
    Prevents encoding crashes on Windows and centralizes stderr verification.
    """
    cmd = ["node"] + args
    res = subprocess.run(
        cmd,
        input=input_str,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{res.stderr}\nSTDOUT:\n{res.stdout}")
    return res
