def run(contract, inventory):
    if inventory["status"] != "VERIFIED":
        return {"status": "BLOCKED", "reason": "inventory is not verified", "items": []}
    items = []
    for row in inventory["items"]:
        if row["exists_at_destination"] and row["destination_sha256"] != row["sha256"]:
            return {"status": "BLOCKED", "reason": f"different content collision: {row['destination']}", "items": []}
        items.append({**row, "duplicate": row["exists_at_destination"]})
    return {"status": "READY_FOR_APPROVAL", "items": items}
