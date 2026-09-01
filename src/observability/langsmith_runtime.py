"""Privacy-safe LangSmith tracing helpers.

The platform processes resumes, interview notes, uploaded documents, model
credentials, and signed source URLs.  Those values must never be sent to an
observability provider.  This module therefore exposes only metadata-oriented
helpers: callers provide a zero-argument business operation and optional safe
summaries, while raw arguments, prompts, outputs, files, and credentials stay
outside the decorated function.

Tracing is best-effort and must never retry a billable business operation.  If
LangSmith fails before the operation starts, the operation is executed once
without tracing.  If tracing fails after the operation starts, the already
produced result (or the remaining original stream) is used instead.
"""

from __future__ import annotations

import logging
import os
import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar


T = TypeVar("T")
logger = logging.getLogger(__name__)

_HIDDEN_VALUE = "[hidden by privacy_mode=metadata_only]"
_REDACTED_VALUE = "[redacted]"
_DEFAULT_PROJECT = "ai-administration-platform"
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:sk|lsv2|ark|github_pat|ghp|re)_[a-z0-9_-]{8,})"
)
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "apikey",
        "api_key",
        "authorization",
        "body",
        "content",
        "cookie",
        "credential",
        "document",
        "file",
        "headers",
        "image",
        "input_text",
        "message",
        "output_text",
        "password",
        "payload",
        "prompt",
        "raw",
        "request",
        "response",
        "resume",
        "secret",
        "text",
        "token",
        "url",
    }
)
_SAFE_STRING_KEYS = frozenset(
    {
        "component",
        "error_class",
        "format",
        "method",
        "model",
        "model_id",
        "node",
        "operation",
        "parser",
        "parser_name",
        "phase",
        "privacy_mode",
        "provider",
        "provider_key",
        "reason_code",
        "retrieval_mode",
        "run_name",
        "source",
        "source_kind",
        "stage",
        "status",
        "task_name",
        "trigger",
        "trigger_type",
        "type",
    }
)


class _SanitizedTraceError(RuntimeError):
    """只让 LangSmith 看见稳定错误类别，不上传业务异常原文。"""

    def __init__(self, error_class: str) -> None:
        super().__init__(f"business_error:{error_class}")


def ensure_langsmith_privacy_defaults() -> None:
    """强制隐藏 SDK 自动采集的输入输出。

    该项目的 LangGraph state 会携带简历、职位、面经、OCR 与聊天正文。即使运维
    误把开关配置成 ``false``，也不能把这些内容上传到观测平台；需要做离线评测时
    应使用独立的脱敏数据集和独立进程，而不是放宽线上进程的隐私边界。
    """

    os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
    os.environ["LANGSMITH_HIDE_OUTPUTS"] = "true"


def is_langsmith_enabled() -> bool:
    """Return whether tracing has an API key and is explicitly enabled."""

    tracing = os.getenv("LANGSMITH_TRACING", os.getenv("LANGSMITH_TRACING_V2", "true"))
    return bool(os.getenv("LANGSMITH_API_KEY", "").strip()) and tracing.strip().lower() == "true"


