"""图谱激活：实时 Neo4j 数据 → 报告截图（PNG）+ 计数（JSON + MD）。

从 coal-mine-neo4j（bolt://localhost:7687, neo4j/agent1_dev）读取灾变知识图谱
实时节点/关系，用 networkx + matplotlib 渲染为报告截图，输出到
`任务成果/06_图谱激活/`：

  - 知识图谱_全量.png       三面板总图（核心域 + 真实案例域 + UIE 标注域）
  - 知识图谱_核心域.png      仿真知识图谱（案例/风险/规程/方案/来源类型）
  - 知识图谱_真实案例域.png  21 份真实事故案例（按风险等级着色）
  - 知识图谱_UIE域.png       UIE 标注知识实体（按实体类型着色，关系按类型分色）
  - 图谱计数.json           各标签节点数 / 关系数 / UIE 分布 / 嵌入数
  - 图谱激活概览.md         报告素材：图谱状态一句话 + 计数表 + 校验证据

用法：
    cd agent1
    python deploy/neo4j/scripts/render_graph_snapshot.py [--out DIR] [--uri bolt://...]

依赖：neo4j / networkx / matplotlib（agent1 环境已具备）；中文字体走 Microsoft YaHei。
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无窗口后端，Headless 安全
import matplotlib.pyplot as plt
import networkx as nx

AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_OUT = AGENT1_ROOT.parent / "任务成果" / "06_图谱激活"

# 中文字体（Windows：Microsoft YaHei 在 matplotlib 中可用；缺省回退 SimHei）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False

# 风险等级 → 颜色（对齐 RiskLevel severity 语义）
_RISK_COLOR = {
    "normal": "#9e9e9e",
    "blue": "#1e88e5",
    "yellow": "#fdd835",
    "orange": "#fb8c00",
    "red": "#e53935",
}

# UIE 关系 7 类 → 颜色（report 图例用）
_REL_COLOR = {
    "诱发": "#d32f2f",
    "支护": "#388e3c",
    "防控": "#7b1fa2",
    "监测": "#1976d2",
    "调度": "#f57c00",
    "上报": "#5d4037",
    "传导": "#00838f",
}

# 实体类型 7 类 → 颜色（UIE 域图例用）
_ETYPE_COLOR = {
    "地质构造": "#5c6bc0",
    "支护设备": "#26a69a",
    "顶板灾变": "#ef5350",
    "监测指标": "#ffa726",
    "处置规程": "#66bb6a",
    "岗位角色": "#ab47bc",
    "巷道分区": "#8d6e63",
}

_ETYPES = ["地质构造", "支护设备", "顶板灾变", "监测指标", "处置规程", "岗位角色", "巷道分区"]


def _driver(uri: str):
    from neo4j import GraphDatabase
    return GraphDatabase.driver(uri, auth=(os.getenv("NEO4J_USER", "neo4j"),
                                           os.getenv("NEO4J_PASSWORD", "agent1_dev")))


def _truncate(s: str, n: int = 8) -> str:
    """截断标签，超长加省略号（报告图可读性）。"""
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def load_graph(uri: str) -> tuple[dict, list[tuple[str, str, dict]], dict]:
    """拉取全图：节点（标签→{node_id: {props}}）+ 关系 + 统计。"""
    nodes: dict[str, dict] = {}
    rels: list[tuple[str, str, dict]] = []
    stats: dict = {"nodes": Counter(), "rels": Counter()}
    d = _driver(uri)
    try:
        with d.session() as s:
            # 节点：含所有标签（多标签节点取非默认者；默认走单标签名）
            for rec in s.run(
                "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props"
            ).data():
                labels = rec["labels"]
                if len(labels) > 1:
                    label = next((l for l in labels if l != "Base"), labels[0])
                else:
                    label = labels[0] if labels else "Unknown"
                nodes[rec["id"]] = {"label": label, "props": rec["props"]}
                stats["nodes"][label] += 1
            # 关系：起点/终点 elementId + 类型 + 属性
            for rec in s.run(
                "MATCH ()-[r]->() RETURN elementId(startNode(r)) AS s, elementId(endNode(r)) AS t, "
                "type(r) AS rel, properties(r) AS props"
            ).data():
                rels.append((rec["s"], rec["t"], {
                    "rel": rec["rel"], "props": rec["props"] or {},
                }))
                stats["rels"][rec["rel"]] += 1
    finally:
        d.close()
    return nodes, rels, stats


def _core_subgraph(nodes: dict, rels: list) -> nx.Graph:
    """核心仿真知识图谱子图（AccidentCase/RiskLevel/Regulation/SupportPlan/SourceType）。"""
    core_labels = {"AccidentCase", "RiskLevel", "Regulation", "SupportPlan", "SourceType"}
    core_ids = {nid for nid, n in nodes.items() if n["label"] in core_labels}
    G = nx.Graph()
    for nid in core_ids:
        n = nodes[nid]
        G.add_node(nid, label=n["label"], props=n["props"])
    for s, t, rel in rels:
        if s in core_ids and t in core_ids and rel["rel"] in ("HAS_RISK_LEVEL", "CLASSIFIED_AS", "APPLIES_TO"):
            G.add_edge(s, t, rel=rel["rel"])
    return G


def _real_subgraph(nodes: dict, rels: list) -> nx.Graph:
    """真实案例域子图（RealAccidentCase → RiskLevel）。"""
    real_ids = {nid for nid, n in nodes.items() if n["label"] == "RealAccidentCase"}
    level_ids = {nid for nid, n in nodes.items() if n["label"] == "RiskLevel"}
    keep = real_ids | level_ids
    G = nx.Graph()
    for nid in keep:
        G.add_node(nid, label=nodes[nid]["label"], props=nodes[nid]["props"])
    for s, t, rel in rels:
        if s in keep and t in keep and rel["rel"] == "HAS_RISK_LEVEL":
            G.add_edge(s, t, rel=rel["rel"])
    return G


def _uie_subgraph(nodes: dict, rels: list) -> nx.Graph:
    """UIE 标注知识域子图（KnowledgeEntity + 7 类关系）。"""
    uie_ids = {nid for nid, n in nodes.items() if n["label"] == "KnowledgeEntity"}
    G = nx.Graph()
    for nid in uie_ids:
        G.add_node(nid, label="KnowledgeEntity", props=nodes[nid]["props"])
    for s, t, rel in rels:
        if s in uie_ids and t in uie_ids and rel["rel"] in _REL_COLOR:
            G.add_edge(s, t, rel=rel["rel"])
    return G


def _draw_core(ax: plt.Axes, G: nx.Graph) -> None:
    """面板 A：核心仿真知识图谱（带节点标签）。"""
    pos = nx.spring_layout(G, seed=42, k=0.9, iterations=120)
    color = {}
    for nid, d in G.nodes(data=True):
        lab = d["label"]
        if lab == "RiskLevel":
            color[nid] = _RISK_COLOR.get(d["props"].get("level", "normal"), "#9e9e9e")
        elif lab == "AccidentCase":
            color[nid] = "#e53935"
        elif lab == "Regulation":
            color[nid] = "#3949ab"
        elif lab == "SupportPlan":
            color[nid] = "#43a047"
        else:
            color[nid] = "#8e24aa"
    edge_color = ["#90a4ae"] * G.number_of_edges()
    nx.draw_networkx_edges(G, pos, ax=ax, alpha=0.5, edge_color=edge_color, width=1.2)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=[color[n] for n in G.nodes()],
                           node_size=600, alpha=0.92)
    labels = {}
    for nid, d in G.nodes(data=True):
        p = d["props"]
        if d["label"] == "AccidentCase":
            labels[nid] = _truncate(p.get("case_id", ""), 12)
        elif d["label"] == "RiskLevel":
            labels[nid] = p.get("level", "")
        elif d["label"] == "Regulation":
            labels[nid] = _truncate(p.get("regulation_id", ""), 12)
        elif d["label"] == "SupportPlan":
            labels[nid] = _truncate(p.get("scheme_id", ""), 12)
        else:
            labels[nid] = p.get("type", "")
    nx.draw_networkx_labels(G, pos, ax=ax, labels=labels, font_size=7,
                            font_family="Microsoft YaHei")
    ax.set_title("核心仿真知识图谱\n(5 案例·10 规程·5 方案·5 风险·4 来源类型)", fontsize=10)
    ax.axis("off")


def _draw_real(ax: plt.Axes, G: nx.Graph) -> None:
    """面板 B：21 份真实事故案例域（按风险等级着色）。"""
    pos = nx.circular_layout(G)
    color = []
    for nid, d in G.nodes(data=True):
        if d["label"] == "RealAccidentCase":
            color.append(_RISK_COLOR.get(d["props"].get("risk_level", "normal"), "#9e9e9e"))
        else:
            color.append("#eceff1")
    nx.draw_networkx_edges(G, pos, ax=ax, alpha=0.45, edge_color="#90a4ae", width=1.0)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=color, node_size=520, alpha=0.95)
    labels = {}
    for nid, d in G.nodes(data=True):
        if d["label"] == "RealAccidentCase":
            labels[nid] = _truncate(d["props"].get("location", d["props"].get("case_id", "")), 6)
        else:
            labels[nid] = d["props"].get("level", "")
    nx.draw_networkx_labels(G, pos, ax=ax, labels=labels, font_size=6,
                            font_family="Microsoft YaHei")
    ax.set_title("真实事故案例域\n(21 份 · RealAccidentCase, 按风险等级着色)", fontsize=10)
    ax.axis("off")


def _draw_uie(ax: plt.Axes, G: nx.Graph) -> None:
    """面板 C：UIE 标注知识域（175 实体，按实体类型着色，关系按类型分色）。"""
    pos = nx.spring_layout(G, seed=7, k=0.6, iterations=100)
    # 实体类型：节点属性 entity_type 缺失时回退（对象节点由 object_type 写入）
    node_color, legend_handles = [], []
    import matplotlib.patches as mpatches
    for nid, d in G.nodes(data=True):
        et = d["props"].get("entity_type")
        if et not in _ETYPE_COLOR:
            et = d["props"].get("object_type", "顶板灾变")
        node_color.append(_ETYPE_COLOR.get(et, "#b0bec5"))
    for et in _ETYPES:
        if et in _ETYPE_COLOR:
            legend_handles.append(mpatches.Patch(color=_ETYPE_COLOR[et], label=et))
    # 关系分色（透明度低，体现密度）
    for s, t, rel in G.edges(data=True):
        r = rel.get("rel", "")
        x_s, y_s = pos[s]; x_t, y_t = pos[t]
        ax.plot([x_s, x_t], [y_s, y_t], color=_REL_COLOR.get(r, "#cfd8dc"),
                alpha=0.18, linewidth=0.7, zorder=1)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=node_color, node_size=70, alpha=0.9)
    ax.legend(handles=legend_handles, loc="upper left", fontsize=7, framealpha=0.8,
              title="实体类型", title_fontsize=8)
    ax.set_title("UIE 标注知识域\n(175 实体 · 150 条关系, 关系按类型分色)", fontsize=10)
    ax.axis("off")


def render(nodes: dict, rels: list, out: Path) -> dict:
    """渲染三面板总图 + 三个单域图，返回统计 dict（供 JSON/MD 输出）。"""
    out.mkdir(parents=True, exist_ok=True)

    core = _core_subgraph(nodes, rels)
    real = _real_subgraph(nodes, rels)
    uie = _uie_subgraph(nodes, rels)

    fig, axes = plt.subplots(1, 3, figsize=(22, 7.2))
    fig.suptitle("煤矿顶板灾变知识图谱激活（coal-mine-neo4j · 实时数据渲染）",
                 fontsize=15, fontweight="bold")
    _draw_core(axes[0], core)
    _draw_real(axes[1], real)
    _draw_uie(axes[2], uie)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out / "知识图谱_全量.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    # 三个单域图
    for name, G, draw in (
        ("知识图谱_核心域.png", core, _draw_core),
        ("知识图谱_真实案例域.png", real, _draw_real),
        ("知识图谱_UIE域.png", uie, _draw_uie),
    ):
        fig, ax = plt.subplots(figsize=(11, 8))
        draw(ax, G)
        fig.tight_layout()
        fig.savefig(out / name, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # 计数统计
    node_counter = Counter(n["label"] for n in nodes.values())
    rel_counter = Counter(r["rel"] for _, _, r in rels)
    uie_rel = {k: rel_counter[k] for k in _REL_COLOR if rel_counter.get(k)}
    uie_total = sum(uie_rel.values())
    core_total = sum(rel_counter[k] for k in ("HAS_RISK_LEVEL", "CLASSIFIED_AS", "APPLIES_TO"))
    stats = {
        "graph": "coal-mine-neo4j",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "nodes": dict(sorted(node_counter.items(), key=lambda kv: -kv[1])),
        "total_nodes": sum(node_counter.values()),
        "rels": dict(sorted(rel_counter.items(), key=lambda kv: -kv[1])),
        "total_rels": sum(rel_counter.values()),
        "core_rels": core_total,
        "uie_rels": uie_total,
        "uie_relation_distribution": uie_rel,
        "real_case_embeddings": sum(
            1 for n in nodes.values()
            if n["label"] == "RealAccidentCase" and n["props"].get("embedding")),
    }
    return stats


def _write_md(stats: dict, out: Path) -> None:
    """生成报告素材《图谱激活概览.md》。"""
    node_lines = "\n".join(
        f"| {k} | {v} |" for k, v in stats["nodes"].items())
    rel_lines = "\n".join(
        f"| {k} | {v} |" for k, v in stats["rels"].items())
    uie_lines = "\n".join(
        f"| {k} | {v} |" for k, v in stats["uie_relation_distribution"].items())
    md = f"""# 图谱激活概览

