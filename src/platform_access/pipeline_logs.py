"""把当前手动工作流线程的标准日志转换为持久事件。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any


AppendPipelineEvent = Callable[..., None]


class PipelineExecutionLogHandler(logging.Handler):
    """只采集创建时指定线程的业务日志，避免混入其他请求。"""

    def __init__(
        self,
        *,
        append_event: AppendPipelineEvent,
        thread_id: int | None = None,
    ) -> None:
        super().__init__(level=logging.INFO)
        self._append_event = append_event
        self._thread_id = threading.get_ident() if thread_id is None else thread_id
        self._emitting = False

    def emit(self, record: logging.LogRecord) -> None:
        """写入单条事件；持久化失败不能反向打断真实工作流。"""

        if record.thread != self._thread_id or record.levelno < logging.INFO or self._emitting:
            return
        try:
            self._emitting = True
            self._append_event(
                event_type=str(getattr(record, "pipeline_event_type", "log")),
                level=record.levelname,
                message=self._safe_message(record),
                task_name=self._optional_text(getattr(record, "pipeline_task_name", None))
                or self._task_name_from_logger(record.name),
                task_run_id=self._optional_text(getattr(record, "pipeline_task_run_id", None)),
            )
        except Exception:
            # logging.Handler.handleError 会把异常输出到 stderr。这里的事件存储是
            # 旁路观测能力，不能因为短暂数据库错误让内容流水线失败。
            self.handleError(record)
        finally:
            self._emitting = False

    @staticmethod
    def _safe_message(record: logging.LogRecord) -> str:
        """限制单条日志大小，避免意外的大响应占满事件表。"""

        message = record.getMessage().strip() or record.name
        return message[:8000]

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:240] if text else None

    @staticmethod
    def _task_name_from_logger(logger_name: str) -> str | None:
        """从 BaseTask 的稳定 logger 名称识别普通业务日志所属 Task。"""

        prefix = "src.tasks.base_task."
        if not logger_name.startswith(prefix):
            return None
        task_name = logger_name.removeprefix(prefix).strip()
        return task_name[:240] if task_name else None
