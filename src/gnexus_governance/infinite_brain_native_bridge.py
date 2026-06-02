"""Gnexus Infinite Brain native bridge.

Provides a unified interface for integrating Infinite Brain as a read-only
source with native Brain (memory) and Library (document) systems.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.gnexus_governance.infinite_brain_manager import (
    InfiniteBrainManager,
    get_infinite_brain_manager,
    get_memory_records,
    get_document_records,
    get_infinite_brain_status,
)


class InfiniteBrainNativeBridge:
    """Bridge for Infinite Brain integration with native systems."""
    
    def __init__(self, repo_root: Optional[str] = None):
        self.manager = InfiniteBrainManager(repo_root)
    
    def get_memories_for_brain(self, owner: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get Infinite Brain records formatted for Brain (memory) system.
        
        Args:
            owner: Optional owner filter (ignored for Infinite Brain as they are system-owned)
            limit: Optional limit on number of records
            
        Returns:
            List of memory records with Infinite Brain metadata
        """
        memories = self.manager.get_memory_records()
        if limit is not None:
            memories = memories[:limit]
        # Ensure read-only flags are set
        for mem in memories:
            mem["readOnly"] = True
            mem["mutationAllowed"] = False
            mem["source"] = "infinite_brain"
        return memories
    
    def get_documents_for_library(self) -> List[Dict[str, Any]]:
        """Get Infinite Brain records formatted for Library (document) system.
        
        Returns:
            List of document records with Infinite Brain metadata
        """
        docs = self.manager.get_document_records()
        for doc in docs:
            doc["readOnly"] = True
            doc["mutationAllowed"] = False
            doc["source"] = "infinite_brain"
        return docs
    
    def rescan(self) -> Dict[str, Any]:
        """Rescan the Infinite Brain and update all derived data.
        
        Returns:
            Scan result dictionary
        """
        return self.manager.rescan()
    
    def get_status(self) -> Dict[str, Any]:
        """Get integration status.
        
        Returns:
            Status dictionary
        """
        return self.manager.get_status()


# Convenience functions
def get_infinite_brain_bridge(repo_root: Optional[str] = None) -> InfiniteBrainNativeBridge:
    return InfiniteBrainNativeBridge(repo_root)


def get_infinite_brain_memories(repo_root: Optional[str] = None, owner: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    return get_infinite_brain_bridge(repo_root).get_memories_for_brain(owner, limit)


def get_infinite_brain_documents(repo_root: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_infinite_brain_bridge(repo_root).get_documents_for_library()


def rescan_infinite_brain(repo_root: Optional[str] = None) -> Dict[str, Any]:
    return get_infinite_brain_bridge(repo_root).rescan()


def get_infinite_brain_integration_status(repo_root: Optional[str] = None) -> Dict[str, Any]:
    return get_infinite_brain_bridge(repo_root).get_status()


if __name__ == "__main__":
    # Simple test
    bridge = InfiniteBrainNativeBridge()
    print("Infinite Brain Native Bridge Test")
    print("==================================")
    status = bridge.get_status()
    print(f"Status: {status}")
    memories = bridge.get_memories_for_brain()
    print(f"\nFound {len(memories)} memory records for Brain")
    if memories:
        print("First memory:")
        import json
        print(json.dumps(memories[0], indent=2))
    documents = bridge.get_documents_for_library()
    print(f"\nFound {len(documents)} document records for Library")
    if documents:
        print("First document:")
        import json
        print(json.dumps(documents[0], indent=2))