from __future__ import annotations

import json
from typing import Any


def dumps_json_or_none(value: dict[str, Any] | None) -> str | None:
    """把可选字典转换为稳定 JSON 字符串。"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads_json_or_empty(value: str | None) -> dict[str, Any]:
    """把可选 JSON 字符串解析为字典；空值返回空字典。"""
    if value is None or not str(value).strip():
        return {}

    loaded = json.loads(str(value))
    if not isinstance(loaded, dict):
        return {}
    return loaded
