import uuid
import logging
from src.mdm.application.agents.base_agent import BaseAgent
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMProfile
from src.mdm.domain.config_builder import ConfigBuilder

logger = logging.getLogger(__name__)


class ProfileAgent(BaseAgent):
    cache_pool = "profiles"

    async def execute(self, action: str, payload: dict):
        with UnitOfWork() as uow:
            if action == "list":
                filters = payload.get("filters", {})
                profiles = uow.profiles.list(filters)
                return [self._to_dict(p) for p in profiles]
            elif action == "get":
                profile = uow.profiles.get(payload["id"])
                if not profile:
                    raise ValueError(f"Profile {payload['id']} not found")
                return self._to_dict(profile)
            elif action == "create":
                builder = ConfigBuilder()
                builder.set_payload(payload.get("payload_json", {}))
                builder.normalize_ios17()
                profile = MDMProfile(
                    id=str(uuid.uuid4()),
                    nom=payload["nom"],
                    description=payload.get("description"),
                    payload_json=builder._payload,
                    ios_min_version=payload.get("ios_min_version"),
                    categorie=payload.get("categorie"),
                )
                uow.profiles.add(profile)
                return self._to_dict(profile)
            elif action == "update":
                profile = uow.profiles.get(payload["id"])
                if not profile:
                    raise ValueError(f"Profile {payload['id']} not found")
                for field in ["nom", "description", "payload_json", "ios_min_version", "categorie", "est_actif"]:
                    if field in payload:
                        setattr(profile, field, payload[field])
                if "payload_json" in payload:
                    builder = ConfigBuilder()
                    builder.set_payload(profile.payload_json)
                    builder.normalize_ios17()
                    profile.payload_json = builder._payload
                uow.profiles.update(profile)
                return self._to_dict(profile)
            elif action == "delete":
                ok = uow.profiles.delete(payload["id"])
                return {"deleted": ok}
            elif action == "normalize":
                profile = uow.profiles.get(payload["id"])
                if not profile:
                    raise ValueError(f"Profile {payload['id']} not found")
                builder = ConfigBuilder()
                builder.set_payload(profile.payload_json)
                builder.normalize_ios17()
                profile.payload_json = builder._payload
                uow.profiles.update(profile)
                return self._to_dict(profile)
            raise ValueError(f"Unknown action: {action}")

    def _to_dict(self, p: MDMProfile) -> dict:
        return {
            "id": p.id, "nom": p.nom, "description": p.description,
            "payload_json": p.payload_json, "ios_min_version": p.ios_min_version,
            "categorie": p.categorie, "est_actif": p.est_actif,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
