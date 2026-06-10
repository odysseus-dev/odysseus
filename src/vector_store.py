import os
from typing import Any, Dict, List, Optional, Protocol

_BACKEND = os.getenv("VECTOR_STORE", "chroma").lower()


def vector_store_backend() -> str:
    return _BACKEND


class VectorCollection(Protocol):
    """
    Interface every vector store backend must satisfy.
    """

    def count(self) -> int:
        """Return the number of vectors stored in the collection."""
        ...

    def get(
        self,
        ids: Optional[List[str]] = None,
        where: Optional[Dict] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Retrieve documents by ID or metadata filter."""
        ...

    def add(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict],
    ) -> None:
        """Insert documents.  Behaviour on duplicate IDs is backend-defined."""
        ...

    def upsert(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict],
    ) -> None:
        """Insert or overwrite documents."""
        ...

    def delete(self, ids: Optional[List[str]] = None) -> None:
        """Delete documents by ID."""
        ...

    def query(
        self,
        query_embeddings: List[List[float]],
        n_results: int = 10,
        where: Optional[Dict] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return the *n_results* nearest neighbours for each query embedding."""
        ...


def get_vector_collection(name: str, metadata: dict = None) -> VectorCollection:
    if _BACKEND == "qdrant":
        from src.qdrant_store import get_qdrant_collection

        return get_qdrant_collection(name)
    from src.chroma_client import get_chroma_client

    return get_chroma_client().get_or_create_collection(
        name=name, metadata=metadata or {}
    )


def delete_vector_collection(name: str) -> None:
    try:
        if _BACKEND == "qdrant":
            from src.qdrant_store import delete_qdrant_collection

            delete_qdrant_collection(name)
        else:
            from src.chroma_client import get_chroma_client

            get_chroma_client().delete_collection(name)
    except Exception:
        pass
