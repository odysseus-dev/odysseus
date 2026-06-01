"""
embeddings.py

Embedding clients for RAG and memory vector search.

Priority order:
  1. HTTP API (Ollama / vLLM / llama.cpp) — set EMBEDDING_URL in .env
  2. Local fastembed (ONNX, ~50MB) — zero config fallback

Set EMBEDDING_URL in .env, e.g.:
  EMBEDDING_URL=http://localhost:11434/v1/embeddings   (ollama)
  EMBEDDING_URL=http://localhost:8000/v1/embeddings    (vllm / llama.cpp)
"""

import os

# Windows: force HuggingFace/fastembed to COPY model files rather than symlink
# them. On a network-share/UNC cache dir Windows can't follow HF's symlinks
# ([WinError 1463] "symbolic link cannot be followed"), so ONNX fails to load the
# model and semantic memory dies. huggingface_hub reads this flag at import time,
# so it must be set before huggingface_hub is first imported — hence module-top.
# (app.py sets the same guard for the server entrypoint.)
if os.name == "nt":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import logging
import numpy as np
import httpx
from typing import List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-minilm:l6-v2"
_DEFAULT_FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingClient:
    """Drop-in replacement for SentenceTransformer.encode() using an HTTP API."""

    def __init__(self, url: Optional[str] = None, model: Optional[str] = None):
        self.url = url or os.getenv(
            "EMBEDDING_URL",
            f"http://{os.getenv('LLM_HOST', 'localhost')}:11434/v1/embeddings",
        )
        self.model = model or os.getenv("EMBEDDING_MODEL", _DEFAULT_MODEL)
        self._dim: Optional[int] = None
        # Short connect timeout so a DOWN embedding endpoint (e.g. Ollama not
        # running on :11434) fast-fails to the local FastEmbed fallback instead
        # of stalling startup ~30s per probe. Read stays generous for a real
        # endpoint (embedding a short string returns in well under a second).
        self._client = httpx.Client(timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0))

    def get_sentence_embedding_dimension(self) -> int:
        """Probe the endpoint for embedding dimension if not yet known."""
        if self._dim is not None:
            return self._dim
        # Embed a single word to discover the dimension
        vec = self.encode(["hello"])
        self._dim = vec.shape[1]
        logger.info(f"Embedding dimension: {self._dim} (model={self.model})")
        return self._dim

    def encode(
        self, texts: List[str], normalize_embeddings: bool = True
    ) -> np.ndarray:
        """Encode texts via the API. Returns (N, dim) float32 array."""
        if not texts:
            return np.array([], dtype="float32")

        # Batch in chunks of 64 to avoid oversized requests
        all_vecs = []
        for i in range(0, len(texts), 64):
            batch = texts[i : i + 64]
            resp = self._client.post(
                self.url,
                json={"input": batch, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()

            # OpenAI format: {"data": [{"embedding": [...], "index": 0}, ...]}
            embeddings = data.get("data", [])
            embeddings.sort(key=lambda e: e.get("index", 0))
            for emb in embeddings:
                all_vecs.append(emb["embedding"])

        vecs = np.array(all_vecs, dtype="float32")

        if normalize_embeddings and vecs.size > 0:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            vecs = vecs / norms

        if self._dim is None and vecs.size > 0:
            self._dim = vecs.shape[1]

        return vecs


class FastEmbedClient:
    """Local embedding client using fastembed (ONNX). No external service needed."""

    def __init__(self, model: Optional[str] = None):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise RuntimeError(
                "Local fastembed is not installed. Either install it "
                "(pip install fastembed) or point the app at a remote "
                "embeddings server."
            ) from e

        self.model = model or os.getenv("FASTEMBED_MODEL", _DEFAULT_FASTEMBED_MODEL)
        # Persistent cache under data/ so the model survives reboots and so
        # the download lands exactly where the admin panel's _is_downloaded()
        # check looks (both default to this same path).
        cache_dir = os.getenv("FASTEMBED_CACHE_PATH") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "fastembed_cache",
        )
        os.makedirs(cache_dir, exist_ok=True)
        # Windows self-heal: the HuggingFace-hub cache stores model files as
        # symlinks (snapshots/<rev>/model.onnx -> ../../blobs/<hash>). On a
        # network-share / UNC data dir Windows refuses to follow them
        # ([WinError 1463] "symbolic link cannot be followed because its type is
        # disabled"), and a cache copied between machines can carry dead symlinks
        # too. Either way fastembed tries to load a broken symlink and fails
        # *without* re-downloading, leaving semantic memory degraded. Detect a
        # broken-symlink model in the cache and drop the contaminated hub dir so
        # fastembed re-fetches (it falls back to its CDN tarball of real files,
        # which load fine). Best-effort; only ever removes a verifiably dead link.
        if os.name == "nt":
            try:
                import glob, shutil
                for _onnx in glob.glob(os.path.join(cache_dir, "**", "*.onnx"), recursive=True):
                    if os.path.islink(_onnx) and not os.path.exists(_onnx):
                        _root = _onnx
                        while os.path.basename(_root) and not os.path.basename(_root).startswith("models--"):
                            _parent = os.path.dirname(_root)
                            if _parent == _root:
                                break
                            _root = _parent
                        if os.path.basename(_root).startswith("models--"):
                            logger.warning(
                                "Embedding cache has a broken symlink (%s); clearing %s "
                                "so fastembed re-downloads real files", _onnx, _root,
                            )
                            shutil.rmtree(_root, ignore_errors=True)
            except Exception as _e:
                logger.debug("embedding cache symlink-heal skipped: %s", _e)
        kwargs = {"model_name": self.model, "cache_dir": cache_dir}
        self._embedding = TextEmbedding(**kwargs)
        self._dim: Optional[int] = None
        self.url = "local://fastembed"
        logger.info(f"FastEmbed loaded model={self.model}")

    def get_sentence_embedding_dimension(self) -> int:
        if self._dim is not None:
            return self._dim
        vec = self.encode(["hello"])
        self._dim = vec.shape[1]
        logger.info(f"Embedding dimension: {self._dim} (model={self.model})")
        return self._dim

    def encode(
        self, texts: List[str], normalize_embeddings: bool = True
    ) -> np.ndarray:
        """Encode texts locally. Returns (N, dim) float32 array."""
        if not texts:
            return np.array([], dtype="float32")

        vecs = np.array(list(self._embedding.embed(texts)), dtype="float32")

        if normalize_embeddings and vecs.size > 0:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            vecs = vecs / norms

        if self._dim is None and vecs.size > 0:
            self._dim = vecs.shape[1]

        return vecs


def _load_persisted_endpoint() -> dict:
    """Load the custom embedding endpoint saved from the admin panel."""
    try:
        endpoint_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "embedding_endpoint.json",
        )
        if os.path.exists(endpoint_file):
            import json
            data = json.loads(open(endpoint_file, encoding="utf-8").read())
            if data.get("url"):
                return data
    except Exception:
        pass
    return {}


