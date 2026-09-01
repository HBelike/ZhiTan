"""验证 Career LLM、视觉、Embedding 与 RAG 的 metadata-only 追踪边界。"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.career_assistant.cloud_vision as vision_module  # noqa: E402
import src.career_assistant.model_clients as model_module  # noqa: E402
from src.career_assistant.cloud_vision import (  # noqa: E402
    CloudVisionResult,
    CloudVisionRouter,
    OpenAICompatibleCloudVisionClient,
)
from src.career_assistant.contracts import ModelCapability  # noqa: E402
from src.career_assistant.interview_library import embedding as embedding_module  # noqa: E402
from src.career_assistant.interview_library import retrieval as retrieval_module  # noqa: E402
from src.career_assistant.interview_library.embedding import (  # noqa: E402
    OpenAICompatibleEmbeddingClient,
)
from src.career_assistant.interview_library.retrieval import (  # noqa: E402
    InterviewRetrievalService,
)
from src.career_assistant.model_clients import (  # noqa: E402
    ChatMessage,
    OpenAICompatibleChatClient,
)
from src.career_assistant.persistence.model_profile_repository import (  # noqa: E402
    ModelCostTier,
    ModelProfileRecord,
)
from src.career_assistant.settings import (  # noqa: E402
    CloudVisionConnectionSettings,
    CloudVisionSettings,
    InterviewEmbeddingSettings,
    InterviewRetrievalSettings,
)


SENSITIVE_VALUES = (
    "SENTINEL-PRIVATE-PROMPT",
    "SENTINEL-PRIVATE-OUTPUT",
    "data:image/png;base64",
    "test-secret-key",
)


class TraceRecorder:
    """执行真实业务闭包，同时检查追踪数据没有正文、输出或凭证。"""

    def __init__(self) -> None:
        self.names: list[str] = []

    def operation(self, **kwargs: Any) -> Any:
        result = kwargs["execute"]()
        summary = kwargs["summarize"](result)
        self._record(kwargs, summary)
        return result

    def stream(self, **kwargs: Any):
        chunks = list(kwargs["execute"]())
        summaries = [
            kwargs["summarize_chunk"](chunk, index)
            for index, chunk in enumerate(chunks, start=1)
        ]
        summary = {
            "chunk_count": len(summaries),
            "output_characters": sum(item["output_characters"] for item in summaries),
        }
        self._record(kwargs, summary)
        return iter(chunks)

    def _record(self, kwargs: dict[str, Any], summary: dict[str, Any]) -> None:
        serialized = json.dumps(
            {
                "metadata": kwargs.get("metadata"),
                "summary": summary,
            },
            ensure_ascii=False,
            default=str,
        )
        for sensitive in SENSITIVE_VALUES:
            assert sensitive not in serialized
        self.names.append(kwargs["run_name"])


class EmptyInterviewRepository:
    """只覆盖 RAG 追踪测试所需的最小 Repository 契约。"""

    def search_lexical_chunks(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def list_chunks_for_experiences(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def _profile() -> ModelProfileRecord:
    now = datetime.now(UTC)
    return ModelProfileRecord(
        id=uuid4(),
        organization_id=uuid4(),
        profile_key="trace-test",
        display_name="Trace Test",
        provider_key="mock-provider",
        model_id="mock-model",
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.FREE_QUOTA,
        priority=1,
        enabled=True,
        api_base_url="https://mock-provider.example/v1",
        created_at=now,
        updated_at=now,
    )


def _verify_chat(recorder: TraceRecorder) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        if payload.get("stream"):
            return httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"SENTINEL-PRIVATE-OUTPUT"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "SENTINEL-PRIVATE-OUTPUT"}}]},
        )

    original_operation = model_module.trace_operation
    original_stream = model_module.trace_stream
    model_module.trace_operation = recorder.operation
    model_module.trace_stream = recorder.stream
    os.environ["CAREER_TRACE_TEST_KEY"] = "test-secret-key"
    try:
        client = OpenAICompatibleChatClient(httpx.Client(transport=httpx.MockTransport(handler)))
        messages = [ChatMessage(role="user", content="SENTINEL-PRIVATE-PROMPT")]
        assert client.complete(_profile(), "CAREER_TRACE_TEST_KEY", messages)
        assert "".join(
            client.stream_complete(_profile(), "CAREER_TRACE_TEST_KEY", messages),
        )
        assert calls == 2
    finally:
        os.environ.pop("CAREER_TRACE_TEST_KEY", None)
        model_module.trace_operation = original_operation
        model_module.trace_stream = original_stream


def _verify_vision(recorder: TraceRecorder) -> None:
    settings = CloudVisionConnectionSettings(
        connection_key="trace-vision",
        enabled=True,
        provider_key="mock-vision",
        model_id="mock-vision-model",
        api_key_env="CAREER_TRACE_VISION_KEY",
        api_base_url_env="CAREER_TRACE_VISION_BASE_URL",
        request_timeout_seconds=5,
        max_completion_tokens=500,
    )
    os.environ["CAREER_TRACE_VISION_KEY"] = "test-secret-key"
    os.environ["CAREER_TRACE_VISION_BASE_URL"] = "https://vision.example/v1"
    original_operation = vision_module.trace_operation
    vision_module.trace_operation = recorder.operation
    try:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "SENTINEL-PRIVATE-OUTPUT"}}]},
            ),
        )
        client = OpenAICompatibleCloudVisionClient(
            settings,
            client=httpx.Client(transport=transport),
        )
        result = client.analyze_image("image/png", b"private-image-bytes")
        assert result.analysis_text == "SENTINEL-PRIVATE-OUTPUT"

        router = CloudVisionRouter(
            CloudVisionSettings(
                enabled=True,
                active_connection_key="trace-vision",
                fallback_connection_keys=(),
                connections=(settings,),
            ),
        )
        for configured_client in router._clients:
            configured_client.close()

        class FakeVisionClient:
            def analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
                return CloudVisionResult(
                    analysis_text="SENTINEL-PRIVATE-OUTPUT",
                    provider_key="mock-vision",
                    model_id="mock-vision-model",
                )

        router._clients = (FakeVisionClient(),)
        routed = router.analyze_image("image/png", b"private-image-bytes")
        assert routed.provider_key == "mock-vision"
    finally:
        os.environ.pop("CAREER_TRACE_VISION_KEY", None)
        os.environ.pop("CAREER_TRACE_VISION_BASE_URL", None)
        vision_module.trace_operation = original_operation


def _verify_embedding(recorder: TraceRecorder) -> None:
    settings = InterviewEmbeddingSettings(
        enabled=True,
        provider_key="mock-embedding",
        model_id="mock-embedding-model",
        api_key_env="CAREER_TRACE_EMBEDDING_KEY",
        api_base_url_env="CAREER_TRACE_EMBEDDING_BASE_URL",
        expected_dimensions=2,
        max_batch_size=8,
        request_timeout_seconds=5,
    )
    os.environ["CAREER_TRACE_EMBEDDING_KEY"] = "test-secret-key"
    os.environ["CAREER_TRACE_EMBEDDING_BASE_URL"] = "https://embedding.example/v1"
    original_operation = embedding_module.trace_operation
    embedding_module.trace_operation = recorder.operation
    client = OpenAICompatibleEmbeddingClient(settings)
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            ),
        ),
    )
    try:
        result = client.embed_texts(["SENTINEL-PRIVATE-PROMPT"])
        assert result.vectors == ((0.1, 0.2),)
    finally:
        client.close()
        os.environ.pop("CAREER_TRACE_EMBEDDING_KEY", None)
        os.environ.pop("CAREER_TRACE_EMBEDDING_BASE_URL", None)
        embedding_module.trace_operation = original_operation


def _verify_retrieval(recorder: TraceRecorder) -> None:
    disabled_embedding = InterviewEmbeddingSettings(
        enabled=False,
        provider_key="disabled",
        model_id=None,
        api_key_env=None,
        api_base_url_env=None,
        expected_dimensions=2,
        max_batch_size=8,
        request_timeout_seconds=5,
    )
    settings = InterviewRetrievalSettings(
        lexical_candidate_limit=8,
        semantic_candidate_limit=8,
        final_limit=4,
        reciprocal_rank_fusion_k=60,
        embedding=disabled_embedding,
    )
    original_operation = retrieval_module.trace_operation
    retrieval_module.trace_operation = recorder.operation
    try:
        service = InterviewRetrievalService(EmptyInterviewRepository(), settings)
        result = service.retrieve(uuid4(), "SENTINEL-PRIVATE-PROMPT")
        assert result.retrieval_mode == "lexical"
        assert not result.candidates
    finally:
        retrieval_module.trace_operation = original_operation


def main() -> None:
    recorder = TraceRecorder()
    _verify_chat(recorder)
    _verify_vision(recorder)
    _verify_embedding(recorder)
    _verify_retrieval(recorder)
    expected = {
        "career.chat.completion",
        "career.chat.stream",
        "career.vision.provider_call",
        "career.vision.route",
        "interview.embedding.batch",
        "interview.rag.retrieve",
    }
    assert expected.issubset(set(recorder.names)), recorder.names
    print("langsmith_career_boundaries_ok")


if __name__ == "__main__":
    main()