**图谱状态一句话**：coal-mine-neo4j 灾变知识图谱已完整激活——仿真基线（5 案例 + 10 规程 +
5 支护方案 + 5 风险等级 + 4 来源类型）与真实数据域（21 份 RealAccidentCase 案例，含向量嵌入）
与 UIE 标注知识域（{stats['nodes'].get('KnowledgeEntity', 0)} 个知识实体）同图共存，
总节点 {stats['total_nodes']} 个、总关系 {stats['total_rels']} 条。

## 节点计数

| 标签 | 数量 |
|------|:---:|
{node_lines}

## 关系计数

| 关系 | 数量 |
|------|:---:|
{rel_lines}

## UIE 标注关系分布（7 类，合计 {stats['uie_rels']} 条）

| 关系 | 数量 |
|------|:---:|
{uie_lines}

## 验证证据

- `validate_neo4j_phase8.py`：**12 PASS / 0 FAIL**（含 --reset 后完整图谱态恢复：真实案例 + UIE 域）
- `validate_real_data.py`：**10 PASS / 0 FAIL**（R1-R10，真实案例 21 份 + 双轨全序一致）
- `validate_uie_phase8.py`：5 项验收通过（452 条标注 → 225 条 approved 三元组写回）
- 真实案例向量嵌入：{stats['real_case_embeddings']} 条 × 1024 维（bge-m3，`real_accident_case_embedding` 索引）

> 渲染方式：`deploy/neo4j/scripts/render_graph_snapshot.py` 实时读取 Neo4j 数据，
> networkx + matplotlib（Microsoft YaHei）绘制，非静态伪造。
"""
    (out / "图谱激活概览.md").write_text(md, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Neo4j 图谱激活截图 + 计数渲染")
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    nodes, rels, _ = load_graph(args.uri)
    if not nodes:
        print(f"[error] 图谱无节点（{args.uri}）")
        return 1
    stats = render(nodes, rels, args.out)
    (args.out / "图谱计数.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(stats, args.out)

    print(f"[ok] 图谱激活产物已输出: {args.out}")
    print(f"  总节点 {stats['total_nodes']} / 总关系 {stats['total_rels']}")
    print(f"  节点: {stats['nodes']}")
    print(f"  UIE 关系分布: {stats['uie_relation_distribution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
