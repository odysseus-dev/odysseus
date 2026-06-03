"""
index_documents.py

A standalone script to index documents from the personal_docs directory
into the vector database using RAGManager. This script scans for text files,
processes them with proper chunking, and adds them to the vector database
with progress reporting and final statistics.

Features:
1. Imports RAGManager from rag_manager
2. Scans personal_docs directory for .txt, .md, .json files
3. Reads each file, chunks it (1000 chars with 200 overlap), and adds to vector database
4. Shows progress during processing and final statistics
5. Incremental Indexing: Tracks file state via MD5 hashes to skip unmodified files
"""

import os
import logging
import sys
import hashlib
import json
from pathlib import Path
from typing import List, Tuple, Dict

# Configure logging for the script
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Fallback path configuration to ensure rag_manager can be imported
sys.path.append(str(Path(__file__).parent))

def get_file_hash(file_path: Path) -> str:
    """Calculate MD5 hash of a file to detect changes."""
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating hash for {file_path}: {e}")
        return ""

def load_state_cache(cache_path: Path) -> Dict[str, str]:
    """Load the previously indexed files cache."""
    if cache_path.exists():
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load state cache: {e}. Re-indexing all files.")
    return {}

def save_state_cache(cache_path: Path, cache: Dict[str, str]):
    """Save the updated indexed files cache."""
    try:
        with open(cache_path, 'w') as f:
            json.dump(cache, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save state cache: {e}")

def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - chunk_overlap
    return chunks

def main():
    """Main function to index documents from personal_docs directory."""
    
    # Import RAGManager
    try:
        from rag_manager import RAGManager
    except ImportError as e:
        logger.error(f"Failed to import RAGManager. Ensure rag_manager.py is in the search path: {e}")
        sys.exit(1)

    # Configuration
    docs_dir = Path("personal_docs")
    cache_file = Path(".index_cache.json")
    supported_extensions = {".txt", ".md", ".json"}
    
    if not docs_dir.exists():
        logger.info(f"Directory '{docs_dir}' not found. Creating it now.")
        docs_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Please place your documents in 'personal_docs' and rerun the script.")
        return

    # Initialize RAG Manager and Cache
    rag = RAGManager()
    state_cache = load_state_cache(cache_file)
    new_cache = {}

    # Scan for files
    all_files = [p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in supported_extensions]
    
    if not all_files:
        logger.info("No supported documents found to index.")
        return

    logger.info(f"Found {len(all_files)} total files in '{docs_dir}'. Processing updates...")

    stats = {"processed": 0, "skipped": 0, "chunks_added": 0, "errors": 0}

    for idx, file_path in enumerate(all_files, 1):
        current_hash = get_file_hash(file_path)
        rel_path = str(file_path.relative_to(docs_dir))

        # Check if file changed
        if state_cache.get(rel_path) == current_hash:
            stats["skipped"] += 1
            new_cache[rel_path] = current_hash
            continue

        logger.info(f"[{idx}/{len(all_files)}] Indexing: {rel_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            chunks = chunk_text(content)
            
            if chunks:
                # Assuming RAGManager has an add_documents or add_chunks capability
                # Adjust method signature based on your actual RAGManager implementation
                metadata = {"source": rel_path}
                rag.add_documents(chunks, metadatas=[metadata] * len(chunks))
                stats["chunks_added"] += len(chunks)
            
            stats["processed"] += 1
            new_cache[rel_path] = current_hash

        except Exception as e:
            logger.error(f"Error processing {rel_path}: {e}")
            stats["errors"] += 1
            # Retain old hash if it failed so it tries again next time
            if rel_path in state_cache:
                new_cache[rel_path] = state_cache[rel_path]

    # Save progress state
    save_state_cache(cache_file, new_cache)

    # Final Summary Statistics
    logger.info("\n" + "="*40 + "\n--- INDEXING REPORT ---\n" + "="*40)
    logger.info(f"Files Skipped (Unchanged): {stats['skipped']}")
    logger.info(f"Files Newly Indexed:      {stats['processed']}")
    logger.info(f"Total Vector Chunks Added: {stats['chunks_added']}")
    logger.info(f"Failed Files:              {stats['errors']}")
    logger.info("="*40)

if __name__ == "__main__":
    main()
        from src.rag_manager import RAGManager
        logger.info("Successfully imported RAGManager")
    except ImportError as e:
        logger.error(f"Failed to import RAGManager: {e}")
        logger.error("Make sure rag_manager.py is in the same directory and accessible")
        return
    
    # Initialize RAGManager
    rag_manager = RAGManager()
    
    # Directory to scan
    docs_directory = "data/personal_docs"
    directory_path = Path(docs_directory)
    
    # Check if directory exists
    if not directory_path.exists():
        logger.error(f"Directory '{docs_directory}' not found!")
        logger.info(f"Please create the directory and add your documents: mkdir {docs_directory}")
        return
    
    # Supported file extensions
    supported_extensions = {'.txt', '.md', '.json'}
    logger.info(f"Scanning '{docs_directory}' for {', '.join(sorted(supported_extensions))} files...")
    
    # Find all supported files
    files_to_index = []
    for ext in supported_extensions:
        files_to_index.extend(directory_path.rglob(f"*{ext}"))
    
    # Sort files for consistent processing
    files_to_index.sort()
    
    if not files_to_index:
        logger.warning(f"No supported files found in '{docs_directory}' directory.")
        logger.info("Add .txt, .md, or .json files to the directory and run this script again.")
        return
    
    logger.info(f"Found {len(files_to_index)} files to index:")
    for file_path in files_to_index:
        logger.info(f"  - {file_path}")
    
    # Index the documents
    logger.info("\nStarting document indexing process...")
    
    try:
        result = rag_manager.index_personal_documents(docs_directory)
        
        # Display results
        logger.info("\n" + "="*50)
        if result["success"]:
            logger.info("✅ Document indexing completed successfully!")
            logger.info(f"   Indexed {result['indexed_count']} document chunks")
            if result.get("failed_count", 0) > 0:
                logger.warning(f"   Failed to process {result['failed_count']} files")
        else:
            logger.error("❌ Document indexing failed!")
            if "message" in result:
                logger.error(f"   Error: {result['message']}")
        
        # Show final statistics
        logger.info("\n" + "-"*30)
        logger.info("Database Statistics:")
        
        stats = rag_manager.get_stats()
        if "error" not in stats:
            for key, value in stats.items():
                logger.info(f"   {key}: {value}")
        else:
            logger.error(f"   Failed to retrieve statistics: {stats['error']}")
        
        logger.info("="*50)
        
    except Exception as e:
        logger.error(f"Failed to index documents: {e}")
        return

if __name__ == "__main__":
    main()
