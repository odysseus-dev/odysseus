import shutil
from ._common import receipt, sha256

def run(contract, plan):
    if not contract.approved or plan.get("status") != "READY_FOR_APPROVAL":
        return [receipt(contract, "FILE_MOVER", "MOVE", None, None, None, 1, "BLOCKED")]
    rows = []
    for item in plan["items"]:
        source, destination = item["source"], item["destination"]
        if sha256(source) != item["sha256"]:
            return rows + [receipt(contract, "FILE_MOVER", contract.operation.value.upper(), source, destination, None, 1, "RECOVERY_REQUIRED")]
        if item["duplicate"]:
            rows.append(receipt(contract, "FILE_MOVER", "DUPLICATE", source, destination, item["sha256"], 0, "VERIFIED")); continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if contract.operation.value == "copy":
            shutil.copy2(source, destination)
        elif contract.operation.value == "move":
            shutil.move(str(source), str(destination))
        else:
            return rows + [receipt(contract, "FILE_MOVER", "MOVE", source, destination, None, 1, "BLOCKED")]
        status = "VERIFIED" if destination.is_file() and sha256(destination) == item["sha256"] else "RECOVERY_REQUIRED"
        rows.append(receipt(contract, "FILE_MOVER", contract.operation.value.upper(), source, destination, item["sha256"], 0 if status == "VERIFIED" else 1, status))
        if status != "VERIFIED": return rows
    return rows
