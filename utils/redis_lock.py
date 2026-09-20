"""分布式锁工具（阶段 8，最小化）。

基于 redis-py 内置 Lock（SET NX PX + Lua 原子释放），跨线程/跨进程互斥。
REDIS_URL 未配置时 no-op（与 InMemory 开发模式一致）。

用法：
    with distributed_lock("run:abc", timeout=5, ttl=60):
        # 临界区
"""

import contextlib
import os

import redis


@contextlib.contextmanager
def distributed_lock(key: str, timeout: float = 5, ttl: float = 30):
    """获取分布式锁（阻塞等待 timeout 秒；持锁 ttl 秒自动释放防死锁）。

    :param key: 锁键（自动加 agent1:lock: 前缀）
    :param timeout: 阻塞获取超时（秒），超时抛 TimeoutError
    :param ttl: 锁自动过期（秒），防持有者崩溃导致死锁
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        # 开发模式（无 Redis）：no-op，不强制分布式互斥
        yield
        return
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        lock = client.lock(f"agent1:lock:{key}", timeout=ttl)
        # Redis 是六 Agent 多进程运行的必需依赖；不可用时必须显式失败，禁止静默降级。
        acquired = lock.acquire(blocking=True, blocking_timeout=timeout)
        if not acquired:
            raise TimeoutError(f"获取分布式锁超时: agent1:lock:{key}")
        try:
            yield
        finally:
            lock.release()
    finally:
        client.close()
