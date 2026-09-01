"""模型 JSON 输出的严格解析工具。"""

from __future__ import annotations

import json
import re


class OnlineAssessmentModelOutputError(ValueError):
    """模型没有返回可校验结构时的领域错误。"""


def parse_json_object(raw: str) -> dict[str, object]:
    """接受纯 JSON 或单个 Markdown JSON 围栏，拒绝数组与尾随说明。"""

    content = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OnlineAssessmentModelOutputError("模型没有返回有效 JSON") from exc
    if not isinstance(value, dict):
        raise OnlineAssessmentModelOutputError("模型输出必须是 JSON 对象")
    return value
