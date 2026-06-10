import asyncio
import logging

logger = logging.getLogger(__name__)


class MDMFlow:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def full_reconcile(self, device_ids: list = None):
        devices_task = asyncio.create_task(
            self.orchestrator.dispatch("device", "list", {"filters": {"est_actif": True} if device_ids else {}})
        )
        devices = await devices_task
        device_list = devices.get("devices", [])
        if not device_list:
            return {"error": "Aucun device actif"}
        match_task = asyncio.create_task(
            self.orchestrator.dispatch("cantique", "match", {"top_k": 3})
        )
        capacity_task = asyncio.create_task(
            self.orchestrator.dispatch("capacity", "calculate")
        )
        profiles_task = asyncio.create_task(
            self.orchestrator.dispatch("profile", "list", {"filters": {"est_actif": True}})
        )
        matches = await match_task
        capacity = await capacity_task
        profiles = await profiles_task
        assignments = []
        for m in matches.get("matches", [])[:10]:
            if not m.get("est_applique"):
                assignments.append({"device_id": m["device_id"], "profile_id": m["profile_id"]})
        autofill_result = {"applied": 0}
        if assignments:
            autofill_result = await self.orchestrator.dispatch("autofill", "batch", {"assignments": assignments})
        return {
            "devices_count": len(device_list),
            "profiles_count": len(profiles),
            "matches_count": matches.get("total", 0),
            "capacity": capacity,
            "autofill": autofill_result,
        }

    async def match_and_apply(self, top_k: int = 3):
        match_result = await self.orchestrator.dispatch("cantique", "match", {"top_k": top_k})
        matches = match_result.get("matches", [])
        top_matches = [m for m in matches if m.get("score", 0) >= 0.5][:5]
        if not top_matches:
            return {"matched": len(matches), "applied": 0, "message": "Aucun match suffisant"}
        assignments = [{"device_id": m["device_id"], "profile_id": m["profile_id"]} for m in top_matches]
        autofill_result = await self.orchestrator.dispatch("autofill", "batch", {"assignments": assignments})
        return {
            "matched": len(matches),
            "applied": autofill_result.get("success", 0),
            "top_assignments": top_matches[:5],
        }
