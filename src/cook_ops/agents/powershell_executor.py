import hashlib, subprocess, tempfile
from pathlib import Path
from ._common import receipt

def run(contract, script: str, approved_script_sha256: str):
    digest = hashlib.sha256(script.encode()).hexdigest()
    if not contract.approved:
        return receipt(contract, "POWERSHELL_EXECUTOR", "POWERSHELL", None, None, digest, 1, "BLOCKED")
    if digest != approved_script_sha256: return receipt(contract, "POWERSHELL_EXECUTOR", "POWERSHELL", None, None, digest, 1, "BLOCKED")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", encoding="utf-8", delete=False) as handle: handle.write(script); path = Path(handle.name)
    try:
        result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)], capture_output=True, text=True, timeout=60)
        return {**receipt(contract, "POWERSHELL_EXECUTOR", "POWERSHELL", path, None, digest, result.returncode, "RUNNING" if result.returncode == 0 else "FAILED"), "stdout": result.stdout[:20000], "stderr": result.stderr[:20000]}
    finally: path.unlink(missing_ok=True)
