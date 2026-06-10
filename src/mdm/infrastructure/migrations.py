import logging
from src.mdm.infrastructure.database import mdm_engine, MDMBase

logger = logging.getLogger(__name__)


def run_migrations():
    logger.info("MDM: Running schema migrations...")
    MDMBase.metadata.create_all(bind=mdm_engine)
    logger.info("MDM: Schema up to date.")
