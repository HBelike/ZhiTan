"""对面经库的真实 PostgreSQL 入库、树查询、编辑重切片与清理做验证。"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewSourceType,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.retrieval import InterviewRetrievalService
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.persistence import CareerDatabase
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ORGANIZATION_ID,
)
from src.career_assistant.settings import load_interview_retrieval_settings


def main() -> None:
    """验证一份面经从 Markdown 到树节点、切片，再到编辑更新的闭环。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = InterviewLibraryRepository(database)
    service = InterviewLibraryService(repository)
    retrieval_service = InterviewRetrievalService(
        repository,
        load_interview_retrieval_settings(),
    )
    experience_id: UUID | None = None
    company_id: UUID | None = None

    try:
        experience = service.ingest(
            DEFAULT_ORGANIZATION_ID,
            InterviewExperienceDraft(
                company_name="面经库验证公司",
                role_name="后端开发工程师",
                interview_date=date(2026, 8, 9),
                source_type=InterviewSourceType.MANUAL_TEXT,
                markdown_content=(
                    "# 一面\n\n"
                    "- 介绍 Java 并发与事务边界。\n\n"
                    "# 二面\n\n"
                    "讨论 RAG 检索、重排和线上可观测性。"
                ),
                tags=("Java", "RAG"),
            ),
            trigger_type=IngestionTriggerType.MANUAL_UPLOAD,
        )
        experience_id = experience.id
        company_id = experience.company_id
        assert experience.status.value == "indexed"
        assert experience.chunking_version == "hierarchical-markdown-v1"

        # Explicit @ references must still yield bounded evidence when the prompt
        # contains no lexical match. This keeps the assistant deterministic.
        selected_result = retrieval_service.retrieve(
            DEFAULT_ORGANIZATION_ID,
            "unmatched-selection-probe",
            limit=3,
            experience_ids=(experience.id,),
        )
        assert selected_result.retrieval_mode == "selected_experience_fallback"
        assert selected_result.candidates
        assert all(item.chunk.experience_id == experience.id for item in selected_result.candidates)

        tree = repository.list_tree(DEFAULT_ORGANIZATION_ID, query="面经库验证")
        assert len(tree) == 1
        assert tree[0]["label"] == "面经库验证公司"
        assert tree[0]["children"][0]["id"] == str(experience.id)

        updated = service.update_markdown(
            DEFAULT_ORGANIZATION_ID,
            experience.id,
            markdown_content="# 更新版\n\n补充系统设计追问与失败案例。",
            summary_text="验证编辑后重建切片。",
            tags=("系统设计",),
        )
        assert updated.status.value == "indexed"
        assert updated.markdown_content.startswith("# 更新版")
        assert repository.search_experiences(
            DEFAULT_ORGANIZATION_ID,
            "后端开发",
        )
        print("interview_library_real_database_ok")
    finally:
        try:
            with database.transaction() as connection:
                if experience_id is not None:
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_ingestion_jobs "
                            "WHERE experience_id = :experience_id",
                        ),
                        {"experience_id": experience_id},
                    )
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_experiences "
                            "WHERE id = :experience_id",
                        ),
                        {"experience_id": experience_id},
                    )
                if company_id is not None:
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_companies "
                            "WHERE id = :company_id",
                        ),
                        {"company_id": company_id},
                    )
        finally:
            retrieval_service.close()
            database.close()


if __name__ == "__main__":
    main()
