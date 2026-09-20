"""LLM 实体关系抽取（补齐知识候选 SPO 的 object）。

真 UIE 微调缺 GPU/训练数据，本期以 DeepSeek V4（OpenAI 兼容 chat）完成
`(entity)-[relation]->?object` 的 object 补齐。仅产出候选，不自动写库、
不自动改 review_status（治理约束：审核门是唯一入库通道）。

微调就绪后替换 extract_object 内部实现即可（接口不变）。
"""

from __future__ import annotations

import json
import os
from typing import Any

# 实体/关系类型枚举（对齐 knowledge_sources._ENTITY_TYPES/_RELATION_TYPES）
ENTITY_TYPES = ["地质构造", "支护设备", "顶板灾变", "监测指标", "处置规程", "岗位角色", "巷道分区"]
RELATION_TYPES = ["诱发", "支护", "防控", "监测", "调度", "上报", "传导"]

_SYSTEM_PROMPT = """你是煤矿顶板灾变领域的实体关系抽取模型（UIE 生产化替代实现）。
输入一个知识候选 {entity, relation, evidence}，候选缺少 object（三元组不完整）。
请从证据片段中抽取补全 object 实体，并判定其实体类型。
只输出一个 JSON 对象，不要任何额外文字。JSON 字段：
{
  "object": "目标实体名称（证据中出现的具体事物，如 '锚索'、'微震事件'、'采空区'）",
  "object_type": "7类之一: 地质构造/支护设备/顶板灾变/监测指标/处置规程/岗位角色/巷道分区",
  "confidence": 0.0-1.0,
  "evidence": "支撑该抽取的证据原文片段"
}
若证据无法支撑完整 SPO，object 置为空字符串。"""


class UIEExtractionError(RuntimeError):
    """UIE 抽取失败（LLM 不可达 / 非 JSON / 非法输出）。调用方应保留候选 pending_review。"""


_ENRICH_ENV = "UIE_ENRICH_KNOWLEDGE_CANDIDATES"  # 开关："1"/"on" 时反思节点运行期补齐 SPO object
_TRUTHY = {"1", "true", "on", "yes"}


def enrich_candidates(
    candidates: list[dict],
    max_batch: int = 3,
    enabled: bool | None = None,
) -> tuple[list[dict], dict]:
    """批量补齐知识候选的 object（UIE 生产化运行时入口）。

    仅填充 object/object_type/confidence/evidence 字段并标记 uie_enriched=True，
    review_status 一律保持 pending_review（治理红线：审核门是唯一入库通道，
    富化只让候选成为完整 SPO，是否写回仍由人工审核决定）。

    :param candidates: 知识候选 dict 列表（可含缺 object 的 pending_review 条目）
    :param max_batch: 单轮最多富化条数（避免拖慢写回节点）
    :param enabled: 显式开关；None 时读环境变量 UIE_ENRICH_KNOWLEDGE_CANDIDATES
    :return: (candidates, summary)；summary = {attempted, enriched, failed, skipped, reason}
      LLM 不可达/未启用 → 原样返回，reason 说明降级原因，绝不抛异常。
    """
    if enabled is None:
        enabled = os.getenv(_ENRICH_ENV, "").strip().lower() in _TRUTHY
    summary = {"attempted": 0, "enriched": 0, "failed": 0, "skipped": 0, "reason": None}
    if not enabled:
        summary["skipped"] = sum(1 for c in candidates if not c.get("object"))
        summary["reason"] = f"未启用（{_ENRICH_ENV}=1 时开启）"
        return candidates, summary

    # 只处理缺 object 的候选（已补齐的跳过），且不触碰已 rejected/approved 条目
    targets = [
        c for c in candidates
        if not c.get("object") and c.get("review_status") == "pending_review"
    ]
    summary["skipped"] = len(candidates) - len(targets)
    if not targets:
        summary["reason"] = "无可富化候选"
        return candidates, summary

    try:
        extractor = EntityRelationExtractor()
    except UIEExtractionError as exc:
        # 缺 API Key → 离线降级：候选保留原样，等待人工/后续富化
        summary["failed"] = len(targets)
        summary["reason"] = f"抽取器不可用：{exc}"
        return candidates, summary

    for c in targets[:max_batch]:
        summary["attempted"] += 1
        try:
            parsed = extractor.extract_object(c)
        except UIEExtractionError:
            summary["failed"] += 1
            continue
        if parsed.get("object"):
            c["object"] = parsed["object"]
            c["object_type"] = parsed.get("object_type", "")
            c["confidence"] = _clamp01(float(parsed.get("confidence", c.get("confidence", 0.5))))
            c["evidence"] = parsed.get("evidence") or c.get("evidence", "")
            c["uie_enriched"] = True
            summary["enriched"] += 1
        else:
            # 模型判定证据无法支撑完整 SPO → 保留待人工补齐
            summary["failed"] += 1
    # 超出 max_batch 的候选保持原样（下轮富化）
    summary["skipped"] += max(0, len(targets) - max_batch)
    return candidates, summary


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _parse_json_object(raw: str) -> Any:
    """宽松解析：去除代码围栏/前导 json 后 json.loads。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


class EntityRelationExtractor:
    """LLM 实体关系抽取器（OpenAI 兼容 chat，默认 DeepSeek V4）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 60,
    ):
        try:  # 与 llm.py 一致：自动加载项目根 .env（agent1/.env 含 OPENAI_API_KEY/LLM_QWEN_MAX）
            from dotenv import find_dotenv, load_dotenv
            load_dotenv(find_dotenv())
        except ImportError:
            pass
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        self.model = model or os.getenv("LLM_QWEN_MAX") or os.getenv("LLM_MODEL_NAME", "deepseek-chat")
        self.timeout = timeout
        if not self.api_key:
            raise UIEExtractionError("缺少 OPENAI_API_KEY，无法调用抽取模型")

    def extract_object(self, candidate: dict) -> dict:
        """对单个候选补齐 object，返回 {object, object_type, confidence, evidence}。"""
        entity = candidate.get("entity", "")
        relation = candidate.get("relation", "")
        evidence = candidate.get("evidence", "")
        user_prompt = (
            f"候选: entity={entity!r}, relation={relation!r}\n"
            f"证据: {evidence}\n请输出 JSON。"
        )
        raw = self._chat(_SYSTEM_PROMPT, user_prompt)
        try:
            parsed = _parse_json_object(raw)
        except Exception as exc:
            raise UIEExtractionError(f"抽取模型输出非 JSON: {raw[:120]!r}") from exc
        if not isinstance(parsed, dict):
            raise UIEExtractionError("抽取模型输出非 JSON 对象")
        otype = str(parsed.get("object_type", "")).strip()
        if otype and otype not in ENTITY_TYPES:
            raise UIEExtractionError(f"object_type 非法: {otype!r}")
        return {
            "object": str(parsed.get("object", "")).strip(),
            "object_type": otype,
            "confidence": _clamp01(float(parsed.get("confidence", 0.5))),
            "evidence": str(parsed.get("evidence", evidence)).strip() or evidence,
        }

    def _chat(self, system: str, user: str) -> str:
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            raise UIEExtractionError(f"抽取模型不可达（{self.base_url}）: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise UIEExtractionError(f"抽取模型返回异常: {data}") from exc
