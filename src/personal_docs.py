# src/personal_docs.py
import os
import re
import json
import logging
from typing import List, Dict, Set, Any, Tuple
from dataclasses import dataclass

from src.markitdown_runtime import MARKITDOWN_EXTS

logger = logging.getLogger(__name__)


def extract_pdf_text(file_path: str) -> str:
    """Extract text from a PDF file using pypdf (permissive, BSD)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = "".join((page.extract_text() or "") for page in reader.pages)
        return text
    except ImportError:
        logger.warning("pypdf not installed, cannot extract PDF text")
        return ""
    except Exception as e:
        logger.error(f"Failed to extract PDF text from {file_path}: {e}")
        return ""


def extract_office_text(file_path: str) -> str:
    """Extract text from an Office/EPUB doc via the optional markitdown dep.

    Returns "" when markitdown is missing or extraction fails, mirroring
    extract_pdf_text — the indexer then simply skips the file's content.
    """
    from src.markitdown_runtime import convert_to_markdown
    return convert_to_markdown(file_path) or ""


@dataclass
class PersonalDocsConfig:
    """Configuration for personal documents management."""
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    DEFAULT_EXTENSIONS: Tuple[str, ...] = (
        ".txt", ".md", ".json", ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub",
    )
    DEFAULT_K: int = 5
    STOP_WORDS: Set[str] = None
    
    def __post_init__(self):
        if self.STOP_WORDS is None:
            self.STOP_WORDS = set("""
            the a an is are was were be been being to of in for on at by with from 
            and or if then else when while as it this that those these i you he she 
            we they my your our their me him her us them
            """.split())

# Initialize configuration
config = PersonalDocsConfig()

def read_text_file(path: str) -> str:
    """Read a text file with error handling."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def split_chunks(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        j = min(i + size, n)
        chunks.append(text[i:j])
        i = j - overlap if j - overlap > i else j
    return chunks

def tokenize(s: str) -> Set[str]:
    """Tokenize string into words, excluding stop words."""
    tokens = re.findall(r"[A-Za-z0-9_\-]+", (s or "").lower())
    return set(t for t in tokens if t not in config.STOP_WORDS and len(t) > 1)

