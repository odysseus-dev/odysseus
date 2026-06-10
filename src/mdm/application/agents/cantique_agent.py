import uuid
import logging
from src.mdm.application.agents.base_agent import BaseAgent
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMMatchResult
from src.mdm.domain.matching_algo import ProfileMatcher, CantiqueStrategy
from src.mdm.domain.entities import DeviceEntity, ProfileEntity
from src.mdm.liquid_cache import LiquidCache

logger = logging.getLogger(__name__)


class CantiqueAgent(BaseAgent):
    cache_pool = "matches"
    circuit_name = "cantique_knn"

    async def execute(self, action: str, payload: dict):
        if action == "match":
            return await self._run_match(payload)
        elif action == "apply":
            return await self._apply_match(payload)
        elif action == "results":
            return await self._get_results(payload)
        raise ValueError(f"Unknown action: {action}")

    async def _run_match(self, payload: dict) -> dict:
        top_k = payload.get("top_k", 3)
        with UnitOfWork() as uow:
            devices = uow.devices.list({"est_actif": True}, page=1, per_page=5000)[0]
            profiles = uow.profiles.list({"est_actif": True})
            if not devices or not profiles:
                return {"matches": [], "total": 0, "message": "Aucun device ou profil actif"}
            device_entities = [DeviceEntity(
                udid=d.udid, modele=d.modele, ios_version=d.ios_version,
                capacite_go=d.capacite_go, stockage_utilise_go=float(d.stockage_utilise_go or 0),
                type_appareil=d.type_appareil,
            ) for d in devices]
            profile_entities = [ProfileEntity(
                nom=p.nom, ios_min_version=p.ios_min_version,
                payload_json=p.payload_json, categorie=p.categorie,
            ) for p in profiles]
            matcher = ProfileMatcher(CantiqueStrategy(top_k=top_k))
            raw_matches = matcher.match(device_entities, profile_entities)
            match_results = []
            for device_ent, profile_ent, score in raw_matches:
                device_id = next(d.id for d in devices if d.udid == device_ent.udid)
                profile_id = next(p.id for p in profiles if p.nom == profile_ent.nom)
                match = MDMMatchResult(
                    id=str(uuid.uuid4()),
                    device_id=device_id,
                    profile_id=profile_id,
                    score=score,
                    strategy="cantique",
                )
                uow.matches.add(match)
                match_results.append({
                    "id": match.id, "device_id": device_id,
                    "profile_id": profile_id, "score": score,
                    "device_udid": device_ent.udid, "profile_nom": profile_ent.nom,
                })
        await LiquidCache.set("matches", "last_results", match_results, ttl=120)
        return {"matches": match_results, "total": len(match_results)}

    async def _apply_match(self, payload: dict) -> dict:
        match_id = payload.get("match_id")
        with UnitOfWork() as uow:
            match = uow.matches.get(match_id)
            if not match:
                raise ValueError(f"Match {match_id} not found")
            from src.mdm.infrastructure.models import MDMEnrollment
            enrollment = MDMEnrollment(
                id=str(uuid.uuid4()),
                device_id=match.device_id,
                profile_id=match.profile_id,
                statut="applied",
            )
            uow.enrollments.add(enrollment)
            match.est_applique = True
            uow.matches.update(match)
        await LiquidCache.invalidate_pool("matches")
        return {"applied": True, "enrollment_id": enrollment.id}

    async def _get_results(self, payload: dict) -> dict:
        cached = await LiquidCache.get("matches", "last_results")
        if cached:
            return {"matches": cached, "total": len(cached), "cached": True}
        with UnitOfWork() as uow:
            matches = uow.matches.list_top(limit=payload.get("limit", 50))
            return {"matches": [{
                "id": m.id, "device_id": m.device_id,
                "profile_id": m.profile_id, "score": m.score,
                "strategy": m.strategy, "est_applique": m.est_applique,
            } for m in matches], "total": len(matches)}
