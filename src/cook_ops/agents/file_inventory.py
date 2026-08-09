from pathlib import Path
from ._common import sha256

def run(contract):
    rows = []
    for source in contract.source_paths:
        if not source.is_file():
            return {"status": "BLOCKED", "reason": f"source inaccessible: {source}", "items": rows}
        destination = contract.destination_path / source.name
        rows.append({"source": source, "destination": destination, "size": source.stat().st_size, "sha256": sha256(source), "exists_at_destination": destination.exists(), "destination_sha256": sha256(destination) if destination.is_file() else None})
    return {"status": "VERIFIED", "items": rows}