_http_embed_down = False  # process-level latch: skip re-probing a dead endpoint


def reset_http_embed_state():
    """Clear the 'HTTP embedding endpoint is down' latch so the next
    get_embedding_client() re-probes. Call this when the embedding endpoint
    setting changes (e.g. the user starts Ollama and saves the endpoint) —
    otherwise a latch tripped at startup would keep us on FastEmbed for the
    whole process even after the endpoint comes back."""
    global _http_embed_down
    _http_embed_down = False


def get_embedding_client():
    """Factory: try HTTP API first, fall back to local fastembed."""
    global _http_embed_down

    # Check for a persisted custom endpoint (saved from admin panel)
    persisted = _load_persisted_endpoint()
    if persisted.get("url"):
        url = persisted["url"]
        model = persisted.get("model", "")
        # Also set in env so other code sees it
        os.environ["EMBEDDING_URL"] = url
        if model:
            os.environ["EMBEDDING_MODEL"] = model

    # Try the HTTP embedding API — unless we already found it down this process
    # (avoids paying the connect timeout again on every RAG/memory/tool probe).
    if not _http_embed_down:
        try:
            client = EmbeddingClient()
            client.get_sentence_embedding_dimension()  # health check
            logger.info(f"Using HTTP embedding API: {client.url} model={client.model}")
            return client
        except Exception as e:
            _http_embed_down = True
            logger.warning(f"HTTP embedding API unavailable ({e}); using local FastEmbed for the rest of this process")

    # Fall back to local fastembed
    try:
        client = FastEmbedClient()
        client.get_sentence_embedding_dimension()
        logger.info(f"Using local FastEmbed: model={client.model}")
        return client
    except ImportError:
        logger.error("fastembed not installed — run: pip install fastembed")
    except Exception as e:
        logger.error(f"FastEmbed init failed: {e}")

    return None
