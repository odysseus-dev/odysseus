# routes/embedding_routes.py
"""Routes for managing local fastembed embedding models and custom endpoints."""
import os
import json
import shutil
import logging
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, Form, Depends
from core.constants import BASE_DIR
from core.middleware import require_admin

logger = logging.getLogger(__name__)

_ENDPOINT_FILE = os.path.join(BASE_DIR, "data", "embedding_endpoint.json")

# Track in-progress downloads
_downloading: dict = {}

# Curated recommendations — good coverage of size/quality tiers
RECOMMENDED_MODELS = {
    "sentence-transformers/all-MiniLM-L6-v2",     # 384d, 90MB  — fast & tiny, good default
    "BAAI/bge-small-en-v1.5",                      # 384d, 67MB  — smallest, solid quality
    "nomic-ai/nomic-embed-text-v1.5-Q",            # 768d, 130MB — quantized, great bang/buck
    "BAAI/bge-base-en-v1.5",                       # 768d, 210MB — balanced mid-range
    "snowflake/snowflake-arctic-embed-m",          # 768d, 430MB — strong performer
    "BAAI/bge-large-en-v1.5",                      # 1024d, 1.2GB — highest quality
}


def _cache_dir() -> str:
    """Get the fastembed cache directory.

    Defaults to a persistent path under the repo's data/ dir. The old
    default lived in /tmp, which many systems wipe on reboot — forcing a
    full re-download of the embedding model after every restart.
    """
    env = os.environ.get("FASTEMBED_CACHE_PATH")
    if env:
        return env
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "fastembed_cache",
    )


def _model_cache_name(hf_source: str) -> str:
    """Convert HF source like 'qdrant/all-MiniLM-L6-v2-onnx' to cache dir name."""
    return "models--" + hf_source.replace("/", "--")


def _is_downloaded(hf_source: str) -> bool:
    """Check if a model is already cached."""
    cache = _cache_dir()
    model_dir = os.path.join(cache, _model_cache_name(hf_source))
    if not os.path.isdir(model_dir):
        return False
    # Check for actual model files (not just empty dir)
    snapshots = os.path.join(model_dir, "snapshots")
    if os.path.isdir(snapshots):
        return any(os.listdir(snapshots))
    # Also check for blobs (older cache format)
    blobs = os.path.join(model_dir, "blobs")
    return os.path.isdir(blobs) and any(os.listdir(blobs))


