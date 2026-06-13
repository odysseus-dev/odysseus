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
"""

import os
import logging
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.constants import PERSONAL_DIR

# Configure logging for the script
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Main function to index documents from personal_docs directory."""
    
    # Import RAGManager
    try:
        from src.rag_manager import RAGManager
        logger.info("Successfully imported RAGManager")
    except ImportError as e:
        logger.error(f"Failed to import RAGManager: {e}")
        logger.error("Make sure rag_manager.py is in the same directory and accessible")
        return
    
    # Initialize RAGManager
    rag_manager = RAGManager()
    
    # Directory to scan
    docs_directory = PERSONAL_DIR
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