def trace_call(
    *,
    run_name: str,
    execute: Callable[[], T],
    run_type: str = "chain",
    inputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    summarize: Callable[[T], Mapping[str, Any]] | None = None,
    tags: Iterable[str] | None = None,
) -> T:
    """Execute one operation and emit a metadata-only LangSmith run.

    ``execute`` is guaranteed to be invoked at most once.  A tracing failure
    before invocation falls back to one untraced invocation.  A tracing or
    summary failure after a successful invocation returns the stored business
    result rather than invoking it again.
    """

    if not is_langsmith_enabled():
        return execute()

    ensure_langsmith_privacy_defaults()
    traceable = _load_traceable()
    if traceable is None:
        return execute()

    safe_metadata = _sanitize_mapping(metadata or {})
    safe_metadata["privacy_mode"] = "metadata_only"
    safe_input = _build_safe_input(run_name, safe_metadata, inputs or {})
    state: _CallState[T] = _CallState()

    def _operation(_: Mapping[str, Any]) -> dict[str, Any]:
        state.started = True
        try:
            value = execute()
        except BaseException as exc:
            state.business_error = exc
            state.business_traceback = exc.__traceback__
            if isinstance(exc, Exception):
                # LangSmith 会把异常 repr 和 traceback 写入 run.error；不能把
                # Provider 原始响应、URL、路径或用户正文随业务异常一起上传。
                raise _SanitizedTraceError(exc.__class__.__name__) from None
            raise

        state.result = value
        state.has_result = True
        if summarize is None:
            return {"status": "completed", "privacy_mode": "metadata_only"}
        return _sanitize_mapping(summarize(value))

    try:
        traced_operation = traceable(
            name=run_name,
            run_type=run_type,
            metadata=safe_metadata,
            tags=_sanitize_tags(tags),
            project_name=os.getenv("LANGSMITH_PROJECT", _DEFAULT_PROJECT),
            process_inputs=lambda _: dict(safe_input),
            process_outputs=_sanitize_trace_output,
        )(_operation)
        traced_operation(safe_input)
        if not state.started:
            # 防御行为异常的 decorator：业务尚未启动时允许执行唯一一次降级调用。
            return execute()
    except BaseException as exc:
        if state.business_error is not None:
            raise state.business_error.with_traceback(state.business_traceback)
        if not isinstance(exc, Exception):
            raise
        if state.has_result:
            _log_trace_failure(run_name, exc)
            return state.result  # type: ignore[return-value]
        if state.started:
            # ``execute`` 已经开始但既未返回也未抛出可恢复业务异常，绝不能重试。
            raise
        _log_trace_failure(run_name, exc)
        return execute()

    return state.result  # type: ignore[return-value]


def trace_operation(
    *,
    run_name: str,
    execute: Callable[[], T],
    run_type: str = "chain",
    inputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    summarize: Callable[[T], Mapping[str, Any]] | None = None,
    tags: Iterable[str] | None = None,
) -> T:
    """Alias with an operation-oriented name for workflow and graph nodes."""

    return trace_call(
        run_name=run_name,
        execute=execute,
        run_type=run_type,
        inputs=inputs,
        metadata=metadata,
        summarize=summarize,
        tags=tags,
    )


