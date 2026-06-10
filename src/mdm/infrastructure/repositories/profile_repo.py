from typing import Optional, List
from sqlalchemy.orm import Session
from src.mdm.infrastructure.models import MDMProfile


class ProfileRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, profile_id: str) -> Optional[MDMProfile]:
        return self.session.query(MDMProfile).filter(MDMProfile.id == profile_id).first()

    def list(self, filters: dict = None):
        q = self.session.query(MDMProfile)
        if filters:
            if filters.get("categorie"):
                q = q.filter(MDMProfile.categorie == filters["categorie"])
            if filters.get("est_actif") is not None:
                q = q.filter(MDMProfile.est_actif == filters["est_actif"])
            if filters.get("search"):
                s = filters["search"]
                q = q.filter(MDMProfile.nom.ilike(f"%{s}%") | MDMProfile.description.ilike(f"%{s}%"))
        return q.order_by(MDMProfile.created_at.desc()).all()

    def add(self, profile: MDMProfile) -> MDMProfile:
        self.session.add(profile)
        self.session.flush()
        return profile

    def update(self, profile: MDMProfile) -> MDMProfile:
        self.session.merge(profile)
        self.session.flush()
        return profile

    def delete(self, profile_id: str) -> bool:
        p = self.get(profile_id)
        if p:
            self.session.delete(p)
            self.session.flush()
            return True
        return False

    def get_compatible(self, ios_version: str) -> List[MDMProfile]:
        return self.session.query(MDMProfile).filter(
            MDMProfile.est_actif == True,
            (MDMProfile.ios_min_version == None) | (MDMProfile.ios_min_version <= ios_version),
        ).all()
