"""Register OLLAMA_BASE_URL as a shared model endpoint when none exists."""
from __future__ import annotations

import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)


def ensure_ollama_endpoint_from_env() -> None:
    raw = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_URL") or "").strip()
    if not raw:
        return
    try:
        from routes.model_routes import _normalize_base, _probe_endpoint
        from core.database import SessionLocal, ModelEndpoint
    except Exception as e:
        logger.debug("Ollama bootstrap skipped (imports): %s", e)
        return

    base = _normalize_base(raw)
    if not base:
        return

    db = SessionLocal()
    try:
        existing = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == base)
            .first()
        )
        models = _probe_endpoint(base, None, timeout=5.0)
        if existing:
            if models:
                existing.cached_models = json.dumps(models)
            existing.is_enabled = True
            db.commit()
            logger.info("Ollama endpoint refreshed (%s, %d models)", base, len(models or []))
            return
        if not models:
            logger.warning("OLLAMA_BASE_URL set but no models at %s", base)
            return
        ep = ModelEndpoint(
            id=str(uuid.uuid4()),
            name="Ollama (auto)",
            base_url=base,
            cached_models=json.dumps(models),
            is_enabled=True,
            endpoint_kind="local",
            owner=None,
        )
        db.add(ep)
        db.commit()
        logger.info("Registered Ollama endpoint %s (%d models)", base, len(models))
        try:
            from src.settings import load_settings, save_settings
            s = load_settings()
            if not s.get("default_endpoint_id"):
                s["default_endpoint_id"] = ep.id
            if not s.get("default_model") and models:
                s["default_model"] = models[0]
            save_settings(s)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Ollama endpoint bootstrap failed: %s", e)
        db.rollback()
    finally:
        db.close()
