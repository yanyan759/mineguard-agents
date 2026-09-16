"""统一 LLM 推理封装层。

职责：with_structured_output(method="json_mode") + 字段类型提示词约束 +
超时/重试/非法输出回退，返回与 tools/ 一致的 {status, data, error}；
供六 Agent 子图复用。实测结论见 plan Task 1：DeepSeek 仅支持 json_object
response_format，且须显式声明字段类型（尤指数组）避免模型误输出。
"""

from __future__ import annotations

import time
from enum import Enum
from types import UnionType
from typing import Any, Callable, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel

from app.agent.llm import model

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """LLM 调用失败（不可达/超时/非法输出）。"""


def _schema_field_hint(schema: type[BaseModel]) -> str:
    """把 Pydantic 字段转成提示词里的类型声明，规避模型把数组/嵌套对象输出成字符串。

    递归展开嵌套模型与对象数组。对于仅支持 json_object 的模型，如果把
    resource_requirements/primary_plan 等嵌套模型字段标成"字符串"，模型会真的把
    对象输出成字符串 → OutputParserException → 重试仍失败。必须逐字段声明。
    """
    return _describe_fields(schema)


def _field_label(ann: Any) -> str:
    """单个字段注解 → 类型声明（递归处理嵌套 Pydantic / 枚举 / Literal / 联合类型）。

    覆盖 DeepSeek json_mode 常见误输出（2026-08-16 实测）：Literal 字段若不列出允许值，
    模型会自造白名单外的值（如 notification channel 输出 "system"）；list 不声明元素类型，
    模型可能把数组输出成字符串或对象；Optional 不提示可空，模型可能缺字段。这些都会导致
    Pydantic 校验失败 → 重试仍失败 → 回退确定性。必须逐字段给足信息。
    """
    origin = get_origin(ann)
    # Optional / 联合类型（X | None / Union[X, None]）→ 取非 None 分支，附 或 null
    if origin in (Union, UnionType):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if not non_none:
            return "null"
        if len(non_none) == 1:
            return f"{_field_label(non_none[0])} 或 null"
        return "或 ".join(_field_label(a) for a in non_none)
    if origin is Literal:
        # 关键：列出允许值，防模型自造
        return "枚举[" + "、".join(repr(v) for v in get_args(ann)) + "]"
    if origin in (list,):
        args = get_args(ann)
        if args:
            return f"{_field_label(args[0])}数组"
        return "数组"
    if ann is Any:
        return "任意类型"
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return f"对象{{{_describe_fields(ann)}}}"
    if isinstance(ann, type) and issubclass(ann, Enum):
        return "枚举[" + "、".join(e.value for e in ann) + "]"
    if ann is float or ann is int:
        return "数字"
    if ann is str:
        return "字符串"
    if ann is bool:
        return "布尔"
    # datetime 等：提示输出 ISO 字符串，Pydantic 解析
    return getattr(ann, "__name__", str(ann)).replace("typing.", "")


def _describe_fields(schema: type[BaseModel]) -> str:
    return "；".join(
        f"{name}: {_field_label(f.annotation)}" for name, f in schema.model_fields.items()
    )


def llm_call_structured(
    schema: type[T],
    system: str,
    user: str,
    *,
    timeout_s: float = 30.0,
    max_retries: int = 1,
) -> dict:
    """结构化调用：返回 {status:'ok', data:T} 或 {status:'error', error:str}。

    基于 DeepSeek 兼容性实测（2026-08-16）：默认 with_structured_output 的
    json_schema response_format 返回 400；改用 method="json_mode"（json_object）
    并在提示词内显式声明字段类型，重试覆盖网络超时 / OutputParserException。
    """
    # DeepSeek 仅支持 json_object，故 method="json_mode"；提示词内显式声明字段类型
    field_hint = _schema_field_hint(schema)
    sys_msg = (
        f"{system}\n\n输出 JSON 对象，字段声明：{field_hint}。只输出 JSON，不要解释。"
        "必填字段不得输出 null；datetime 字段输出 ISO-8601 字符串（无法推导时用当前时间）；"
        "枚举字段必须取声明中列出的值；数组字段必须输出 JSON 数组，不得输出字符串或对象。"
    )
    try:
        structured = model.with_structured_output(schema, method="json_mode")
        last_err: Exception | None = None
        user_prompt = user
        for attempt in range(max_retries + 1):
            try:
                result = structured.invoke(
                    [("system", sys_msg), ("user", user_prompt)],
                    config={"timeout": timeout_s},
                )
                if result is None or not isinstance(result, schema):
                    raise LLMError(f"非法输出: {type(result).__name__}")
                return {"status": "ok", "data": result}
            except Exception as e:  # 网络/超时/OutputParserException 统一捕获重试
                last_err = e
                # 重试带上上一次校验失败原因，引导模型自纠（json_mode 非法输出常见）
                user_prompt = (
                    user
                    + f"\n\n（提示：上一次输出未通过校验：{str(e)[:300]}。"
                      "请严格按字段声明输出合法 JSON，不要新增未声明字段。）"
                )
        return {"status": "error", "error": f"LLM 调用失败: {last_err}"}
    except Exception as e:
        return {"status": "error", "error": f"LLM 封装异常: {e}"}


def run_with_deterministic_fallback(
    deterministic_fn: Callable[[dict], dict],
    llm_fn: Callable[[dict], dict],
    state: dict,
    *,
    fallback_mark: bool = True,
) -> dict:
    """确定性兜底包装：LLM 失败/异常 → 回退确定性逻辑 + model_mode 标记。

    :param deterministic_fn: 现有确定性节点函数（含输出字段构造）
    :param llm_fn: LLM 增强函数，返回 {status, data} 或抛 LLMError
    :param state: 节点输入 WorkflowState（dict）
    :param fallback_mark: 是否在确定性输出上打 model_mode="deterministic_fallback"
    :return: LLM 成功 → {result: data, model_mode: "llm"}；回退 → 确定性节点 dict
             （其上标记 model_mode="deterministic_fallback"）

    注意：model_mode 记返回值外层（对应 WorkflowState.model_mode），不塞进结果
    Schema——六结果 Schema 均为 extra=forbid 且无 model_mode 字段（Global Constraint），
    直接写 data["model_mode"] 会对 Pydantic 实例抛 ValidationError。
    """
    try:
        result = llm_fn(state)
        if result.get("status") == "ok":
            return {"result": result["data"], "model_mode": "llm"}
    except (LLMError, KeyError, TypeError, ValueError):
        pass  # LLM 失败/输出不合法 → 走确定性兜底
    out = deterministic_fn(state)
    if fallback_mark:
        out["model_mode"] = "deterministic_fallback"
    return out
