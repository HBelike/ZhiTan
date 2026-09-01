from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from typing import Any

from src.observability.langsmith_runtime import trace_operation
from src.repositories.error_event_repository import ErrorEventRepository
from src.repositories.task_run_repository import TaskRunRepository
from src.tasks.task_context import TaskContext
from src.tasks.task_result import TaskResult


class BaseTask(ABC):
    """所有任务的共同执行骨架，统一处理状态、日志和异常。"""

    task_name: str

    def __init__(
        self,
        task_run_repository: TaskRunRepository,
        error_event_repository: ErrorEventRepository,
    ) -> None:
        self.task_run_repository = task_run_repository
        self.error_event_repository = error_event_repository
        self.logger = logging.getLogger(f"{__name__}.{self.task_name}")

    def run(self, context: TaskContext) -> TaskResult:
        """运行任务，并把生命周期写入 task_runs。"""
        task_run = self.task_run_repository.create(
            task_name=self.task_name,
            metadata={"phase": "created"},
        )
        self.logger.info("任务创建：%s run_id=%s", self.task_name, task_run.run_id)

        return trace_operation(
            run_name=f"wechat.task.{self.task_name}",
            run_type="chain",
            inputs={
                "task_name": self.task_name,
                "task_run_id": task_run.run_id,
            },
            metadata={
                "component": "wechat_task",
                "task_name": self.task_name,
                "task_run_id": task_run.run_id,
                "privacy_mode": "metadata_only",
            },
            tags=("wechat", "task", f"task:{self.task_name}"),
            execute=lambda: self._run_task_lifecycle(
                context=context,
                task_run=task_run,
            ),
            summarize=lambda result: {
                "status": "succeeded",
                "metadata_field_count": len(result.metadata),
            },
        )

    def _run_task_lifecycle(self, context: TaskContext, task_run: Any) -> TaskResult:
        """执行一次任务生命周期；由可选的 LangSmith chain 父 Trace 包裹。"""

        try:
            self.task_run_repository.mark_running(task_run.id)
            self.logger.info(
                "任务开始：%s run_id=%s",
                self.task_name,
                task_run.run_id,
                extra=self._pipeline_event_extra("task_started", task_run.run_id),
            )

            metadata = self.execute(context)
            self.task_run_repository.mark_succeeded(task_run.id, metadata=metadata)
            self.logger.info(
                "任务成功：%s run_id=%s",
                self.task_name,
                task_run.run_id,
                extra=self._pipeline_event_extra("task_succeeded", task_run.run_id),
            )
            return TaskResult(
                task_name=self.task_name,
                run_id=task_run.run_id,
                metadata=metadata,
            )
        except Exception as exc:
            self._record_failure(task_run_id=task_run.id, exc=exc)
            self.logger.exception(
                "任务失败：%s run_id=%s",
                self.task_name,
                task_run.run_id,
                extra=self._pipeline_event_extra("task_failed", task_run.run_id),
            )
            raise

    def _pipeline_event_extra(self, event_type: str, task_run_id: str) -> dict[str, str]:
        """为实时日志 Handler 提供无需解析正文的 Task 关联字段。"""

        return {
            "pipeline_event_type": event_type,
            "pipeline_task_name": self.task_name,
            "pipeline_task_run_id": task_run_id,
        }

    @abstractmethod
    def execute(self, context: TaskContext) -> dict[str, Any]:
        """执行具体任务逻辑，由子类实现。"""

    def _record_failure(self, task_run_id: int, exc: Exception) -> None:
        """记录失败状态和异常事件。"""
        error_message = str(exc) or exc.__class__.__name__
        stack_trace = traceback.format_exc()
        metadata = {
            "error_type": exc.__class__.__name__,
        }

        self.task_run_repository.mark_failed(
            task_run_id=task_run_id,
            error_message=error_message,
            metadata=metadata,
        )
        self.error_event_repository.create(
            task_run_id=task_run_id,
            task_name=self.task_name,
            severity="error",
            message=error_message,
            error_type=exc.__class__.__name__,
            stack_trace=stack_trace,
            metadata=metadata,
        )
