"""管理员手动触发内容流水线的后台执行器。"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from src.app.application import Application
from src.platform_access.contracts import PlatformUser
from src.platform_access.pipeline_logs import PipelineExecutionLogHandler
from src.platform_access.service import PlatformAccessService
from src.tasks.task_result import TaskResult


class ManualPipelineRunner:
    """将一次管理员点击转换为可追踪、可恢复查看的后台流水线执行。

    Web 请求只负责登记和启动线程，不等待长时间的 GitHub、模型或媒体调用，
    因此不会触发浏览器超时。每次运行都使用提交时冻结的配置快照。
    """

    def __init__(self, project_root: Path, access_service: PlatformAccessService) -> None:
        self._project_root = project_root
        self._access_service = access_service
        self._active_request_ids: set[str] = set()
        self._lock = Lock()

    def request_run(self, user: PlatformUser, *, idempotency_key: str) -> dict[str, object]:
        """登记手动运行；同一幂等键重复提交只返回原记录，不重复启动任务。"""

        request, config_item = self._access_service.create_manual_pipeline_request(
            user,
            idempotency_key=idempotency_key,
        )
        request_id = str(request["id"])
        should_start = False
        with self._lock:
            if request["status"] == "queued" and request_id not in self._active_request_ids:
                self._active_request_ids.add(request_id)
                should_start = True
        if should_start:
            try:
                # 先持久化“运行中”，再启动后台线程。原先把状态切换留在 daemon
                # 线程内，开发热重载或进程刚退出时会留下永久 queued 的孤儿记录。
                request = self._access_service.update_manual_pipeline_request(
                    user,
                    request_id,
                    status="running",
                    metadata={
                        "executor_pid": os.getpid(),
                        "executor_started_at": datetime.now(UTC).isoformat(),
                    },
                )
                thread = Thread(
                    target=self._run,
                    args=(user, request_id, config_item.get("config", {})),
                    name=f"manual-pipeline-{request_id[:8]}",
                    # 手动流水线可能包含长时间的模型和媒体请求，不能在正常的
                    # Python 进程退出时被 daemon 线程直接丢弃。
                    daemon=False,
                )
                thread.start()
            except Exception as exc:
                self._access_service.update_manual_pipeline_request(
                    user,
                    request_id,
                    status="failed",
                    error_message=f"后台执行器启动失败：{_safe_error_message(exc)}",
                    metadata={"executor_start_failed_at": datetime.now(UTC).isoformat()},
                )
                with self._lock:
                    self._active_request_ids.discard(request_id)
                raise
        return request

    def list_runs(self, user: PlatformUser) -> list[dict[str, object]]:
        """返回当前账号可见的最近运行历史。"""

        self._recover_orphaned_queued_runs(user)
        return self._access_service.list_manual_pipeline_requests(user)

    def _run(self, user: PlatformUser, request_id: str, runtime_config: object) -> None:
        """在线程内执行真实流水线，并把每个 Task 的结果收敛为审计摘要。"""

        handler = PipelineExecutionLogHandler(
            append_event=lambda **event: self._access_service.append_manual_pipeline_event(
                user,
                request_id,
                **event,
            ),
        )
        try:
            if not isinstance(runtime_config, dict):
                raise ValueError("运行配置快照无效")

            self._append_event_safely(
                user,
                request_id,
                event_type="run_started",
                level="INFO",
                message="手动工作流开始执行",
            )

            results = Application(
                self._project_root,
                runtime_config=runtime_config,
                extra_log_handlers=(handler,),
            ).run_manual_pipeline()
            payload = {"tasks": [_serialize_task_result(item) for item in results]}
            self._append_event_safely(
                user,
                request_id,
                event_type="run_succeeded",
                level="INFO",
                message=f"手动工作流执行完成，共完成 {len(results)} 个任务",
            )
            self._access_service.update_manual_pipeline_request(
                user,
                request_id,
                status="succeeded",
                metadata=payload,
            )
        except Exception as exc:  # 后台任务必须把故障回写，而不是静默丢失。
            self._append_event_safely(
                user,
                request_id,
                event_type="run_failed",
                level="ERROR",
                message=f"手动工作流执行失败：{_safe_error_message(exc)}",
            )
            self._access_service.update_manual_pipeline_request(
                user,
                request_id,
                status="failed",
                error_message=_safe_error_message(exc),
            )
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
            with self._lock:
                self._active_request_ids.discard(request_id)

    def _append_event_safely(
        self,
        user: PlatformUser,
        request_id: str,
        **event: object,
    ) -> None:
        """观测事件写入失败时保留真实流水线执行能力。"""

        try:
            self._access_service.append_manual_pipeline_event(
                user,
                request_id,
                **event,
            )
        except Exception:
            logging.getLogger(__name__).exception("工作流实时日志事件写入失败：request_id=%s", request_id)

    def _recover_orphaned_queued_runs(self, user: PlatformUser) -> None:
        """清理已超过短暂接管窗口的 queued 记录。

        正常请求会在创建线程前同步切换为 ``running``。因此停留在 ``queued`` 超过
        两分钟的记录一定没有被当前执行器接管，必须显式结束，避免首页误判为仍在运行。
        这里只处理 queued，不擅自中止可能仍在执行的 running 任务。
        """

        cutoff = datetime.now(UTC) - timedelta(minutes=2)
        for item in self._access_service.list_manual_pipeline_requests(user):
            if item.get("status") != "queued":
                continue
            updated_at = _parse_datetime(item.get("updated_at"))
            if updated_at is None or updated_at > cutoff:
                continue
            self._access_service.update_manual_pipeline_request(
                user,
                str(item["id"]),
                status="failed",
                error_message="后台执行器未接管该请求，已结束这条过期队列；请重新运行。",
                metadata={
                    "executor_recovered_at": datetime.now(UTC).isoformat(),
                    "executor_recovery_reason": "queued_not_claimed",
                },
            )


def _serialize_task_result(result: TaskResult) -> dict[str, Any]:
    """去除可能很大的中间对象，仅保存管理台需要的任务审计信息。"""

    return {
        "task_name": result.task_name,
        "run_id": result.run_id,
        "metadata_keys": sorted(str(key) for key in result.metadata.keys()),
    }


def _safe_error_message(error: Exception) -> str:
    """控制返回给管理台的错误长度，避免把敏感参数或长堆栈写进数据库。"""

    text = str(error).strip() or error.__class__.__name__
    return text[:600]


def _parse_datetime(value: object) -> datetime | None:
    """解析仓储返回的 ISO 时间；异常记录不参与自动清理。"""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
