import uuid
import pandas as pd
import logging
from src.mdm.infrastructure.unit_of_work import UnitOfWork
from src.mdm.infrastructure.models import MDMDevice
from src.mdm.liquid_cache import LiquidCache

logger = logging.getLogger(__name__)


class DeviceImport:
    async def from_csv(self, filepath: str) -> dict:
        try:
            df = pd.read_csv(filepath)
        except Exception as e:
            return {"imported": 0, "errors": [f"CSV error: {e}"]}
        return self._import_dataframe(df)

    async def from_xlsx(self, filepath: str) -> dict:
        try:
            df = pd.read_excel(filepath, engine="openpyxl")
        except Exception as e:
            return {"imported": 0, "errors": [f"XLSX error: {e}"]}
        return self._import_dataframe(df)

    def _import_dataframe(self, df: pd.DataFrame) -> dict:
        imported = 0
        errors = []
        required = {"udid", "modele", "ios_version", "capacite_go"}
        missing = required - set(df.columns)
        if missing:
            return {"imported": 0, "errors": [f"Colonnes manquantes: {', '.join(missing)}"]}
        with UnitOfWork() as uow:
            for idx, row in df.iterrows():
                try:
                    data = row.to_dict()
                    existing = uow.devices.get_by_udid(str(data["udid"]))
                    if existing:
                        for field in ["modele", "ios_version", "capacite_go", "stockage_utilise_go", "proprietaire", "type_appareil", "notes"]:
                            if field in data and pd.notna(data[field]):
                                setattr(existing, field, data[field] if field != "capacite_go" else int(data[field]))
                        uow.devices.update(existing)
                    else:
                        device = MDMDevice(
                            id=str(uuid.uuid4()),
                            udid=str(data["udid"]),
                            modele=str(data["modele"]),
                            ios_version=str(data["ios_version"]),
                            capacite_go=int(data["capacite_go"]),
                            stockage_utilise_go=float(data.get("stockage_utilise_go", 0)) if pd.notna(data.get("stockage_utilise_go", 0)) else 0,
                            proprietaire=str(data.get("proprietaire", "")) if pd.notna(data.get("proprietaire")) else None,
                            type_appareil=str(data.get("type_appareil", "iPad")) if pd.notna(data.get("type_appareil")) else "iPad",
                            notes=str(data.get("notes", "")) if pd.notna(data.get("notes")) else None,
                        )
                        uow.devices.add(device)
                    imported += 1
                except Exception as e:
                    errors.append(f"Ligne {idx + 2}: {e}")
        if imported:
            import asyncio
            asyncio.create_task(LiquidCache.invalidate_pool("explorer"))
            asyncio.create_task(LiquidCache.invalidate_pool("devices"))
            asyncio.create_task(LiquidCache.invalidate_pool("stats"))
        return {"imported": imported, "errors": errors, "total": len(df)}
