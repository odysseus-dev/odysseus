from ._common import sha256

def run(contract, plan):
    if plan.get("status") != "READY_FOR_APPROVAL": return {"status": "BLOCKED", "reason": "unapproved plan"}
    for item in plan["items"]:
        source, destination = item["source"], item["destination"]
        if not destination.is_file() or sha256(destination) != item["sha256"]: return {"status": "RECOVERY_REQUIRED", "reason": f"destination hash mismatch: {destination}"}
        if contract.operation.value == "copy" and (not source.is_file() or sha256(source) != item["sha256"]): return {"status": "RECOVERY_REQUIRED", "reason": f"copy source state mismatch: {source}"}
        if contract.operation.value == "move" and source.exists(): return {"status": "RECOVERY_REQUIRED", "reason": f"moved source remains: {source}"}
    return {"status": "VERIFIED"}
