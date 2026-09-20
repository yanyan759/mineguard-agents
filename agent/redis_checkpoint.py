"""RedisSaver 适配：读取检查点时还原自定义 Pydantic 模型。

背景：langgraph-checkpoint-redis 的 JsonPlusRedisSerializer（orjson 路径）
只还原 LangChain 对象 / dataclass / Interrupt / Send。项目自定义的
Pydantic 模型（app.schemas.workflow 下的六 Agent 结果与上下文模型）以
{'lc':2,'type':'constructor','id':[模块,类名],'kwargs':{...}} 信封 dict
形式存于 Redis。若不做还原：

- 进程重启后 get_state 返回 dict 信封而非模型实例（与 InMemory 语义不一致）
- 重启续跑时，节点收到 dict 信封，访问 .risk_level 等属性抛 AttributeError
  （node_dispatch_decision 等节点即为此类访问），"重启可恢复"名存实亡

本适配仅在读路径（get_tuple / aget_tuple）对受信任 schema 模块的信封做
递归还原，写入仍走原 RedisSaver 序列化，不改六 Agent 节点与编排。
"""

from __future__ import annotations

import asyncio
import importlib

from langgraph.checkpoint.redis import RedisSaver

# 受信任 schema 模块：仅还原本项目自定义模型（安全边界，避免任意类构造）
_TRUSTED_SCHEMA_MODULE = "app.schemas.workflow"


def _revive_envelope(value):
    """递归把 Redis 检查点中的 lc:2 构造器信封还原为 Pydantic 模型。

    匹配规则：dict 且 {'lc':2,'type':'constructor','id':[...],'kwargs':{...}}，
    且 id 指向受信任 schema 模块 → importlib 导入类并 cls(**kwargs) 构造
    （kwargs 中嵌套信封先递归还原，枚举/日期等纯值由 pydantic 自动校正）。
    不匹配或构造失败 → 保持原值，绝不静默丢数据。
    """
    if isinstance(value, dict):
        # 先递归还原子结构（嵌套模型信封、kwargs 内的嵌套信封）
        revived = {k: _revive_envelope(v) for k, v in value.items()}
        if (
            revived.get("lc") == 2
            and revived.get("type") == "constructor"
            and isinstance(revived.get("id"), list)
            and len(revived["id"]) >= 2
            and ".".join(revived["id"][:-1]) == _TRUSTED_SCHEMA_MODULE
        ):
            cls_name = revived["id"][-1]
            try:
                module = importlib.import_module(_TRUSTED_SCHEMA_MODULE)
                cls = getattr(module, cls_name)
                return cls(**revived.get("kwargs", {}))
            except Exception:
                # 构造失败：保持信封 dict，避免异常吞掉整个检查点
                return revived
        return revived
    if isinstance(value, list):
        return [_revive_envelope(v) for v in value]
    return value


class CoalMineRedisSaver(RedisSaver):
    """RedisSaver 适配：读取检查点时把自定义 Pydantic 模型信封还原为模型实例。

    仅覆盖读路径（get_tuple / aget_tuple）；写入仍走原 RedisSaver 序列化。
    这样 get_state 语义与 InMemorySaver 一致（返回模型实例），
    进程重启后同 thread_id 续跑时节点也能拿到真正的模型对象。
    """

    def get_tuple(self, config):
        tup = super().get_tuple(config)
        if tup is not None:
            tup.checkpoint["channel_values"] = _revive_envelope(
                tup.checkpoint.get("channel_values", {})
            )
            if tup.checkpoint.get("pending_writes"):
                tup.checkpoint["pending_writes"] = [
                    (tid, task_id, channel, _revive_envelope(value))
                    for (tid, task_id, channel, value) in tup.checkpoint["pending_writes"]
                ]
        return tup

    # ═══════════════════════════════════════════════════════════════════════
    # 为异步工作流补全 Redis 检查点接口
    #
    # 背景：langgraph-checkpoint-redis 0.5.1 的 RedisSaver 只实现同步接口，
    # async 版本（aget_tuple/aput/alist 等）全部继承基类 BaseCheckpointSaver
    # 的 NotImplementedError。驱动层改 astream/aget_state 后，Redis 检查点
    # 在 async 路径必然抛错（实测空错误消息即来源）。故在此为 async 接口
    # 逐一提供 asyncio.to_thread 委托：异步调用 → 线程池跑同步实现 →
    # aget_tuple 复用上方同步适配（Pydantic 信封还原），语义与 InMemory 一致。
    # ═══════════════════════════════════════════════════════════════════════

    async def aget_tuple(self, config):
        # 复用同步 get_tuple（含 Pydantic 信封还原），经线程池执行避免阻塞事件循环
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def aget(self, config):
        return await asyncio.to_thread(self.get, config)

    async def alist(self, config=None, *, filter=None, before=None, limit=None):
        # 同步 list 返回生成器：线程池内转列表，再异步逐项产出
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aget_delta_channel_history(self, *, config, channels):
        return await asyncio.to_thread(
            self.get_delta_channel_history, config=config, channels=channels
        )

    async def aprune(self, thread_ids, *, strategy="keep_latest"):
        return await asyncio.to_thread(self.prune, thread_ids, strategy=strategy)

    async def acopy_thread(self, source_thread_id, target_thread_id):
        return await asyncio.to_thread(self.copy_thread, source_thread_id, target_thread_id)

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)

    async def adelete_for_runs(self, run_ids):
        return await asyncio.to_thread(self.delete_for_runs, run_ids)
