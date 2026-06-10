import time
import logging
from src.mdm.liquid_cache import LiquidCache
from src.mdm.liquid_pool import LiquidPool
from src.mdm.application.agents.device_agent import DeviceAgent
from src.mdm.application.agents.profile_agent import ProfileAgent
from src.mdm.application.agents.cantique_agent import CantiqueAgent
from src.mdm.application.agents.capacity_agent import CapacityAgent
from src.mdm.application.agents.autofill_agent import AutofillAgent
from src.mdm.application.flows.mdm_flow import MDMFlow
from src.mdm.meta import MetaBrain

logger = logging.getLogger(__name__)


class MDMOrchestrator:
    def __init__(self):
        self.device_agent = DeviceAgent()
        self.profile_agent = ProfileAgent()
        self.cantique_agent = CantiqueAgent()
        self.capacity_agent = CapacityAgent()
        self.autofill_agent = AutofillAgent()
        self.flow = MDMFlow(self)
        self.brain = MetaBrain()

    async def init(self):
        LiquidPool.init(pool_size=10, max_workers=4)
        from src.mdm.infrastructure.migrations import run_migrations
        run_migrations()
        await self.brain.history.load("data/mdm/meta_history.json")
        self.brain.update_policies()
        logger.info("MDMOrchestrator initialized")

    async def warmup(self):
        from src.mdm.infrastructure.database import MDMSessionLocal
        from src.mdm.infrastructure.models import MDMDevice
        db = MDMSessionLocal()
        try:
            devices = db.query(MDMDevice).limit(100).all()
            if devices:
                warmup_data = {d.id: {"id": d.id, "udid": d.udid, "modele": d.modele} for d in devices}
                await LiquidCache.warmup("devices", warmup_data, ttl=300)
                logger.info("MDM cache warmed with %d devices", len(devices))
        finally:
            db.close()

    async def shutdown(self):
        self.brain.history.persist("data/mdm/meta_history.json")
        await LiquidCache.invalidate_all()
        await LiquidPool.shutdown()
        logger.info("MDMOrchestrator shut down")

    async def dispatch(self, agent_name: str, action: str, payload: dict = None, user: str = None):
        agents = {
            "device": self.device_agent,
            "profile": self.profile_agent,
            "cantique": self.cantique_agent,
            "capacity": self.capacity_agent,
            "autofill": self.autofill_agent,
        }
        agent = agents.get(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' not found. Available: {list(agents.keys())}")
        t0 = time.time()
        try:
            result = await agent.handle(action, payload or {}, user)
            duration_ms = round((time.time() - t0) * 1000, 1)
            self.brain.observe_step(agent_name, action, {"status": "success"}, duration_ms)
            return result
        except Exception as e:
            duration_ms = round((time.time() - t0) * 1000, 1)
            self.brain.observe_step(agent_name, action, {"status": "error", "error": str(e)}, duration_ms)
            raise

    async def health(self) -> dict:
        cache_stats = await LiquidCache.get_stats()
        brain_insights = self.brain.export_insights()
        return {
            "status": "healthy",
            "agents": ["device", "profile", "cantique", "capacity", "autofill"],
            "cache": cache_stats,
            "brain": {
                "policies": brain_insights["current_policies"],
                "stats": brain_insights["summary"],
            },
        }
