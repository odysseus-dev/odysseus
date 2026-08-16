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
        chunks = []

        for result in results:
            if not isinstance(result, dict):
                continue

            metadata = result.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            text = result.get("document")
            if text is None:
                text = result.get("text")
            if text is None:
                text = result.get("content")
            if text is None:
                text = ""

            source = result.get("source")
            if source is None:
                source = metadata.get("source")
            if source is None:
                source = "unknown"

            score = result.get("similarity")
            if score is None:
                score = result.get("score")
            if score is None:
                score = 0.0

            chunks.append(
                DocChunk(
                    text=text,
                    source=source,
                    score=score,
                    metadata=metadata,
                )
            )

        return chunks

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
            indexed=result.get("indexed_count", result.get("indexed", 0)),
            failed=result.get("failed_count", result.get("failed", 0)),
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
