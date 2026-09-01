"""简历优化历史的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text

from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.resume_assistant.models import (
    ResumeOptimizationRecord,
    ResumeSuggestion,
)


class ResumeOptimizationRepository:
    """只负责不可变历史的创建、检索和详情读取。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create(self, record: ResumeOptimizationRecord) -> ResumeOptimizationRecord:
        """写入一条新历史；不提供更新入口。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.resume_optimization_records (
                        id, organization_id, owner_id, creator_name, job_title,
                        model_profile_id, model_display_name, provider_key, model_id,
                        source_filename, source_media_type, original_file_path,
                        original_preview_pdf_path, original_preview_markdown, job_description_text, extra_prompt,
                        suggestions_json, optimized_markdown, optimized_text_path, optimized_docx_path, optimized_pdf_path, created_at
                    ) VALUES (
                        :id, :organization_id, :owner_id, :creator_name, :job_title,
                        :model_profile_id, :model_display_name, :provider_key, :model_id,
                        :source_filename, :source_media_type, :original_file_path,
                        :original_preview_pdf_path, :original_preview_markdown, :job_description_text, :extra_prompt,
                        CAST(:suggestions_json AS jsonb), :optimized_markdown,
                        :optimized_text_path, :optimized_docx_path, :optimized_pdf_path, :created_at
                    )
                    RETURNING *
                    """,
                ),
                {
                    "id": record.id,
                    "organization_id": record.organization_id,
                    "owner_id": record.owner_id,
                    "creator_name": record.creator_name,
                    "job_title": record.job_title,
                    "model_profile_id": record.model_profile_id,
                    "model_display_name": record.model_display_name,
                    "provider_key": record.provider_key,
                    "model_id": record.model_id,
                    "source_filename": record.source_filename,
                    "source_media_type": record.source_media_type,
                    "original_file_path": record.original_file_path,
                    "original_preview_pdf_path": record.original_preview_pdf_path,
                    "original_preview_markdown": record.original_preview_markdown,
                    "job_description_text": record.job_description_text,
                    "extra_prompt": record.extra_prompt,
                    "suggestions_json": json.dumps(
                        [suggestion.__dict__ for suggestion in record.suggestions],
                        ensure_ascii=False,
                    ),
                    "optimized_markdown": record.optimized_markdown,
                    "optimized_text_path": record.optimized_text_path,
                    "optimized_docx_path": record.optimized_docx_path,
                    "optimized_pdf_path": record.optimized_pdf_path,
                    "created_at": record.created_at,
                },
            ).mappings().one()
        return self._to_record(row)

    def list_records(
        self,
        organization_id: UUID,
        *,
        owner_id: UUID | None,
        job_title: str | None,
        start_date: date | None,
        end_date: date | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ResumeOptimizationRecord], int]:
        """按组织和权限范围分页检索历史。"""

        filters = ["organization_id = :organization_id"]
        params: dict[str, object] = {"organization_id": organization_id}
        if owner_id is not None:
            filters.append("owner_id = :owner_id")
            params["owner_id"] = owner_id
        if job_title:
            filters.append("job_title ILIKE :job_title")
            params["job_title"] = f"%{job_title.strip()}%"
        if start_date:
            filters.append("created_at >= :start_at")
            params["start_at"] = datetime.combine(start_date, time.min)
        if end_date:
            filters.append("created_at < :end_at")
            params["end_at"] = datetime.combine(end_date + timedelta(days=1), time.min)
        where_clause = " AND ".join(filters)
        params.update({"limit": page_size, "offset": (page - 1) * page_size})
        with self._database.transaction() as connection:
            total = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM career_assistant.resume_optimization_records "
                        f"WHERE {where_clause}",
                    ),
                    params,
                ).scalar_one(),
            )
            rows = connection.execute(
                text(
                    "SELECT * FROM career_assistant.resume_optimization_records "
                    f"WHERE {where_clause} ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
                ),
                params,
            ).mappings().all()
        return [self._to_record(row) for row in rows], total

    def get(
        self,
        organization_id: UUID,
        record_id: UUID,
        *,
        owner_id: UUID | None,
    ) -> ResumeOptimizationRecord | None:
        """读取详情；普通用户必须同时匹配 owner_id。"""

        owner_filter = "" if owner_id is None else " AND owner_id = :owner_id"
        params = {
            "organization_id": organization_id,
            "record_id": record_id,
            "owner_id": owner_id,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM career_assistant.resume_optimization_records "
                    "WHERE organization_id = :organization_id AND id = :record_id"
                    f"{owner_filter}",
                ),
                params,
            ).mappings().one_or_none()
        return self._to_record(row) if row is not None else None

    @staticmethod
    def _string_or_empty(value: object) -> str:
        if isinstance(value, str):
            return value
        return ""

    def _to_record(row: RowMapping) -> ResumeOptimizationRecord:
        suggestions_data = row["suggestions_json"] or []
        if isinstance(suggestions_data, str):
            suggestions_data = json.loads(suggestions_data)
        return ResumeOptimizationRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            owner_id=row["owner_id"],
            creator_name=str(row["creator_name"]),
            job_title=str(row["job_title"]),
            model_profile_id=row["model_profile_id"],
            model_display_name=str(row["model_display_name"]),
            provider_key=str(row["provider_key"]),
            model_id=str(row["model_id"]),
            source_filename=str(row["source_filename"]),
            source_media_type=str(row["source_media_type"]),
            original_file_path=ResumeOptimizationRepository._string_or_empty(row["original_file_path"]),
            original_preview_pdf_path=ResumeOptimizationRepository._string_or_empty(
                row["original_preview_pdf_path"],
            ),
            original_preview_markdown=str(row["original_preview_markdown"]),
            job_description_text=str(row["job_description_text"]),
            extra_prompt=row["extra_prompt"],
            suggestions=tuple(ResumeSuggestion(**item) for item in suggestions_data),
            optimized_markdown=str(row["optimized_markdown"]),
            optimized_text_path=ResumeOptimizationRepository._string_or_empty(
                row["optimized_text_path"],
            ),
            optimized_docx_path=ResumeOptimizationRepository._string_or_empty(
                row["optimized_docx_path"],
            ),
            optimized_pdf_path=ResumeOptimizationRepository._string_or_empty(
                row["optimized_pdf_path"],
            ),
            created_at=row["created_at"],
        )
