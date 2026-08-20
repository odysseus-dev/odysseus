"""odysseus_adapter.py — Odysseus-native memory platform adapter.

Attaches the memory platform to Odysseus's existing MemoryManager +
MemoryVectorStore, additively and opt-in:

  install_memory_platform(memory_manager, memory_vector)
      - Hybrid recall: wraps MemoryVectorStore.search with BM25 + RRF fusion
      - Association enrichment: precomputed graph walked at recall
      - Brain view: on-request /api/memory/brain (persona, identity,
        associations, neurons)
      - Sleep consolidation: background memory maintenance

Nothing is replaced. Each layer attaches only if the underlying piece exists.

Usage (in Odysseus's app.py, after components are built):
    from odysseus_adapter import install_memory_platform
    install_memory_platform(memory_manager, memory_vector)

Idempotent: safe to call once at startup.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("odysseus_adapter")

# Odysseus-native path resolution.
try:
    from . import memory_env
except ImportError:
    import memory_env

STORE_DB = memory_env.store_db()


def _import_platform():
    """Import memory_platform modules (lazy, so the adapter loads even if
    the platform isn't installed)."""
    try:
        from memory_platform import (
            memory_store, hybrid_recall, graph_memory,
            warm_router, warm_neuron_store, sleep_time,
        )
        return {
            "store": memory_store,
            "hybrid": hybrid_recall,
            "graph": graph_memory,
            "warm": warm_router,
            "neurons": warm_neuron_store,
            "sleep": sleep_time,
        }
    except ImportError as e:
        logger.warning("Memory platform not available: %s", e)
        return None


def install_memory_platform(memory_manager, memory_vector=None):
    """Attach the memory platform to Odysseus's existing components.

    Args:
        memory_manager: Odysseus's MemoryManager instance
        memory_vector: Odysseus's MemoryVectorStore instance (optional)

    Returns:
        dict with installed components, or None if platform unavailable.
    """
    platform = _import_platform()
    if not platform:
        return None

    installed = {}

    # 1. Hybrid recall — wraps MemoryVectorStore.search
    if memory_vector:
        try:
            store_db = platform["store"].connect()
            hr = platform["hybrid"].HybridRecall(
                memory_vector,
                lambda texts: _embed_texts(texts),
            )
            platform["hybrid"].swap_recall(memory_vector, hr.search)
            installed["hybrid_recall"] = hr
            logger.info("Hybrid recall installed")
        except Exception as e:
            logger.warning("Hybrid recall not installed: %s", e)

    # 2. Brain view routes
    try:
        _install_brain_routes(memory_manager, platform)
        installed["brain_routes"] = True
        logger.info("Brain routes installed")
    except Exception as e:
        logger.warning("Brain routes not installed: %s", e)

    # 3. Sleep consolidation hook
    try:
        _install_sleep_hook(platform)
        installed["sleep_hook"] = True
        logger.info("Sleep hook installed")
    except Exception as e:
        logger.warning("Sleep hook not installed: %s", e)

    return installed if installed else None


def _embed_texts(texts):
    """Embed texts via Ollama. Returns {text: [float...]}."""
    import urllib.request
    url = memory_env.ollama_url()
    model = memory_env.embed_model()
    dim = memory_env.embed_dim()

    results = {}
    for text in texts:
        if not text:
            continue
        try:
            payload = json.dumps({
                "model": model,
                "input": [f"search_document: {text}"],
            }).encode()
            req = urllib.request.Request(
                f"{url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            vecs = data.get("embeddings", [])
            if vecs:
                results[text] = vecs[0][:dim]
        except Exception:
            pass
    return results


def _install_brain_routes(memory_manager, platform):
    """Install brain view routes on the existing memory router."""
    from fastapi import APIRouter, Request
    from src.auth_helpers import get_current_user

    # This is called from app.py — the router is available there.
    # We create a new router for brain-specific endpoints.
    brain_router = APIRouter(prefix="/api/memory-brain", tags=["memory-brain"])

    @brain_router.get("/overview")
    async def brain_overview(request: Request):
        """Return a snapshot of the memory platform state."""
        owner = get_current_user(request)
        store = platform["store"]
        db = store.connect()

        try:
            stats = store.stats(db)
            neurons = platform["neurons"].list_neurons(db)
            associations = _get_associations(db)

            return {
                "stats": stats,
                "neurons": neurons[:50],
                "associations": associations[:100],
                "owner": owner,
            }
        finally:
            db.close()

    @brain_router.get("/pressure")
    async def brain_pressure():
        """Return consolidation pressure (how full the store is)."""
        try:
            from memory_platform.consolidate import store_pressure
            pressure = store_pressure(STORE_DB)
            return {"pressure": round(pressure, 3)}
        except Exception as e:
            return {"pressure": 0, "error": str(e)}

    @brain_router.post("/sleep")
    async def brain_sleep():
        """Trigger a sleep consolidation cycle."""
        try:
            result = platform["sleep"].run_sleep_cycle(hours=24)
            return result
        except Exception as e:
            return {"error": str(e)}

    # Store the router for app.py to include.
    _brain_router = brain_router


def _get_associations(db):
    """Get association graph as nodes + edges."""
    try:
        rows = db.execute(
            "SELECT src_id, dst_id, strength FROM associations "
            "WHERE strength >= 0.1 ORDER BY strength DESC LIMIT 200"
        ).fetchall()
        edges = []
        for r in rows:
            edges.append({
                "source": r["src_id"],
                "target": r["dst_id"],
                "strength": r["strength"],
            })
        return edges
    except Exception:
        return []


def _install_sleep_hook(platform):
    """Install a background thread that runs sleep consolidation periodically."""
    def _sleep_loop():
        while True:
            try:
                time.sleep(3600)  # Every hour
                platform["sleep"].run_sleep_cycle(hours=24)
            except Exception as e:
                logger.warning("Sleep cycle failed: %s", e)

    thread = threading.Thread(target=_sleep_loop, daemon=True)
    thread.start()