def load_personal_index(
    personal_dir: str, 
    extensions: Tuple[str, ...] = config.DEFAULT_EXTENSIONS
) -> List[Dict[str, Any]]:
    """Load and index personal documents."""
    files = []
    for root, _, names in os.walk(personal_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            if not os.path.isfile(p):
                continue
            if not any(name.lower().endswith(ext) for ext in extensions):
                continue
            size = os.path.getsize(p)
            ext = os.path.splitext(name)[1].lower()
            if ext == ".pdf":
                text = extract_pdf_text(p)
            elif ext in MARKITDOWN_EXTS:
                text = extract_office_text(p)
            else:
                text = read_text_file(p)
            chunks = split_chunks(text)
            display = os.path.relpath(p, personal_dir)
            files.append({"name": display, "path": p, "size": size, "chunks": chunks})
    return files

def retrieve_personal_keyword(personal_index: List[Dict], query: str, k: int = 5) -> List[str]:
    """
    Retrieve relevant documents using keyword search.

    Args:
        personal_index: The loaded document index
        query: Search query
        k: Number of results to return

    Returns:
        List of formatted search results
    """
    q = tokenize(query)
    if not q:
        return []

    scored = []
    for f in personal_index:
        for idx, ch in enumerate(f["chunks"]):
            score = len(q & tokenize(ch))
            if score > 0:
                scored.append((score, f["name"], idx, ch))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for s, fname, idx, ch in scored[:k]:
        out.append(f"[{fname} :: chunk {idx+1}]\n{ch}")
    return out

def retrieve_personal(personal_index: List[Dict], query: str, k: int = 5,
                     rag_manager=None) -> List[str]:
    """
    Retrieve relevant personal documents using vector search first, falling back to keyword search.

    Args:
        personal_index: The loaded document index
        query: The search query
        k: Number of results to return
        rag_manager: Optional RAGManager instance for vector search

    Returns:
        List of formatted search results
    """
    if not query:
        return []

    # First try vector search if RAGManager is available
    if rag_manager:
        try:
            vector_results = rag_manager.search(query, k)
            if vector_results:
                # Format vector results
                out = []
                for result in vector_results:
                    # Extract filename from path
                    source = result["metadata"].get("source", "")
                    filename = os.path.basename(source)

                    # Format the result
                    formatted = f"[{filename} :: vector search]\n{result['document']}"
                    out.append(formatted)
                return out
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to keyword search: {e}")

    # Fall back to keyword search
    return retrieve_personal_keyword(personal_index, query, k)

class PersonalDocsManager:
    """Manager class for personal document indexing and retrieval."""

    def __init__(self, personal_dir: str, rag_manager=None):
        self.personal_dir = personal_dir
        self.rag_manager = rag_manager
        self.index = []
        self.indexed_directories = []  # Track additional directories
        self.excluded_files: Set[str] = set()  # Files removed from RAG listing
        self.directories_file = os.path.join(personal_dir, "indexed_directories.json")
        self._excluded_file = os.path.join(personal_dir, "excluded_files.json")
        self.load_directories()
        self._load_excluded()
        self.refresh_index()

    def load_directories(self):
        """Load the list of indexed directories from persistent storage."""
        try:
            if os.path.exists(self.directories_file):
                with open(self.directories_file, 'r', encoding="utf-8") as f:
                    self.indexed_directories = json.load(f)
                logger.info(f"Loaded {len(self.indexed_directories)} indexed directories")
            else:
                self.indexed_directories = []
        except Exception as e:
            logger.error(f"Error loading directories: {e}")
            self.indexed_directories = []

    def save_directories(self):
        """Save the list of indexed directories to persistent storage."""
        try:
            with open(self.directories_file, 'w', encoding="utf-8") as f:
                json.dump(self.indexed_directories, f, indent=2)
            logger.info(f"Saved {len(self.indexed_directories)} indexed directories")
        except Exception as e:
            logger.error(f"Error saving directories: {e}")

    def _load_excluded(self):
        """Load the set of excluded file paths from persistent storage."""
        try:
            if os.path.exists(self._excluded_file):
                with open(self._excluded_file, 'r', encoding="utf-8") as f:
                    self.excluded_files = set(json.load(f))
            else:
                self.excluded_files = set()
        except Exception as e:
            logger.error(f"Error loading excluded files: {e}")
            self.excluded_files = set()

    def _save_excluded(self):
        try:
            with open(self._excluded_file, 'w', encoding="utf-8") as f:
                json.dump(list(self.excluded_files), f)
        except Exception as e:
            logger.error(f"Error saving excluded files: {e}")

    def exclude_file(self, filepath: str):
        """Exclude a file from the listing. Persists across restarts."""
        self.excluded_files.add(os.path.abspath(filepath))
        self._save_excluded()
        self.index = [f for f in self.index if os.path.abspath(f.get("path", "")) != os.path.abspath(filepath)]

    def add_directory(self, directory: str, *, index: bool = True, owner: str = None):
        """Add a directory to the tracking list and optionally index it."""
        # Normalize the path
        directory = os.path.abspath(directory)

        # Clear any exclusions for files in this directory
        self.excluded_files = {p for p in self.excluded_files if not p.startswith(directory)}
        self._save_excluded()

        if directory not in self.indexed_directories:
            self.indexed_directories.append(directory)
            self.save_directories()
            logger.info(f"Added directory to tracking: {directory}")
            
            # If RAG manager is available, index the directory immediately.
            # Callers that already indexed with owner metadata can pass
            # index=False so we do not create a second ownerless copy.
            if index and self.rag_manager:
                try:
                    result = self.rag_manager.index_personal_documents(directory, owner=owner)
                    logger.info(f"Indexed {result.get('indexed_count', 0)} chunks from {directory}")
                except Exception as e:
                    logger.error(f"Failed to index directory {directory}: {e}")
            
            # Refresh the local index to include the new directory
            self.refresh_index()
        else:
            logger.info(f"Directory already indexed: {directory}")

    def remove_directory(self, directory: str):
        """Remove a directory from the tracking list."""
        # Normalize the path
        directory = os.path.abspath(directory)
        
        if directory in self.indexed_directories:
            self.indexed_directories.remove(directory)
            self.save_directories()
            logger.info(f"Removed directory from tracking: {directory}")
            
            # Refresh the index to exclude the removed directory
            self.refresh_index()
            
            # If RAG manager is available, we should rebuild the index
            # This is a simple approach - in production you might want more sophisticated removal
            if self.rag_manager:
                try:
                    logger.info("Rebuilding RAG index after directory removal")
                    self.rag_manager.rebuild_index()
                    # Re-index remaining directories
                    for dir_path in self.indexed_directories:
                        if os.path.exists(dir_path):
                            self.rag_manager.index_personal_documents(dir_path)
                except Exception as e:
                    logger.error(f"Failed to rebuild RAG index: {e}")
        else:
            logger.info(f"Directory not in index: {directory}")

    def get_indexed_directories(self):
        """Get the list of all indexed directories."""
        return self.indexed_directories.copy()

    def refresh_index(self):
        """Refresh the document index including all tracked directories."""
        self.index = []

        # Index the base personal directory
        base_files = load_personal_index(self.personal_dir)
        for f in base_files:
            if os.path.abspath(f.get("path", "")) in self.excluded_files:
                continue
            f['source_dir'] = self.personal_dir
            self.index.append(f)

        # Index additional directories
        for directory in self.indexed_directories:
            if not os.path.exists(directory):
                logger.warning(f"Directory no longer exists: {directory}")
                continue

            if not os.path.isdir(directory):
                logger.warning(f"Path is not a directory: {directory}")
                continue

            # Load files from this directory
            dir_files = load_personal_index(directory)
            for f in dir_files:
                if os.path.abspath(f.get("path", "")) in self.excluded_files:
                    continue
                # Update the name to include the directory for clarity
                f['source_dir'] = directory
                f['name'] = f"{os.path.basename(directory)}/{f['name']}"
                self.index.append(f)

        logger.info(f"Refreshed index: {len(self.index)} documents from {len(self.indexed_directories) + 1} directories")

    def retrieve(self, query: str, k: int = 5) -> List[str]:
        """Retrieve relevant documents for a query."""
        return retrieve_personal(self.index, query, k, self.rag_manager)

    def get_file_list(self) -> List[Dict[str, Any]]:
        """Get list of indexed files with metadata."""
        return [{"name": f["name"], "size": f["size"]} for f in self.index]

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about indexed documents."""
        total_docs = len(self.index)
        total_chunks = sum(len(doc.get('chunks', [])) for doc in self.index)
        total_size = sum(doc.get('size', 0) for doc in self.index)
        
        extensions = {}
        for doc in self.index:
            ext = os.path.splitext(doc['path'])[1]
            extensions[ext] = extensions.get(ext, 0) + 1
        
        return {
            'total_documents': total_docs,
            'total_chunks': total_chunks,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'file_types': extensions,
            'directories_count': len(self.indexed_directories) + 1,
            'base_directory': self.personal_dir,
            'additional_directories': self.indexed_directories
        }
        
    def index_all_directories(self):
        """Re-index all tracked directories in the RAG system."""
        if not self.rag_manager:
            logger.warning("No RAG manager available for indexing")
            return
        
        success_count = 0
        failure_count = 0
        
        # Index the base personal directory
        try:
            result = self.rag_manager.index_personal_documents(self.personal_dir)
            if result.get('success'):
                success_count += 1
                logger.info(f"Indexed base directory: {self.personal_dir}")
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to index base directory {self.personal_dir}: {e}")
        
        # Index additional directories
        for directory in self.indexed_directories:
            if not os.path.exists(directory):
                logger.warning(f"Skipping non-existent directory: {directory}")
                failure_count += 1
                continue
            
            try:
                result = self.rag_manager.index_personal_documents(directory)
                if result.get('success'):
                    success_count += 1
                    logger.info(f"Indexed directory: {directory}")
                else:
                    failure_count += 1
                    logger.error(f"Failed to index directory {directory}: {result.get('message')}")
            except Exception as e:
                failure_count += 1
                logger.error(f"Failed to index directory {directory}: {e}")
        
        logger.info(f"Indexing complete: {success_count} succeeded, {failure_count} failed")
        return {"success": success_count, "failed": failure_count}
