from typing import Optional, List
from sqlalchemy.orm import Session
from src.mdm.infrastructure.models import MDMEnrollment


class EnrollmentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, enrollment_id: str) -> Optional[MDMEnrollment]:
        return self.session.query(MDMEnrollment).filter(MDMEnrollment.id == enrollment_id).first()

    def list_by_device(self, device_id: str) -> List[MDMEnrollment]:
        return self.session.query(MDMEnrollment).filter(MDMEnrollment.device_id == device_id).all()

    def list_by_profile(self, profile_id: str) -> List[MDMEnrollment]:
        return self.session.query(MDMEnrollment).filter(MDMEnrollment.profile_id == profile_id).all()

    def list_by_statut(self, statut: str) -> List[MDMEnrollment]:
        return self.session.query(MDMEnrollment).filter(MDMEnrollment.statut == statut).all()

    def add(self, enrollment: MDMEnrollment) -> MDMEnrollment:
        self.session.add(enrollment)
        self.session.flush()
        return enrollment

    def update(self, enrollment: MDMEnrollment) -> MDMEnrollment:
        self.session.merge(enrollment)
        self.session.flush()
        return enrollment

    def delete(self, enrollment_id: str) -> bool:
        e = self.get(enrollment_id)
        if e:
            self.session.delete(e)
            self.session.flush()
            return True
        return False

    def find(self, device_id: str, profile_id: str) -> Optional[MDMEnrollment]:
        return self.session.query(MDMEnrollment).filter(
            MDMEnrollment.device_id == device_id,
            MDMEnrollment.profile_id == profile_id,
        ).first()
