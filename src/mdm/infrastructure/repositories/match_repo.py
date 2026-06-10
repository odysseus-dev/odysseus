from typing import Optional, List
from sqlalchemy.orm import Session
from src.mdm.infrastructure.models import MDMMatchResult


class MatchRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, match_id: str) -> Optional[MDMMatchResult]:
        return self.session.query(MDMMatchResult).filter(MDMMatchResult.id == match_id).first()

    def list_by_device(self, device_id: str) -> List[MDMMatchResult]:
        return self.session.query(MDMMatchResult).filter(MDMMatchResult.device_id == device_id).order_by(MDMMatchResult.score.desc()).all()

    def list_top(self, limit: int = 10) -> List[MDMMatchResult]:
        return self.session.query(MDMMatchResult).filter(MDMMatchResult.est_applique == False).order_by(MDMMatchResult.score.desc()).limit(limit).all()

    def add(self, match: MDMMatchResult) -> MDMMatchResult:
        self.session.add(match)
        self.session.flush()
        return match

    def add_batch(self, matches: List[MDMMatchResult]) -> List[MDMMatchResult]:
        for m in matches:
            self.session.add(m)
        self.session.flush()
        return matches

    def mark_applied(self, match_id: str) -> bool:
        m = self.get(match_id)
        if m:
            m.est_applique = True
            self.session.flush()
            return True
        return False

    def delete_old(self, before) -> int:
        deleted = self.session.query(MDMMatchResult).filter(MDMMatchResult.matched_at < before).delete()
        self.session.flush()
        return deleted
