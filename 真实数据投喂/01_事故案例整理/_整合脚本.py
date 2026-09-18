# -*- coding: utf-8 -*-
"""事故案例交叉整合脚本：我的 16 份高质量解析 + codex URL/metadata + 金刚 blue 案例。
运行后生成 事故案例_真实报告FINAL.yaml（17 份），并校验完整性。
"""
from pathlib import Path
import os
import yaml, json

# 根据脚本位置定位发布包；外部整理资料通过环境变量显式传入，避免绑定某台电脑的目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MY = PROJECT_ROOT / "真实数据投喂/01_事故案例整理"
REFERENCE_ROOT = Path(os.getenv("MINEGUARD_REFERENCE_DATA_DIR", str(PROJECT_ROOT.parent / "任务成果")))
CODE = REFERENCE_ROOT / "01_事故案例整理"

# 我的 case_id -> codex case_id（按矿名一一对应，两套编号体系不同，必须显式映射）
MINE_TO_CODEX = {
    "real-2021-001": "real-2021-019",  # 王家塔
    "real-2022-001": "real-2022-018",  # 新汶华丰
    "real-2022-002": "real-2022-017",  # 织金兴荣
    "real-2022-003": "real-2022-016",  # 山东新陶阳
    "real-2022-004": "real-2022-014",  # 山西凤凰台
    "real-2022-005": "real-2022-015",  # 宣威烂坝
    "real-2023-001": "real-2023-013",  # 湖南嘉禾浦溪井
    "real-2023-002": "real-2023-012",  # 新郑煤电
    "real-2023-003": "real-2023-011",  # 枣庄王晁
    "real-2023-004": "real-2023-009",  # 甘肃兰阿阿井
    "real-2024-001": "real-2024-007",  # 渑池九六八
    "real-2024-002": "real-2024-006",  # 承德兴程
    "real-2024-003": "real-2024-005",  # 开滦钱家营
    "real-2025-001": "real-2025-004",  # 安徽潘二
    "real-2025-002": "real-2025-003",  # 镇雄宝树
    "real-2025-003": "real-2025-001",  # 中煤新登
}

# 1. 读我的 V1（16 份）
v1 = yaml.safe_load((MY / "事故案例_真实报告V1.yaml").read_text(encoding="utf-8"))
cases = v1["cases"]
by_id = {c["case_id"]: c for c in cases}

# 2. 读 codex metadata（仅取 source_url / local_file / published_at）
def load_codex_meta(codex_id):
    p = CODE / f"{codex_id}.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    return d.get("metadata", {})

# 3. 合并：我的正文 + codex URL 元数据
for mine_id, codex_id in MINE_TO_CODEX.items():
    c = by_id.get(mine_id)
    if c is None:
        print(f"!! 缺 {mine_id}"); continue
    m = load_codex_meta(codex_id)
    c["metadata"] = {
        "source_url": m.get("source_url", ""),
        "local_file": m.get("local_file", ""),
        "published_at": m.get("published_at", ""),
    }
    # 校验 URL 是否取到
    if not c["metadata"]["source_url"]:
        print(f"!! {mine_id} 未取到 URL")

