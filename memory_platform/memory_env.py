#!/usr/bin/env python3
"""memory_env.py — Odysseus-native path resolution for the memory platform.

Every path in the package derives from Odysseus's DATA_DIR (the single source
of truth for all persisted data). No hardcoded home paths, no machine-specific
assumptions, no user-specific venv paths.

Override via environment variables:
  ODYSSEUS_DATA_DIR    Odysseus data root (default: <app_root>/data)
  MEMORY_STORE_DB      explicit store DB path (default: <DATA_DIR>/memory_platform/store/memory.db)
  MEMORY_PYTHON        interpreter for memory subprocesses (default: sys.executable or python3)
  OLLAMA_URL           Ollama API endpoint (default: http://localhost:11434)

Rules:
  - Never hardcode a user path; always go through these helpers.
  - All paths resolve under DATA_DIR — owner isolation is filesystem-enforced.
"""

import os
import shutil
import sys


def expand(p):
    """expanduser + expandvars, for env values that may contain ~ or $HOME."""
    if not p:
        return p
    return os.path.expanduser(os.path.expandvars(p))


def _app_root():
    """Resolve the application root directory.

    In source runs this is the repo root (parent of src/).
    In frozen builds (PyInstaller) it's the bundle directory.
    """
    # If we're imported from within the Odysseus repo, walk up to find src/
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(here)
    if os.path.isdir(os.path.join(candidate, "src")):
        return candidate
    # Fallback: current working directory
    return os.getcwd()


def _default_data_dir():
    """Default DATA_DIR: <app_root>/data for source runs, ~/.odysseus/data for frozen."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".odysseus", "data")
    return os.path.join(_app_root(), "data")


def data_dir():
    """The Odysseus data root. Single source of truth for all persisted data."""
    return expand(os.environ.get("ODYSSEUS_DATA_DIR", _default_data_dir()))


def memory_dir():
    """Memory platform data directory: <DATA_DIR>/memory_platform."""
    return os.path.join(data_dir(), "memory_platform")


def store_dir():
    """Store directory: <memory_platform>/store."""
    return os.path.join(memory_dir(), "store")


def store_db():
    """Path to the hybrid memory store SQLite database."""
    return expand(os.environ.get(
        "MEMORY_STORE_DB",
        os.path.join(store_dir(), "memory.db")))


def graph_dir():
    """Graph memory directory: <memory_platform>/graph."""
    return os.path.join(memory_dir(), "graph")


def graph_db():
    """Path to the graph memory SQLite database."""
    return os.path.join(graph_dir(), "graph.sqlite")


def reflect_dir():
    """Reflection output directory: <memory_platform>/reflect."""
    return os.path.join(memory_dir(), "reflect")


def transcripts_dir():
    """Session transcripts directory: <memory_platform>/transcripts."""
    return os.path.join(memory_dir(), "transcripts")


def status_file():
    """Sleep-time status file: <memory_platform>/status.json."""
    return os.path.join(memory_dir(), "status.json")


def python_bin():
    """Interpreter for memory subprocesses.

    Uses the current Python (same venv) or python3 from PATH.
    No user-specific venv assumptions.
    """
    env = os.environ.get("MEMORY_PYTHON")
    if env:
        return expand(env)
    return sys.executable or shutil.which("python3") or "python3"


def ollama_url():
    """Ollama API endpoint."""
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def embed_model():
    """Embedding model name for vector operations."""
    return os.environ.get("MEMORY_EMBED_MODEL", "nomic-embed-text")


def embed_dim():
    """Embedding dimension (Matryoshka-truncated)."""
    return int(os.environ.get("MEMORY_EMBED_DIM", "256"))