def _active_model() -> str:
    """Get the currently configured fastembed model name."""
    return os.environ.get("FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def _dir_size_mb(path: str) -> float:
    """Get directory size in MB."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return round(total / (1024 * 1024), 1)


def _load_custom_endpoint() -> dict:
    """Load the saved custom embedding endpoint, if any."""
    try:
        if os.path.exists(_ENDPOINT_FILE):
            return json.loads(Path(_ENDPOINT_FILE).read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_custom_endpoint(data: dict):
    Path(_ENDPOINT_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(_ENDPOINT_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def setup_embedding_routes():
    router = APIRouter(prefix="/api/embeddings", dependencies=[Depends(require_admin)])

    @router.get("/models")
    def list_models():
        """List all available fastembed models with download status."""
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise HTTPException(503, "fastembed is not installed")

        active = _active_model()
        catalog = TextEmbedding.list_supported_models()
        result = []

        for m in catalog:
            hf_src = m.get("sources", {}).get("hf", "")
            downloaded = _is_downloaded(hf_src) if hf_src else False

            cached_size = None
            if downloaded and hf_src:
                model_path = os.path.join(_cache_dir(), _model_cache_name(hf_src))
                cached_size = _dir_size_mb(model_path)

            result.append({
                "model": m["model"],
                "dim": m.get("dim"),
                "size_gb": m.get("size_in_GB", 0),
                "description": m.get("description", ""),
                "downloaded": downloaded,
                "downloading": m["model"] in _downloading,
                "active": m["model"] == active,
                "recommended": m["model"] in RECOMMENDED_MODELS,
                "cached_size_mb": cached_size,
            })

        # Sort: active first, then downloaded, then by size
        result.sort(key=lambda x: (not x["active"], not x["downloaded"], x["size_gb"]))
        return result

    @router.post("/models/{model_name:path}/download")
    async def download_model(model_name: str):
        """Download a fastembed model. Returns when complete."""
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise HTTPException(503, "fastembed is not installed")

        # Validate model exists
        catalog = {m["model"]: m for m in TextEmbedding.list_supported_models()}
        if model_name not in catalog:
            raise HTTPException(404, f"Unknown model: {model_name}")

        hf_src = catalog[model_name].get("sources", {}).get("hf", "")
        if hf_src and _is_downloaded(hf_src):
            return {"status": "already_downloaded", "model": model_name}

        if model_name in _downloading:
            return {"status": "already_downloading", "model": model_name}

        _downloading[model_name] = True
        try:
            # Run in thread to not block the event loop
            loop = asyncio.get_event_loop()
            cache = _cache_dir()
            await loop.run_in_executor(
                None,
                lambda: TextEmbedding(model_name=model_name, cache_dir=cache),
            )
            return {"status": "downloaded", "model": model_name}
        except Exception as e:
            logger.error(f"Failed to download {model_name}: {e}")
            raise HTTPException(500, f"Download failed: {str(e)}")
        finally:
            _downloading.pop(model_name, None)

    @router.get("/models/{model_name:path}/status")
    def download_status(model_name: str):
        """Check download status of a model."""
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise HTTPException(503, "fastembed is not installed")

        catalog = {m["model"]: m for m in TextEmbedding.list_supported_models()}
        if model_name not in catalog:
            raise HTTPException(404, f"Unknown model: {model_name}")

        hf_src = catalog[model_name].get("sources", {}).get("hf", "")
        downloaded = _is_downloaded(hf_src) if hf_src else False

        return {
            "model": model_name,
            "downloaded": downloaded,
            "downloading": model_name in _downloading,
        }

    @router.delete("/models/{model_name:path}")
    def delete_model(model_name: str):
        """Delete a cached model."""
        if model_name == _active_model():
            raise HTTPException(400, "Cannot delete the active embedding model")

        if model_name in _downloading:
            raise HTTPException(400, "Model is currently downloading")

        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise HTTPException(503, "fastembed is not installed")

        catalog = {m["model"]: m for m in TextEmbedding.list_supported_models()}
        if model_name not in catalog:
            raise HTTPException(404, f"Unknown model: {model_name}")

        hf_src = catalog[model_name].get("sources", {}).get("hf", "")
        if not hf_src:
            raise HTTPException(400, "No cache source for this model")

        model_path = os.path.join(_cache_dir(), _model_cache_name(hf_src))
        if not os.path.isdir(model_path):
            return {"deleted": False, "message": "Model not cached"}

        shutil.rmtree(model_path)
        logger.info(f"Deleted cached model: {model_name} ({model_path})")
        return {"deleted": True, "model": model_name}

    @router.get("/endpoint")
    def get_endpoint():
        """Get the current custom embedding endpoint config."""
        saved = _load_custom_endpoint()
        current_url = os.environ.get("EMBEDDING_URL", "")
        return {
            "url": saved.get("url", current_url),
            "model": saved.get("model", os.environ.get("EMBEDDING_MODEL", "")),
            "active": bool(saved.get("url") or current_url),
        }

    @router.post("/endpoint")
    def set_endpoint(url: str = Form(...), model: str = Form("")):
        """Save a custom embedding endpoint URL."""
        url = url.strip()
        if not url:
            raise HTTPException(400, "URL is required")

        # Quick health check
        try:
            import httpx
            resp = httpx.post(
                url,
                json={"input": ["test"], "model": model or "test"},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            raise HTTPException(400, f"Endpoint unreachable: {e}")

        # Persist and set in environment for immediate use
        data = {"url": url}
        if model:
            data["model"] = model
        _save_custom_endpoint(data)
        os.environ["EMBEDDING_URL"] = url
        if model:
            os.environ["EMBEDDING_MODEL"] = model

        # Reset the RAG singleton so it picks up the new endpoint
        import src.rag_singleton as _rs
        _rs.rag_instance = None
        _rs._last_attempt = 0

        # Clear the HTTP-embedding "down" latch so the new endpoint is re-probed
        # instead of staying on the FastEmbed fallback for the process lifetime.
        try:
            from src.embeddings import reset_http_embed_state
            reset_http_embed_state()
        except Exception:
            pass

        # Reset ChromaDB client (collections will be recreated with new embeddings)
        try:
            from src.chroma_client import reset_client
            reset_client()
        except Exception:
            pass

        logger.info(f"Custom embedding endpoint set: {url}")
        return {"success": True, "url": url, "model": model}

    @router.delete("/endpoint")
    def clear_endpoint():
        """Clear the custom endpoint and revert to local fastembed."""
        if os.path.exists(_ENDPOINT_FILE):
            os.remove(_ENDPOINT_FILE)

        # Remove from environment
        os.environ.pop("EMBEDDING_URL", None)
        os.environ.pop("EMBEDDING_MODEL", None)

        # Reset the RAG singleton so it falls back to fastembed
        import src.rag_singleton as _rs
        _rs.rag_instance = None
        _rs._last_attempt = 0
        try:
            from src.embeddings import reset_http_embed_state
            reset_http_embed_state()
        except Exception:
            pass

        # Reset ChromaDB client
        try:
            from src.chroma_client import reset_client
            reset_client()
        except Exception:
            pass

        logger.info("Custom embedding endpoint cleared, reverting to local fastembed")
        return {"success": True}

    return router
