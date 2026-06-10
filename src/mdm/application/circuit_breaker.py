import time
import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    _states: dict[str, str] = {}
    _failures: dict[str, int] = {}
    _last_fail: dict[str, float] = {}
    THRESHOLD = 5
    TIMEOUT = 30

    @classmethod
    def _get_state(cls, name: str) -> str:
        state = cls._states.get(name, "CLOSED")
        if state == "OPEN":
            if time.time() - cls._last_fail.get(name, 0) > cls.TIMEOUT:
                cls._states[name] = "HALF_OPEN"
                return "HALF_OPEN"
        return state

    @classmethod
    async def call(cls, name: str, fn, fallback=None):
        state = cls._get_state(name)
        if state == "OPEN":
            logger.warning("Circuit %s is OPEN, using fallback", name)
            if fallback:
                return await fallback() if hasattr(fallback, '__call__') else fallback
            raise Exception(f"Circuit {name} is OPEN")
        try:
            result = await fn()
            cls._states[name] = "CLOSED"
            cls._failures[name] = 0
            return result
        except Exception as e:
            cls._failures[name] = cls._failures.get(name, 0) + 1
            cls._last_fail[name] = time.time()
            if cls._failures[name] >= cls.THRESHOLD:
                cls._states[name] = "OPEN"
                logger.error("Circuit %s OPEN after %d failures", name, cls._failures[name])
            raise
