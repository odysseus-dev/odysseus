import asyncio
from concurrent.futures import ThreadPoolExecutor
from src.mdm.infrastructure.database import MDMSessionLocal


class LiquidPool:
    _thread_pool: ThreadPoolExecutor = None
    _semaphore: asyncio.Semaphore = None
    _pool_size: int = 10

    @classmethod
    def init(cls, pool_size: int = 10, max_workers: int = 4):
        cls._pool_size = pool_size
        cls._semaphore = asyncio.Semaphore(pool_size)
        cls._thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mdm-cpu")

    @classmethod
    async def get_db(cls):
        async with cls._semaphore:
            return MDMSessionLocal()

    @classmethod
    async def run_cpu(cls, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(cls._thread_pool, lambda: fn(*args, **kwargs))

    @classmethod
    async def gather(cls, *tasks):
        return await asyncio.gather(*tasks, return_exceptions=True)

    @classmethod
    async def shutdown(cls):
        if cls._thread_pool:
            cls._thread_pool.shutdown(wait=False)
