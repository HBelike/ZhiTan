from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.scheduler.weekly_schedule import WeeklySchedule


@dataclass(frozen=True)
class ScheduledJob:
    """一个可注册到 SchedulerManager 的周期性 Job。"""

    name: str
    task_name: str
    schedule: WeeklySchedule
    enabled: bool = True

    def next_run_after(self, now: datetime) -> datetime:
        """返回该 Job 在给定时间后的下一次运行时间。"""
        return self.schedule.next_run_after(now)
