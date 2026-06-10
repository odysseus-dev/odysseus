from src.mdm.infrastructure.unit_of_work import UnitOfWork


class CapacitySim:
    async def simulate_add(self, count: int, modele: str = "iPad Pro", capacite_go: int = 128) -> dict:
        with UnitOfWork(read_only=True) as uow:
            stats = uow.devices.stats()
            new_total = stats["total_devices"] + count
            new_cap = stats["capacite_totale_go"] + (capacite_go * count)
            new_used = stats["utilise_go"] + (capacite_go * count * 0.3)
            return {
                "scenario": f"Ajout de {count} {modele} ({capacite_go}Go)",
                "current": {"devices": stats["total_devices"], "capacite_go": stats["capacite_totale_go"], "utilise_go": stats["utilise_go"]},
                "projected": {"devices": new_total, "capacite_go": new_cap, "utilise_go": round(new_used, 1), "libre_go": round(new_cap - new_used, 1)},
            }

    async def simulate_remove(self, count: int) -> dict:
        with UnitOfWork(read_only=True) as uow:
            stats = uow.devices.stats()
            actual_remove = min(count, stats["total_devices"])
            ratio = actual_remove / max(stats["total_devices"], 1)
            new_total = stats["total_devices"] - actual_remove
            new_cap = stats["capacite_totale_go"] * (1 - ratio)
            new_used = stats["utilise_go"] * (1 - ratio)
            return {
                "scenario": f"Suppression de {actual_remove} appareils",
                "current": {"devices": stats["total_devices"], "capacite_go": stats["capacite_totale_go"]},
                "projected": {"devices": new_total, "capacite_go": round(new_cap, 1), "utilise_go": round(new_used, 1)},
            }

    async def simulate_upgrade(self, ios_version: str) -> dict:
        with UnitOfWork(read_only=True) as uow:
            stats = uow.devices.stats()
            return {
                "scenario": f"Mise à jour iOS vers {ios_version}",
                "current": {"ios_versions": stats["ios_versions"]},
                "projected": {"ios_versions": {ios_version: stats["total_devices"]}},
                "compatible": True,
            }

    async def compare(self, scenarios: list) -> list:
        results = []
        for s in scenarios:
            t = s.get("type", "add")
            if t == "add":
                r = await self.simulate_add(s.get("count", 1), s.get("modele", "iPad"), s.get("capacite_go", 128))
            elif t == "remove":
                r = await self.simulate_remove(s.get("count", 1))
            elif t == "upgrade":
                r = await self.simulate_upgrade(s.get("ios_version", "18.0"))
            else:
                continue
            results.append(r)
        return results
