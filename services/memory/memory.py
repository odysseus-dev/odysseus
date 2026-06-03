"""Re-export shim — the canonical ``MemoryManager`` now lives in ``src/memory.py``.

This package historically defined a SECOND ``MemoryManager`` that wrote the same
``data/memory.json`` as ``src/memory.py`` with no shared lock — a silent
lost-update race — and with a divergent feature set (this copy lacked
``increment_uses`` and the ``uses`` field; ``src`` lacked ``claim_ownerless``).

The two are now unified: ``src/memory.py`` holds the single implementation (the
superset, with ``claim_ownerless`` merged in and a locked ``mutate()`` wrapper)
and this module re-exports it, so every existing ``services.memory`` /
``services.memory.memory`` import keeps working unchanged.
"""
from src.memory import MemoryManager, tokenize, get_text_similarity  # noqa: F401

__all__ = ["MemoryManager", "tokenize", "get_text_similarity"]
