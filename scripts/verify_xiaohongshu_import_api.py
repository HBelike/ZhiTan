"""小红书面经导入 HTTP 路由与后台任务的离线回归验证。

此脚本不访问小红书、不调用真实模型，也不连接 PostgreSQL。它用真实 FastAPI
``BackgroundTasks`` 调用链与可注入的失败采集器，验证四个对外契约：

* 创建接口立即返回 HTTP 202 与可 JSON 序列化的 job metadata，且默认等待人工确认；
* 后台任务发生入口页读取失败时，持久化状态会收敛为 ``FAILED``，不会卡在
  ``RUNNING``；
* 非小红书链接会在创建阶段返回 422；
* FastAPI shutdown 会继续调用 collection service 的 ``close``，释放采集器连接。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import src.career_assistant.web.router as career_router
from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewCollectionService,
)
from src.career_assistant.interview_library.models import (
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionJobRecord,
)


ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000222")


class MiniRepository:
    """只实现本次接口与后台任务必经的 job 状态写入契约。"""

    def __init__(self) -> None:
        self.jobs: dict[UUID, InterviewCollectionJobRecord] = {}

    def create_collection_job(self, **kwargs) -> InterviewCollectionJobRecord:  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        record = InterviewCollectionJobRecord(
            id=uuid4(),
            organization_id=kwargs["organization_id"],
            platform_key=kwargs["platform_key"],
            keyword=kwargs["keyword"],
            requested_limit=kwargs["requested_limit"],
            connector_kind=kwargs["connector_kind"],
            status=CollectionJobStatus.QUEUED,
            policy_decision=kwargs["policy_decision"],
            error_code=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
            metadata_json=dict(kwargs.get("metadata_json") or {}),
        )
        self.jobs[record.id] = record
        return record

    def get_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        record = self.jobs.get(job_id)
        return record if record and record.organization_id == organization_id else None

    def claim_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        """模拟生产仓储的 queued→running 原子抢占语义。"""

        current = self.get_collection_job(organization_id, job_id)
        if current is None or current.status is not CollectionJobStatus.QUEUED:
            return None
        return self.update_collection_job_status(
            organization_id,
            job_id,
            status=CollectionJobStatus.RUNNING,
        )

    def update_collection_job_status(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        status: CollectionJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata_json: dict[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionJobRecord:
        current = self.get_collection_job(organization_id, job_id)
        assert current is not None
        metadata = dict(current.metadata_json) if merge_metadata else {}
        if metadata_json is not None:
            metadata.update(metadata_json)
        now = datetime.now(UTC)
        record = replace(
            current,
            status=status,
            error_code=error_code,
            error_message=error_message,
            metadata_json=metadata,
            started_at=current.started_at or (
                now if status is CollectionJobStatus.RUNNING else None
            ),
            completed_at=(
                now
                if status in {CollectionJobStatus.SUCCEEDED, CollectionJobStatus.FAILED}
                else None
            ),
            updated_at=now,
        )
        self.jobs[job_id] = record
        return record


class FailingPublicSourceAdapter:
    """模拟公开列表页没有暴露笔记，覆盖任务入口失败后的状态收敛。"""

    def __init__(self) -> None:
        self.closed = False
        self.collect_calls = 0

    def collect(self, _source_url: str, *, requested_limit: int):  # type: ignore[no-untyped-def]
        assert requested_limit == 2
        self.collect_calls += 1
        raise CollectionOperationError(
            "source_content_unavailable",
            "测试用公开页面未暴露可导入笔记。",
        )

    def close(self) -> None:
        self.closed = True


class NoopLibraryService:
    """入口发现失败前不会调用入库服务，保留注入形状即可。"""


class ClosingServices:
    """模拟应用共享服务容器，验证 shutdown 仍会委派给采集服务。"""

    def __init__(self, collection_service: InterviewCollectionService) -> None:
        self._collection_service = collection_service
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._collection_service.close()


def main() -> None:
    """以真实路由和 BackgroundTasks 验证接口响应、终态与关闭调用链。"""

    repository = MiniRepository()
    adapter = FailingPublicSourceAdapter()
    collection_service = InterviewCollectionService(
        repository,
        NoopLibraryService(),
        xiaohongshu_source_adapter=adapter,
    )
    closing_services = ClosingServices(collection_service)
    original_get_services = career_router.get_career_services
    app = FastAPI()
    career_router.install_career_assistant_api(app, PROJECT_ROOT)
    app.state.career_assistant_services = closing_services

    def get_fake_services(_request):  # type: ignore[no-untyped-def]
        return SimpleNamespace(interview_collection_service=collection_service)

    career_router.get_career_services = get_fake_services
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/career/interview-library/xiaohongshu-imports",
                json={
                    "source_url": (
                        "https://www.xiaohongshu.com/search_result?keyword=%E9%9D%A2%E7%BB%8F"
                    ),
                    "requested_limit": 2,
                    "include_images": True,
                },
            )
            assert response.status_code == 202, response.text
            assert response.headers["content-type"].startswith("application/json")
            payload = response.json()
            json.dumps(payload, ensure_ascii=False)
            job_payload = payload["job"]
            assert job_payload["status"] == "queued"
            assert job_payload["metadata"] == {
                "source_kind": "xiaohongshu_public_url",
                "source_url": (
                    "https://www.xiaohongshu.com/search_result?keyword=%E9%9D%A2%E7%BB%8F"
                ),
                "include_images": True,
                "auto_import": False,
                "phase": "discover",
                "progress_percent": 0,
                "progress_message": "任务已创建，正在等待后台读取公开页面。",
                "summary": {
                    "discovered_count": 0,
                    "processed_count": 0,
                    "valid_count": 0,
                    "imported_count": 0,
                    "rejected_count": 0,
                    "incomplete_count": 0,
                    "failed_count": 0,
                },
            }

            job_id = UUID(job_payload["id"])
            persisted = repository.jobs[job_id]
            assert adapter.collect_calls == 1
            assert persisted.status is CollectionJobStatus.FAILED
            assert persisted.error_code == "source_content_unavailable"
            assert persisted.metadata_json["phase"] == "failed"
            assert persisted.metadata_json["progress_percent"] == 100
            assert persisted.completed_at is not None

            unsupported = client.post(
                "/api/career/interview-library/xiaohongshu-imports",
                json={
                    "source_url": "https://example.com/interview/123",
                    "requested_limit": 2,
                },
            )
            assert unsupported.status_code == 422, unsupported.text
            assert "小红书" in unsupported.json()["detail"]
    finally:
        career_router.get_career_services = original_get_services

    assert closing_services.close_calls == 1
    assert adapter.closed
    print("xiaohongshu_import_api_background_contract_ok")


if __name__ == "__main__":
    main()
