#!/usr/bin/env python3
"""Import local Ollama models into Juniperus / Gnexus Operations Console.

Local-first. Detects a local Ollama daemon, builds a one-endpoint registry
(name="Local Ollama (All Models)", base_url=http://127.0.0.1:11434/v1), caches
ALL discovered model names on that single endpoint, and registers it in the
model DB idempotently (no duplicate per-model endpoints). Also runs a tiny
local-only smoke test.

Usage:
  .\\venv\\Scripts\\python.exe .\\scripts\\gnexus\\Import-LocalOllamaModels.py [--no-smoke]

Exit codes:
  0  success (endpoint registered OR ollama offline with clear record)
  2  ollama running but registration failed
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    run_smoke = "--no-smoke" not in sys.argv

    try:
        from src.gnexus_governance import ollama_readiness as olr
    except Exception as exc:  # pragma: no cover
        print("[FAIL] could not import ollama_readiness: %s" % exc)
        return 2

    print("[*] Detecting local Ollama...")
    reg = olr.build_registry()
    reg_path = olr.save_registry(reg)
    print("[*] Registry written: %s" % reg_path)

    running = reg["ollama"]["running"]
    names = reg["endpoint"]["cached_models"]
    print("[*] Ollama running: %s (source=%s)" % (running, reg["ollama"]["source"]))
    print("[*] Models discovered: %d" % len(names))

    registered = None
    if running and names:
        registered = _register_endpoint(olr, names)
    elif running and not names:
        print("[WARN] Ollama is running but reports zero models. Pull one, e.g. 'ollama pull llama3.2'.")
    else:
        print("[WARN] Ollama is not running. Endpoint NOT registered. Start Ollama and re-run.")

    # Update registry with registration status and re-save.
    reg["endpoint"]["registered_in_picker"] = registered
    olr.save_registry(reg)

    smoke = None
    if run_smoke and running and names:
        print("[*] Running local smoke test (Say OK)...")
        smoke = olr.run_smoke_test()
        if smoke.get("ok"):
            print("[OK] Smoke test passed in %sms (model=%s)" % (smoke.get("latencyMs"), smoke.get("model")))
        else:
            print("[WARN] Smoke test failed: %s (%s)" % (smoke.get("error"), smoke.get("detail")))

    # Write an import receipt.
    receipt = {
        "schema": "gnexus.ollama.import-receipt.v1",
        "ranAt": _utc_now(),
        "ollamaRunning": running,
        "modelCount": len(names),
        "endpoint": {
            "name": olr.ENDPOINT_NAME,
            "base_url": olr.ENDPOINT_BASE_URL,
            "registered": registered,
        },
        "smoke": smoke,
        "status": "OLLAMA_IMPORT_OK" if (registered or not running) else "OLLAMA_IMPORT_FAILED",
    }
    rdir = REPO_ROOT / "data" / "gnexus" / "receipts"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "ollama-import-receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("[*] Import receipt: %s" % (rdir / "ollama-import-receipt.json"))

    if running and names and not registered:
        print("[FAIL] Ollama has models but endpoint registration failed.")
        return 2
    print("[DONE] Local Ollama import complete.")
    return 0


def _register_endpoint(olr, names):
    """Idempotently register ONE endpoint in the model DB. Returns True/False/None."""
    try:
        from core.database import SessionLocal, ModelEndpoint  # type: ignore
    except Exception as exc:
        print("[WARN] DB unavailable, cannot register endpoint: %s" % exc)
        return None

    db = SessionLocal()
    try:
        existing = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == olr.ENDPOINT_BASE_URL)
            .first()
        )
        cached = json.dumps(names)
        if existing:
            existing.name = olr.ENDPOINT_NAME
            existing.is_enabled = True
            existing.model_type = "llm"
            existing.cached_models = cached
            db.commit()
            print("[OK] Updated existing endpoint '%s' with %d cached models." % (olr.ENDPOINT_NAME, len(names)))
            return True

        ep = ModelEndpoint(
            id=uuid.uuid4().hex[:8],
            name=olr.ENDPOINT_NAME,
            base_url=olr.ENDPOINT_BASE_URL,
            api_key=None,
            is_enabled=True,
            model_type="llm",
            cached_models=cached,
            supports_tools=None,
            owner=None,  # shared / visible to all users
        )
        db.add(ep)
        db.commit()
        print("[OK] Registered endpoint '%s' with %d cached models." % (olr.ENDPOINT_NAME, len(names)))
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print("[FAIL] endpoint registration error: %s" % exc)
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
