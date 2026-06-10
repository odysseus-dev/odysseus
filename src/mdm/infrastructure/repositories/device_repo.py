from typing import Optional
from sqlalchemy.orm import Session
from src.mdm.infrastructure.models import MDMDevice


class DeviceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, device_id: str) -> Optional[MDMDevice]:
        return self.session.query(MDMDevice).filter(MDMDevice.id == device_id).first()

    def get_by_udid(self, udid: str) -> Optional[MDMDevice]:
        return self.session.query(MDMDevice).filter(MDMDevice.udid == udid).first()

    def list(self, filters: dict = None, page: int = 1, per_page: int = 50):
        q = self.session.query(MDMDevice)
        if filters:
            if filters.get("modele"):
                q = q.filter(MDMDevice.modele.ilike(f"%{filters['modele']}%"))
            if filters.get("ios_version"):
                q = q.filter(MDMDevice.ios_version >= filters["ios_version"])
            if filters.get("est_actif") is not None:
                q = q.filter(MDMDevice.est_actif == filters["est_actif"])
            if filters.get("type_appareil"):
                q = q.filter(MDMDevice.type_appareil == filters["type_appareil"])
            if filters.get("proprietaire"):
                q = q.filter(MDMDevice.proprietaire.ilike(f"%{filters['proprietaire']}%"))
            if filters.get("search"):
                s = filters["search"]
                q = q.filter(
                    MDMDevice.udid.ilike(f"%{s}%")
                    | MDMDevice.modele.ilike(f"%{s}%")
                    | MDMDevice.proprietaire.ilike(f"%{s}%")
                )
        total = q.count()
        q = q.order_by(MDMDevice.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        return q.all(), total

    def add(self, device: MDMDevice) -> MDMDevice:
        self.session.add(device)
        self.session.flush()
        return device

    def update(self, device: MDMDevice) -> MDMDevice:
        self.session.merge(device)
        self.session.flush()
        return device

    def delete(self, device_id: str) -> bool:
        d = self.get(device_id)
        if d:
            self.session.delete(d)
            self.session.flush()
            return True
        return False

    def stats(self) -> dict:
        from sqlalchemy import func
        total = self.session.query(func.count(MDMDevice.id)).scalar() or 0
        actif = self.session.query(func.count(MDMDevice.id)).filter(MDMDevice.est_actif == True).scalar() or 0
        capacite = self.session.query(func.sum(MDMDevice.capacite_go)).scalar() or 0
        utilise = self.session.query(func.sum(MDMDevice.stockage_utilise_go)).scalar() or 0
        models = self.session.query(MDMDevice.modele, func.count(MDMDevice.id).label("cnt")).group_by(MDMDevice.modele).all()
        ios_versions = self.session.query(MDMDevice.ios_version, func.count(MDMDevice.id).label("cnt")).group_by(MDMDevice.ios_version).all()
        return {
            "total_devices": total,
            "actifs": actif,
            "capacite_totale_go": float(capacite or 0),
            "utilise_go": float(utilise or 0),
            "models": {m: c for m, c in models},
            "ios_versions": {v: c for v, c in ios_versions},
        }
