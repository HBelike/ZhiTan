"""离线验证 Skill 搜索的 LangSmith 元数据边界与单次执行语义。"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.skill_library_service import SkillLibraryService, SkillSearchResult


class RecordingTraceable:
    """不连接 LangSmith，仅记录 SDK 最终可见的配置、输入和输出。"""

    def __init__(self) -> None:
        self.configs: list[dict[str, object]] = []
        self.inputs: list[object] = []
        self.outputs: list[object] = []

    def __call__(self, **config: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        self.configs.append(config)

        def decorator(function: Callable[..., object]) -> Callable[..., object]:
            def wrapped(*args: object, **kwargs: object) -> object:
                process_inputs = config.get("process_inputs")
                safe_input = process_inputs(args[0]) if callable(process_inputs) else args[0]
                self.inputs.append(safe_input)
                result = function(*args, **kwargs)
                process_outputs = config.get("process_outputs")
                safe_output = process_outputs(result) if callable(process_outputs) else result
                self.outputs.append(safe_output)
                return result

            return wrapped

        return decorator


def _result() -> SkillSearchResult:
    return SkillSearchResult(
        items=[],
        used_llm=True,
        model="deepseek-chat",
        fallback_reason=None,
        search_scope="github_open_skill",
        normalized_query="private normalized query",
        status_message="private status containing https://github.com/private/repository",
        cache_hit=False,
        elapsed_ms=42,
    )


def main() -> int:
    recorder = RecordingTraceable()
    service = SkillLibraryService.__new__(SkillLibraryService)
    calls = 0
    private_query = "private skill keyword https://github.com/private/repository/SKILL.md"

    def execute_once(query: str) -> SkillSearchResult:
        nonlocal calls
        calls += 1
        assert query == private_query
        return _result()

    service._search_skills_untraced = execute_once  # type: ignore[method-assign]
    environment = {
        "LANGSMITH_API_KEY": "offline-test-key",
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_PROJECT": "offline-tests",
    }
    with patch.dict(os.environ, environment, clear=False), patch("langsmith.traceable", recorder):
        result = service.search_skills(private_query)

    assert result.model == "deepseek-chat"
    assert calls == 1, "业务检索必须只执行一次"
    assert recorder.configs[0]["name"] == "skills.search"
    assert recorder.configs[0]["run_type"] == "chain"

    captured = repr((recorder.configs, recorder.inputs, recorder.outputs))
    for forbidden in (
        private_query,
        "private skill keyword",
        "private normalized query",
        "https://github.com/private/repository",
        "SKILL.md",
        "private status",
    ):
        assert forbidden not in captured, f"追踪数据泄漏敏感内容：{forbidden}"
    for required in (
        "query_characters",
        "result_count",
        "used_llm",
        "cache_hit",
        "elapsed_ms",
        "search_scope",
        "deepseek-chat",
    ):
        assert required in captured, f"追踪数据缺少安全元数据：{required}"

    failure_calls = 0

    def fail_once(_: str) -> SkillSearchResult:
        nonlocal failure_calls
        failure_calls += 1
        raise RuntimeError("private business failure")

    service._search_skills_untraced = fail_once  # type: ignore[method-assign]
    with patch.dict(os.environ, environment, clear=False), patch("langsmith.traceable", recorder):
        try:
            service.search_skills(private_query)
        except RuntimeError as exc:
            assert str(exc) == "private business failure"
        else:
            raise AssertionError("原始业务异常必须继续抛给调用方")
    assert failure_calls == 1, "异常路径也必须只执行一次"

    print("LangSmith Skill 搜索隐私边界验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
