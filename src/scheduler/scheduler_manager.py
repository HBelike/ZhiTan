from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config.config_manager import AppConfig
from src.scheduler.scheduled_job import ScheduledJob
from src.scheduler.weekly_schedule import WeeklySchedule, parse_hhmm, parse_weekday


JobHandler = Callable[[], None]


class SchedulerManager:
    """负责注册周期性 Job，并在常驻模式下按时间触发它们。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.timezone = self._load_timezone(config.timezone_name)
        self.jobs = self._build_jobs()

    def preview_registered_jobs(self, now: datetime | None = None) -> None:
        """输出当前已注册 Job 的调度预览，不阻塞进程。"""
        current = now if now is not None else datetime.now(self.timezone)
        self.logger.info(
            "Scheduler 预览启动：timezone=%s loop_interval_seconds=%s",
            self.timezone.key,
            self.config.scheduler_loop_interval_seconds,
        )

        for job in self.jobs:
            if not job.enabled:
                self.logger.info("Job 已禁用：%s task=%s", job.name, job.task_name)
                continue

            next_run = job.next_run_after(current)
            self.logger.info(
                "Job 已注册：%s task=%s next_run=%s",
                job.name,
                job.task_name,
                next_run.isoformat(),
            )

    def run_forever(self, handlers: dict[str, JobHandler]) -> None:
        """进入常驻调度循环，直到进程被 Ctrl+C 或系统信号停止。"""
        next_run_by_name = self._initial_next_runs()
        self._log_next_runs(next_run_by_name)

        while True:
            current = datetime.now(self.timezone)
            for job in self.jobs:
                if not job.enabled:
                    continue
                next_run = next_run_by_name[job.name]
                if current < next_run:
                    continue

                self._run_job(job=job, handlers=handlers, scheduled_at=next_run)
                next_run_by_name[job.name] = job.next_run_after(current)
                self.logger.info(
                    "Job 下一次运行时间已更新：%s next_run=%s",
                    job.name,
                    next_run_by_name[job.name].isoformat(),
                )

            time.sleep(self._sleep_seconds(next_run_by_name))

    def _run_job(self, job: ScheduledJob, handlers: dict[str, JobHandler], scheduled_at: datetime) -> None:
        """执行一个到期 Job，并捕获异常避免调度循环退出。"""
        handler = handlers.get(job.name)
        if handler is None:
            self.logger.error("Job 缺少处理函数：%s task=%s", job.name, job.task_name)
            return

        self.logger.info("Job 开始执行：%s task=%s scheduled_at=%s", job.name, job.task_name, scheduled_at.isoformat())
        try:
            handler()
        except Exception:
            self.logger.exception("Job 执行失败：%s task=%s", job.name, job.task_name)
            return
        self.logger.info("Job 执行完成：%s task=%s", job.name, job.task_name)

    def _initial_next_runs(self) -> dict[str, datetime]:
        """计算所有启用 Job 的初始下一次运行时间。"""
        current = datetime.now(self.timezone)
        return {
            job.name: job.next_run_after(current)
            for job in self.jobs
            if job.enabled
        }

    def _log_next_runs(self, next_run_by_name: dict[str, datetime]) -> None:
        """记录调度循环启动时的时间表。"""
        for job_name, next_run in next_run_by_name.items():
            self.logger.info("Scheduler 常驻 Job 已就绪：%s next_run=%s", job_name, next_run.isoformat())

    def _sleep_seconds(self, next_run_by_name: dict[str, datetime]) -> int:
        """计算本轮循环后的睡眠时间，避免过长睡眠影响退出响应。"""
        configured_interval = max(1, int(self.config.scheduler_loop_interval_seconds))
        if not next_run_by_name:
            return min(configured_interval, 60)

        current = datetime.now(self.timezone)
        seconds_to_next = min(max(1, int((next_run - current).total_seconds())) for next_run in next_run_by_name.values())
        return min(configured_interval, seconds_to_next, 60)

    def _build_jobs(self) -> list[ScheduledJob]:
        """根据配置创建两个周五 Job。"""
        production_schedule = WeeklySchedule(
            weekday=parse_weekday(self.config.production_day),
            time_of_day=parse_hhmm(self.config.production_time),
            timezone=self.timezone,
        )
        draft_schedule = WeeklySchedule(
            weekday=parse_weekday(self.config.production_day),
            time_of_day=parse_hhmm(self.config.draft_time),
            timezone=self.timezone,
        )

        return [
            ScheduledJob(
                name="weekly_content_production",
                task_name="WeeklyContentProductionJob",
                schedule=production_schedule,
            ),
            ScheduledJob(
                name="weekly_draft_creation",
                task_name="WeeklyDraftCreationJob",
                schedule=draft_schedule,
            ),
        ]

    def _load_timezone(self, timezone_name: str) -> ZoneInfo:
        """加载 IANA 时区配置。"""
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"不支持的时区配置：{timezone_name}") from exc
