"""离线验证小红书批量采集的元数据与远程图片临时存储接缝。"""

from __future__ import annotations

import base64
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentSettings, TemporaryAttachmentStore
from src.career_assistant.contracts import AttachmentKind
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "a1jPAAAAAElFTkSuQmCC",
)


def _verify_metadata_contract() -> None:
    """确认元数据只能是 JSON 对象，且领域对象保留向后兼容的空默认值。"""

    now = datetime.now(UTC)
    organization_id = uuid4()
    job = InterviewCollectionJobRecord(
        id=uuid4(),
        organization_id=organization_id,
        platform_key="xiaohongshu",
        keyword="东方财富面经",
        requested_limit=20,
        connector_kind=CollectionConnectorKind.URL_IMPORT,
        status=CollectionJobStatus.QUEUED,
        policy_decision="用户授权的公开链接导入",
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    candidate = InterviewCollectionCandidateRecord(
        id=uuid4(),
        collection_job_id=job.id,
        source_url="https://www.xiaohongshu.com/explore/example",
        canonical_url="https://www.xiaohongshu.com/explore/example",
        source_platform="xiaohongshu",
        title=None,
        snippet=None,
        published_at=None,
        extracted_markdown=None,
        content_hash=None,
        status=CollectionCandidateStatus.DISCOVERED,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    assert job.metadata_json == {}
    assert candidate.metadata_json == {}
    assert InterviewLibraryRepository._normalize_metadata_json(
        {"image_count": 3, "ocr": {"available": True}},
        "测试元数据",
    ) == {"image_count": 3, "ocr": {"available": True}}

    try:
        InterviewLibraryRepository._normalize_metadata_json(
            {"not_json": object()},
            "测试元数据",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("不可序列化对象必须被拒绝")


def _verify_remote_image_storage() -> None:
    """确认下载后的图片字节可进入临时附件路径且可被安全清理。"""

    with tempfile.TemporaryDirectory(prefix="career-metadata-check-") as root:
        store = TemporaryAttachmentStore(
            AttachmentSettings(
                temporary_root=Path(root),
                max_size_bytes=1024 * 1024,
                ttl_seconds=300,
            ),
        )
        attachment = store.save_bytes(
            _ONE_PIXEL_PNG,
            "xiaohongshu-evidence.png",
            "image/png; charset=binary",
            AttachmentKind.INTERVIEW_EVIDENCE_IMAGE,
        )
        assert attachment.kind is AttachmentKind.INTERVIEW_EVIDENCE_IMAGE
        assert attachment.temporary_path.exists()
        assert attachment.temporary_path.read_bytes() == _ONE_PIXEL_PNG
        store.cleanup((attachment,))
        assert not attachment.temporary_path.exists()


def main() -> None:
    _verify_metadata_contract()
    _verify_remote_image_storage()
    print("interview collection metadata contract: ok")


if __name__ == "__main__":
    main()