def trace_stream(
    *,
    run_name: str,
    execute: Callable[[], Iterator[T]],
    run_type: str = "llm",
    inputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    summarize_chunk: Callable[[T, int], Mapping[str, Any]] | None = None,
    tags: Iterable[str] | None = None,
) -> Iterator[T]:
    """Yield one business stream while tracing only sanitized chunk summaries.

    The stream factory is invoked lazily and at most once.  If LangSmith fails
    after streaming starts, iteration resumes from the same underlying iterator
    so a model request is never restarted and no chunk is duplicated.
    """

    def _iterator() -> Iterator[T]:
        if not is_langsmith_enabled():
            yield from execute()
            return

        ensure_langsmith_privacy_defaults()
        traceable = _load_traceable()
        if traceable is None:
            yield from execute()
            return

        safe_metadata = _sanitize_mapping(metadata or {})
        safe_metadata["privacy_mode"] = "metadata_only"
        safe_input = _build_safe_input(run_name, safe_metadata, inputs or {})
        state: _StreamState[T] = _StreamState()

        def _business_stream(_: Mapping[str, Any]) -> Iterator[T]:
            state.started = True
            try:
                stream = iter(execute())
                state.stream = stream
                for item in stream:
                    state.chunk_count += 1
                    # traceable 的流包装器可能会预取多个片段。先将所有已由业务流
                    # 产生、但尚未交给调用方的片段排队；观测中途失败时按原顺序补发。
                    state.pending_chunks.append(item)
                    yield item
                state.completed = True
            except BaseException as exc:
                state.business_error = exc
                state.business_traceback = exc.__traceback__
                if isinstance(exc, Exception):
                    # 与非流式调用一致：Trace 只记录错误类别，调用方仍在外层收到
                    # 原始业务异常及原 traceback。
                    raise _SanitizedTraceError(exc.__class__.__name__) from None
                raise

        def _process_chunk(chunk: T) -> dict[str, Any]:
            index = state.chunk_count
            if summarize_chunk is None:
                return {"chunk_index": index, "content": _HIDDEN_VALUE}
            try:
                return _sanitize_mapping(summarize_chunk(chunk, index))
            except Exception as exc:
                logger.warning(
                    "LangSmith 流片段摘要失败，已隐藏片段：run=%s error=%s",
                    run_name,
                    exc.__class__.__name__,
                )
                return {"chunk_index": index, "content": _HIDDEN_VALUE}

        def _reduce_chunks(chunks: list[Mapping[str, Any]]) -> dict[str, Any]:
            return {
                "status": "completed" if state.completed else "closed",
                "chunk_count": len(chunks),
                "privacy_mode": "metadata_only",
            }

        traced_iterator: Iterator[T] | None = None
        try:
            traced_stream = traceable(
                name=run_name,
                run_type=run_type,
                metadata=safe_metadata,
                tags=_sanitize_tags(tags),
                project_name=os.getenv("LANGSMITH_PROJECT", _DEFAULT_PROJECT),
                process_inputs=lambda _: dict(safe_input),
                process_chunk=_process_chunk,
                reduce_fn=_reduce_chunks,
            )(_business_stream)
            traced_iterator = iter(traced_stream(safe_input))
            for item in traced_iterator:
                if state.pending_chunks:
                    state.pending_chunks.popleft()
                state.delivered_count += 1
                yield item
            return
        except GeneratorExit:
            raise
        except BaseException as exc:
            if state.business_error is not None:
                while state.pending_chunks:
                    state.delivered_count += 1
                    yield state.pending_chunks.popleft()
                raise state.business_error.with_traceback(state.business_traceback)
            if not isinstance(exc, Exception):
                raise
            if not state.started:
                _log_trace_failure(run_name, exc)
                yield from execute()
                return
            if state.stream is None:
                # 流工厂已进入但未返回迭代器，无法安全判断业务状态，绝不重试。
                raise
            _log_trace_failure(run_name, exc)
            while state.pending_chunks:
                # 某些 decorator 会预取一个或多个片段后才失败；逐个补发，但仍
                # 从同一个模型迭代器续传，既不丢片段也不产生第二次付费请求。
                state.delivered_count += 1
                yield state.pending_chunks.popleft()
            if state.completed:
                return
            # Trace 中途失败时，直接从原始迭代器当前位置继续。
            try:
                yield from state.stream
                state.completed = True
            except BaseException as business_exc:
                raise business_exc
        finally:
            if traced_iterator is not None and hasattr(traced_iterator, "close"):
                try:
                    traced_iterator.close()  # type: ignore[attr-defined]
                except Exception:
                    # 关闭观测包装器失败不能覆盖业务流的返回或异常。
                    pass

    return _iterator()


def trace_llm_call(
    *,
    run_name: str,
    provider: str,
    model: str,
    message_count: int,
    input_characters: int,
    execute: Callable[[], T],
    summarize: Callable[[T], dict[str, Any]],
    metadata: Mapping[str, Any] | None = None,
) -> T:
    """Backward-compatible metadata-only wrapper for one LLM request."""

    trace_metadata = dict(metadata or {})
    # 核心供应商信息由封装器写入，避免调用方意外覆盖模型归属。
    trace_metadata.update(
        {
            "provider": provider,
            "model": model,
            "message_count": message_count,
            "input_characters": input_characters,
        }
    )

    return trace_call(
        run_name=run_name,
        run_type="llm",
        metadata=trace_metadata,
        execute=execute,
        summarize=summarize,
    )


@dataclass
class _CallState:
    """Track whether a single business operation was already invoked."""

    started: bool = False
    has_result: bool = False
    result: Any = None
    business_error: BaseException | None = None
    business_traceback: Any = None


