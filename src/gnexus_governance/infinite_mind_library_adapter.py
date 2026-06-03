"""
JUNIPERUS110 - Infinite Mind Library Adapter

Since Juniperus does not have a dedicated Library source system (only Documents/personal docs),
this adapter bridges Infinite Mind context into the document/knowledge workflow.

Pattern: Library source adapter for read-only external knowledge sources.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.gnexus_governance.infinite_mind_bridge import get_bridge

BASE_DIR = Path(__file__).resolve().parents[2]
LIBRARY_DATA_ROOT = BASE_DIR / "data" / "gnexus" / "infinite-mind"


class InfiniteMindLibraryAdapter:
    """Adapt Infinite Mind context into Library/document structures."""

    def __init__(self):
        self.bridge = get_bridge()
        self.data_root = LIBRARY_DATA_ROOT
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure required directories."""
        self.data_root.mkdir(parents=True, exist_ok=True)

    def list_library_sources(self) -> List[Dict[str, Any]]:
        """
        List all available library sources, including Infinite Mind.
        
        Returns sources in format compatible with document system.
        """
        sources = []

        # Infinite Mind as library source
        state = self.bridge.get_infinite_mind_state()
        sources.append({
            "sourceId": "infinite-mind-bridge",
            "sourceName": "Infinite Mind",
            "sourceType": "infinite_mind",
            "sourceRoot": str(self.bridge.source_root),
            "readOnly": True,
            "writebackAllowed": False,
            "itemCount": state.get("fileCount", 0),
            "collectionCount": state.get("contextPackCount", 0),
            "lastUpdated": state.get("indexedAt", None),
            "status": state.get("scanStatus", "unknown"),
            "warnings": state.get("warnings", []),
        })

        return sources

    def list_library_items(self, source_id: str = "infinite-mind-bridge") -> List[Dict[str, Any]]:
        """
        List all items in a library source.
        
        For Infinite Mind, items are indexed files.
        """
        if source_id != "infinite-mind-bridge":
            return []

        index = self.bridge.load_index()
        if not index:
            return []

        items = []
        for record in index:
            items.append({
                "itemId": record.get("id"),
                "sourceId": "infinite-mind-bridge",
                "title": record.get("titleGuess", record.get("relativePath", "Unknown")),
                "type": "file",
                "classification": record.get("classification", "unknown"),
                "filePath": record.get("relativePath"),
                "fileType": record.get("fileType"),
                "sizeBytes": record.get("sizeBytes"),
                "modifiedAt": record.get("modifiedAt"),
                "snippet": record.get("snippet"),
                "tags": record.get("tags", []),
                "confidence": record.get("confidence", 0.0),
                "readOnly": True,
            })

        return items

    def list_library_collections(self, source_id: str = "infinite-mind-bridge") -> List[Dict[str, Any]]:
        """
        List collections in a library source.
        
        For Infinite Mind, collections are context packs.
        """
        if source_id != "infinite-mind-bridge":
            return []

        packs = self.bridge.list_context_packs()
        collections = []
        
        for pack in packs:
            collections.append({
                "collectionId": pack.get("packId"),
                "sourceId": "infinite-mind-bridge",
                "title": pack.get("title"),
                "purpose": pack.get("purpose"),
                "itemCount": len(pack.get("sourceFiles", [])),
                "description": pack.get("summary"),
                "tags": [],
                "readOnly": True,
            })

        return collections

    def get_library_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific library item (indexed file).
        """
        index = self.bridge.load_index()
        if not index:
            return None

        for record in index:
            if record.get("id") == item_id:
                return {
                    "itemId": record.get("id"),
                    "sourceId": "infinite-mind-bridge",
                    "title": record.get("titleGuess", record.get("relativePath")),
                    "type": "file",
                    "classification": record.get("classification"),
                    "filePath": record.get("relativePath"),
                    "fileType": record.get("fileType"),
                    "sizeBytes": record.get("sizeBytes"),
                    "modifiedAt": record.get("modifiedAt"),
                    "sha256": record.get("sha256"),
                    "snippet": record.get("snippet"),
                    "tags": record.get("tags", []),
                    "confidence": record.get("confidence"),
                    "readOnly": True,
                    "sourceRoot": str(self.bridge.source_root),
                }

        return None

    def search_library(self, query: str, source_id: str = "infinite-mind-bridge", limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search library items.
        
        For Infinite Mind, searches the indexed files.
        """
        if source_id != "infinite-mind-bridge":
            return []

        results = self.bridge.search_infinite_mind(query, limit=limit)
        
        items = []
        for record in results:
            items.append({
                "itemId": record.get("id"),
                "sourceId": "infinite-mind-bridge",
                "title": record.get("titleGuess", record.get("relativePath")),
                "type": "file",
                "classification": record.get("classification"),
                "filePath": record.get("relativePath"),
                "fileType": record.get("fileType"),
                "snippet": record.get("snippet"),
                "tags": record.get("tags", []),
                "searchScore": record.get("searchScore", 0.0),
                "readOnly": True,
            })

        return items

    def export_library_manifest(self) -> Dict[str, Any]:
        """
        Export full library manifest for Infinite Mind.
        """
        sources = self.list_library_sources()
        items = self.list_library_items()
        collections = self.list_library_collections()

        manifest = {
            "manifestVersion": "1.0",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "sourceCount": len(sources),
            "itemCount": len(items),
            "collectionCount": len(collections),
            "sources": sources,
            "items": items,
            "collections": collections,
            "readOnly": True,
            "writebackAllowed": False,
        }

        return manifest

    def save_library_source(self):
        """Save library source metadata to file."""
        sources = self.list_library_sources()
        manifest = self.export_library_manifest()

        source_file = self.data_root / "library-source.json"
        source_file.write_text(json.dumps(sources[0] if sources else {}, indent=2), encoding="utf-8")

        manifest_file = self.data_root / "library-manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


# Global singleton
_adapter_instance = None


def get_library_adapter() -> InfiniteMindLibraryAdapter:
    """Get or create the global library adapter instance."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = InfiniteMindLibraryAdapter()
    return _adapter_instance
