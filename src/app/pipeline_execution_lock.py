"""单机部署下跨容器互斥执行内容流水线的文件锁。"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Iterator
from uuid import uuid4


class PipelineAlreadyRunningError(RuntimeError):
    """同一份内容流水线已经由另一个进程持有执行权。"""


@dataclass(frozen=True)
class PipelineExecutionLease:
    """一次成功获得的锁租约，用唯一 token 防止误删其他进程的锁。"""

    token: str
    owner: str
    acquired_at: datetime


class PipelineExecutionLock:
    """通过共享数据卷中的原子创建文件实现单机跨容器互斥。

    当前公众号工作流仍保存于 SQLite，因此 Scheduler 与管理台的手动完整流水线不能
    并行执行。该锁同时覆盖 API 容器和 Scheduler 容器挂载的 ``/app/data``。容器崩溃
    会留下锁文件，超过配置的保守超时后才会由下一次获取者清理；不会无期限阻塞服务。
    """

    def __init__(self, path: Path, *, stale_after_seconds: int) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("流水线锁过期时间必须大于零")
        self._path = path
        self._stale_after_seconds = stale_after_seconds

    @contextmanager
    def hold(self, owner: str) -> Iterator[PipelineExecutionLease]:
        """获取锁并确保任务成功、失败或中断后都只释放自身锁。"""

        lease = self.acquire(owner)
        try:
            yield lease
        finally:
            self.release(lease)

    def acquire(self, owner: str) -> PipelineExecutionLease:
        """原子获取锁；发现已过期残留锁时只重试一次。"""

        normalized_owner = owner.strip() or "unknown"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            lease = PipelineExecutionLease(
                token=uuid4().hex,
                owner=normalized_owner,
                acquired_at=datetime.now(UTC),
            )
            payload = {
                "token": lease.token,
                "owner": lease.owner,
                "pid": os.getpid(),
                "acquired_at": lease.acquired_at.isoformat(),
            }
            try:
                descriptor = os.open(
                    self._path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if attempt == 0 and self._remove_if_stale():
                    continue
                raise PipelineAlreadyRunningError(self._busy_message()) from None

            try:
                os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            finally:
                os.close(descriptor)
            return lease

        raise PipelineAlreadyRunningError(self._busy_message())

    def release(self, lease: PipelineExecutionLease) -> None:
        """仅当文件仍属于当前租约时删除，避免误释放新执行者的锁。"""

        metadata = self._read_metadata()
        if metadata.get("token") != lease.token:
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            return

    def _remove_if_stale(self) -> bool:
        """只清理超出保守 TTL 的孤儿锁，正常长任务不会被抢占。"""

        try:
            age_seconds = max(0.0, time() - self._path.stat().st_mtime)
        except FileNotFoundError:
            return False
        if age_seconds < self._stale_after_seconds:
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _busy_message(self) -> str:
        """生成不含敏感信息的冲突原因，供日志和管理台呈现。"""

        metadata = self._read_metadata()
        owner = str(metadata.get("owner", "未知执行者"))[:80]
        return f"内容流水线正在由 {owner} 执行，请等待其结束后再重试"

    def _read_metadata(self) -> dict[str, object]:
        """容忍意外截断的锁文件，不让诊断路径二次失败。"""

        try:
            raw = self._path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
