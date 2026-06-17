"""RAG manager wrapper around VectorRAG."""
import logging
import os
from typing import Any, Dict, List, Optional

from src.constants import CHROMA_DIR

try:
    from rag_vector import VectorRAG
except ImportError:
    try:
        from .rag_vector import VectorRAG
    except ImportError:
        from src.rag_vector import VectorRAG

logger = logging.getLogger(__name__)


class RAGManager:
    """A manager class that wraps VectorRAG for backward compatibility."""

    def __init__(self, persist_directory: str = CHROMA_DIR):
        self.vector_rag = VectorRAG(persist_directory=persist_directory)
        logger.info("RAGManager initialized as wrapper for VectorRAG")

    def search(self, query: str, k: int = 5, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for documents, optionally filtering by owner."""
        return self.vector_rag.search(query, k, owner=owner)

    def index_personal_documents(
        self,
        directory: str,
        file_extensions: Optional[set] = None,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.vector_rag.index_personal_documents(
            directory,
            file_extensions=file_extensions,
            owner=owner,
        )

    def retrieve(self, query: str, k: int = 5, owner: Optional[str] = None) -> List[str]:
        if owner is None:
            return self.vector_rag.retrieve(query, k)
        return [r["document"] for r in self.vector_rag.search(query, k, owner=owner)]

    def remove_directory(self, directory: str, owner: Optional[str] = None) -> Dict[str, Any]:
        """Remove indexed chunks under a directory, optionally only for one owner."""
        if owner is None:
            return self.vector_rag.remove_directory(directory)

        if not self.vector_rag.healthy:
            return {"success": False, "message": "Collection not initialized"}

        directory = os.path.abspath(directory)
        owner = (owner or "").strip().lower() or None

        try:
            removed_ids = set()
            collections_for_delete = getattr(self.vector_rag, "_collections_for_delete", None)
            if not callable(collections_for_delete):
                return {"success": False, "message": "VectorRAG does not expose collection deletion"}

            for _lane_name, collection in collections_for_delete():
                results = collection.get(include=["metadatas"])
                ids = [
                    results["ids"][i]
                    for i, meta in enumerate(results["metadatas"])
                    if isinstance(meta, dict)
                    and isinstance(meta.get("source"), str)
                    and (meta["source"] == directory or meta["source"].startswith(directory + os.sep))
                    and str(meta.get("owner", "")).strip().lower() == owner
                ]
                if ids:
                    collection.delete(ids=ids)
                    removed_ids.update(ids)

            if not removed_ids:
                return {"success": True, "removed_count": 0, "message": "No docs found"}

            count = len(removed_ids)
            logger.info("Removed %s chunks from %s for owner=%s", count, directory, owner)
            return {"success": True, "removed_count": count, "message": f"Removed {count} chunks"}
        except Exception as exc:
            logger.error("remove_directory %s owner=%s: %s", directory, owner, exc)
            return {"success": False, "message": str(exc)}

    def reindex_directory(
        self,
        directory: str,
        file_extensions: Optional[set] = None,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        remove_result = self.remove_directory(directory, owner=owner)
        if not remove_result.get("success"):
            return remove_result
        index_result = self.index_personal_documents(directory, file_extensions, owner=owner)
        return {
            "success": index_result.get("success", False),
            "message": (
                f"Re-index for {directory}: removed {remove_result.get('removed_count', 0)}, "
                f"{index_result.get('message', '')}"
            ),
            "removed_count": remove_result.get("removed_count", 0),
            "indexed_count": index_result.get("indexed_count", 0),
            "failed_count": index_result.get("failed_count", 0),
        }

    def rebuild_index(self) -> bool:
        return self.vector_rag.rebuild_index()

    def get_stats(self) -> Dict[str, Any]:
        return self.vector_rag.get_stats()

    def add_document(self, text: str, metadata: Dict[str, Any]) -> bool:
        return self.vector_rag.add_document(text, metadata)

    def add_documents_batch(self, docs: List[tuple]) -> Dict[str, Any]:
        return self.vector_rag.add_documents_batch(docs)

    def delete_by_source(self, source: str) -> int:
        return self.vector_rag.delete_by_source(source)

    def rename_owner(
        self,
        old_owner: str,
        new_owner: str,
        *,
        path_map: Optional[Dict[str, str]] = None,
        path_prefixes: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        return self.vector_rag.rename_owner(
            old_owner,
            new_owner,
            path_map=path_map,
            path_prefixes=path_prefixes,
        )

    def _split_into_chunks(self, *args, **kwargs):
        return self.vector_rag._split_into_chunks(*args, **kwargs)
