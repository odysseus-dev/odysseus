import json
import os
import uuid
import tempfile
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from src.mdm.application.orchestrator import MDMOrchestrator
from src.mdm.middleware.validation_middleware import ValidationMiddleware
from src.mdm.middleware.rate_limiter import MDMRateLimiter
from src.mdm.middleware.audit_middleware import AuditMiddleware

router = APIRouter(prefix="/api/mdm", tags=["MDM"])
orchestrator: MDMOrchestrator = None


def setup_mdm_routes(mdm_orchestrator=None) -> APIRouter:
    global orchestrator
    orchestrator = mdm_orchestrator or MDMOrchestrator()

    @router.get("/health")
    async def health():
        return await orchestrator.health()

    @router.get("/devices")
    async def list_devices(request: Request, filters: str = "", page: int = 1, per_page: int = 50):
        if not MDMRateLimiter.check(request.client.host, "devices:read"):
            raise HTTPException(429, "Rate limit exceeded")
        f = json.loads(filters) if filters else {}
        return await orchestrator.dispatch("device", "list", {"filters": f, "page": page, "per_page": per_page})

    @router.get("/devices/{device_id}")
    async def get_device(device_id: str):
        return await orchestrator.dispatch("device", "get", {"id": device_id})

    @router.post("/devices")
    async def create_device(data: dict):
        errors = ValidationMiddleware.validate_device(data)
        if errors:
            raise HTTPException(400, {"errors": errors})
        data = ValidationMiddleware.sanitize(data, ValidationMiddleware.DEVICE_FIELDS)
        return await orchestrator.dispatch("device", "create", data)

    @router.put("/devices/{device_id}")
    async def update_device(device_id: str, data: dict):
        data["id"] = device_id
        data = ValidationMiddleware.sanitize(data, ValidationMiddleware.DEVICE_FIELDS)
        return await orchestrator.dispatch("device", "update", data)

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str):
        return await orchestrator.dispatch("device", "delete", {"id": device_id})

    @router.get("/devices/stats")
    async def device_stats():
        return await orchestrator.dispatch("device", "stats", {})

    @router.post("/devices/import")
    async def import_devices(file: UploadFile = File(...)):
        if not MDMRateLimiter.check("import", "import"):
            raise HTTPException(429, "Rate limit exceeded")
        suffix = ".csv" if file.filename.endswith(".csv") else ".xlsx"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            content = await file.read()
            tmp.write(content)
            tmp.close()
            from src.mdm.application.agents.device_import import DeviceImport
            importer = DeviceImport()
            if suffix == ".csv":
                result = await importer.from_csv(tmp.name)
            else:
                result = await importer.from_xlsx(tmp.name)
            return result
        finally:
            os.unlink(tmp.name)

    @router.get("/profiles")
    async def list_profiles(categorie: str = ""):
        filters = {}
        if categorie:
            filters["categorie"] = categorie
        return await orchestrator.dispatch("profile", "list", {"filters": filters})

    @router.post("/profiles")
    async def create_profile(data: dict):
        errors = ValidationMiddleware.validate_profile(data)
        if errors:
            raise HTTPException(400, {"errors": errors})
        data = ValidationMiddleware.sanitize(data, ValidationMiddleware.PROFILE_FIELDS)
        return await orchestrator.dispatch("profile", "create", data)

    @router.put("/profiles/{profile_id}")
    async def update_profile(profile_id: str, data: dict):
        data["id"] = profile_id
        data = ValidationMiddleware.sanitize(data, ValidationMiddleware.PROFILE_FIELDS)
        return await orchestrator.dispatch("profile", "update", data)

    @router.delete("/profiles/{profile_id}")
    async def delete_profile(profile_id: str):
        return await orchestrator.dispatch("profile", "delete", {"id": profile_id})

    @router.post("/profiles/{profile_id}/normalize")
    async def normalize_profile(profile_id: str):
        return await orchestrator.dispatch("profile", "normalize", {"id": profile_id})

    @router.post("/match")
    async def run_match(top_k: int = 3):
        return await orchestrator.dispatch("cantique", "match", {"top_k": top_k})

    @router.get("/match/results")
    async def match_results(limit: int = 50):
        return await orchestrator.dispatch("cantique", "results", {"limit": limit})

    @router.post("/match/{match_id}/apply")
    async def apply_match(match_id: str):
        return await orchestrator.dispatch("cantique", "apply", {"match_id": match_id})

    @router.get("/capacity")
    async def get_capacity():
        return await orchestrator.dispatch("capacity", "calculate")

    @router.get("/capacity/history")
    async def capacity_history(days: int = 30):
        return await orchestrator.dispatch("capacity", "history", {"days": days})

    @router.post("/capacity/simulate")
    async def simulate_capacity(scenario: dict):
        from src.mdm.application.agents.capacity_sim import CapacitySim
        sim = CapacitySim()
        t = scenario.get("type", "add")
        if t == "add":
            return await sim.simulate_add(scenario.get("count", 1), scenario.get("modele", "iPad"), scenario.get("capacite_go", 128))
        elif t == "remove":
            return await sim.simulate_remove(scenario.get("count", 1))
        elif t == "upgrade":
            return await sim.simulate_upgrade(scenario.get("ios_version", "18.0"))
        raise HTTPException(400, "Type de scénario invalide")

    @router.post("/autofill/preview")
    async def preview_autofill(data: dict):
        return await orchestrator.dispatch("autofill", "preview", data)

    @router.post("/autofill/apply")
    async def apply_autofill(data: dict):
        return await orchestrator.dispatch("autofill", "apply", data)

    @router.post("/autofill/batch")
    async def batch_autofill(data: dict):
        return await orchestrator.dispatch("autofill", "batch", data)

    @router.get("/export/{device_id}/{profile_id}/mobileconfig")
    async def export_mobileconfig(device_id: str, profile_id: str):
        device = await orchestrator.dispatch("device", "get", {"id": device_id})
        profile = await orchestrator.dispatch("profile", "get", {"id": profile_id})
        from src.mdm.application.agents.config_writer import ConfigWriter
        xml = ConfigWriter.export_mobileconfig(device, profile)
        return Response(content=xml, media_type="application/xml", headers={
            "Content-Disposition": f'attachment; filename="mdm_{device.get("udid", "unknown")}.mobileconfig"'
        })

    @router.get("/stats")
    async def dashboard_stats():
        devices = await orchestrator.dispatch("device", "stats", {})
        capacity = await orchestrator.dispatch("capacity", "calculate")
        matches = await orchestrator.dispatch("cantique", "results", {"limit": 5})
        audit = AuditMiddleware.get_stats()
        return {
            "total_devices": devices.get("total_devices", 0),
            "actifs": devices.get("actifs", 0),
            "capacite_go": capacity.get("capacite_totale_go", 0),
            "utilise_go": capacity.get("utilise_go", 0),
            "taux_utilisation": capacity.get("taux_utilisation", 0),
            "models": devices.get("models", {}),
            "matches_total": matches.get("total", 0),
            "audit_actions": audit.get("total", 0),
        }

    @router.get("/flow/reconcile")
    async def flow_reconcile():
        return await orchestrator.flow.full_reconcile()

    @router.get("/flow/match-apply")
    async def flow_match_apply(top_k: int = 3):
        return await orchestrator.flow.match_and_apply(top_k)

    @router.get("/audit")
    async def get_audit(limit: int = 50):
        return {"entries": AuditMiddleware.get_recent(limit)}

    @router.get("/brain")
    async def brain_insights():
        return orchestrator.brain.export_insights()

    @router.post("/brain/learn")
    async def brain_learn():
        orchestrator.brain.update_policies()
        return orchestrator.brain.export_insights()

    return router
