"""Gnexus Infinite Brain manager.

Provides read-only access to Infinite Brain records for integration with
native Brain (memory) and Library (document) systems.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gnexus_governance.infinite_brain_scanner import (
    scan_infinite_brain,
    write_scan_outputs,
)


class InfiniteBrainManager:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or os.getenv("GNEXUS_REPO_ROOT", os.getcwd()))
        self.data_dir = self.repo_root / "data" / "gnexus" / "infinite-brain-native"
        self.mission_control_dir = self.repo_root / "data" / "gnexus" / "mission-control"
        self._scan_cache: Optional[Dict[str, Any]] = None
        self._memory_records_cache: Optional[List[Dict[str, Any]]] = None
        self._document_records_cache: Optional[List[Dict[str, Any]]] = None

    def _ensure_data_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.mission_control_dir.mkdir(parents=True, exist_ok=True)

    def load_scan_result(self) -> Dict[str, Any]:
        """Load the latest scan result from file-index.json or rescan if missing."""
        index_file = self.data_dir / "file-index.json"
        if index_file.exists():
            try:
                return json.loads(index_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        # If missing or corrupt, rescan
        return self.rescan()

    def rescan(self) -> Dict[str, Any]:
        """Rescan the Infinite Brain and update all outputs."""
        scan_result = scan_infinite_brain(str(self.repo_root / "06_INFINITE_BRAIN"))
        outputs = write_scan_outputs(str(self.repo_root), scan_result)
        self._scan_cache = scan_result
        self._memory_records_cache = None
        self._document_records_cache = None
        return scan_result

    def get_memory_records(self) -> List[Dict[str, Any]]:
        """Get Infinite Brain records formatted as memories for Brain integration."""
        if self._memory_records_cache is not None:
            return self._memory_records_cache

        scan_result = self.load_scan_result()
        memory_records = []
        for f in scan_result.get("files", []):
            # Determine if this file should be treated as a memory
            # Heuristic: files in memory-related categories or text files with reasonable size
            if f["category"] in ["memory", "memories"] or (
                f["isText"]
                and f["size"] < 1024 * 64  # Less than 64KB
                and f["category"]
                in [
                    "unknown",
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
                ]
            ):
                memory_record = {
                    "id": f["id"],
                    "text": f["contentPreview"] or f["name"],
                    "source": "infinite_brain",
                    "readOnly": True,
                    "mutationAllowed": False,
                    "category": f["category"],
                    "timestamp": f["modified"],
                    "filePath": f["relativePath"],
                    "size": f["size"],
                }
                memory_records.append(memory_record)

        self._memory_records_cache = memory_records
        return memory_records

    def get_document_records(self) -> List[Dict[str, Any]]:
        """Get Infinite Brain records formatted as documents for Library integration."""
        if self._document_records_cache is not None:
            return self._document_records_cache

        scan_result = self.load_scan_result()
        document_records = []
        for f in scan_result.get("files", []):
            # Determine if this file should be treated as a document
            # Heuristic: text files that are not too large and not binary
            if f["isText"] and f["size"] < 1024 * 1024:  # Less than 1MB
                document_record = {
                    "id": f["id"],
                    "title": f["name"],
                    "content": f["contentPreview"] or "",
                    "source": "infinite_brain",
                    "readOnly": True,
                    "mutationAllowed": False,
                    "category": f["category"],
                    "timestamp": f["modified"],
                    "filePath": f["relativePath"],
                    "size": f["size"],
                }
                document_records.append(document_record)

        self._document_records_cache = document_records
        return document_records

    def get_status(self) -> Dict[str, Any]:
        """Get the overall status of Infinite Brain integration."""
        scan_result = self.load_scan_result()
        return {
            "infiniteBrainRoot": str(self.repo_root / "06_INFINITE_BRAIN"),
            "scanStatus": scan_result.get("status"),
            "lastScan": scan_result.get("generatedAt"),
            "fileCount": scan_result.get("fileCount", 0),
            "memoryRecordCount": len(self.get_memory_records()),
            "documentRecordCount": len(self.get_document_records()),
            "integrationReady": scan_result.get("status") == "SCAN_COMPLETE",
        }

    def get_context_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific context pack by ID."""
        context_pack_path = self.data_dir / "context-packs" / f"{pack_id}.json"
        if not context_pack_path.exists():
            return None
        try:
            return json.loads(context_pack_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_context_packs(self) -> List[str]:
        """List available context pack IDs."""
        context_packs_dir = self.data_dir / "context-packs"
        if not context_packs_dir.exists():
            return []
        return [f.stem for f in context_packs_dir.glob("*.json") if f.is_file()]


# Convenience functions for external use
def get_infinite_brain_manager(repo_root: Optional[str] = None) -> InfiniteBrainManager:
    return InfiniteBrainManager(repo_root)


def get_memory_records(repo_root: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_infinite_brain_manager(repo_root).get_memory_records()


def get_document_records(repo_root: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_infinite_brain_manager(repo_root).get_document_records()


def get_infinite_brain_status(repo_root: Optional[str] = None) -> Dict[str, Any]:
    return get_infinite_brain_manager(repo_root).get_status()


if __name__ == "__main__":
    # Simple test
    manager = InfiniteBrainManager()
    print("Infinite Brain Manager Test")
    print("==========================")
    status = manager.get_status()
    print(f"Status: {json.dumps(status, indent=2)}")
    memories = manager.get_memory_records()
    print(f"\nFound {len(memories)} memory records")
    if memories:
        print("First memory:")
        print(json.dumps(memories[0], indent=2))
    documents = manager.get_document_records()
    print(f"\nFound {len(documents)} document records")
    if documents:
        print("First document:")
        print(json.dumps(documents[0], indent=2))