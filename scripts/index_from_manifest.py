#!/usr/bin/env python3
"""
index_from_manifest.py

Reads rag-manifest.yaml from the repo root and indexes each listed source
directory into ChromaDB via RAGManager. Called automatically by start-macos.sh
after ChromaDB is ready; also safe to run manually.

Usage:
    ./venv/bin/python scripts/index_from_manifest.py
    ./venv/bin/python scripts/index_from_manifest.py --manifest /path/to/manifest.yaml
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.constants import RAG_MANIFEST_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index RAG sources from rag-manifest.yaml")
    parser.add_argument(
        "--manifest",
        default=RAG_MANIFEST_FILE,
        help=f"Path to manifest file (default: {RAG_MANIFEST_FILE})",
    )
    args = parser.parse_args()

    try:
        import yaml
    except ImportError:
        logger.error("PyYAML not installed — run: pip install pyyaml")
        sys.exit(1)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.warning(f"No manifest found at {manifest_path} — skipping RAG indexing")
        return

    manifest = yaml.safe_load(manifest_path.read_text())
    sources = manifest.get("sources", [])
    if not sources:
        logger.info("rag-manifest.yaml has no sources — nothing to index")
        return

    logger.info(f"Loaded {len(sources)} source(s) from {manifest_path.name}")

    try:
        from src.rag_manager import RAGManager
    except Exception as e:
        logger.error(f"Failed to import RAGManager: {e}")
        sys.exit(1)

    rag = RAGManager()

    for source in sources:
        raw_path = source.get("path", "").strip()
        label = source.get("label", raw_path)

        if not raw_path:
            logger.warning(f"Source entry has no path — skipping: {source}")
            continue

        expanded = Path(raw_path).expanduser().resolve()
        if not expanded.exists():
            logger.warning(f"[{label}] Path not found: {expanded} — skipping")
            continue

        logger.info(f"[{label}] Indexing {expanded} …")
        try:
            result = rag.index_personal_documents(str(expanded))
            if result.get("success"):
                count = result.get("indexed_count", 0)
                failed = result.get("failed_count", 0)
                logger.info(f"[{label}] Done — {count} chunks indexed" + (f", {failed} failed" if failed else ""))
            else:
                logger.error(f"[{label}] Failed: {result.get('message', 'unknown error')}")
        except Exception as e:
            logger.error(f"[{label}] Exception during indexing: {e}")


if __name__ == "__main__":
    main()
