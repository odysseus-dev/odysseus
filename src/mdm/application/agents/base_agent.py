import time
import logging
from abc import ABC, abstractmethod
from src.mdm.liquid_cache import LiquidCache
from src.mdm.middleware.audit_middleware import AuditMiddleware
from src.mdm.application.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    cache_pool: str = ""
    circuit_name: str = ""

    @abstractmethod
    async def execute(self, action: str, payload: dict):
        pass

    async def handle(self, action: str, payload: dict, user: str = None):
        entry = await AuditMiddleware.log(self.__class__.__name__, action, payload, user)
        start = time.time()
        try:
            cache_key = f"{action}:{hash(frozenset(payload.items()))}" if payload else action
            if action.startswith("get_"):
                cached = await LiquidCache.get(self.cache_pool, cache_key)
                if cached is not None:
                    entry.complete((time.time() - start) * 1000)
                    return cached
            if self.circuit_name:
                result = await CircuitBreaker.call(self.circuit_name, lambda: self.execute(action, payload))
            else:
                result = await self.execute(action, payload)
            if action.startswith("get_") and result is not None:
                await LiquidCache.set(self.cache_pool, cache_key, result)
            entry.complete((time.time() - start) * 1000)
            return result
        except Exception as e:
            entry.complete((time.time() - start) * 1000)
            logger.exception("Agent %s action %s failed: %s", self.__class__.__name__, action, e)
            raise
