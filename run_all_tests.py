"""agent1 六智能体系统 — 一键全量验收套件运行器（跨平台，Windows/Linux 通用）。

用途：
  在 agent1/ 目录下执行 `python run_all_tests.py`，自动运行全部验收套件，
  按"离线可复现 / 需 LLM Key / 需容器"三档归类，输出汇总表，并把每套件
  原始输出落盘到 `运行验证/run_<时间戳>/`（供报告取证）。

约定：
  1. 离线套件（不依赖外部密钥与容器）是本运行器的硬性契约：
     全部通过 → 退出码 0；任一失败 → 退出码 1。
  2. 需 LLM Key / 容器 的套件：当前环境不满足前置时如实标记为
     NEED_KEY / NEED_CONTAINER，不计入退出码判定（其通过条件见各套件
     输出的"前置"说明与《运行指南.md》）。
  3. 环境变量透传：LLM_NODES、REAL_CASES 等按套件自身约定设置。

示例：
  python run_all_tests.py            # 全量（含容器/密钥套件的如实状态）
  python run_all_tests.py --offline  # 仅跑离线套件（快速自检）
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# 运行器与各套件共享同一份 .env 视图（占位符/真实 key 的判定、子进程环境继承）
load_dotenv(find_dotenv())

ROOT = Path(__file__).resolve().parent
RUN_DIR = (ROOT.parent / "运行验证" / f"run_{time.strftime('%Y%m%d_%H%M%S')}").resolve()

# 套件定义：name → (命令参数, 档位, 期望说明)
# 档位：offline=离线可复现；key=需真实 LLM Key；container=需 Docker 容器
SUITES: list[tuple[str, list[str], str, str]] = [
    ("validate_fixtures",   ["tests/validate_fixtures.py"],   "offline",   "阶段1 数据契约 5/5"),
    ("validate_tools",      ["tests/validate_tools.py"],      "offline",   "阶段3 工具层 41/41"),
    ("validate_gap_closure",["tests/validate_gap_closure.py"],"offline",   "审计缺口结构性补齐 G1-G9 40/40"),
    ("validate_workflow",   ["tests/validate_workflow.py"],   "offline",   "阶段4 工作流 7 场景+6 路径"),
    ("validate_mcp_phase8", ["tests/validate_mcp_phase8.py"], "offline",   "阶段8 MCP 生产化 7/7"),
    ("validate_llm_nodes",  ["tests/validate_llm_nodes.py"],  "offline",   "阶段8 LLM 双模式(off) 10/10"),
    ("bootstrap",           ["bootstrap.py"],                 "key",       "全模块导入+规则引擎+LLM 连通冒烟"),
    ("validate_uie_phase8", ["tests/validate_uie_phase8.py"], "container", "阶段8 UIE：用例4 写回需 Neo4j；用例5 需真实 Key"),
    ("validate_mysql_phase7",["tests/validate_mysql_phase7.py"],"container","阶段7 MySQL 6 张表+只读白名单"),
    ("validate_redis_phase8",["tests/validate_redis_phase8.py"],"container","阶段8 Redis 检查点+分布式锁"),
    ("validate_neo4j_phase8",["tests/validate_neo4j_phase8.py"],"container","阶段8 Neo4j 知识图谱 12/12"),
    ("validate_real_data",  ["tests/validate_real_data.py"],  "container", "阶段8 真实数据域 R1-R10"),
    ("validate_deliverable",["../任务成果/validate成果.py"],  "offline",   "任务成果数据包最低交付契约"),
]

# 容器端口探测：用于在汇总表中标注"容器未启动"
PORT_PROBES = {
    "Redis": 6380,
    "Neo4j": 7687,
    "MySQL": 3307,
}


def _port_open(port: int) -> bool:
    """快速探测本机端口是否可连接（用于标注容器状态，不阻塞主流程）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except OSError:
        return False


