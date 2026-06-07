"""
rag_manager.py

A thin wrapper around VectorRAG for backward compatibility and additional features.
"""

import logging
from typing import List, Dict, Any, Optional

# Try to import from different possible locations
try:
    from rag_vector import VectorRAG
except ImportError:
    try:
        from .rag_vector import VectorRAG
    except ImportError:
        from src.rag_vector import VectorRAG

logger = logging.getLogger(__name__)

class RAGManager:
    """
    A manager class that wraps VectorRAG for backward compatibility.
    Most methods delegate directly to VectorRAG.
    """
    
    def __init__(self, persist_directory: str = "data/chroma"):
        """Initialize the RAGManager with VectorRAG."""
        self.vector_rag = VectorRAG(persist_directory=persist_directory)
        logger.info("RAGManager initialized as wrapper for VectorRAG")
    
    # Delegate all methods to VectorRAG
    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Search for documents - delegates to VectorRAG."""
        return self.vector_rag.search(query, k)
    
    def index_personal_documents(
        self,
        directory: str,
        file_extensions: Optional[set] = None,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Index documents - delegates to VectorRAG."""
        return self.vector_rag.index_personal_documents(
            directory,
            file_extensions=file_extensions,
            owner=owner,
        )
    
    def retrieve(self, query: str, k: int = 5) -> List[str]:
        """Retrieve relevant chunks - delegates to VectorRAG."""
        return self.vector_rag.retrieve(query, k)
    
    def rebuild_index(self) -> bool:
        """Rebuild index - delegates to VectorRAG."""
        return self.vector_rag.rebuild_index()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get stats - delegates to VectorRAG."""
        return self.vector_rag.get_stats()
    
    def add_document(self, text: str, metadata: Dict[str, Any]) -> bool:
        """Add single document - delegates to VectorRAG."""
        return self.vector_rag.add_document(text, metadata)
    
    def add_documents_batch(self, docs: List[tuple]) -> Dict[str, Any]:
        """Add documents in batch - delegates to VectorRAG."""
        return self.vector_rag.add_documents_batch(docs)
