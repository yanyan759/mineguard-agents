"""权限矩阵 V2（HALLUCINATION 清零）：7 角色 × 9 动作，带真实条款出处。

权威源：`任务成果/03_权限矩阵依据/权限条款动作映射.csv`（14 条映射，逐条带
regulation_source / clause_no / clause_text / URL 官方链接），来源说明见同目录
`PM1-PM6来源说明.md`。

设计原则（对齐 docs/真实资料爬取与整理要求清单.md §PM）：
- **「内置矩阵 = 种子矩阵 = 条款出处矩阵」**：permission_check_tool（内置回退矩阵）
  与 deploy/mysql seed（role_permissions 落库矩阵）均从本模块读取，杜绝两处漂移；
- **override_threshold 不落入矩阵**：PM1-PM6 说明明确「暂无足够直接授权依据，
  不强行落入矩阵」；
- **view_all 为复合词**：不属于动作映射表（9 动作见 docs/真实资料爬取与整理要求清单.md）。
"""

from __future__ import annotations

# ── 9 类系统动作 × 风险等级（PM 动作映射沿用，未在 PM 内定义的 action 视为 unknown）──
ACTION_RISK: dict[str, str] = {
    "view_warning": "low",
    "acknowledge_alert": "low",
    "report_status": "low",
    "execute_plan": "medium",
    "dispatch_work_order": "medium",
    "escalate_alert": "medium",
    "approve_plan": "high",
    "override_threshold": "high",
    "initiate_emergency": "critical",
}

# 官方来源 URL（PM1 条例 / PM2 国发23号 / PM4-5 永城市预案）
_URL_条例 = "https://www.chinamine-safety.gov.cn/zfxxgk/fdzdgknr/zcfg/xzfg/202602/t20260203_594029.shtml"
_URL_国发23号 = "https://www.gov.cn/xxgk/pub/govpublic/zzmlxz/201011/P020101124591539530747.pdf"
_URL_永城市预案 = "https://www.ycs.gov.cn/xzxxgk/msz/fdzdgknr42/yjgl59/yjya50/content_15504"

# ── 角色 → 权限列表（7 角色，全部可溯源）──
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "矿长":           ["approve_plan", "initiate_emergency", "report_status"],
    "矿总工程师":     ["approve_plan"],
    "安全副矿长":     ["escalate_alert", "initiate_emergency"],
    "调度室主任":     ["initiate_emergency", "dispatch_work_order"],
    "支护工程师":     ["execute_plan", "report_status"],
    "监测值班员":     ["view_warning", "acknowledge_alert"],
    "操作工":         ["report_status", "view_warning"],
}

# 可用角色列表（供 check_permission 报错提示用）
AVAILABLE_ROLES: list[str] = list(ROLE_PERMISSIONS.keys())

# ── 角色 × 动作 → 条款出处（14 条映射，逐条溯源）──
# 每条含 regulation_source（法规/预案名称）、clause_no（条款编号）、clause_text（原文要点）、
# url（官方链接）。derived_action 是面向系统动作的工程映射，非法规原文（见 PM1-PM6 说明）。
PERMISSION_SOURCES: dict[tuple[str, str], dict[str, str]] = {
    ("矿长", "approve_plan"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第四条、十八条",
        "clause_text": "主要负责人是安全生产第一责任人，并组织制定实施规章制度、作业规程和应急预案。",
        "url": _URL_条例,
    },
    ("矿长", "initiate_emergency"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十八条第六项",
        "clause_text": "煤矿企业主要负责人组织制定并实施生产安全事故应急救援预案。",
        "url": _URL_条例,
    },
    ("矿长", "report_status"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十八条第七项",
        "clause_text": "煤矿企业主要负责人及时、如实报告煤矿生产安全事故。",
        "url": _URL_条例,
    },
    ("矿总工程师", "approve_plan"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十九条、第二十二条",
        "clause_text": "煤矿企业应当配备主要技术负责人，并为煤矿配备总工程师。",
        "url": _URL_条例,
    },
    ("安全副矿长", "escalate_alert"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十九条第五项",
        "clause_text": "安全生产管理人员发现威胁安全的紧急情况时，有权要求停止危险区域作业并撤出人员。",
        "url": _URL_条例,
    },
    ("安全副矿长", "initiate_emergency"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十九条第五项",
        "clause_text": "安全生产管理人员发现威胁安全的紧急情况时，有权要求停止危险区域作业并撤出人员。",
        "url": _URL_条例,
    },
    ("调度室主任", "initiate_emergency"): {
        "regulation_source": "国务院关于进一步加强企业安全生产工作的通知",
        "clause_no": "国发〔2010〕23号第17项",
        "clause_text": "赋予企业生产现场带班人员、班组长和调度人员遇险时第一时间下达停产撤人命令的直接决策权和指挥权。",
        "url": _URL_国发23号,
    },
    ("调度室主任", "dispatch_work_order"): {
        "regulation_source": "永城市煤矿生产安全事故应急预案",
        "clause_no": "预警行动（2）",
        "clause_text": "煤矿企业下达预警指令，启动预警行动方案，停止生产，组织人员撤离。",
        "url": _URL_永城市预案,
    },
    ("支护工程师", "execute_plan"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第十九条第一、八项",
        "clause_text": "安全生产管理机构和人员参与拟订作业规程并督促落实安全生产整改措施。",
        "url": _URL_条例,
    },
    ("支护工程师", "report_status"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第三十七条",
        "clause_text": "煤矿企业对监管监察发现的事故隐患应当立即整改并按要求报告整改结果。",
        "url": _URL_条例,
    },
    ("监测值班员", "view_warning"): {
        "regulation_source": "永城市煤矿生产安全事故应急预案",
        "clause_no": "监测、预警条款",
        "clause_text": "煤矿企业应及时收集监测数据，组织安全评估、报告备案和监控整改。",
        "url": _URL_永城市预案,
    },
    ("监测值班员", "acknowledge_alert"): {
        "regulation_source": "永城市煤矿生产安全事故应急预案",
        "clause_no": "预警行动（2）",
        "clause_text": "重大危险源失控或事故征兆出现时，立即发布和传递预警信息并按程序报告。",
        "url": _URL_永城市预案,
    },
    ("操作工", "report_status"): {
        "regulation_source": "煤矿安全生产条例",
        "clause_no": "第二十条第三项",
        "clause_text": "煤矿企业从业人员应及时报告发现的事故隐患或者其他不安全因素。",
        "url": _URL_条例,
    },
    ("操作工", "view_warning"): {
        "regulation_source": "永城市煤矿生产安全事故应急预案",
        "clause_no": "预警信息发布条款",
        "clause_text": "预警信息可通过广播、短信、微信、互联网、警报器等方式发布并确保对象无遗漏。",
        "url": _URL_永城市预案,
    },
}


def permission_source(role: str, action: str) -> dict | None:
    """查询角色×动作的条款出处；无映射返回 None（该权限无直接授权依据）。"""
    return PERMISSION_SOURCES.get((role, action))
