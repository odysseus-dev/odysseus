import asyncio
import logging

logger = logging.getLogger(__name__)


class RetryPolicy:
    def __init__(self, max_retries: int = 3, base_delay: float = 0.5, max_delay: float = 10):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def execute(self, fn, *args, **kwargs):
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.warning("Retry %d/%d for %s in %.1fs: %s", attempt + 1, self.max_retries, fn.__name__, delay, e)
                    await asyncio.sleep(delay)
        raise last_exc
