def run(contract, result):
    status = result.get("status", "BLOCKED")
    if status == "VERIFIED": text = "The approved outcome was independently verified."
    elif status == "RECOVERY_REQUIRED": text = "I stopped safely and preserved evidence; Jeremy must choose the next action."
    else: text = "No execution claim is made; the task is blocked or awaiting approval."
    return {"status": status, "human": text}
