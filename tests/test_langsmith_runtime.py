"""LangSmith runtime 的离线隐私与恰好一次执行测试。"""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable, Iterator
from unittest.mock import patch

from src.observability.langsmith_runtime import (
    ensure_langsmith_privacy_defaults,
    trace_call,
    trace_llm_call,
    trace_stream,
)


class RecordingTraceable:
    """不联网的 traceable 替身，记录 SDK 最终可见的数据。"""

    def __init__(self) -> None:
        self.configs: list[dict[str, object]] = []
        self.inputs: list[object] = []
        self.outputs: list[object] = []
        self.chunks: list[object] = []
        self.errors: list[str] = []

    def __call__(self, **config: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        self.configs.append(config)

        def decorator(function: Callable[..., object]) -> Callable[..., object]:
            def wrapped(*args: object, **kwargs: object) -> object:
                process_inputs = config.get("process_inputs")
                self.inputs.append(process_inputs({"raw": args}) if callable(process_inputs) else args)
                try:
                    result = function(*args, **kwargs)
                except BaseException as exc:
                    self.errors.append(repr(exc))
                    raise
                if hasattr(result, "__next__"):
                    return self._wrap_stream(result, config)
                process_outputs = config.get("process_outputs")
                self.outputs.append(process_outputs(result) if callable(process_outputs) else result)
                return result

            return wrapped

        return decorator

    def _wrap_stream(self, stream: object, config: dict[str, object]) -> Iterator[object]:
        process_chunk = config.get("process_chunk")
        reduce_fn = config.get("reduce_fn")
        reduced_chunks: list[object] = []
        try:
            for chunk in stream:  # type: ignore[union-attr]
                safe_chunk = process_chunk(chunk) if callable(process_chunk) else chunk
                self.chunks.append(safe_chunk)
                reduced_chunks.append(safe_chunk)
                yield chunk
        except BaseException as exc:
            self.errors.append(repr(exc))
            raise
        self.outputs.append(reduce_fn(reduced_chunks) if callable(reduce_fn) else reduced_chunks)


class LangSmithRuntimeTest(unittest.TestCase):
    """验证运行时不会泄露正文，并且业务不会重复执行。"""

    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "LANGSMITH_API_KEY": "test-key-not-a-real-secret",
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_PROJECT": "offline-tests",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_privacy_defaults_force_sensitive_graph_state_hidden(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANGSMITH_HIDE_INPUTS", None)
            os.environ["LANGSMITH_HIDE_OUTPUTS"] = "false"
            ensure_langsmith_privacy_defaults()
            self.assertEqual(os.environ["LANGSMITH_HIDE_INPUTS"], "true")
            self.assertEqual(os.environ["LANGSMITH_HIDE_OUTPUTS"], "true")

    def test_trace_call_uploads_only_sanitized_metadata(self) -> None:
        recorder = RecordingTraceable()
        calls = 0

        def execute() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"answer": "private resume answer"}

        with patch("langsmith.traceable", recorder):
            result = trace_call(
                run_name="career.turn",
                run_type="chain",
                inputs={
                    "attachment_count": 1,
                    "content": "private resume input",
                },
                metadata={
                    "provider": "deepseek",
                    "conversation_id": "conversation-1",
                    "prompt": "my private resume",
                    "api_key": "test-langsmith-key",
                    "note": "untrusted free-form text",
                    "message_count": 2,
                },
                execute=execute,
                summarize=lambda value: {
                    "status": "completed",
                    "output_characters": len(value["answer"]),
                    "content": value["answer"],
                },
            )

        self.assertEqual(result["answer"], "private resume answer")
        self.assertEqual(calls, 1)
        captured = repr((recorder.configs, recorder.inputs, recorder.outputs))
        self.assertNotIn("my private resume", captured)
        self.assertNotIn("super-secret", captured)
        self.assertNotIn("private resume answer", captured)
        self.assertNotIn("private resume input", captured)
        self.assertIn("attachment_count", captured)
        self.assertNotIn("conversation-1", captured)
        self.assertIn("output_characters", captured)

    def test_trace_call_preserves_token_usage_but_sanitizes_tags(self) -> None:
        recorder = RecordingTraceable()
        with patch("langsmith.traceable", recorder):
            trace_call(
                run_name="career.model.chat",
                metadata={"prompt_tokens": 12, "total_tokens": 20},
                tags=["career.llm", "private candidate name"],
                execute=lambda: "done",
            )

        captured = repr(recorder.configs)
        self.assertIn("12", captured)
        self.assertIn("20", captured)
        self.assertIn("career.llm", captured)
        self.assertNotIn("private candidate name", captured)

    def test_trace_call_falls_back_once_when_decorator_fails_before_execution(self) -> None:
        calls = 0

        def broken_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            raise RuntimeError("tracer setup failed")

        def execute() -> str:
            nonlocal calls
            calls += 1
            return "business-result"

        with patch("langsmith.traceable", broken_traceable):
            result = trace_call(run_name="workflow.node", execute=execute)

        self.assertEqual(result, "business-result")
        self.assertEqual(calls, 1)

    def test_trace_call_does_not_retry_when_tracer_fails_after_execution(self) -> None:
        calls = 0

        def late_failure_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(function: Callable[..., object]) -> Callable[..., object]:
                def wrapped(*args: object, **kwargs: object) -> object:
                    function(*args, **kwargs)
                    raise RuntimeError("trace flush failed")

                return wrapped

            return decorator

        def execute() -> str:
            nonlocal calls
            calls += 1
            return "paid-model-result"

        with patch("langsmith.traceable", late_failure_traceable):
            result = trace_call(run_name="career.model.chat", execute=execute)

        self.assertEqual(result, "paid-model-result")
        self.assertEqual(calls, 1)

    def test_trace_call_propagates_business_error_once(self) -> None:
        recorder = RecordingTraceable()
        calls = 0

        def execute() -> str:
            nonlocal calls
            calls += 1
            raise ValueError("business failed")

        with patch("langsmith.traceable", recorder):
            with self.assertRaisesRegex(ValueError, "business failed"):
                trace_call(run_name="career.model.chat", execute=execute)

        self.assertEqual(calls, 1)
        self.assertNotIn("business failed", repr(recorder.errors))
        self.assertIn("business_error:ValueError", repr(recorder.errors))

    def test_trace_call_does_not_upload_identifiers_or_file_names(self) -> None:
        recorder = RecordingTraceable()
        with patch("langsmith.traceable", recorder):
            trace_call(
                run_name="career.document.parse",
                metadata={
                    "conversation_id": "conversation-private-42",
                    "task_run_id": "task-private-42",
                    "user_id": 981273,
                    "file_name": "candidate-private-resume.pdf",
                    "component": "document_parser",
                    "parser_name": "docling-serve",
                },
                execute=lambda: "done",
            )

        captured = repr((recorder.configs, recorder.inputs))
        self.assertNotIn("conversation-private-42", captured)
        self.assertNotIn("task-private-42", captured)
        self.assertNotIn("981273", captured)
        self.assertNotIn("candidate-private-resume.pdf", captured)
        self.assertIn("document_parser", captured)
        self.assertIn("docling-serve", captured)

    def test_trace_llm_call_keeps_existing_contract(self) -> None:
        recorder = RecordingTraceable()
        calls = 0

        def execute() -> str:
            nonlocal calls
            calls += 1
            return "raw model output"

        with patch("langsmith.traceable", recorder):
            result = trace_llm_call(
                run_name="platform.llm.chat",
                provider="deepseek-compatible",
                model="deepseek-chat",
                message_count=3,
                input_characters=42,
                execute=execute,
                summarize=lambda value: {
                    "model": "deepseek-chat",
                    "output_characters": len(value),
                },
                metadata={
                    "attempt_index": 2,
                    "phase": "repair",
                    "reason_code": "quality_contract_repair",
                },
            )

        self.assertEqual(result, "raw model output")
        self.assertEqual(calls, 1)
        self.assertNotIn("raw model output", repr(recorder.outputs))
        self.assertEqual(recorder.configs[0]["run_type"], "llm")
        metadata = recorder.configs[0]["metadata"]
        self.assertEqual(metadata["attempt_index"], 2)
        self.assertEqual(metadata["phase"], "repair")
        self.assertEqual(metadata["reason_code"], "quality_contract_repair")

    def test_trace_stream_yields_raw_chunks_but_records_only_summaries(self) -> None:
        recorder = RecordingTraceable()
        factory_calls = 0

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "private chunk one"
            yield "private chunk two"

        with patch("langsmith.traceable", recorder):
            chunks = list(
                trace_stream(
                    run_name="career.model.stream",
                    metadata={"provider": "deepseek", "prompt": "private prompt"},
                    execute=execute,
                    summarize_chunk=lambda chunk, index: {
                        "chunk_index": index,
                        "output_characters": len(chunk),
                        "content": chunk,
                    },
                )
            )

        self.assertEqual(chunks, ["private chunk one", "private chunk two"])
        self.assertEqual(factory_calls, 1)
        captured = repr((recorder.configs, recorder.inputs, recorder.chunks, recorder.outputs))
        self.assertNotIn("private prompt", captured)
        self.assertNotIn("private chunk one", captured)
        self.assertNotIn("private chunk two", captured)
        self.assertIn("chunk_count", captured)

    def test_trace_stream_resumes_same_iterator_after_trace_failure(self) -> None:
        factory_calls = 0

        def midstream_failure_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(function: Callable[..., object]) -> Callable[..., object]:
                def wrapped(*args: object, **kwargs: object) -> Iterator[str]:
                    traced = iter(function(*args, **kwargs))

                    def proxy() -> Iterator[str]:
                        yield next(traced)
                        raise RuntimeError("trace transport failed")

                    return proxy()

                return wrapped

            return decorator

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "one"
            yield "two"
            yield "three"

        with patch("langsmith.traceable", midstream_failure_traceable):
            chunks = list(trace_stream(run_name="career.model.stream", execute=execute))

        self.assertEqual(chunks, ["one", "two", "three"])
        self.assertEqual(factory_calls, 1)

    def test_trace_stream_hides_business_error_but_preserves_caller_error(self) -> None:
        recorder = RecordingTraceable()
        factory_calls = 0

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "one"
            raise RuntimeError("private provider body and signed URL")

        with patch("langsmith.traceable", recorder):
            stream = trace_stream(run_name="career.model.stream", execute=execute)
            with self.assertRaisesRegex(RuntimeError, "private provider body"):
                list(stream)

        self.assertEqual(factory_calls, 1)
        self.assertNotIn("private provider body", repr(recorder.errors))
        self.assertIn("business_error:RuntimeError", repr(recorder.errors))

    def test_trace_stream_keeps_chunk_consumed_before_trace_failure(self) -> None:
        factory_calls = 0

        def consume_then_fail_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(function: Callable[..., object]) -> Callable[..., object]:
                def wrapped(*args: object, **kwargs: object) -> Iterator[str]:
                    traced = iter(function(*args, **kwargs))

                    def proxy() -> Iterator[str]:
                        next(traced)
                        raise RuntimeError("trace failed after consuming chunk")
                        yield  # pragma: no cover

                    return proxy()

                return wrapped

            return decorator

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "one"
            yield "two"

        with patch("langsmith.traceable", consume_then_fail_traceable):
            chunks = list(trace_stream(run_name="career.model.stream", execute=execute))

        self.assertEqual(chunks, ["one", "two"])
        self.assertEqual(factory_calls, 1)

    def test_trace_stream_keeps_multiple_prefetched_chunks_before_trace_failure(self) -> None:
        factory_calls = 0

        def prefetch_then_fail_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(function: Callable[..., object]) -> Callable[..., object]:
                def wrapped(*args: object, **kwargs: object) -> Iterator[str]:
                    traced = iter(function(*args, **kwargs))

                    def proxy() -> Iterator[str]:
                        next(traced)
                        next(traced)
                        raise RuntimeError("trace failed after prefetching chunks")
                        yield  # pragma: no cover

                    return proxy()

                return wrapped

            return decorator

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "one"
            yield "two"
            yield "three"

        with patch("langsmith.traceable", prefetch_then_fail_traceable):
            chunks = list(trace_stream(run_name="career.model.stream", execute=execute))

        self.assertEqual(chunks, ["one", "two", "three"])
        self.assertEqual(factory_calls, 1)

    def test_trace_stream_keeps_prefetched_chunks_when_business_stream_already_completed(self) -> None:
        factory_calls = 0

        def consume_all_then_fail_traceable(**_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(function: Callable[..., object]) -> Callable[..., object]:
                def wrapped(*args: object, **kwargs: object) -> Iterator[str]:
                    traced = iter(function(*args, **kwargs))

                    def proxy() -> Iterator[str]:
                        list(traced)
                        raise RuntimeError("trace flush failed after consuming stream")
                        yield  # pragma: no cover

                    return proxy()

                return wrapped

            return decorator

        def execute() -> Iterator[str]:
            nonlocal factory_calls
            factory_calls += 1
            yield "one"
            yield "two"

        with patch("langsmith.traceable", consume_all_then_fail_traceable):
            chunks = list(trace_stream(run_name="career.model.stream", execute=execute))

        self.assertEqual(chunks, ["one", "two"])
        self.assertEqual(factory_calls, 1)


if __name__ == "__main__":
    unittest.main()
