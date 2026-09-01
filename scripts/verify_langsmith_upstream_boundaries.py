"""离线验证 Docling 与职位抓取的 LangSmith metadata-only 边界。"""

from __future__ import annotations

import tempfile
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import (
    DoclingServiceDocumentParser,
    DocumentUnderstandingError,
)
from src.career_assistant.job_sources import JobPostingExtractor, JobSourceError
from src.career_assistant.settings import DocumentUnderstandingSettings


def _capture_trace(calls: list[dict[str, Any]]):
    """创建不联网的 Trace 替身，并保留包装器传入的安全字段。"""

    def _trace_operation(**kwargs: Any) -> Any:
        result = kwargs["execute"]()
        summary = kwargs["summarize"](result)
        calls.append(
            {
                "run_name": kwargs["run_name"],
                "run_type": kwargs["run_type"],
                "inputs": kwargs.get("inputs"),
                "metadata": kwargs.get("metadata"),
                "tags": kwargs.get("tags"),
                "summary": summary,
            }
        )
        return result

    return _trace_operation


def _docling_settings() -> DocumentUnderstandingSettings:
    return DocumentUnderstandingSettings(
        enabled=True,
        service_base_url="http://127.0.0.1:5001",
        api_key_env=None,
        request_timeout_seconds=5,
        force_ocr=True,
        table_mode="accurate",
        max_attempts=1,
    )


def _verify_docling_boundary() -> None:
    calls: list[dict[str, Any]] = []
    secret_filename = "candidate-secret-name.pdf"
    secret_text = "PRIVATE_RESUME_BODY_SENTINEL"
    with tempfile.TemporaryDirectory(prefix="langsmith-upstream-") as directory:
        path = Path(directory) / secret_filename
        path.write_bytes(b"safe-test-pdf")
        attachment = AttachmentDescriptor(
            attachment_id=uuid4(),
            kind=AttachmentKind.RESUME_PDF,
            original_filename=secret_filename,
            media_type="application/pdf",
            size_bytes=path.stat().st_size,
            temporary_path=path,
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )

        def success_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "processing_time": 0.25,
                    "document": {"md_content": secret_text},
                },
            )

        parser = DoclingServiceDocumentParser(
            _docling_settings(),
            client=httpx.Client(transport=httpx.MockTransport(success_handler)),
        )
        with patch(
            "src.career_assistant.document_parsing.trace_operation",
            _capture_trace(calls),
        ):
            result = parser.parse_document(attachment)
        parser.close()

        assert result.analysis_text == secret_text
        assert calls[0]["run_name"] == "career.document.docling_parse"
        assert calls[0]["run_type"] == "tool"
        assert calls[0]["summary"]["output_characters"] == len(secret_text)
        serialized_trace = repr(calls)
        for secret in (secret_filename, str(path), secret_text, "127.0.0.1:5001"):
            assert secret not in serialized_trace

        def failing_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "PROVIDER_SECRET_SENTINEL"})

        failing_parser = DoclingServiceDocumentParser(
            _docling_settings(),
            client=httpx.Client(transport=httpx.MockTransport(failing_handler)),
        )
        try:
            with patch(
                "src.career_assistant.document_parsing.trace_operation",
                _capture_trace([]),
            ):
                failing_parser.parse_document(attachment)
        except DocumentUnderstandingError as exc:
            assert "鉴权失败" in str(exc)
        else:
            raise AssertionError("Docling 包装后应保持原有异常语义")
        finally:
            failing_parser.close()


def _verify_job_source_boundary() -> None:
    calls: list[dict[str, Any]] = []
    secret_url = "https://jobs.example.com/PRIVATE_URL_SENTINEL"
    secret_title = "PRIVATE_TITLE_SENTINEL"
    secret_text = "PRIVATE_JOB_TEXT_SENTINEL"

    def success_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=f"<html><title>{secret_title}</title><body>{secret_text}</body></html>",
            request=request,
        )

    extractor = JobPostingExtractor(
        client=httpx.Client(transport=httpx.MockTransport(success_handler)),
    )
    with (
        patch(
            "src.career_assistant.job_sources.trace_operation",
            _capture_trace(calls),
        ),
        patch.object(
            JobPostingExtractor,
            "_validate_public_url",
            side_effect=lambda value: value,
        ),
    ):
        snapshot = extractor.extract(secret_url)
    extractor._client.close()

    assert snapshot.title == secret_title
    assert secret_text in snapshot.visible_text
    assert calls[0]["run_name"] == "career.job_source.fetch"
    assert calls[0]["run_type"] == "tool"
    assert calls[0]["summary"]["title_characters"] == len(secret_title)
    serialized_trace = repr(calls)
    for secret in (secret_url, "jobs.example.com", secret_title, secret_text):
        assert secret not in serialized_trace

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    failing_extractor = JobPostingExtractor(
        client=httpx.Client(transport=httpx.MockTransport(failing_handler)),
    )
    try:
        with (
            patch(
                "src.career_assistant.job_sources.trace_operation",
                _capture_trace([]),
            ),
            patch.object(
                JobPostingExtractor,
                "_validate_public_url",
                side_effect=lambda value: value,
            ),
        ):
            failing_extractor.extract(secret_url)
    except JobSourceError as exc:
        assert "当前不可访问" in str(exc)
    else:
        raise AssertionError("职位抓取包装后应保持原有异常语义")
    finally:
        failing_extractor._client.close()


def main() -> None:
    _verify_docling_boundary()
    _verify_job_source_boundary()
    print("langsmith_upstream_boundaries_ok")


if __name__ == "__main__":
    main()
