"""Compatibility import for the canonical memory manager.

Historically this package carried a second copy of ``MemoryManager``. The
application runtime instantiates ``src.memory.MemoryManager``, so keeping a
parallel implementation here risks silent drift between import paths.
"""

from src.memory import (
    MemoryManager,
    MemoryStoreUnreadable,
    get_text_similarity,
    tokenize,
)

__all__ = [
    "MemoryManager",
    "MemoryStoreUnreadable",
    "get_text_similarity",
    "tokenize",
]