def _env_with(name: str, extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(extra)
    return env


def _run_suite(name: str, argv: list[str]) -> int:
    """运行单个套件并落盘输出；返回退出码（-1 超时，-2 启动失败）。"""
    out_file = RUN_DIR / f"{name}.out"
    with out_file.open("w", encoding="utf-8") as fh:
        try:
            proc = subprocess.run(
                [sys.executable, *argv],
                cwd=ROOT,
                env=_env_with(name, {"LLM_NODES": "off"} if name == "validate_llm_nodes" else {}),
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
            return proc.returncode
        except subprocess.TimeoutExpired:
            fh.write("\n[run_all_tests] 套件超时（>30min），已中止\n")
            return -1
        except Exception as exc:  # 子进程启动失败（如缺解释器）
            fh.write(f"\n[run_all_tests] 启动失败: {exc}\n")
            return -2


def _is_placeholder() -> bool:
    """判断 OPENAI_API_KEY 是否为副本占位符（占位符下 LLM 真实调用必然 401）。"""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    return "REPLACE" in key or key.startswith("sk-placeholder")


def main() -> int:
    parser = argparse.ArgumentParser(description="agent1 一键全量验收套件运行器")
    parser.add_argument("--offline", action="store_true", help="仅运行离线可复现套件")
    parser.add_argument("--list", action="store_true", help="列出全部套件及档位后退出")
    args = parser.parse_args()

    if args.list:
        print(f"{'套件':<26}{'档位':<12}期望")
        for name, argv, cat, note in SUITES:
            print(f"{name:<26}{cat:<12}{note}")
        return 0

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 72)
    print("  agent1 六智能体系统 — 一键全量验收")
    print(f"  输出目录: {RUN_DIR}")
    print(f"  Python: {sys.version.split()[0]}  |  PYTHONDONTWRITEBYTECODE 保护")
    print("=" * 72)

    # 容器状态探测（仅信息标注）
    container_up = {k: _port_open(v) for k, v in PORT_PROBES.items()}
    up_names = [k for k, up in container_up.items() if up]
    status_parts = []
    for k, up in container_up.items():
        status_parts.append(k + (":UP" if up else ":down"))
    print("  容器探测: " + "、".join(status_parts))
    if not up_names:
        print("  提示: 当前无容器运行 → 容器类套件将按『需容器』标注（离线套件不受影响）")
    print()

    results: list[tuple[str, str, str, str]] = []
    offline_failed = False

    for name, argv, cat, note in SUITES:
        if args.offline and cat != "offline":
            results.append((name, cat, "SKIP", "仅离线模式"))
            continue
        print(f"  ▶ {name:<24} [{cat}] ...", end=" ", flush=True)
        code = _run_suite(name, argv)

        if cat == "offline":
            ok = code == 0
            if not ok:
                offline_failed = True
            verdict = "PASS" if ok else "FAIL"
            results.append((name, cat, verdict, note))
            print(verdict)
        elif cat == "key":
            placeholder = _is_placeholder()
            if placeholder:
                verdict = "NEED_KEY"  # 占位符下 LLM 真实调用必然失败，如实标注
                note += "（当前 OPENAI_API_KEY 为占位符）"
            else:
                verdict = "PASS" if code == 0 else "FAIL"
            results.append((name, cat, verdict, note))
            print(verdict)
        else:  # container
            need = [k for k, up in container_up.items() if not up]
            if need:
                verdict = "NEED_CONTAINER"
                note += f"（缺容器: {', '.join(need)}）"
            else:
                verdict = "PASS" if code == 0 else "FAIL"
            results.append((name, cat, verdict, note))
            print(verdict)

    # 汇总表
    print("\n" + "=" * 72)
    print(f"  汇总（共 {len(results)} 套件，输出见 {RUN_DIR}）")
    print("=" * 72)
    print(f"  {'套件':<26}{'档位':<12}{'结论':<16}说明")
    print("  " + "-" * 66)
    for name, cat, verdict, note in results:
        print(f"  {name:<26}{cat:<12}{verdict:<16}{note}")
    print()

    offline_count = sum(1 for _, c, v, _ in results if c == "offline" and v in ("PASS", "FAIL"))
    offline_pass = sum(1 for _, c, v, _ in results if c == "offline" and v == "PASS")
    print(f"  离线套件契约: {offline_pass}/{offline_count} 通过"
          + ("  → ✅ 满足（退出码 0）" if not offline_failed else "  → ❌ 存在失败（退出码 1）"))
    print("  提示: NEED_KEY / NEED_CONTAINER 为当前环境前置不满足的如实标注，"
          "补齐前置（见《运行指南.md》）后重跑即可验证")
    return 1 if offline_failed else 0


if __name__ == "__main__":
    sys.exit(main())
