import uuid
import logging
from datetime import datetime, timezone
from src.mdm.application.agents.base_agent import BaseAgent
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMDevice, MDMCapacityLog
from src.mdm.liquid_cache import LiquidCache

logger = logging.getLogger(__name__)


class DeviceAgent(BaseAgent):
    cache_pool = "devices"

    async def execute(self, action: str, payload: dict):
        with UnitOfWork() as uow:
            if action == "list":
                filters = payload.get("filters", {})
                page = payload.get("page", 1)
                per_page = payload.get("per_page", 50)
                devices, total = uow.devices.list(filters, page, per_page)
                return {"devices": [self._to_dict(d) for d in devices], "total": total, "page": page, "per_page": per_page}
            elif action == "get":
                device = uow.devices.get(payload["id"])
                if not device:
                    raise ValueError(f"Device {payload['id']} not found")
                return self._to_dict(device)
            elif action == "create":
                device = MDMDevice(
                    id=str(uuid.uuid4()),
                    udid=payload["udid"],
                    modele=payload["modele"],
                    ios_version=payload["ios_version"],
                    capacite_go=int(payload["capacite_go"]),
                    stockage_utilise_go=float(payload.get("stockage_utilise_go", 0)),
                    proprietaire=payload.get("proprietaire"),
                    type_appareil=payload.get("type_appareil", "iPad"),
                    notes=payload.get("notes"),
                )
                uow.devices.add(device)
                await LiquidCache.invalidate_pool("explorer")
                return self._to_dict(device)
            elif action == "update":
                device = uow.devices.get(payload["id"])
                if not device:
                    raise ValueError(f"Device {payload['id']} not found")
                for field in ["modele", "ios_version", "capacite_go", "stockage_utilise_go", "proprietaire", "type_appareil", "notes", "est_actif"]:
                    if field in payload:
                        setattr(device, field, payload[field])
                uow.devices.update(device)
                await LiquidCache.invalidate_pool("explorer")
                await LiquidCache.delete("devices", payload["id"])
                return self._to_dict(device)
            elif action == "delete":
                ok = uow.devices.delete(payload["id"])
                await LiquidCache.invalidate_pool("explorer")
                await LiquidCache.delete("devices", payload["id"])
                return {"deleted": ok}
            elif action == "stats":
                return uow.devices.stats()
            raise ValueError(f"Unknown action: {action}")

    def _to_dict(self, d: MDMDevice) -> dict:
        return {
            "id": d.id, "udid": d.udid, "modele": d.modele,
            "ios_version": d.ios_version, "capacite_go": d.capacite_go,
            "stockage_utilise_go": d.stockage_utilise_go,
            "stockage_libre_go": float(d.capacite_go) - float(d.stockage_utilise_go or 0),
            "taux_utilisation": round((float(d.stockage_utilise_go or 0) / max(d.capacite_go, 1)) * 100, 1),
            "proprietaire": d.proprietaire, "type_appareil": d.type_appareil,
            "notes": d.notes, "est_actif": d.est_actif,
            "derniere_sync": d.derniere_sync.isoformat() if d.derniere_sync else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