# 4. 金刚 blue 案例（依据通报原文整理，非 codex 转述）
jgm = load_codex_meta("real-2023-010")
cases.append({
    "case_id": "real-2023-005",
    "location": "四川川煤华荣能源有限责任公司金刚煤矿 2123机巷（北）扩刷工作面（利用已开采的原2121采面回风巷，经修复扩刷作为2123采煤工作面运输巷，锚杆（索）+锚网+钢带+\"T\"型钢带联合支护）",
    "occurred_at": "2023-11-13T15:27:00",
    "risk_level": "blue",
    "risk_level_original": "涉险（顶板涉险事故通报：3名作业人员被困，经救援全部脱险，未伤亡）",
    "causes": [
        "风险研判管控不到位：对该巷道施工工程地质构造变化、煤层增厚、采动影响、沿空护巷+扩大断面等因素对巷道支护的破坏影响认识不足，未收集原巷道变形观测、顶板离层监测实际数据，未探明巷道支护失效及围岩变形下沉真实情况，工程启动前未组织风险研判",
        "支护设计论证不到位：未执行\"一巷一设计\"，未根据该段巷道断面、支护及围岩实际情况组织科学论证，未按特殊地段编制安全可靠的支护设计，补强支护措施安全可靠性不够",
        "定期巡查制度不落实：巷道为锚杆（索）+锚网支护，使用年限已超两年，未按《强化煤矿锚杆支护巷道顶板管理规定》定期巡查，未能及时发现和处理隐患",
        "地质工作有差距：未采取钻探等手段进一步探明顶板情况，地质说明书柱状图与实际出入较大，地质编录不详细不精准，未反映事故地段煤层增厚情况",
        "技术措施不完善：2123机巷维修整改及补强支护安全技术措施对巷道现状无描述、对安全风险无评估，支护质量要求、安全防范措施和施工顺序不清晰，对顶板断裂未提出明确处理措施",
        "现场管理不到位：巷道已维修施工400余米，未按规定进行顶板离层观测，现场多处出现局部冒落、顶板离层、支护不到位，部分锚杆不达质量标准，处置顶板断裂问题不力",
    ],
    "precursors": [
        "巷道已维修施工400余米，未按规定进行顶板离层观测（前兆监测缺失）",
        "现场多处出现局部冒落、顶板离层、支护不到位问题，部分锚杆达不到质量标准",
        "使用年限已超两年的锚杆（索）+锚网支护巷道，未定期巡查，处置顶板断裂问题不力",
    ],
    "actions": [
        "预警触发后立即停止受威胁区域作业并撤至安全地点，涉险被困人员救援后全部脱险",
        "对超期服役的锚杆（索）支护巷道建立定期巡查制度，及时处理顶板离层、支护失效隐患并归档闭环",
        "落实\"一巷一设计\"，对特殊地段巷道按实际围岩条件组织支护论证与超前治理，加密顶板离层、围岩表面位移、锚杆（索）载荷监测",
        "巷道施工前查明工程地质构造、煤层增厚、采动影响等因素，完善地质预报，杜绝凭经验施工",
    ],
    "outcome": "造成3名作业人员被困，经煤矿企业、矿山救护队和政府部门全力救援全部脱险，无人员伤亡（涉险未遂事故）；国家矿山安全监察局与四川省局等对事故开展调查并通报",
    "lessons": [
        "锚杆（索）+锚网支护巷道超期服役必须建立定期巡查制度，杜绝顶板隐患长期累积",
        "沿空护巷、扩大断面、地质变化叠加时应升级风险研判，支护设计须经科学论证而非经验补强",
        "顶板离层、锚杆载荷等监测必须落实到专人并贯穿施工全程，监测数据是支护决策依据",
        "国有煤矿同样存在顶板管理认识不到位、整治措施不力问题，必须强化顶板风险辨识与过程管控",
    ],
    "source": "国家矿山安全监察局四川局（sc.chinamine-safety.gov.cn）·《关于川煤华荣能源有限责任公司金刚煤矿\"11.13\"顶板涉险事故的通报》矿安川〔2023〕147号",
    "metadata": {
        "source_url": jgm.get("source_url", ""),
        "local_file": jgm.get("local_file", ""),
        "published_at": jgm.get("published_at", ""),
    },
})

# 5. 排序 + 校验
cases.sort(key=lambda c: (c["case_id"][5:9], c["case_id"]))
ids = [c["case_id"] for c in cases]
assert len(ids) == len(set(ids)), "case_id 重复"
req = {"case_id", "location", "occurred_at", "risk_level", "causes",
       "precursors", "actions", "outcome", "lessons", "source", "metadata"}
for c in cases:
    miss = req - set(c)
    assert not miss, f"{c['case_id']} 缺 {miss}"
    assert "source_url" in c["metadata"] and c["metadata"]["source_url"], f"{c['case_id']} 缺 URL"

from collections import Counter
print("案例总数:", len(cases))
print("年份分布:", dict(sorted(Counter(c['case_id'][5:9] for c in cases).items())))
print("等级分布:", dict(Counter(c['risk_level'] for c in cases)))
print("URL 完整:", all(c['metadata']['source_url'] for c in cases))

out = MY / "事故案例_真实报告FINAL.yaml"
payload = {
    "# 说明": "真实事故案例 FINAL —— 正文为 Claude 侧 16 份逐字解析 + 金刚涉险 blue 案例（共 17 份），"
             "metadata.source_url/local_file/published_at 来自 Codex 任务成果。详见 README.md",
    "cases": cases,
}
out.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
print("已写入:", out)
