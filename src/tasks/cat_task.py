from __future__ import annotations

from collections import Counter
from typing import Any

from src.repositories.json_utils import loads_json_or_empty
from src.repositories.task_run_repository import TaskRunRecord
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class CatTask(BaseTask):
    """负责观察全局任务运行状态，归纳异常和阻塞原因。"""

    task_name = "CatTask"
    audio_task_names = frozenset({"AudioTask", "SegmentedAudioTask"})
    video_task_names = frozenset(
        {
            "ShortVideoPromptTask",
            "VideoClipPlanTask",
            "VideoTask",
            "SeedanceClipTask",
            "SeedanceClipStatusTask",
            "VideoVisualQualityTask",
            "VideoNarrationTimelineTask",
            "VideoAssemblyTask",
            "VideoStatusTask",
        }
    )

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最近任务与异常事件，生成健康摘要。"""
        recent_runs = [
            run
            for run in self.task_run_repository.list_recent(limit=80)
            if run.task_name != self.task_name
            and not self._task_channel_disabled(context.config, run.task_name)
        ]
        recent_errors = [
            error
            for error in self.error_event_repository.list_recent(limit=10)
            if not self._task_channel_disabled(context.config, error.task_name)
        ]
        latest_by_task = self._latest_run_by_task(recent_runs)
        latest_failed_runs = [
            run
            for run, _metadata in latest_by_task.values()
            if run.status == "failed"
        ]
        historical_failed_runs = [run for run in recent_runs if run.status == "failed"]
        blocking_items = self._collect_blocking_items(latest_by_task)
        skipped_tasks = [
            {
                "task_name": task_name,
                "skip_reason": metadata.get("skip_reason"),
            }
            for task_name, (_, metadata) in latest_by_task.items()
            if metadata.get("skipped") is True
        ]

        health_status = self._health_status(
            failed_runs=latest_failed_runs,
            blocking_items=blocking_items,
        )
        summary = {
            "health_status": health_status,
            "observed_task_count": len(latest_by_task),
            "recent_run_count": len(recent_runs),
            "failed_run_count": len(latest_failed_runs),
            "historical_failed_run_count": len(historical_failed_runs),
            "recent_error_count": len(recent_errors),
            "blocking_item_count": len(blocking_items),
            "latest_tasks": self._latest_tasks_payload(latest_by_task),
            "blocking_items": blocking_items,
            "skipped_tasks": skipped_tasks,
            "recent_errors": [
                {
                    "id": error.id,
                    "task_name": error.task_name,
                    "severity": error.severity,
                    "message": error.message,
                    "error_type": error.error_type,
                    "created_at": error.created_at,
                }
                for error in recent_errors
            ],
            "top_blocking_reasons": self._top_blocking_reasons(blocking_items),
        }

        if health_status == "healthy":
            self.logger.info("CatTask 健康检查通过：observed_tasks=%s", len(latest_by_task))
        elif health_status == "blocked_by_requirements":
            self.logger.warning("CatTask 发现前置条件阻塞：items=%s", len(blocking_items))
        else:
            self.logger.error("CatTask 发现当前最新任务失败：failed_runs=%s", len(latest_failed_runs))

        return summary

    def _latest_run_by_task(self, recent_runs: list[TaskRunRecord]) -> dict[str, tuple[TaskRunRecord, dict[str, Any]]]:
        """提取每个任务最近一次运行。"""
        latest: dict[str, tuple[TaskRunRecord, dict[str, Any]]] = {}
        for run in recent_runs:
            if run.task_name in latest:
                continue
            latest[run.task_name] = (run, loads_json_or_empty(run.metadata_json))
        return latest

    def _collect_blocking_items(
        self,
        latest_by_task: dict[str, tuple[TaskRunRecord, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """从任务 metadata 中归纳阻塞原因。"""
        blocking_items: list[dict[str, Any]] = []
        for task_name, (run, metadata) in latest_by_task.items():
            reasons = self._extract_missing_requirements(metadata)
            skip_reason = metadata.get("skip_reason")
            if not reasons and isinstance(skip_reason, str) and skip_reason:
                if skip_reason not in {
                    "all_images_already_exist",
                    "audio_asset_already_exists",
                    "video_asset_already_exists",
                    "video_task_already_submitted",
                    "video_asset_already_exists",
                    "no_video_task_asset",
                    "STORAGE_LOCAL_PUBLIC_BASE_URL 未配置",
                    "没有待上传媒体资产",
                }:
                    reasons = [skip_reason]

            for reason in reasons:
                blocking_items.append(
                    {
                        "task_name": task_name,
                        "run_id": run.run_id,
                        "reason": reason,
                    }
                )
        return blocking_items

    @classmethod
    def _task_channel_disabled(cls, config: Any, task_name: str) -> bool:
        """主动关闭的媒体任务不参与当前健康状态计算。"""

        if task_name in cls.audio_task_names and not config.audio_enabled:
            return True
        if task_name in cls.video_task_names and not config.video_submit_enabled:
            return True
        return False

    def _extract_missing_requirements(self, metadata: dict[str, Any]) -> list[str]:
        """从不同任务的 metadata 结构里提取缺失项。"""
        candidates: list[Any] = [
            metadata.get("missing_requirements"),
            metadata.get("checks", {}).get("missing_requirements") if isinstance(metadata.get("checks"), dict) else None,
        ]
        extracted: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, list):
                continue
            for item in candidate:
                normalized = str(item).strip()
                if normalized:
                    extracted.append(normalized)
        return extracted

    def _latest_tasks_payload(
        self,
        latest_by_task: dict[str, tuple[TaskRunRecord, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """把每个任务最近状态整理成简洁结构。"""
        payload: list[dict[str, Any]] = []
        for task_name, (run, metadata) in sorted(latest_by_task.items()):
            payload.append(
                {
                    "task_name": task_name,
                    "status": run.status,
                    "run_id": run.run_id,
                    "skipped": bool(metadata.get("skipped", False)),
                    "skip_reason": metadata.get("skip_reason"),
                    "network_called": bool(metadata.get("network_called", False)),
                    "error_message": run.error_message,
                }
            )
        return payload

    def _top_blocking_reasons(self, blocking_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """统计高频阻塞原因。"""
        counter = Counter(str(item["reason"]) for item in blocking_items)
        return [
            {
                "reason": reason,
                "count": count,
            }
            for reason, count in counter.most_common(10)
        ]

    def _health_status(self, failed_runs: list[TaskRunRecord], blocking_items: list[dict[str, Any]]) -> str:
        """计算全局健康状态。"""
        if failed_runs:
            return "failed_recently"
        if blocking_items:
            return "blocked_by_requirements"
        return "healthy"
