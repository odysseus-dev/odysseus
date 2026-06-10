import time
import logging
from collections import defaultdict

logger = logging.getLogger("mdm.ratelimit")


class MDMRateLimiter:
    _windows: dict[str, list[float]] = defaultdict(list)
    _limits = {
        "devices:write": (60, 30),
        "devices:read": (60, 100),
        "match": (60, 10),
        "capacity": (60, 20),
        "autofill": (60, 15),
        "import": (300, 5),
    }

    @classmethod
    def check(cls, key: str, action: str = "devices:read") -> bool:
        limit, window = cls._limits.get(action, (60, 30))
        now = time.time()
        cls._windows[key].append(now)
        cls._windows[key] = [t for t in cls._windows[key] if now - t < window]
        if len(cls._windows[key]) > limit:
            logger.warning("Rate limit exceeded for %s on %s", key, action)
            return False
        return True

    @classmethod
    def get_limits(cls) -> dict:
        return dict(cls._limits)
