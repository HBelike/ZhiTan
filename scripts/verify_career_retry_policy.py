"""验证求职助手阶段级重试边界，不访问真实模型、OCR 或外网服务。"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.cloud_vision import (  # noqa: E402
    CloudVisionError,
    CloudVisionResult,
    CloudVisionRouter,
)
from src.career_assistant.contracts import (  # noqa: E402
    AttachmentDescriptor,
    AttachmentKind,
)
from src.career_assistant.document_parsing import DoclingServiceDocumentParser  # noqa: E402
from src.career_assistant.model_clients import (  # noqa: E402
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.settings import (  # noqa: E402
    CloudVisionConnectionSettings,
    CloudVisionSettings,
    DocumentUnderstandingSettings,
)


def _document_attachment(path: Path) -> AttachmentDescriptor:
    """构造无真实简历内容的临时 PDF 描述符。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=AttachmentKind.RESUME_PDF,
        original_filename="candidate.pdf",
        media_type="application/pdf",
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )


def _verify_docling_retry() -> None:
    """Docling 仅在短暂服务故障时执行一次受控重试。"""

    with tempfile.TemporaryDirectory(prefix="career-retry-test-") as directory:
        document_path = Path(directory) / "source.pdf"
        document_path.write_bytes(b"not-a-real-pdf-but-a-valid-upload-boundary")
        call_count = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "document": {"md_content": "# 可分析文本\n- Java"},
                },
            )

        parser = DoclingServiceDocumentParser(
            DocumentUnderstandingSettings(
                enabled=True,
                service_base_url="http://127.0.0.1:5001",
                api_key_env=None,
                request_timeout_seconds=5,
                force_ocr=True,
                table_mode="accurate",
                max_attempts=2,
                retry_backoff_seconds=0.001,
            ),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            result = parser.parse_document(_document_attachment(document_path))
        finally:
            parser.close()

        assert call_count == 2
        assert result.analysis_text.endswith("Java")


class _RetryableVisionClient:
    """用可控替身验证视觉路由器不会无限重复调用。"""

    def __init__(self) -> None:
        self.call_count = 0

    def analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        self.call_count += 1
        assert media_type == "image/png"
        assert image_bytes == b"image"
        if self.call_count == 1:
            raise CloudVisionError("临时服务异常", retryable=True)
        return CloudVisionResult(
            analysis_text="图片类型：简历",
            provider_key="mock",
            model_id="mock-vision",
        )

    def close(self) -> None:
        """与真实客户端保持同一资源释放契约。"""


def _verify_vision_retry() -> None:
    """云端视觉服务失败一次后只重试一次，并返回第二次成功结果。"""

    connection = CloudVisionConnectionSettings(
        connection_key="vision-primary",
        enabled=True,
        provider_key="mock",
        model_id="mock-vision",
        api_key_env="UNUSED_VISION_KEY",
        api_base_url_env="UNUSED_VISION_BASE_URL",
        request_timeout_seconds=5,
        max_completion_tokens=500,
    )
    router = CloudVisionRouter(
        CloudVisionSettings(
            enabled=True,
            active_connection_key="vision-primary",
            fallback_connection_keys=(),
            connections=(connection,),
            max_attempts=2,
            retry_backoff_seconds=0.001,
        ),
    )
    fake_client = _RetryableVisionClient()
    original_clients = router._clients
    router._clients = (fake_client,)
    try:
        result = router.analyze_image("image/png", b"image")
    finally:
        router.close()
        for client in original_clients:
            client.close()

    assert fake_client.call_count == 2
    assert result.analysis_text == "图片类型：简历"


def _verify_model_error_classification() -> None:
    """模型服务 5xx 是可重试故障，401 等鉴权错误不会被重复提交。"""

    service_error_client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        ),
    )
    try:
        service_error_client.test_connection(
            ModelConnectionTarget(
                provider_key="openrouter",
                model_id="mock-model",
                api_base_url="https://mock.example/v1",
            ),
            "test-key",
        )
    except ModelInvocationError as exc:
        assert exc.retryable is True
    else:
        raise AssertionError("503 应被映射为可重试的模型故障")

    credential_error_client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(401)),
        ),
    )
    try:
        credential_error_client.test_connection(
            ModelConnectionTarget(
                provider_key="openrouter",
                model_id="mock-model",
                api_base_url="https://mock.example/v1",
            ),
            "test-key",
        )
    except ModelInvocationError as exc:
        assert exc.retryable is False
    else:
        raise AssertionError("401 不应被重复提交")


def main() -> None:
    """执行全部离线重试策略验证。"""

    _verify_docling_retry()
    _verify_vision_retry()
    _verify_model_error_classification()
    print("career_retry_policy_ok")


if __name__ == "__main__":
    main()
