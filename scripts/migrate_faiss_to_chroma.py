#!/usr/bin/env python3
"""
migrate_faiss_to_chroma.py

One-time migration of existing FAISS data to ChromaDB.

Migrates:
  - Memory vectors: data/memory_vectors/ -> odysseus_memories collection
  - RAG vectors:    data/rag/            -> odysseus_rag collection

Usage:
    python scripts/migrate_faiss_to_chroma.py

Requires: faiss-cpu, chromadb-client, and the embedding endpoint to be running.
"""

import json
import os
import sys
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _memory_map(rows):
    memories = {}
    if not isinstance(rows, list):
        return memories
    for row in rows:
        if not isinstance(row, dict):
            continue
        memory_id = row.get("id", "")
        if memory_id:
            memories[memory_id] = row
    return memories


def _rag_docstore(data):
    if not isinstance(data, dict):
        return [], [], []
    ids = data.get("ids", [])
    documents = data.get("documents", [])
    metadatas = data.get("metadatas", [])
    if not isinstance(ids, list) or not isinstance(documents, list) or not isinstance(metadatas, list):
        return [], [], []
    count = min(len(ids), len(documents), len(metadatas))
    return ids[:count], documents[:count], metadatas[:count]


def migrate_memories():
    """Migrate memory vectors from FAISS to ChromaDB."""
    from src.chroma_client import get_chroma_client
    from src.embeddings import get_embedding_client
    from src.constants import DATA_DIR

    ids_path = os.path.join(DATA_DIR, "memory_vectors", "ids.json")
    memory_path = os.path.join(DATA_DIR, "memory.json")

    if not os.path.exists(ids_path):
        logger.info("No memory FAISS index found, skipping memory migration")
        return

    ids = _load_json(ids_path, [])
    if not isinstance(ids, list):
        ids = []
    if not ids:
        logger.info("Memory FAISS index is empty, skipping")
        return

    # Load memory texts
    memories = {}
    if os.path.exists(memory_path):
        memories = _memory_map(_load_json(memory_path, []))

    embed = get_embedding_client()
    if not embed:
        logger.error("No embedding client available")
        return

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name="odysseus_memories",
        metadata={"hnsw:space": "cosine"},
    )

    batch_ids, batch_texts, batch_metas = [], [], []
    for mid in ids:
        mem = memories.get(mid)
        if not mem:
            continue
        text = mem.get("text", "").strip()
        if not text:
            continue
        batch_ids.append(mid)
        batch_texts.append(text)
        batch_metas.append({"source": "memory", "category": mem.get("category", "fact")})

    if batch_texts:
        vecs = embed.encode(batch_texts, normalize_embeddings=True).tolist()
        for i in range(0, len(batch_texts), 100):
            collection.add(
                ids=batch_ids[i:i+100],
                embeddings=vecs[i:i+100],
                documents=batch_texts[i:i+100],
                metadatas=batch_metas[i:i+100],
            )
        logger.info(f"Migrated {len(batch_texts)} memories to ChromaDB")
    else:
        logger.info("No memory entries to migrate")


def migrate_rag():
    """Migrate RAG documents from FAISS DocStore to ChromaDB."""
    from src.chroma_client import get_chroma_client
    from src.embeddings import get_embedding_client

    docs_path = os.path.join("data", "rag", "docs.json")
    if not os.path.exists(docs_path):
        logger.info("No RAG DocStore found, skipping RAG migration")
        return

    ids, documents, metadatas = _rag_docstore(_load_json(docs_path, {}))

    if not ids:
        logger.info("RAG DocStore is empty, skipping")
        return

    embed = get_embedding_client()
    if not embed:
        logger.error("No embedding client available")
        return

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name="odysseus_rag",
        metadata={"hnsw:space": "cosine"},
    )

    for i in range(0, len(ids), 100):
        batch_ids = ids[i:i+100]
        batch_docs = documents[i:i+100]
        batch_metas = metadatas[i:i+100]
        vecs = embed.encode(batch_docs, normalize_embeddings=True).tolist()
        collection.add(
            ids=batch_ids,
            embeddings=vecs,
            documents=batch_docs,
            metadatas=batch_metas,
        )

    logger.info(f"Migrated {len(ids)} RAG chunks to ChromaDB")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logger.info("Starting FAISS -> ChromaDB migration")
    migrate_memories()
    migrate_rag()
    logger.info("Migration complete")