@dataclass
class _StreamState:
    """Track the original iterator so tracing failures can resume it safely."""

    started: bool = False
    completed: bool = False
    chunk_count: int = 0
    delivered_count: int = 0
    pending_chunks: deque[Any] = field(default_factory=deque)
    stream: Iterator[Any] | None = None
    business_error: BaseException | None = None
    business_traceback: Any = None


def _load_traceable() -> Callable[..., Any] | None:
    """Load LangSmith lazily so the business path works without the SDK."""

    try:
        from langsmith import traceable
    except Exception as exc:
        logger.warning("LangSmith 初始化失败，已跳过观测：%s", exc.__class__.__name__)
        return None
    return traceable


def _build_safe_input(
    run_name: str,
    metadata: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the only input object ever passed to a traced callable."""

    return {
        "operation": run_name,
        "privacy_mode": "metadata_only",
        "metadata": dict(metadata),
        "input_summary": _sanitize_mapping(inputs),
        "content": _HIDDEN_VALUE,
    }


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively retain counters and operational labels while hiding text."""

    return {str(key): _sanitize_value(item, key=str(key)) for key, item in value.items()}


def _sanitize_value(value: Any, *, key: str) -> Any:
    normalized_key = key.strip().lower()
    if _is_identifier_key(normalized_key):
        return _REDACTED_VALUE
    if isinstance(value, (int, float)) and not isinstance(value, bool) and _is_metric_key(normalized_key):
        return value
    if _is_sensitive_key(normalized_key):
        return _REDACTED_VALUE
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_value(item, key=normalized_key) for item in value]
    if isinstance(value, bytes):
        return {"hidden_bytes": len(value)}
    if isinstance(value, str):
        if _SECRET_VALUE_PATTERN.search(value):
            return _REDACTED_VALUE
        if _is_safe_string_key(normalized_key):
            return value[:160]
        return _HIDDEN_VALUE
    return {"hidden_type": value.__class__.__name__}


def _is_sensitive_key(key: str) -> bool:
    if key in {"input_characters", "output_characters", "message_count", "token_count"}:
        return False
    components = {part for part in re.split(r"[^a-z0-9]+", key) if part}
    return key in _SENSITIVE_KEY_PARTS or bool(components & _SENSITIVE_KEY_PARTS)


def _is_metric_key(key: str) -> bool:
    return key in {"latency", "pages", "top_k"} or key.endswith(
        (
            "_bytes",
            "_characters",
            "_count",
            "_dimensions",
            "_milliseconds",
            "_ms",
            "_pages",
            "_seconds",
            "_tokens",
        )
    )


def _is_identifier_key(key: str) -> bool:
    """屏蔽任意类型的业务标识符，避免数字 ID 绕过字符串白名单。"""

    if key in _SAFE_STRING_KEYS:
        return False
    return key in {"id", "uuid", "email", "filename", "file_name", "path"} or key.endswith(
        ("_id", "_uuid", "_email", "_filename", "_file_name", "_path")
    )


def _is_safe_string_key(key: str) -> bool:
    return (
        key in _SAFE_STRING_KEYS
        or key.endswith("_type")
        or key.endswith("_status")
        or key.endswith("_model")
        or key.endswith("_provider")
        or key.endswith("_class")
        or key.endswith("_stage")
        or key.endswith("_mode")
        or key.endswith("_format")
    )


def _sanitize_tags(tags: Iterable[str] | None) -> list[str] | None:
    if tags is None:
        return None
    safe_tags: list[str] = []
    for tag in tags:
        value = str(tag)
        if _SECRET_VALUE_PATTERN.search(value) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
            safe_tags.append(_REDACTED_VALUE)
        else:
            safe_tags.append(value)
    return safe_tags


def _sanitize_trace_output(output: Any) -> dict[str, Any]:
    if isinstance(output, Mapping):
        return _sanitize_mapping(output)
    return {"status": "completed", "privacy_mode": "metadata_only"}


def _log_trace_failure(run_name: str, exc: BaseException) -> None:
    """Log only the exception class; provider messages may echo credentials."""

    logger.warning(
        "LangSmith 观测失败，业务继续：run=%s error=%s",
        run_name,
        exc.__class__.__name__,
    )
