from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskResult:
    """Task 执行完成后返回给调用方的结果摘要。"""

    task_name: str
    run_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
