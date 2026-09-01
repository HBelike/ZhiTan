"""验证单机跨容器流水线锁的互斥、释放与孤儿锁恢复行为。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from time import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.pipeline_execution_lock import PipelineAlreadyRunningError, PipelineExecutionLock


def main() -> None:
    """不接触真实数据卷，在临时目录中验证锁生命周期。"""

    with tempfile.TemporaryDirectory() as temporary_directory:
        lock_path = Path(temporary_directory) / "pipeline.lock"
        first_lock = PipelineExecutionLock(lock_path, stale_after_seconds=900)
        second_lock = PipelineExecutionLock(lock_path, stale_after_seconds=900)

        with first_lock.hold("first"):
            try:
                second_lock.acquire("second")
            except PipelineAlreadyRunningError:
                pass
            else:
                raise AssertionError("已有执行者时不应获得第二把锁")

        with second_lock.hold("second"):
            assert lock_path.exists()
        assert not lock_path.exists()

        lock_path.write_text('{"owner":"crashed"}', encoding="utf-8")
        stale_time = time() - 901
        os.utime(lock_path, (stale_time, stale_time))
        with PipelineExecutionLock(lock_path, stale_after_seconds=900).hold("recovered"):
            assert lock_path.exists()

    print("pipeline_execution_lock_ok")


if __name__ == "__main__":
    main()
