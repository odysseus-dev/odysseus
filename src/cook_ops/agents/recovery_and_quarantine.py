def run(contract, failure): return {"status": "RECOVERY_REQUIRED", "task_id": contract.task_id, "evidence": failure, "next_action": "Escalate to Jeremy; no automatic retry or deletion."}
