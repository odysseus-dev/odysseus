"""
bootstrap_graph.py

One-shot import of existing memory.json entries into the Kuzu knowledge graph.

For each owner's memories, this script runs the triple extractor over the
flat text and writes the resulting typed entities + relationships to
data/graph/. Safe to re-run — the graph store's upsert + idempotent edge
checks deduplicate on re-ingest.

Usage:
    python -m scripts.bootstrap_graph                    # all owners, default settings
    python -m scripts.bootstrap_graph --owner alice      # only one owner
    python -m scripts.bootstrap_graph --batch-size 20    # how many memories per LLM call
    python -m scripts.bootstrap_graph --dry-run          # extract + print, don't write

Requires a configured default model in data/settings.json (same source the
extract/audit pipeline uses).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Optional

# Allow running as `python -m scripts.bootstrap_graph` from the repo root and
# as a plain `python scripts/bootstrap_graph.py` from elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import DATA_DIR  # noqa: E402
from services.memory import MemoryManager, GraphStore  # noqa: E402
from services.memory.triple_extractor import extract_triples_async, ingest_triples  # noqa: E402

logger = logging.getLogger("bootstrap_graph")


def _resolve_default_endpoint() -> tuple[Optional[str], Optional[str], dict]:
    """Mirror routes/memory_routes.py::api_audit_memories — pick the user's
    configured default LLM endpoint from settings.json + the DB."""
    from routes.model_routes import _load_settings, _normalize_base, build_chat_url
    from core.database import SessionLocal, ModelEndpoint

    settings = _load_settings()
    ep_id = settings.get("default_endpoint_id", "")
    default_model = settings.get("default_model", "")
    if not ep_id:
        return None, None, {}

    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == ep_id, ModelEndpoint.is_enabled == True
        ).first()
        if not ep:
            return None, None, {}
        base = _normalize_base(ep.base_url)
        endpoint_url = build_chat_url(base)
        model = default_model
        if not model and ep.models:
            try:
                models = json.loads(ep.models) if isinstance(ep.models, str) else ep.models
                if models:
                    model = models[0]
            except Exception:
                pass
        headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
        return endpoint_url, model, headers
    finally:
        db.close()


def _format_memory_as_user_message(text: str) -> dict:
    """Wrap a flat memory entry as a synthetic user turn for the extractor.

    The triple extractor expects a chat-like message list, so each memory
    becomes a one-line 'user said' message. Phrasing it this way nudges the
    LLM to treat the content as a first-person statement.
    """
    return {"role": "user", "content": text}


async def _process_owner(
    owner_key: Optional[str],
    entries: list[dict],
    graph: GraphStore,
    endpoint_url: str,
    model: str,
    headers: dict,
    batch_size: int,
    dry_run: bool,
) -> dict:
    """Run extraction over one owner's memories in batches. Returns counters."""
    total_triples = 0
    total_edges = 0
    skipped = 0

    # Pre-create the User node so the first triple doesn't race the upsert.
    if not dry_run:
        graph.upsert_user(owner_key)

    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        # Synthesize a "user said" turn per memory entry plus a synthetic
        # leading assistant turn so the extractor's min-2-messages guard
        # passes even on a single-memory batch.
        msgs = [{"role": "assistant", "content": "(historical memories follow)"}]
        for e in batch:
            text = (e.get("text") or "").strip()
            if not text:
                skipped += 1
                continue
            msgs.append(_format_memory_as_user_message(text))

        if len(msgs) < 2:
            continue

        triples = await extract_triples_async(
            msgs, endpoint_url, model, headers=headers,
            max_messages=len(msgs),  # keep all messages in this synthetic batch
        )
        total_triples += len(triples)
        if not triples:
            continue

        if dry_run:
            for t in triples:
                logger.info(
                    "  [DRY] %s(%s) -[%s]-> %s(%s)",
                    t["subject_label"], t["subject_name"],
                    t["relation"],
                    t["object_label"], t["object_name"],
                )
        else:
            # Provenance pointer: use the last memory id in the batch. Phase 1
            # tolerates that triples within a multi-memory batch may share a
            # source pointer — the bootstrap is a coarse-grained import, not
            # per-turn extraction.
            provenance = batch[-1].get("id") if batch else None
            total_edges += ingest_triples(graph, owner_key, triples, source_memory_id=provenance)

    return {
        "memories": len(entries),
        "triples_extracted": total_triples,
        "edges_added": total_edges,
        "skipped_empty": skipped,
    }


async def _main_async(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    manager = MemoryManager(DATA_DIR)
    all_entries = manager.load_all()
    if not all_entries:
        logger.warning("memory.json is empty — nothing to bootstrap")
        return 0

    endpoint_url, model, headers = _resolve_default_endpoint()
    if not endpoint_url or not model:
        logger.error("No default LLM endpoint configured in settings.json — aborting.")
        return 2

    # Partition by owner so the graph's per-owner isolation is preserved.
    by_owner: dict[Optional[str], list[dict]] = defaultdict(list)
    for e in all_entries:
        by_owner[e.get("owner")].append(e)

    targets = [args.owner] if args.owner else list(by_owner.keys())
    graph = GraphStore(DATA_DIR)
    if not graph.healthy:
        logger.error("GraphStore is unhealthy (kuzu missing or DB failed to open) — aborting.")
        return 2

    grand_total = {"memories": 0, "triples_extracted": 0, "edges_added": 0, "skipped_empty": 0}
    for owner_key in targets:
        entries = by_owner.get(owner_key, [])
        if not entries:
            logger.info("No memories for owner=%r — skipping", owner_key)
            continue
        logger.info("Bootstrapping owner=%r (%d memories)...", owner_key, len(entries))
        result = await _process_owner(
            owner_key, entries, graph,
            endpoint_url, model, headers,
            batch_size=args.batch_size, dry_run=args.dry_run,
        )
        logger.info("  -> %s", result)
        for k, v in result.items():
            grand_total[k] += v

    logger.info("Done. Totals: %s", grand_total)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", help="Only process this owner (default: all owners)")
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="Memories per LLM extraction call (default: 10). "
             "Lower if you hit context limits on a small model."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Extract and print triples but do not write to the graph.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
