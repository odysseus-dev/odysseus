import uuid
import copy
import logging
from datetime import datetime, timezone
from src.mdm.application.agents.base_agent import BaseAgent
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMEnrollment
from src.mdm.domain.config_builder import ConfigBuilder
from src.mdm.liquid_cache import LiquidCache

logger = logging.getLogger(__name__)


class AutofillAgent(BaseAgent):
    cache_pool = "matches"

    async def execute(self, action: str, payload: dict):
        if action == "preview":
            return await self._preview(payload)
        elif action == "apply":
            return await self._apply(payload)
        elif action == "batch":
            return await self._batch(payload)
        raise ValueError(f"Unknown action: {action}")

    async def _preview(self, payload: dict) -> dict:
        device_id = payload.get("device_id")
        profile_id = payload.get("profile_id")
        with UnitOfWork(read_only=True) as uow:
            device = uow.devices.get(device_id)
            profile = uow.profiles.get(profile_id)
            if not device or not profile:
                raise ValueError("Device ou Profile introuvable")
            builder = ConfigBuilder()
            builder.set_metadata("device_udid", device.udid)
            builder.set_metadata("profile_nom", profile.nom)
            if profile.payload_json:
                p = copy.deepcopy(profile.payload_json)
                builder.set_payload(p)
                builder.normalize_ios17()
            config = builder.build()
            warnings = ConfigBuilder.validate(config)
            return {
                "device": {"id": device.id, "udid": device.udid, "modele": device.modele},
                "profile": {"id": profile.id, "nom": profile.nom},
                "config": config,
                "warnings": warnings,
                "normalized": True,
            }

    async def _apply(self, payload: dict) -> dict:
        device_id = payload.get("device_id")
        profile_id = payload.get("profile_id")
        with UnitOfWork() as uow:
            profile = uow.profiles.get(profile_id)
            if profile and profile.payload_json:
                builder = ConfigBuilder()
                builder.set_payload(profile.payload_json)
                builder.normalize_ios17()
                profile.payload_json = builder._payload
                uow.profiles.update(profile)
            existing = uow.enrollments.find(device_id, profile_id)
            if existing:
                existing.statut = "applied"
                existing.applied_at = datetime.now(timezone.utc).replace(tzinfo=None)
                uow.enrollments.update(existing)
                return {"applied": True, "enrollment_id": existing.id, "was_existing": True}
            enrollment = MDMEnrollment(
                id=str(uuid.uuid4()),
                device_id=device_id,
                profile_id=profile_id,
                statut="applied",
                applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            uow.enrollments.add(enrollment)
        await LiquidCache.invalidate_pool("matches")
        return {"applied": True, "enrollment_id": enrollment.id, "was_existing": False}

    async def _batch(self, payload: dict) -> dict:
        assignments = payload.get("assignments", [])
        results = []
        for assignment in assignments:
            try:
                res = await self._apply(assignment)
                results.append({**assignment, "status": "ok", "enrollment_id": res["enrollment_id"]})
            except Exception as e:
                results.append({**assignment, "status": "error", "error": str(e)})
        return {"total": len(assignments), "success": sum(1 for r in results if r["status"] == "ok"), "results": results}
