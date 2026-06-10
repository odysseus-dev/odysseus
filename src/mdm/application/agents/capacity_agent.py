import uuid
import logging
from datetime import datetime, timezone
from src.mdm.application.agents.base_agent import BaseAgent
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMCapacityLog
from src.mdm.liquid_cache import LiquidCache

logger = logging.getLogger(__name__)


class CapacityAgent(BaseAgent):
    cache_pool = "capacity"

    async def execute(self, action: str, payload: dict):
        if action == "calculate":
            return await self._calculate()
        elif action == "history":
            return await self._history(payload)
        raise ValueError(f"Unknown action: {action}")

    async def _calculate(self) -> dict:
        with UnitOfWork() as uow:
            stats = uow.devices.stats()
            result = {
                "total_devices": stats["total_devices"],
                "actifs": stats["actifs"],
                "capacite_totale_go": stats["capacite_totale_go"],
                "utilise_go": stats["utilise_go"],
                "libre_go": round(stats["capacite_totale_go"] - stats["utilise_go"], 1),
                "taux_utilisation": round((stats["utilise_go"] / max(stats["capacite_totale_go"], 1)) * 100, 1),
                "models": stats["models"],
                "ios_versions": stats["ios_versions"],
            }
            log = MDMCapacityLog(
                id=str(uuid.uuid4()),
                total_devices=stats["total_devices"],
                total_capacite_go=stats["capacite_totale_go"],
                total_utilise_go=stats["utilise_go"],
            )
            uow.session.add(log)
            uow.commit()
        return result

    async def _history(self, payload: dict) -> list:
        days = payload.get("days", 30)
        from datetime import timedelta
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        with UnitOfWork(read_only=True) as uow:
            logs = uow.session.query(MDMCapacityLog).filter(MDMCapacityLog.logged_at >= since).order_by(MDMCapacityLog.logged_at).all()
            return [{
                "id": l.id, "total_devices": l.total_devices,
                "capacite_go": l.total_capacite_go, "utilise_go": l.total_utilise_go,
                "logged_at": l.logged_at.isoformat() if l.logged_at else None,
            } for l in logs]
