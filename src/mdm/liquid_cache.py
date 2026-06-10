import asyncio
import time
import logging

logger = logging.getLogger(__name__)


class LiquidCache:
    _pools: dict[str, dict] = {}
    _ttls: dict[str, dict] = {}
    _stats: dict[str, dict] = {}
    _lock = asyncio.Lock()

    POOLS = {
        "devices": {"ttl": 300, "desc": "Cache devices"},
        "profiles": {"ttl": 600, "desc": "Cache profils"},
        "matches": {"ttl": 120, "desc": "Cache résultats matching"},
        "capacity": {"ttl": 60, "desc": "Cache calculs capacité"},
        "stats": {"ttl": 30, "desc": "Cache stats dashboard"},
        "explorer": {"ttl": 15, "desc": "Cache listes explorateur"},
    }

    @classmethod
    def _ensure_pool(cls, pool: str):
        if pool not in cls._pools:
            cls._pools[pool] = {}
            cls._ttls[pool] = {}
            cls._stats[pool] = {"hits": 0, "misses": 0, "evictions": 0}

    @classmethod
    async def get(cls, pool: str, key: str):
        async with cls._lock:
            cls._ensure_pool(pool)
            now = time.time()
            expiry = cls._ttls[pool].get(key)
            if expiry is not None and now > expiry:
                del cls._pools[pool][key]
                del cls._ttls[pool][key]
                cls._stats[pool]["evictions"] += 1
                cls._stats[pool]["misses"] += 1
                return None
            val = cls._pools[pool].get(key)
            if val is not None:
                cls._stats[pool]["hits"] += 1
                return val
            cls._stats[pool]["misses"] += 1
            return None

    @classmethod
    async def set(cls, pool: str, key: str, value, ttl: int = None):
        async with cls._lock:
            cls._ensure_pool(pool)
            if ttl is None:
                ttl = cls.POOLS.get(pool, {}).get("ttl", 300)
            cls._pools[pool][key] = value
            cls._ttls[pool][key] = time.time() + ttl

    @classmethod
    async def delete(cls, pool: str, key: str):
        async with cls._lock:
            cls._ensure_pool(pool)
            cls._pools[pool].pop(key, None)
            cls._ttls[pool].pop(key, None)

    @classmethod
    async def invalidate_pool(cls, pool: str):
        async with cls._lock:
            if pool in cls._pools:
                cls._pools[pool].clear()
                cls._ttls[pool].clear()
                logger.debug("LiquidCache invalidated pool: %s", pool)

    @classmethod
    async def invalidate_all(cls):
        async with cls._lock:
            for pool in cls._pools:
                cls._pools[pool].clear()
                cls._ttls[pool].clear()
            logger.debug("LiquidCache all pools invalidated")

    @classmethod
    async def warmup(cls, pool: str, data: dict, ttl: int = None):
        async with cls._lock:
            cls._ensure_pool(pool)
            ttl = ttl or cls.POOLS.get(pool, {}).get("ttl", 300)
            now = time.time()
            for key, value in data.items():
                cls._pools[pool][key] = value
                cls._ttls[pool][key] = now + ttl
            logger.info("LiquidCache warmed pool '%s' with %d entries", pool, len(data))

    @classmethod
    async def get_stats(cls) -> dict:
        async with cls._lock:
            result = {}
            for pool, stats in cls._stats.items():
                pool_size = len(cls._pools.get(pool, {}))
                result[pool] = {**stats, "size": pool_size}
            return result
