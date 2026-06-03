"""
JUNIPERUS110_INFINITE_MIND_BRIDGE - API Routes

Provides:
- GET /api/gnexus/infinite-mind/state
- GET /api/gnexus/infinite-mind/search?q=...
- GET /api/gnexus/infinite-mind/context-packs
- GET /api/gnexus/infinite-mind/context-pack/{pack_id}
- POST /api/gnexus/infinite-mind/rescan
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

try:
    from core.constants import BASE_DIR
except Exception:
    BASE_DIR = Path(__file__).resolve().parents[2]

from src.gnexus_governance.infinite_mind_bridge import get_bridge

logger = logging.getLogger(__name__)


def setup_gnexus_infinite_mind_routes() -> APIRouter:
    """Setup routes for Infinite Mind access."""
    router = APIRouter(tags=["gnexus-infinite-mind"], prefix="/api/gnexus/infinite-mind")
    bridge = get_bridge()

    @router.get("/state", include_in_schema=False)
    async def get_state():
        """Get current Infinite Mind binding state."""
        try:
            state = bridge.get_infinite_mind_state()
            
            # Redact sensitive data
            redacted_state = {k: v for k, v in state.items() if "secret" not in k.lower()}
            
            return {
                "status": "ok",
                "state": redacted_state,
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as e:
            logger.error(f"Failed to get state: {e}")
            return {
                "status": "error",
                "error": str(e),
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }

    @router.get("/search", include_in_schema=False)
    async def search(q: str = Query(..., min_length=1, max_length=200)):
        """
        Search indexed Infinite Mind.
        
        Reads from generated index files (file-index.json), not recursive scans.
        """
        try:
            results = bridge.search_infinite_mind(q, limit=20)
            
            # Redact sensitive data from results
            safe_results = []
            for record in results:
                safe_record = {k: v for k, v in record.items() if "secret" not in k.lower()}
                safe_record["snippet"] = bridge.redact_sensitive_text(safe_record.get("snippet", ""))
                safe_results.append(safe_record)
            
            return {
                "status": "ok",
                "query": q,
                "resultCount": len(safe_results),
                "results": safe_results,
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {
                "status": "error",
                "query": q,
                "error": str(e),
                "resultCount": 0,
                "results": [],
            }

    @router.get("/context-packs", include_in_schema=False)
    async def list_packs():
        """List all context packs."""
        try:
            packs = bridge.list_context_packs()
            return {
                "status": "ok",
                "packCount": len(packs),
                "packs": packs,
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as e:
            logger.error(f"Failed to list packs: {e}")
            return {
                "status": "error",
                "error": str(e),
                "packCount": 0,
                "packs": [],
            }

    @router.get("/context-pack/{pack_id}", include_in_schema=False)
    async def get_pack(pack_id: str):
        """Get a specific context pack."""
        try:
            pack = bridge.load_context_pack(pack_id)
            if not pack:
                raise HTTPException(status_code=404, detail=f"Pack {pack_id} not found")
            
            # Redact sensitive data
            safe_pack = {k: v for k, v in pack.items() if "secret" not in k.lower()}
            
            return {
                "status": "ok",
                "packId": pack_id,
                "pack": safe_pack,
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get pack {pack_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/rescan", include_in_schema=False)
    async def rescan():
        """
        Rescan Infinite Mind.
        
        - Local-only, read-only
        - Does NOT mutate 06_INFINITE_BRAIN
        - Regenerates indexes from scratch
        """
        try:
            report = bridge.scan_infinite_mind(max_size_mb=10)
            
            # Also create initial context packs if needed
            _ensure_context_packs(bridge)
            
            return {
                "status": "ok",
                "scanReport": report,
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }
        except Exception as e:
            logger.error(f"Rescan failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "generatedAt": datetime.utcnow().isoformat() + "Z",
            }

    return router


def _ensure_context_packs(bridge):
    """
    LAYER 3: Create initial context packs from scanned files.
    Only run after scan to avoid hallucinating content.
    """
    packs_dir = bridge.data_root / "context-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    
    index_file = packs_dir / "index.json"
    
    # Don't overwrite if already exists
    if index_file.exists():
        return

    index = bridge.load_index()
    if not index:
        return

    packs = []

    # Pack 1: Operations Console Context
    ops_files = [r for r in index if "mission" in r.get("classification", "") or "console" in r.get("titleGuess", "").lower()]
    if ops_files:
        pack = {
            "packId": "operations-console-context",
            "title": "Operations Console Context",
            "purpose": "Operational state and console configuration",
            "sourceFiles": [f["relativePath"] for f in ops_files[:5]],
            "summary": f"Contains {len(ops_files)} operational files related to console state",
            "importantAnchors": [],
            "relevantCommands": [],
            "relatedReceipts": [],
            "operationalWarnings": [],
            "suggestedUse": "Load when starting operator loop or checking console health",
            "sourceStatus": "complete" if len(ops_files) > 0 else "insufficient_source",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }
        packs.append(pack)
        _save_pack(packs_dir, pack)

    # Pack 2: Infinite Brain Canon Context
    canon_files = [r for r in index if r.get("classification") in ["canon", "protocol", "runbook"]]
    if canon_files:
        pack = {
            "packId": "infinite-brain-canon-context",
            "title": "Infinite Brain Canon Context",
            "purpose": "Canonical protocols and runbooks",
            "sourceFiles": [f["relativePath"] for f in canon_files[:10]],
            "summary": f"Contains {len(canon_files)} canonical files: protocols, runbooks, and standards",
            "importantAnchors": [],
            "relevantCommands": [],
            "relatedReceipts": [],
            "operationalWarnings": [],
            "suggestedUse": "Reference when implementing governed operations",
            "sourceStatus": "complete" if len(canon_files) > 0 else "insufficient_source",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }
        packs.append(pack)
        _save_pack(packs_dir, pack)

    # Pack 3: Mission Runtime Context
    mission_files = [r for r in index if "mission" in r.get("titleGuess", "").lower() or "mission-control" in r.get("classification", "")]
    if mission_files:
        pack = {
            "packId": "mission-runtime-context",
            "title": "Mission Runtime Context",
            "purpose": "Active mission state and runtime information",
            "sourceFiles": [f["relativePath"] for f in mission_files[:8]],
            "summary": f"Contains {len(mission_files)} mission-related files",
            "importantAnchors": [],
            "relevantCommands": [],
            "relatedReceipts": [],
            "operationalWarnings": [],
            "suggestedUse": "Load before executing mission-dependent operations",
            "sourceStatus": "complete" if len(mission_files) > 0 else "insufficient_source",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }
        packs.append(pack)
        _save_pack(packs_dir, pack)

    # Pack 4: Operator Loop Context
    operator_files = [r for r in index if "operator" in r.get("titleGuess", "").lower() or "operator-loop" in r.get("classification", "")]
    if operator_files:
        pack = {
            "packId": "operator-loop-context",
            "title": "Operator Loop Context",
            "purpose": "Operator loop state and decisions",
            "sourceFiles": [f["relativePath"] for f in operator_files[:5]],
            "summary": f"Contains {len(operator_files)} operator loop related files",
            "importantAnchors": [],
            "relevantCommands": [],
            "relatedReceipts": [],
            "operationalWarnings": [],
            "suggestedUse": "Load to understand current operator loop status",
            "sourceStatus": "complete" if len(operator_files) > 0 else "insufficient_source",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }
        packs.append(pack)
        _save_pack(packs_dir, pack)

    # Pack 5: Model Routing Context
    routing_files = [r for r in index if "routing" in r.get("titleGuess", "").lower() or "memory-routing" in r.get("classification", "")]
    if routing_files:
        pack = {
            "packId": "model-routing-context",
            "title": "Model Routing Context",
            "purpose": "Model selection and routing policies",
            "sourceFiles": [f["relativePath"] for f in routing_files[:5]],
            "summary": f"Contains {len(routing_files)} model routing configuration files",
            "importantAnchors": [],
            "relevantCommands": [],
            "relatedReceipts": [],
            "operationalWarnings": [],
            "suggestedUse": "Load when making model selection decisions",
            "sourceStatus": "complete" if len(routing_files) > 0 else "insufficient_source",
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }
        packs.append(pack)
        _save_pack(packs_dir, pack)

    # Save index
    index_data = {
        "packCount": len(packs),
        "packs": [{"packId": p["packId"], "title": p["title"], "purpose": p["purpose"]} for p in packs],
        "generatedAt": datetime.utcnow().isoformat() + "Z",
    }
    index_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")


def _save_pack(packs_dir: Path, pack: dict):
    """Save a context pack to disk."""
    pack_file = packs_dir / f"{pack['packId']}.json"
    pack_file.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
