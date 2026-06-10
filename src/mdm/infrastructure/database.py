import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

MDMBase = declarative_base()

MDM_DATABASE_URL = os.getenv("MDM_DATABASE_URL", "sqlite:///./data/mdm/mdm.db")

mdm_engine = create_engine(
    MDM_DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in MDM_DATABASE_URL else {},
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

MDMSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=mdm_engine)


def init_mdm_db():
    from src.mdm.infrastructure.models import MDMDevice, MDMProfile, MDMEnrollment, MDMProfileAttribute, MDMCapacityLog, MDMMatchResult
    MDMBase.metadata.create_all(bind=mdm_engine)


def get_mdm_db():
    db = MDMSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_mdm_db_sync():
    return MDMSessionLocal()
