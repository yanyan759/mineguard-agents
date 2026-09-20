"""工作流 Checkpoint 配置。

配置 REDIS_URL 时使用 RedisSaver（Redis 持久化检查点，
进程重启后同 thread_id 可恢复）；未配置时回退 InMemorySaver（开发环境）。

降级策略：
- REDIS_URL 未配置 → InMemorySaver（开发模式，重启后状态丢失）
- REDIS_URL 已配置但 Redis 连不上 → get_checkpointer 启动即抛错（不静默降级）
"""

import os

from langgraph.checkpoint.memory import InMemorySaver

_redis_saver = None   # 模块级缓存：避免每次编译工作流重复建索引


def get_checkpointer():
    """获取 Checkpoint 实例。

    - REDIS_URL 已配置：返回 RedisSaver（单例，setup 建索引 + 健康检查）
    - REDIS_URL 未配置：返回 InMemorySaver（开发环境）
    """
    global _redis_saver
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return InMemorySaver()
    if _redis_saver is None:
        # 用适配器而非原生 RedisSaver：读取时把自定义 Pydantic 模型信封
        # 还原为模型实例，保证 get_state 与重启续跑语义与 InMemory 一致
        from app.agent.redis_checkpoint import CoalMineRedisSaver

        try:
            # 构造器内 configure_client 会立即触发 client_setinfo 真实连接，
            # setup() 创建 RediSearch 索引（幂等，create overwrite=False）；
            # 两者一起作为启动健康检查：Redis 不可达时抛错，避免运行中途才发现
            saver = CoalMineRedisSaver(redis_url=redis_url)
            saver.setup()
        except Exception as exc:
            raise RuntimeError(
                f"Redis 检查点初始化失败（REDIS_URL={redis_url}）。"
                "请确认 agent1/deploy/redis 容器已启动。"
            ) from exc
        _redis_saver = saver
    return _redis_saver
