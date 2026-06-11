# services/docs/service.py
"""Docs service — personal document RAG."""

from dataclasses import dataclass
from typing import List, Dict, Any

from src.rag_manager import RAGManager
from src.constants import CHROMA_DIR


@dataclass
class DocChunk:
    """A retrieved document chunk."""
    text: str
    source: str
    score: float
    metadata: Dict[str, Any] = None


@dataclass
class IndexResult:
    """Result of indexing documents."""
    indexed: int
    failed: int
    errors: List[str]


class DocsService:
    """
    Document RAG service.

    Usage:
        service = DocsService()
        await service.index("/path/to/docs")
        results = await service.query("what is async await?")
    """

    def __init__(self, persist_dir: str = CHROMA_DIR):
        self.rag = RAGManager(persist_directory=persist_dir)

    async def query(self, query: str, top_k: int = 5) -> List[DocChunk]:
        """
        Query the document index.

        Args:
            query: Search query
            top_k: Number of results

        Returns:
            List of DocChunk objects
        """
        results = self.rag.search(query, k=top_k)
        return [
            DocChunk(
                text=r.get("text", r.get("content", "")),
                source=r.get("source", r.get("metadata", {}).get("source", "unknown")),
                score=r.get("score", 0.0),
                metadata=r.get("metadata"),
            )
            for r in results
            if isinstance(r, dict)
        ]

    async def index(self, directory: str) -> IndexResult:
        """
        Index documents from a directory.

        Args:
            directory: Path to documents

        Returns:
            IndexResult with stats
        """
        result = self.rag.index_personal_documents(directory)
        return IndexResult(
            indexed=result.get("indexed", 0),
            failed=result.get("failed", 0),
            errors=result.get("errors", []),
        )

    async def add_document(self, text: str, metadata: Dict[str, Any]) -> bool:
        """Add a single document to the index."""
        return self.rag.add_document(text, metadata)

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        return self.rag.get_stats()

    def rebuild_index(self) -> bool:
        """Rebuild the entire index."""
        return self.rag.rebuild_index()
