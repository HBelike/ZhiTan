from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class WeeklySchedule:
    """每周固定某一天某一时间运行的调度规则。"""

    weekday: int
    time_of_day: time
    timezone: ZoneInfo

    def next_run_after(self, now: datetime) -> datetime:
        """计算给定时间之后的下一次运行时间。"""
        current = self._normalize_now(now)
        days_until_target = (self.weekday - current.weekday()) % 7
        candidate_date = (current + timedelta(days=days_until_target)).date()
        candidate = datetime.combine(candidate_date, self.time_of_day, self.timezone)

        if candidate <= current:
            candidate += timedelta(days=7)

        return candidate

    def _normalize_now(self, now: datetime) -> datetime:
        """把输入时间转换到调度使用的时区。"""
        if now.tzinfo is None:
            return now.replace(tzinfo=self.timezone)
        return now.astimezone(self.timezone)


def parse_weekday(value: str) -> int:
    """把配置里的星期字符串转换成 Python weekday 索引。"""
    normalized = value.strip().lower()
    if normalized not in WEEKDAY_INDEX:
        supported = ", ".join(WEEKDAY_INDEX)
        raise ValueError(f"不支持的星期配置：{value}，支持：{supported}")
    return WEEKDAY_INDEX[normalized]


def parse_hhmm(value: str) -> time:
    """解析 HH:MM 格式的时间配置。"""
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError(f"时间配置必须是 HH:MM 格式：{value}") from exc
    return parsed.time()
