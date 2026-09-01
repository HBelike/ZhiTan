"""会话记忆 Worker 设置与并发边界测试。"""

import pytest

from src.career_assistant.settings import CareerMemoryWorkerSettings


def test_memory_worker_defaults_are_low_cost() -> None:
    settings = CareerMemoryWorkerSettings(worker_id="memory-1")
    settings.validate()
    assert settings.worker_concurrency == 1
    assert settings.poll_seconds == 1.0
    assert settings.lease_seconds == 120.0


def test_memory_worker_rejects_short_lease() -> None:
    with pytest.raises(ValueError, match="租约"):
        CareerMemoryWorkerSettings(worker_id="memory-1", lease_seconds=5).validate()
