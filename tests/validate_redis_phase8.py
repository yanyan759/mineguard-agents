"""阶段 8 Redis 分布式检查点 + 分布式锁验收脚本。

运行：cd agent1 && python tests/validate_redis_phase8.py
前置：Redis 容器已启动（agent1/deploy/redis/docker-compose.yaml，端口 6380，密码 agent1_dev）

6 个用例：
  1. Redis 容器可达（PING = PONG）
  2. get_checkpointer 配置驱动（设 REDIS_URL → RedisSaver；未设 → InMemorySaver）
  3. 持久化 + 重启恢复（normal 场景跑完 → 清缓存新建 checkpointer 模拟重启 → 同 thread_id get_state 关键字段一致）
  4. 分布式锁（获取成功 / 未释放二次获取超时 / 释放后重取 / 短 TTL 自动过期）
  5. start() 防重（运行中同 run_id 复用同一记录；已完成允许覆盖新建）
  6. 全量回归（bootstrap 全链路 + validate_workflow 7 场景，均以 Redis 模式子进程运行）
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

# 导入设置：agent1/ 在 sys.path 上才能解析 from app.xxx
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 本脚本为 Redis 模式验收，默认注入 REDIS_URL（已配置则沿用）
REDIS_URL = os.environ.setdefault(
    "REDIS_URL", "redis://:agent1_dev@localhost:6380/0"
)

import redis  # noqa: E402

import app.agent.checkpoint as checkpoint_mod  # noqa: E402
from app.agent.checkpoint import get_checkpointer  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# 用例 1：Redis 容器可达
# ═══════════════════════════════════════════════════════════════════════════


def test_redis_reachable() -> str:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    assert client.ping() is True, f"Redis PING 失败: {REDIS_URL}"
    # MODULE LIST 返回 [{name, ver, path, args}, ...]，取各条 name 字段
    modules = client.execute_command("MODULE LIST")
    names = " ".join(
        str(m.get("name", "")) for m in modules if isinstance(m, dict)
    ).lower()
    assert "rejson" in names, "缺少 ReJSON（RedisJSON）模块，RedisSaver 无法存取检查点"
    assert "search" in names, "缺少 search（RediSearch）模块，RedisSaver 无法建索引"
    client.close()
    return "PING=PONG, 模块 ReJSON+search 就绪"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 2：get_checkpointer 配置驱动
# ═══════════════════════════════════════════════════════════════════════════


def test_checkpointer_config_driven() -> str:
    from langgraph.checkpoint.redis import RedisSaver

    saved = os.environ.get("REDIS_URL")
    try:
        # 已配置 → RedisSaver（含适配器子类 CoalMineRedisSaver）
        os.environ["REDIS_URL"] = REDIS_URL
        checkpoint_mod._redis_saver = None
        assert isinstance(get_checkpointer(), RedisSaver), "设 REDIS_URL 应返回 RedisSaver（或其子类）"

        # 未配置 → InMemorySaver
        os.environ.pop("REDIS_URL", None)
        checkpoint_mod._redis_saver = None
        assert type(get_checkpointer()).__name__ == "InMemorySaver", "未设 REDIS_URL 应返回 InMemorySaver"

        # 配置了但连不上 → RuntimeError（失败报错，不静默降级）
        os.environ["REDIS_URL"] = "redis://:bad@localhost:6999/0"
        checkpoint_mod._redis_saver = None
        try:
            get_checkpointer()
        except RuntimeError as exc:
            assert "Redis 检查点初始化失败" in str(exc)
        else:
            raise AssertionError("Redis 不可达时应抛 RuntimeError")
    finally:
        if saved is None:
            os.environ.pop("REDIS_URL", None)
        else:
            os.environ["REDIS_URL"] = saved
        checkpoint_mod._redis_saver = None  # 复位缓存，避免影响后续用例
    return "RedisSaver / InMemorySaver / 失败报错 三态正确"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 3：持久化 + 重启恢复
# ═══════════════════════════════════════════════════════════════════════════


def test_persistence_restart() -> str:
    from app.agent.fixture_loader import FixtureDataSource
    from app.agent.workflow import create_workflow
    from app.agent.workflow_state import new_workflow_state

    loader = FixtureDataSource()
    scenario = loader.load_scenario("normal")
    ctx = scenario.context.model_copy(
        update={"iteration_count": 0, "max_iterations": 2}
    )
    thread_id = f"redis-e2e-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    # 第 1 次运行：normal 场景 → perception_warning → END（无需审批）
    workflow = create_workflow()
    state = new_workflow_state(ctx, scenario_id="normal")
    for _ in workflow.stream(state, config):
        pass
    snap1 = workflow.get_state(config)
    final1 = snap1.values
    assert final1["fixture_scenario_id"] == "normal", "运行期 fixture_scenario_id 应为 normal"
    risk1 = final1["perception_result"].risk_level.value

    # 模拟进程重启：清模块缓存 → 新建 checkpointer → 同 thread_id 从 Redis 读回
    checkpoint_mod._redis_saver = None
    workflow2 = create_workflow()
    snap2 = workflow2.get_state(config)
    final2 = snap2.values
    assert final2["fixture_scenario_id"] == "normal", "重启后 fixture_scenario_id 应一致"
    # 适配器把 lc:2 信封还原为模型实例（与 InMemory 语义一致），而非 dict
    assert type(final2["perception_result"]).__name__ == "PerceptionWarningResult", (
        "重启后 perception_result 应还原为 PerceptionWarningResult 模型"
    )
    assert final2["perception_result"].risk_level.value == risk1, "重启后 risk_level 应一致"
    assert snap2.next == snap1.next, "重启后下个节点（END）应一致"
    return f"同 thread_id 重启恢复一致（risk={risk1}，模型已还原）"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 4：分布式锁
# ═══════════════════════════════════════════════════════════════════════════


def test_distributed_lock() -> str:
    from app.utils.redis_lock import distributed_lock

    # 4a. 正常获取 + 释放
    with distributed_lock("t:normal", timeout=2, ttl=5):
        pass

    # 4b. 未释放二次获取 → 阻塞超时抛 TimeoutError
    # （直接经 redis-py 持有锁，模拟其他持有者，避免 contextmanager 的 None yield）
    holder = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    held_lock = holder.lock("agent1:lock:t:held", timeout=5)
    held_lock.acquire()
    try:
        try:
            with distributed_lock("t:held", timeout=0.3, ttl=5):
                raise AssertionError("持锁未释放时二次获取应阻塞超时")
        except TimeoutError:
            pass
    finally:
        held_lock.release()
        holder.close()

    # 4c. 释放后可重取
    with distributed_lock("t:held", timeout=2, ttl=5):
        pass

    # 4d. 短 TTL 自动过期（持锁不释放，等过期后可重取）
    ttl_holder = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    ttl_lock = ttl_holder.lock("agent1:lock:t:ttl", timeout=1)
    ttl_lock.acquire()
    time.sleep(1.3)
    try:
        with distributed_lock("t:ttl", timeout=2, ttl=5):
            pass  # TTL 过期后原锁自动释放，此处可获取
    finally:
        ttl_holder.close()
    return "互斥/超时/释放重取/TTL 自动过期 4 态正确"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 5：start() 防重
# ═══════════════════════════════════════════════════════════════════════════


def test_start_dedup() -> str:
    from app.api.workflow_runner import WorkflowRunner, WorkflowRunRecord

    # 5a. running 状态同 run_id 二次 start → 复用同一记录（不新起线程）
    runner = WorkflowRunner()
    rec_running = WorkflowRunRecord(
        run_id="wf-dedup-running", thread_id="wf-dedup-running",
        scenario_id="normal", status="running",
    )
    runner._runs["wf-dedup-running"] = rec_running
    ret = runner.start(scenario_id="normal", run_id="wf-dedup-running")
    assert ret is rec_running, "运行中应复用原记录"
    assert ret.thread is None, "复用记录不应新起线程"

    # 5b. completed 状态同 run_id 二次 start → 允许覆盖（新建记录并驱动）
    runner2 = WorkflowRunner()
    rec_old = WorkflowRunRecord(
        run_id="wf-dedup-done", thread_id="wf-dedup-done",
        scenario_id="normal", status="completed",
    )
    runner2._runs["wf-dedup-done"] = rec_old
    rec_new = runner2.start(scenario_id="normal", run_id="wf-dedup-done")
    assert rec_new is not rec_old, "已完成应允许覆盖新建记录"
    assert rec_new.thread is not None, "新建记录应新起线程驱动"
    rec_new.thread.join(timeout=120)  # 等后台工作流跑完，避免残留线程
    return "运行中防重 + 已完成覆盖 正确"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 6：全量回归（Redis 模式子进程）
# ═══════════════════════════════════════════════════════════════════════════


def test_full_regression() -> str:
    env = os.environ.copy()
    env["REDIS_URL"] = REDIS_URL

    # 6a. bootstrap 全链路（导入 + 规则引擎）
    r1 = subprocess.run(
        [sys.executable, "bootstrap.py"], cwd=AGENT1_ROOT, env=env,
        capture_output=True, text=True, timeout=180,
    )
    assert r1.returncode == 0, f"bootstrap 失败: {r1.stderr[-500:]}"

    # 6b. validate_workflow 7 场景（Redis 模式）
    r2 = subprocess.run(
        [sys.executable, "tests/validate_workflow.py"], cwd=AGENT1_ROOT, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert r2.returncode == 0, f"validate_workflow 失败: {r2.stderr[-500:]}"
    return "bootstrap + validate_workflow(7 场景) 全过"


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 60)
    print("  阶段 8 — Redis 分布式检查点 + 分布式锁验收")
    print(f"  REDIS_URL = {REDIS_URL}")
    print("=" * 60)

    cases = [
        ("1. Redis 容器可达", test_redis_reachable),
        ("2. checkpointer 配置驱动", test_checkpointer_config_driven),
        ("3. 持久化 + 重启恢复", test_persistence_restart),
        ("4. 分布式锁", test_distributed_lock),
        ("5. start() 防重", test_start_dedup),
        ("6. 全量回归", test_full_regression),
    ]

    passed = 0
    failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"  [PASS] {name}  —  {detail}")
        except Exception as exc:  # noqa: BLE001 — 验收脚本聚合展示
            failed += 1
            print(f"  [FAIL] {name}  —  {type(exc).__name__}: {str(exc)[:200]}")

    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
