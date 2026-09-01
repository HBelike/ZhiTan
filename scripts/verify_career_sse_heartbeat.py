"""离线验证求职助手 SSE 保活与异常桥接，不访问数据库或外部模型。"""

from __future__ import annotations

import sys
from pathlib import Path
from threading import Event
from time import sleep


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.web.router import _stream_events_with_heartbeats  # noqa: E402


def _slow_events():
    """模拟 Docling 在首个业务事件前暂时无输出。"""

    sleep(0.04)
    yield "event: progress\ndata: {\"key\": \"parsed\"}\n\n"


def _broken_events():
    """模拟业务生成器意外异常，必须转化为 SSE 错误帧。"""

    raise RuntimeError("unexpected")
    yield ""


def _events_that_must_finish_after_disconnect(completed: Event):
    """模拟浏览器已断开，但模型仍需完成持久化收口的场景。"""

    yield "event: progress\ndata: {\"key\": \"started\"}\n\n"
    sleep(0.03)
    completed.set()
    yield "event: done\ndata: {}\n\n"


def main() -> None:
    """确保长处理期间有心跳，且异常不丢失到后台线程。"""

    frames = list(
        _stream_events_with_heartbeats(
            _slow_events(),
            heartbeat_seconds=0.01,
            expected_turn_seconds=0.02,
        ),
    )
    assert ": keepalive\n\n" in frames
    assert any("extended_processing" in frame for frame in frames)
    assert any("parsed" in frame for frame in frames)

    error_frames = list(
        _stream_events_with_heartbeats(
            _broken_events(),
            heartbeat_seconds=0.01,
            expected_turn_seconds=1,
        ),
    )
    assert len(error_frames) == 1
    assert "服务处理出现异常" in error_frames[0]

    completion_signal = Event()
    disconnected_stream = _stream_events_with_heartbeats(
        _events_that_must_finish_after_disconnect(completion_signal),
        heartbeat_seconds=0.01,
        expected_turn_seconds=1,
    )
    next(disconnected_stream)
    disconnected_stream.close()
    assert completion_signal.wait(timeout=0.5)
    print("career_sse_heartbeat_ok")


if __name__ == "__main__":
    main()
