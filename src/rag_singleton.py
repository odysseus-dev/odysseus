"""
RAG singleton instance for the application.
"""

import os
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

rag_instance = None
_last_attempt = 0.0
_RETRY_INTERVAL = 30  # seconds between re-init attempts


def get_rag_manager():
    """Disabled: vector document RAG (VectorRAG/ChromaDB) is unused and its
    client is incompatible with the installed pydantic. Return None so personal-
    doc routes fall back to non-vector behavior instead of re-attempting (and
    re-hanging on) a broken ChromaDB init every 30s."""
    return None


def _get_rag_manager_legacy():
    """Original lazy initializer, kept for reference / easy re-enable."""
    global rag_instance, _last_attempt

    if rag_instance is not None:
        return rag_instance

    now = time.monotonic()
    if now - _last_attempt < _RETRY_INTERVAL:
        return None  # too soon to retry

    _last_attempt = now

    try:
        from src.rag_vector import VectorRAG

        base_dir = Path(__file__).parent.parent
        persist_dir = os.path.join(base_dir, "data", "rag")

        rag_instance = VectorRAG(persist_directory=persist_dir)
        if not rag_instance.healthy:
            logger.warning("VectorRAG created but not healthy, will retry later")
            rag_instance = None
        else:
            logger.info("Initialized VectorRAG with ChromaDB")

    except ImportError as e:
        logger.warning(f"VectorRAG not available: {e}")
        rag_instance = None
    except Exception as e:
        logger.error(f"Failed to initialize RAG: {e}")
        rag_instance = None

    return rag_instance
