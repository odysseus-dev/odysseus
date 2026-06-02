r"""Gnexus Infinite Brain read-only scanner.

Discovers and indexes files under C:\Users\iamcy\CymaticsDev\06_INFINITE_BRAIN
without executing commands, installing packages, or mutating contents.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

INFINITE_BRAIN_ROOT = os.getenv(
    "GNEXUS_INFINITE_BRAIN_ROOT", r"C:\Users\iamcy\CymaticsDev\06_INFINITE_BRAIN"
)
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".next", "dist",
    "build", "logs", "data", ".mypy_cache", ".pytest_cache", ".cache",
    # Also skip any .jnexus or .gnx if they are not part of the structure? 
    # But we might want to include .gnexus and .gnx as they are part of the Brain/Library structure.
    # However, the Infinite Brain root already has .gnexus and .gnx directories.
    # We'll skip only the ones that are typically build/cache.
}

# File type classifications
FILE_TYPE_MAP = {
    ".md": "markdown",
    ".txt": "text",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ps1": "powershell",
    ".py": "python",
    ".html": "html",
    ".csv": "csv",
    ".log": "log",
}

# Risk levels for content (for redacting secrets)
SECRET_PATTERNS = [
    r"password\s*=",
    r"api[_-]?key\s*=",
    r"secret\s*=",
    r"token\s*=",
    r"-----BEGIN.*PRIVATE KEY-----",
]

# Categories for files
FILE_CATEGORIES = {
    "finalizer": ["finalizer", "finalize", "closeout"],
    "receipt": ["receipt", "receipts"],
    "verifier": ["verifier", "verify"],
    "mission-control": ["mission", "mission-control", "mission_control"],
    "memory": ["memory", "memories"],
    "skill": ["skill", "skills"],
    "canon": ["canon", "canonical"],
    "protocol": ["protocol", "protocols"],
    "runbook": ["runbook", "runbooks"],
    "dashboard": ["dashboard", "dashboards"],
    "bridge": ["bridge", "bridges"],
    "repair": ["repair", "repairs"],
    "replay": ["replay", "replays"],
    "ledger": ["ledger", "ledgers"],
    "unknown": []
}


@dataclass
class FileRecord:
    id: str
    path: str
    relativePath: str
    name: str
    extension: str
    size: int
    modified: str
    category: str
    fileType: str
    isText: bool
    contentPreview: str | None
    hash: str | None
    source: str = "infinite_brain"
    readOnly: bool = True
    mutationAllowed: bool = False


def _safe_id(text: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return out[:80] or "file"


def _get_file_category(file_path: Path, name: str) -> str:
    lower_name = name.lower()
    for category, keywords in FILE_CATEGORIES.items():
        for keyword in keywords:
            if keyword in lower_name:
                return category
    return "unknown"


def _is_text_file(extension: str) -> bool:
    return extension in {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".ps1", ".py", ".html", ".csv", ".log"}


def _read_preview(file_path: Path, max_chars: int = 200) -> str | None:
    if not _is_text_file(file_path.suffix.lower()):
        return None
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        # Redact potential secrets
        lines = content.split("\n")
        redacted_lines = []
        for line in lines:
            redacted_line = line
            for pattern in SECRET_PATTERNS:
                redacted_line = re.sub(pattern, "[REDACTED]", redacted_line, flags=re.IGNORECASE)
            redacted_lines.append(redacted_line)
        content = "\n".join(redacted_lines)
        if len(content) > max_chars:
            return content[:max_chars] + "..."
        return content
    except Exception:
        return None


def _hash_file(file_path: Path) -> str | None:
    try:
        import hashlib
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _iter_dirs(root: Path, max_depth: int = 10):
    root = root.resolve()
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except Exception:
            continue
        for child in reversed(children):
            if child.name in SKIP_DIRS:
                continue
            if child.name.startswith(".") and child.name not in {".gnexus", ".gnx"}:
                # We allow .gnexus and .gnx as they are part of the structure
                continue
            stack.append((child, depth + 1))


def scan_infinite_brain(
    brain_root: str | None = None, max_depth: int = 10
) -> Dict[str, Any]:
    root = Path(brain_root or INFINITE_BRAIN_ROOT)
    file_records: List[FileRecord] = []

    if not root.exists():
        return {
            "schema": "gnexus.infinite-brain-scan.v1",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "brainRoot": str(root),
            "status": "BRAIN_ROOT_NOT_FOUND",
            "files": [],
        }

    for path, depth in _iter_dirs(root, max_depth):
        # path is a directory
        try:
            for child in path.iterdir():
                if not child.is_file():
                    continue
                rel_path = child.relative_to(root)
                stat = child.stat()
                file_record = FileRecord(
                    id=_safe_id(str(rel_path)),
                    path=str(child),
                    relativePath=str(rel_path),
                    name=child.name,
                    extension=child.suffix.lower(),
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    category=_get_file_category(child, child.name),
                    fileType=FILE_TYPE_MAP.get(child.suffix.lower(), "unknown"),
                    isText=_is_text_file(child.suffix.lower()),
                    contentPreview=_read_preview(child),
                    hash=_hash_file(child) if stat.st_size < 1024 * 1024 else None,  # Only hash if < 1MB
                )
                file_records.append(file_record)
        except Exception:
            # If we cannot iterate the directory, skip it
            continue

    # Determine overall status
    if not file_records:
        status = "BRAIN_ROOT_EMPTY"
    else:
        status = "SCAN_COMPLETE"

    return {
        "schema": "gnexus.infinite-brain-scan.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": str(root),
        "status": status,
        "fileCount": len(file_records),
        "files": [asdict(fr) for fr in file_records],
    }


def write_scan_outputs(repo_root: str, scan_result: Dict[str, Any]) -> Dict[str, Any]:
    """Write the scan results to the required JSON files in data/gnexus/infinite-brain-native/."""
    repo = Path(repo_root)
    data_dir = repo / "data" / "gnexus" / "infinite-brain-native"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. source-binding.json
    source_binding = {
        "schema": "gnexus.infinite-brain-source-binding.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "infiniteBrainRoot": scan_result.get("brainRoot"),
        "status": scan_result.get("status"),
        "lastScan": scan_result.get("generatedAt"),
    }
    (data_dir / "source-binding.json").write_text(
        json.dumps(source_binding, indent=2), encoding="utf-8"
    )

    # 2. file-index.json
    file_index = {
        "schema": "gnexus.infinite-brain-file-index.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "totalFiles": scan_result.get("fileCount", 0),
        "files": scan_result.get("files", []),
    }
    (data_dir / "file-index.json").write_text(
        json.dumps(file_index, indent=2), encoding="utf-8"
    )

    # 3. candidate-records.json (we'll create a simplified version for now)
    candidate_records = {
        "schema": "gnexus.infinite-brain-candidate-records.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "candidates": [
            {
                "id": f["id"],
                "path": f["path"],
                "category": f["category"],
                "fileType": f["fileType"],
                "size": f["size"],
                "readOnly": f["readOnly"],
                "mutationAllowed": f["mutationAllowed"],
            }
            for f in scan_result.get("files", [])
        ],
    }
    (data_dir / "candidate-records.json").write_text(
        json.dumps(candidate_records, indent=2), encoding="utf-8"
    )

    # 4. source-map.json (mapping of categories to files)
    source_map = {
        "schema": "gnexus.infinite-brain-source-map.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "categories": {},
    }
    for f in scan_result.get("files", []):
        cat = f["category"]
        if cat not in source_map["categories"]:
            source_map["categories"][cat] = []
        source_map["categories"][cat].append(
            {
                "id": f["id"],
                "path": f["path"],
                "name": f["name"],
                "size": f["size"],
            }
        )
    (data_dir / "source-map.json").write_text(
        json.dumps(source_map, indent=2), encoding="utf-8"
    )

    # 5. scan-report.json (essentially the scan result itself)
    scan_report = {
        "schema": "gnexus.infinite-brain-scan-report.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "status": scan_result.get("status"),
        "fileCount": scan_result.get("fileCount", 0),
        "scanDurationMs": 0,  # Placeholder
    }
    (data_dir / "scan-report.json").write_text(
        json.dumps(scan_report, indent=2), encoding="utf-8"
    )

    # 6. native-memory-records.json (we'll extract files that look like memories)
    native_memory_records = {
        "schema": "gnexus.infinite-brain-native-memory-records.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "memoryRecords": [
            {
                "id": f["id"],
                "text": f["contentPreview"] or "",
                "source": f["source"],
                "readOnly": f["readOnly"],
                "mutationAllowed": f["mutationAllowed"],
                "category": f["category"],
                "timestamp": f["modified"],
            }
            for f in scan_result.get("files", [])
            if f["category"] in ["memory", "memories"] or f["fileType"] in ["markdown", "text", "json"]
        ],
    }
    (data_dir / "native-memory-records.json").write_text(
        json.dumps(native_memory_records, indent=2), encoding="utf-8"
    )

    # 7. native-document-records.json (we'll extract files that look like documents)
    native_document_records = {
        "schema": "gnexus.infinite-brain-native-document-records.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "documentRecords": [
            {
                "id": f["id"],
                "title": f["name"],
                "content": f["contentPreview"] or "",
                "source": f["source"],
                "readOnly": f["readOnly"],
                "mutationAllowed": f["mutationAllowed"],
                "category": f["category"],
                "modified": f["modified"],
            }
            for f in scan_result.get("files", [])
            if f["category"]
            in [
                "canon",
                "protocol",
                "runbook",
                "skill",
                "finalizer",
                "receipt",
                "verifier",
                "mission-control",
                "bridge",
                "repair",
                "replay",
                "ledger",
                "unknown",
            ]
            and f["isText"]
        ],
    }
    (data_dir / "native-document-records.json").write_text(
        json.dumps(native_document_records, indent=2), encoding="utf-8"
    )

    # 8. context-packs (we'll create a few placeholder context packs)
    context_packs_dir = data_dir / "context-packs"
    context_packs_dir.mkdir(parents=True, exist_ok=True)

    # index.json for context packs
    context_packs_index = {
        "schema": "gnexus.infinite-brain-context-packs-index.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "contextPacks": [
            "operations-console-context.json",
            "infinite-brain-canon-context.json",
            "mission-runtime-context.json",
            "operator-loop-context.json",
        ],
    }
    (context_packs_dir / "index.json").write_text(
        json.dumps(context_packs_index, indent=2), encoding="utf-8"
    )

    # operations-console-context.json
    operations_console_context = {
        "schema": "gnexus.infinite-brain-context-pack.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "contextPackId": "operations-console-context",
        "name": "Operations Console Context",
        "description": "Context about the Juniperus/Gnexus Operations Console itself",
        "sourceFiles": [
            f["relativePath"]
            for f in scan_result.get("files", [])
            if f["category"] in ["finalizer", "receipt", "verifier", "bridge"]
        ],
        "content": "This context pack contains information about the Juniperus operations console, its governance, and operational artifacts.",
        "readOnly": True,
        "mutationAllowed": False,
    }
    (context_packs_dir / "operations-console-context.json").write_text(
        json.dumps(operations_console_context, indent=2), encoding="utf-8"
    )

    # infinite-brain-canon-context.json
    infinite_brain_canon_context = {
        "schema": "gnexus.infinite-brain-context-pack.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "contextPackId": "infinite-brain-canon-context",
        "name": "Infinite Brain Canon Context",
        "description": "Canonical knowledge and protocols from Infinite Brain",
        "sourceFiles": [
            f["relativePath"]
            for f in scan_result.get("files", [])
            if f["category"] in ["canon", "protocol", "runbook", "skill"]
        ],
        "content": "This context pack contains canonical knowledge, protocols, and runbooks from the Infinite Brain.",
        "readOnly": True,
        "mutationAllowed": False,
    }
    (context_packs_dir / "infinite-brain-canon-context.json").write_text(
        json.dumps(infinite_brain_canon_context, indent=2), encoding="utf-8"
    )

    # mission-runtime-context.json
    mission_runtime_context = {
        "schema": "gnexus.infinite-brain-context-pack.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "contextPackId": "mission-runtime-context",
        "name": "Mission Runtime Context",
        "description": "Context about mission runtime and workflows",
        "sourceFiles": [
            f["relativePath"]
            for f in scan_result.get("files", [])
            if f["category"] in ["mission-control", "02_MEMORY_OBJECTS", "03_CLAIMS"]
        ],
        "content": "This context pack contains information about mission runtime, workflows, and execution contexts.",
        "readOnly": True,
        "mutationAllowed": False,
    }
    (context_packs_dir / "mission-runtime-context.json").write_text(
        json.dumps(mission_runtime_context, indent=2), encoding="utf-8"
    )

    # operator-loop-context.json
    operator_loop_context = {
        "schema": "gnexus.infinite-brain-context-pack.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "contextPackId": "operator-loop-context",
        "name": "Operator Loop Context",
        "description": "Context about operator loop and governance",
        "sourceFiles": [
            f["relativePath"]
            for f in scan_result.get("files", [])
            if f["category"] in ["05_UI_TRUTH", "06_PATTERN_BINDINGS", "07_PROOF", "08_GOOGLE_AI_STUDIO_EXPORTS"]
        ],
        "content": "This context pack contains information about the operator loop, truth verification, and pattern bindings.",
        "readOnly": True,
        "mutationAllowed": False,
    }
    (context_packs_dir / "operator-loop-context.json").write_text(
        json.dumps(operator_loop_context, indent=2), encoding="utf-8"
    )

    # 9. context-request-queue.json (empty for now)
    context_request_queue = {
        "schema": "gnexus.infinite-brain-context-request-queue.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "requests": [],
    }
    (data_dir / "context-request-queue.json").write_text(
        json.dumps(context_request_queue, indent=2), encoding="utf-8"
    )

    # 10. mission-control state
    mission_control_dir = repo / "data" / "gnexus" / "mission-control"
    mission_control_dir.mkdir(parents=True, exist_ok=True)
    infinite_brain_native_state = {
        "schema": "gnexus.mission-control.infinite-brain-native-state.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "brainRoot": scan_result.get("brainRoot"),
        "status": scan_result.get("status"),
        "lastScan": scan_result.get("generatedAt"),
        "fileCount": scan_result.get("fileCount", 0),
        "integrationStatus": "READY_FOR_NATIVE_INTEGRATION",
        "brainIntegrationStatus": "NOT_YET_INTEGRATED",
        "libraryIntegrationStatus": "NOT_YET_INTEGRATED",
        "writebackLocked": True,
        "mutationLocked": True,
    }
    (mission_control_dir / "infinite-brain-native-state.json").write_text(
        json.dumps(infinite_brain_native_state, indent=2), encoding="utf-8"
    )

    return {
        "sourceBinding": str(data_dir / "source-binding.json"),
        "fileIndex": str(data_dir / "file-index.json"),
        "candidateRecords": str(data_dir / "candidate-records.json"),
        "sourceMap": str(data_dir / "source-map.json"),
        "scanReport": str(data_dir / "scan-report.json"),
        "nativeMemoryRecords": str(data_dir / "native-memory-records.json"),
        "nativeDocumentRecords": str(data_dir / "native-document-records.json"),
        "contextPacksIndex": str(context_packs_dir / "index.json"),
        "operationsConsoleContext": str(context_packs_dir / "operations-console-context.json"),
        "infiniteBrainCanonContext": str(context_packs_dir / "infinite-brain-canon-context.json"),
        "missionRuntimeContext": str(context_packs_dir / "mission-runtime-context.json"),
        "operatorLoopContext": str(context_packs_dir / "operator-loop-context.json"),
        "contextRequestQueue": str(data_dir / "context-request-queue.json"),
        "missionControlState": str(mission_control_dir / "infinite-brain-native-state.json"),
    }


if __name__ == "__main__":
    # When run directly, perform a scan and write outputs for the current repo
    import sys

    repo_root = os.getenv("GNEXUS_REPO_ROOT", os.getcwd())
    if len(sys.argv) > 1:
        repo_root = sys.argv[1]

    print(f"Scanning Infinite Brain at {INFINITE_BRAIN_ROOT}")
    scan_result = scan_infinite_brain()
    print(f"Scan status: {scan_result.get('status')}")
    print(f"Files found: {scan_result.get('fileCount', 0)}")

    outputs = write_scan_outputs(repo_root, scan_result)
    print("Wrote outputs:")
    for key, path in outputs.items():
        print(f"  {key}: {path}")