import hashlib
import re

_FORBIDDEN = re.compile(r"\b(New-Service|Set-Service|Register-ScheduledTask|schtasks|Set-ItemProperty|New-ItemProperty|Set-Acl|icacls|Invoke-WebRequest|curl|wget)\b", re.I)
def run(contract, script: str):
    if contract.operation.value != "powershell": return {"status": "BLOCKED", "reason": "not a PowerShell contract"}
    if _FORBIDDEN.search(script): return {"status": "BLOCKED", "reason": "forbidden PowerShell capability"}
    if script.count("'") % 2 or script.count('"') % 2: return {"status": "BLOCKED", "reason": "unbalanced quote"}
    return {"status": "READY_FOR_APPROVAL", "script_sha256": hashlib.sha256(script.encode()).hexdigest()}
