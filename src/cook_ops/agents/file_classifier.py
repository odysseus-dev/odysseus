def run(contract, inventory, rules): return {"status": "BLOCKED" if not rules else "VERIFIED", "rules": tuple(rules)}
