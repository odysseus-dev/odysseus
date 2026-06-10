import logging
from contextlib import asynccontextmanager
from src.mdm.infrastructure.database import MDMSessionLocal
from src.mdm.infrastructure.repositories.device_repo import DeviceRepository
from src.mdm.infrastructure.repositories.profile_repo import ProfileRepository
from src.mdm.infrastructure.repositories.enrollment_repo import EnrollmentRepository
from src.mdm.infrastructure.repositories.match_repo import MatchRepository

logger = logging.getLogger(__name__)


class UnitOfWork:
    def __init__(self):
        self.session = MDMSessionLocal()
        self.devices = DeviceRepository(self.session)
        self.profiles = ProfileRepository(self.session)
        self.enrollments = EnrollmentRepository(self.session)
        self.matches = MatchRepository(self.session)

    def commit(self):
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception:
                logger.exception("UoW commit failed")
                raise
        else:
            self.rollback()
        self.close()
