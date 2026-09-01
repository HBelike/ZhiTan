"""求职助手会话历史的 PostgreSQL 仓储实现。"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.contracts import ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.records import (
    AgentTurnRecord,
    AgentTurnStatus,
    ConversationRecord,
    MessageRecord,
    MessageRole,
    SessionSummaryRecord,
)


DEFAULT_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
"""未启用登录前使用的本地默认组织。"""

DEFAULT_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")
"""未启用登录前使用的本地默认用户。"""


class CareerConversationRepository:
    """管理会话、脱敏消息和脱敏摘要的稳定数据访问接口。

    未来的 CareerChannel、AgentLoop 与历史会话 API 只调用本类，不直接拼写 SQL。
    本类不接受附件路径、原始 PDF 或图片，避免把需要临时处理的文件带进长期数据库。
    """

    def __init__(self, database: CareerDatabase) -> None:
        """保存数据库边界；不持有单次请求的连接或事务。"""

        self._database = database

    def create_conversation(
        self,
        organization_id: UUID,
        actor_id: UUID,
        title: str,
    ) -> ConversationRecord:
        """创建属于指定用户的空会话，并返回数据库生成时间。"""

        normalized_title = self._normalize_content(title, "会话标题", maximum_length=160)
        conversation_id = uuid4()

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.conversations
                        (id, organization_id, actor_id, title, career_space_id)
                    VALUES
                        (:id, :organization_id, :actor_id, :title, :career_space_id)
                    RETURNING id, organization_id, actor_id, career_space_id, title, status,
                              created_at, updated_at, archived_at
                    """,
                ),
                {
                    "id": conversation_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "title": normalized_title,
                    "career_space_id": None,
                },
            ).mappings().one()

        return self._to_conversation_record(row)

    def list_conversations(
        self,
        actor_id: UUID,
        *,
        limit: int | None = None,
        include_archived: bool = False,
    ) -> list[ConversationRecord]:
        """按最近更新时间倒序读取当前用户可见的会话列表。"""

        if limit is not None:
            self._validate_limit(limit)
        statuses = ("active", "archived") if include_archived else ("active",)
        limit_clause = "LIMIT :limit" if limit is not None else ""
        parameters: dict[str, object] = {
            "actor_id": actor_id,
            "statuses": list(statuses),
        }
        if limit is not None:
            parameters["limit"] = limit

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT id, organization_id, actor_id, career_space_id, title, status,
                           created_at, updated_at, archived_at
                    FROM career_assistant.conversations
                    WHERE actor_id = :actor_id
                      AND status = ANY(:statuses)
                    ORDER BY updated_at DESC, id DESC
                    {limit_clause}
                    """,
                ),
                parameters,
            ).mappings().all()

        return [self._to_conversation_record(row) for row in rows]

    def list_conversation_page(
        self,
        actor_id: UUID,
        *,
        page: int = 1,
        page_size: int = 10,
        include_archived: bool = False,
    ) -> tuple[list[ConversationRecord], int]:
        """分页读取会话，并在同一事务中返回当前筛选条件下的总数。"""

        if page < 1:
            raise ValueError("page 必须大于等于 1")
        self._validate_limit(page_size, field_name="page_size")
        statuses = ("active", "archived") if include_archived else ("active",)
        parameters: dict[str, object] = {
            "actor_id": actor_id,
            "statuses": list(statuses),
            "page_size": page_size,
            "offset": (page - 1) * page_size,
        }
        with self._database.transaction() as connection:
            total = int(
                connection.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM career_assistant.conversations
                        WHERE actor_id = :actor_id
                          AND status = ANY(:statuses)
                        """,
                    ),
                    parameters,
                ).scalar_one()
            )
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, actor_id, career_space_id, title, status,
                           created_at, updated_at, archived_at
                    FROM career_assistant.conversations
                    WHERE actor_id = :actor_id
                      AND status = ANY(:statuses)
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :page_size OFFSET :offset
                    """,
                ),
                parameters,
            ).mappings().all()
        return [self._to_conversation_record(row) for row in rows], total

    def get_conversation(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> ConversationRecord | None:
        """按用户范围读取一个未删除会话，防止跨用户读取历史。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, actor_id, career_space_id, title, status,
                           created_at, updated_at, archived_at
                    FROM career_assistant.conversations
                    WHERE id = :conversation_id
                      AND actor_id = :actor_id
                      AND status <> 'deleted'
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()

        return self._to_conversation_record(row) if row is not None else None

    def rename_conversation(
        self,
        actor_id: UUID,
        conversation_id: UUID,
        title: str,
    ) -> ConversationRecord | None:
        """重命名用户自己的未删除会话，并返回更新后的会话。"""

        normalized_title = self._normalize_content(title, "会话标题", maximum_length=160)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversations
                    SET title = :title, updated_at = NOW()
                    WHERE id = :conversation_id
                      AND actor_id = :actor_id
                      AND status <> 'deleted'
                    RETURNING id, organization_id, actor_id, career_space_id, title, status,
                              created_at, updated_at, archived_at
                    """,
                ),
                {
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "title": normalized_title,
                },
            ).mappings().one_or_none()

        return self._to_conversation_record(row) if row is not None else None

    def append_redacted_message(
        self,
        conversation_id: UUID,
        role: MessageRole,
        content_text: str,
        *,
        turn_id: UUID | None = None,
    ) -> MessageRecord:
        """兼容既有调用方：追加一条明确已脱敏的消息。"""

        return self.append_message(
            conversation_id,
            role,
            content_text,
            turn_id=turn_id,
            is_redacted=True,
        )

    def append_message(
        self,
        conversation_id: UUID,
        role: MessageRole,
        content_text: str,
        *,
        turn_id: UUID | None = None,
        is_redacted: bool,
    ) -> MessageRecord:
        """追加一条历史消息，并如实记录当前部署是否执行过脱敏。"""

        if not isinstance(is_redacted, bool):
            raise ValueError("消息脱敏状态必须是布尔值")

        normalized_content = self._normalize_content(
            content_text,
            "消息内容",
            maximum_length=30_000,
        )
        message_id = uuid4()

        with self._database.transaction() as connection:
            inserted_row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.messages
                        (id, conversation_id, turn_id, role, content_text, is_redacted)
                    SELECT :id, id, :turn_id, :role, :content_text, :is_redacted
                    FROM career_assistant.conversations
                    WHERE id = :conversation_id
                      AND status = 'active'
                    RETURNING id, conversation_id, turn_id, role, content_text,
                              is_redacted, created_at
                    """,
                ),
                {
                    "id": message_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "role": role.value,
                    "content_text": normalized_content,
                    "is_redacted": is_redacted,
                },
            ).mappings().one_or_none()

            if inserted_row is None:
                raise LookupError("会话不存在、已归档或已删除，不能追加消息")

            connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversations
                    SET updated_at = NOW()
                    WHERE id = :conversation_id
                    """,
                ),
                {"conversation_id": conversation_id},
            )

        return self._to_message_record(inserted_row)

    def list_messages(
        self,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        limit: int | None = None,
    ) -> list[MessageRecord]:
        """读取一个用户拥有的会话最近消息，并恢复为正向时间顺序。"""

        if limit is not None:
            self._validate_limit(limit, maximum=200)
        if limit is None:
            query = """
                SELECT message.id, message.conversation_id, message.turn_id,
                       message.role, message.content_text, message.is_redacted,
                       message.created_at
                FROM career_assistant.messages AS message
                INNER JOIN career_assistant.conversations AS conversation
                    ON conversation.id = message.conversation_id
                WHERE message.conversation_id = :conversation_id
                  AND conversation.actor_id = :actor_id
                  AND conversation.status <> 'deleted'
                ORDER BY message.created_at ASC, message.id ASC
                """
            parameters: dict[str, object] = {
                "actor_id": actor_id,
                "conversation_id": conversation_id,
            }
        else:
            query = """
                SELECT id, conversation_id, turn_id, role, content_text,
                       is_redacted, created_at
                FROM (
                    SELECT message.id, message.conversation_id, message.turn_id,
                           message.role, message.content_text, message.is_redacted,
                           message.created_at
                    FROM career_assistant.messages AS message
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = message.conversation_id
                    WHERE message.conversation_id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY message.created_at DESC, message.id DESC
                    LIMIT :limit
                ) AS recent_messages
                ORDER BY created_at ASC, id ASC
                """
            parameters = {
                "actor_id": actor_id,
                "conversation_id": conversation_id,
                "limit": limit,
            }
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(query),
                parameters,
            ).mappings().all()

        return [self._to_message_record(row) for row in rows]

    def upsert_redacted_summary(
        self,
        conversation_id: UUID,
        summary_text: str,
    ) -> SessionSummaryRecord:
        """创建或替换脱敏会话摘要，并在更新时递增摘要版本。"""

        normalized_summary = self._normalize_content(
            summary_text,
            "会话摘要",
            maximum_length=12_000,
        )
        summary_id = uuid4()

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.session_summaries
                        (id, conversation_id, summary_text, contains_sensitive_data)
                    SELECT :id, id, :summary_text, FALSE
                    FROM career_assistant.conversations
                    WHERE id = :conversation_id
                      AND status = 'active'
                    ON CONFLICT (conversation_id) DO UPDATE
                    SET summary_text = EXCLUDED.summary_text,
                        summary_version = career_assistant.session_summaries.summary_version + 1,
                        contains_sensitive_data = FALSE,
                        updated_at = NOW()
                    RETURNING id, conversation_id, summary_text, summary_version,
                              contains_sensitive_data, created_at, updated_at,
                              covered_through_message_id, summary_schema_version,
                              compacted_with_profile_id, compacted_input_tokens,
                              compacted_output_tokens
                    """,
                ),
                {
                    "id": summary_id,
                    "conversation_id": conversation_id,
                    "summary_text": normalized_summary,
                },
            ).mappings().one_or_none()

        if row is None:
            raise LookupError("会话不存在、已归档或已删除，不能保存摘要")
        return self._to_summary_record(row)

    def get_valid_summary(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> SessionSummaryRecord | None:
        """按组织和用户读取 V2 摘要，非法历史摘要视为不可用。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT summary.id, summary.conversation_id, summary.summary_text,
                           summary.summary_version, summary.contains_sensitive_data,
                           summary.created_at, summary.updated_at,
                           summary.covered_through_message_id,
                           summary.summary_schema_version,
                           summary.compacted_with_profile_id,
                           summary.compacted_input_tokens,
                           summary.compacted_output_tokens
                    FROM career_assistant.session_summaries AS summary
                    INNER JOIN career_assistant.conversations AS conversation
                      ON conversation.id = summary.conversation_id
                    WHERE summary.conversation_id = :conversation_id
                      AND conversation.organization_id = :organization_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                      AND summary.summary_schema_version = :schema_version
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "schema_version": "career-conversation-summary-v2",
                },
            ).mappings().one_or_none()
        if row is None:
            return None
        from src.career_assistant.conversation_memory import validate_summary

        try:
            validate_summary(json.loads(row["summary_text"]))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        return self._to_summary_record(row)

    def list_completed_dialogue_messages(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        exclude_turn_id: UUID | None = None,
        limit: int = 200,
    ) -> list[MessageRecord]:
        """读取成功 Turn 的完整候选消息；分组完整性由领域层再次检查。"""

        self._validate_limit(limit, maximum=400)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT message.id, message.conversation_id, message.turn_id,
                           message.role, message.content_text, message.is_redacted,
                           message.created_at
                    FROM career_assistant.messages AS message
                    INNER JOIN career_assistant.agent_turns AS turn
                      ON turn.id = message.turn_id
                    INNER JOIN career_assistant.conversations AS conversation
                      ON conversation.id = message.conversation_id
                    WHERE message.conversation_id = :conversation_id
                      AND conversation.organization_id = :organization_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                      AND turn.status = 'succeeded'
                      AND message.role IN ('user', 'assistant')
                      AND (:exclude_turn_id IS NULL OR turn.id <> :exclude_turn_id)
                    ORDER BY message.created_at ASC, message.id ASC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "exclude_turn_id": exclude_turn_id,
                    "limit": limit,
                },
            ).mappings().all()
        return [self._to_message_record(row) for row in rows]

    def save_summary_if_current(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        summary_text: str,
        covered_through_message_id: UUID,
        expected_summary_version: int,
        expected_covered_through_message_id: UUID | None,
        compacted_with_profile_id: UUID,
        compacted_input_tokens: int | None,
        compacted_output_tokens: int | None,
    ) -> SessionSummaryRecord | None:
        """校验后以摘要版本和游标执行 CAS，冲突时不覆盖新摘要。"""

        from src.career_assistant.conversation_memory import validate_summary

        validated = validate_summary(json.loads(summary_text))
        return self._compare_and_swap_summary(
            organization_id=organization_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            summary_text=validated.to_json(),
            covered_through_message_id=covered_through_message_id,
            expected_summary_version=expected_summary_version,
            expected_covered_through_message_id=expected_covered_through_message_id,
            compacted_with_profile_id=compacted_with_profile_id,
            compacted_input_tokens=compacted_input_tokens,
            compacted_output_tokens=compacted_output_tokens,
        )

    def _compare_and_swap_summary(
        self,
        **parameters: object,
    ) -> SessionSummaryRecord | None:
        parameters = {"id": uuid4(), **parameters}
        expected_version = int(parameters["expected_summary_version"])
        if expected_version == 0:
            statement = """
                INSERT INTO career_assistant.session_summaries (
                  id, conversation_id, summary_text, summary_version,
                  contains_sensitive_data, covered_through_message_id,
                  summary_schema_version, compacted_with_profile_id,
                  compacted_input_tokens, compacted_output_tokens
                )
                SELECT :id, conversation.id, :summary_text, 1, FALSE,
                       :covered_through_message_id,
                       'career-conversation-summary-v2',
                       :compacted_with_profile_id, :compacted_input_tokens,
                       :compacted_output_tokens
                FROM career_assistant.conversations AS conversation
                WHERE conversation.id = :conversation_id
                  AND conversation.organization_id = :organization_id
                  AND conversation.actor_id = :actor_id
                  AND conversation.status = 'active'
                ON CONFLICT (conversation_id) DO NOTHING
                RETURNING id, conversation_id, summary_text, summary_version,
                          contains_sensitive_data, created_at, updated_at,
                          covered_through_message_id, summary_schema_version,
                          compacted_with_profile_id, compacted_input_tokens,
                          compacted_output_tokens
            """
        else:
            statement = """
                UPDATE career_assistant.session_summaries AS summary
                SET summary_text = :summary_text,
                    summary_version = summary.summary_version + 1,
                    contains_sensitive_data = FALSE,
                    covered_through_message_id = :covered_through_message_id,
                    summary_schema_version = 'career-conversation-summary-v2',
                    compacted_with_profile_id = :compacted_with_profile_id,
                    compacted_input_tokens = :compacted_input_tokens,
                    compacted_output_tokens = :compacted_output_tokens,
                    updated_at = NOW()
                FROM career_assistant.conversations AS conversation
                WHERE summary.conversation_id = :conversation_id
                  AND conversation.id = summary.conversation_id
                  AND conversation.organization_id = :organization_id
                  AND conversation.actor_id = :actor_id
                  AND conversation.status = 'active'
                  AND summary.summary_version = :expected_summary_version
                  AND summary.covered_through_message_id
                      IS NOT DISTINCT FROM :expected_covered_through_message_id
                RETURNING summary.id, summary.conversation_id,
                          summary.summary_text, summary.summary_version,
                          summary.contains_sensitive_data, summary.created_at,
                          summary.updated_at, summary.covered_through_message_id,
                          summary.summary_schema_version,
                          summary.compacted_with_profile_id,
                          summary.compacted_input_tokens,
                          summary.compacted_output_tokens
            """
        with self._database.transaction() as connection:
            row = connection.execute(
                text(statement),
                parameters,
            ).mappings().one_or_none()
        return self._to_summary_record(row) if row is not None else None

    def archive_conversation(self, actor_id: UUID, conversation_id: UUID) -> bool:
        """将用户自己的活动会话归档，保留历史但禁止继续写入。"""

        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversations
                    SET status = 'archived', archived_at = NOW(), updated_at = NOW()
                    WHERE id = :conversation_id
                      AND actor_id = :actor_id
                      AND status = 'active'
                    """,
                ),
                {"actor_id": actor_id, "conversation_id": conversation_id},
            )

        return result.rowcount == 1

    def create_agent_turn(
        self,
        turn_id: UUID,
        conversation_id: UUID,
        actor_id: UUID,
        model_selection: ModelSelectionRequest,
        input_kind_codes: frozenset[str],
    ) -> AgentTurnRecord:
        """为已校验的用户输入创建 queued Turn，不保存原始输入正文。

        ``turn_id`` 由 CareerChannel 在请求进入 AgentLoop 时生成，能够在流式连接断开
        后继续定位同一轮执行。输入类型只保存枚举标签，例如 ``text`` 或 ``resume_pdf``。
        """

        model_selection.validate()
        if not input_kind_codes:
            raise ValueError("Agent Turn 至少需要一种输入类型")

        normalized_input_kinds = self._normalize_input_kind_codes(input_kind_codes)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.agent_turns (
                        id,
                        conversation_id,
                        actor_id,
                        requested_selection_mode,
                        requested_model_profile_id,
                        input_kind_codes
                    )
                    SELECT
                        :turn_id,
                        conversation.id,
                        :actor_id,
                        :requested_selection_mode,
                        :requested_model_profile_id,
                        CAST(:input_kind_codes AS jsonb)
                    FROM career_assistant.conversations AS conversation
                    WHERE conversation.id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status = 'active'
                    RETURNING id, conversation_id, actor_id,
                              requested_selection_mode, requested_model_profile_id,
                              input_kind_codes, status, error_code, error_message,
                              started_at, completed_at, created_at, updated_at
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "actor_id": actor_id,
                    "requested_selection_mode": model_selection.mode.value,
                    "requested_model_profile_id": model_selection.profile_id,
                    "input_kind_codes": json.dumps(normalized_input_kinds),
                },
            ).mappings().one_or_none()

        if row is None:
            raise LookupError("会话不存在、无访问权限、已归档或已删除")
        return self._to_agent_turn_record(row)

    def mark_agent_turn_running(self, actor_id: UUID, turn_id: UUID) -> AgentTurnRecord:
        """将 queued Turn 原子切换到 running，拒绝重复启动。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns AS turn
                    SET status = 'running',
                        started_at = NOW(),
                        updated_at = NOW()
                    FROM career_assistant.conversations AS conversation
                    WHERE turn.id = :turn_id
                      AND turn.conversation_id = conversation.id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status = 'active'
                      AND turn.status = 'queued'
                    RETURNING turn.id, turn.conversation_id, turn.actor_id,
                              turn.requested_selection_mode,
                              turn.requested_model_profile_id,
                              turn.input_kind_codes, turn.status, turn.error_code,
                              turn.error_message, turn.started_at, turn.completed_at,
                              turn.created_at, turn.updated_at
                    """,
                ),
                {"turn_id": turn_id, "actor_id": actor_id},
            ).mappings().one_or_none()

        if row is None:
            raise LookupError("Agent Turn 不存在、无访问权限或当前状态不可启动")
        return self._to_agent_turn_record(row)

    def finish_agent_turn(
        self,
        actor_id: UUID,
        turn_id: UUID,
        status: AgentTurnStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentTurnRecord:
        """将 running Turn 收口为成功、失败或取消状态。

        错误信息只允许是已面向用户整理的简短文本，不能把原始异常堆栈或附件解析内容
        直接传入长期存储。
        """

        if status not in {
            AgentTurnStatus.SUCCEEDED,
            AgentTurnStatus.FAILED,
            AgentTurnStatus.CANCELLED,
        }:
            raise ValueError("Agent Turn 只能收口为 succeeded、failed 或 cancelled")

        normalized_error_code, normalized_error_message = self._normalize_turn_error(
            status,
            error_code,
            error_message,
        )
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns AS turn
                    SET status = :status,
                        error_code = :error_code,
                        error_message = :error_message,
                        completed_at = NOW(),
                        updated_at = NOW()
                    FROM career_assistant.conversations AS conversation
                    WHERE turn.id = :turn_id
                      AND turn.conversation_id = conversation.id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND turn.status = 'running'
                    RETURNING turn.id, turn.conversation_id, turn.actor_id,
                              turn.requested_selection_mode,
                              turn.requested_model_profile_id,
                              turn.input_kind_codes, turn.status, turn.error_code,
                              turn.error_message, turn.started_at, turn.completed_at,
                              turn.created_at, turn.updated_at
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "actor_id": actor_id,
                    "status": status.value,
                    "error_code": normalized_error_code,
                    "error_message": normalized_error_message,
                },
            ).mappings().one_or_none()

        if row is None:
            raise LookupError("Agent Turn 不存在、无访问权限或当前状态不可收口")
        return self._to_agent_turn_record(row)

    def get_agent_turn(self, actor_id: UUID, turn_id: UUID) -> AgentTurnRecord | None:
        """按用户范围读取单次 Turn，供断线恢复和任务状态 UI 使用。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT turn.id, turn.conversation_id, turn.actor_id,
                           turn.requested_selection_mode,
                           turn.requested_model_profile_id,
                           turn.input_kind_codes, turn.status, turn.error_code,
                           turn.error_message, turn.started_at, turn.completed_at,
                           turn.created_at, turn.updated_at
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE turn.id = :turn_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    """,
                ),
                {"turn_id": turn_id, "actor_id": actor_id},
            ).mappings().one_or_none()

        return self._to_agent_turn_record(row) if row is not None else None

    def get_latest_agent_turn(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> AgentTurnRecord | None:
        """读取会话最近一次 Turn，供刷新后的任务恢复界面使用。

        该查询只返回当前用户仍有权限访问的会话中的状态元数据；不会返回
        附件路径、原始材料或模型凭证。WebUI 因此可以在浏览器断线后重新
        打开会话，并准确判断上一轮是否仍在服务端处理。
        """

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT turn.id, turn.conversation_id, turn.actor_id,
                           turn.requested_selection_mode,
                           turn.requested_model_profile_id,
                           turn.input_kind_codes, turn.status, turn.error_code,
                           turn.error_message, turn.started_at, turn.completed_at,
                           turn.created_at, turn.updated_at
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE turn.conversation_id = :conversation_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY turn.created_at DESC, turn.id DESC
                    LIMIT 1
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()

        return self._to_agent_turn_record(row) if row is not None else None

    def count_successful_turns(self, actor_id: UUID, conversation_id: UUID) -> int:
        """统计会话中已经成功完成的完整轮次。"""

        with self._database.transaction() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM career_assistant.agent_turns AS turn
                        INNER JOIN career_assistant.conversations AS conversation
                          ON conversation.id = turn.conversation_id
                        WHERE turn.conversation_id = :conversation_id
                          AND turn.actor_id = :actor_id
                          AND conversation.actor_id = :actor_id
                          AND conversation.status <> 'deleted'
                          AND turn.status = 'succeeded'
                        """,
                    ),
                    {"conversation_id": conversation_id, "actor_id": actor_id},
                ).scalar_one(),
            )

    def get_last_model_selection(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> ModelSelectionRequest | None:
        """读取某个会话最近一次已提交的模型选择。

        模型选择属于一次 Agent Turn，而不是浏览器页面的全局状态。求职会话被重新
        打开时，Web 层通过本方法恢复用户上一次实际提交的选择；没有任何 Turn 的
        新会话则由调用方回退到免费额度优先策略。该查询不读取 API Key，也不涉及
        附件或原始对话内容。
        """

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT turn.requested_selection_mode,
                           turn.requested_model_profile_id
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE turn.conversation_id = :conversation_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY turn.created_at DESC, turn.id DESC
                    LIMIT 1
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()

        if row is None:
            return None
        return ModelSelectionRequest(
            mode=ModelSelectionMode(row["requested_selection_mode"]),
            profile_id=row["requested_model_profile_id"],
        )

    def delete_conversation_permanently(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> bool:
        """永久删除用户本人会话及其级联运行记录。

        这是唯一的不可恢复操作，只会删除 actor_id 同时匹配的会话，避免越权删除。
        后续 WebUI 必须在用户二次确认后才调用。
        """

        with self._database.transaction() as connection:
            # 检索反馈的外键策略是 SET NULL。永久删除时主动清理，避免留下无法追溯
            # 到具体会话的孤立反馈；其余消息、摘要和运行记录由数据库级联删除。
            connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.interview_retrieval_feedback
                    WHERE conversation_id IN (
                        SELECT id
                        FROM career_assistant.conversations
                        WHERE id = :conversation_id
                          AND actor_id = :actor_id
                    )
                    """,
                ),
                {"actor_id": actor_id, "conversation_id": conversation_id},
            )
            result = connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.conversations
                    WHERE id = :conversation_id
                      AND actor_id = :actor_id
                    """,
                ),
                {"actor_id": actor_id, "conversation_id": conversation_id},
            )

        return result.rowcount == 1

    @staticmethod
    def _normalize_content(value: str, field_name: str, *, maximum_length: int) -> str:
        """统一执行空值和长度边界检查，避免异常大字段占用历史库。"""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name}不能为空")
        if len(normalized_value) > maximum_length:
            raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符")
        return normalized_value

    @staticmethod
    def _validate_limit(
        limit: int,
        *,
        maximum: int = 100,
        field_name: str = "limit",
    ) -> None:
        """阻止历史列表的无界查询。"""

        if not 1 <= limit <= maximum:
            raise ValueError(f"{field_name} 必须在 1 到 {maximum} 之间")

    @staticmethod
    def _normalize_input_kind_codes(input_kind_codes: frozenset[str]) -> list[str]:
        """校验并稳定排序输入类型标签，避免无意义或不可序列化数据进入 JSONB。"""

        normalized_codes = sorted(code.strip() for code in input_kind_codes if code.strip())
        if not normalized_codes:
            raise ValueError("Agent Turn 至少需要一种有效输入类型")
        if any(len(code) > 80 for code in normalized_codes):
            raise ValueError("输入类型标签不能超过 80 个字符")
        return normalized_codes

    @staticmethod
    def _normalize_turn_error(
        status: AgentTurnStatus,
        error_code: str | None,
        error_message: str | None,
    ) -> tuple[str | None, str | None]:
        """限制持久化错误字段，避免意外记录堆栈、URL 或用户材料。"""

        normalized_code = (error_code or "").strip() or None
        normalized_message = (error_message or "").strip() or None
        if status is AgentTurnStatus.SUCCEEDED:
            if normalized_code is not None or normalized_message is not None:
                raise ValueError("成功的 Agent Turn 不能携带错误信息")
            return None, None

        if normalized_code is None:
            raise ValueError("失败或取消的 Agent Turn 必须提供错误代码")
        if len(normalized_code) > 80:
            raise ValueError("错误代码不能超过 80 个字符")
        if normalized_message is not None and len(normalized_message) > 500:
            raise ValueError("错误说明不能超过 500 个字符")
        return normalized_code, normalized_message

    @staticmethod
    def _to_conversation_record(row: RowMapping) -> ConversationRecord:
        """将 SQL 行映射为领域读取模型。"""

        return ConversationRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            actor_id=row["actor_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            career_space_id=row.get("career_space_id"),
        )

    @staticmethod
    def _to_message_record(row: RowMapping) -> MessageRecord:
        """将 SQL 行映射为已脱敏消息读取模型。"""

        return MessageRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            role=MessageRole(row["role"]),
            content_text=row["content_text"],
            is_redacted=row["is_redacted"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _to_summary_record(row: RowMapping) -> SessionSummaryRecord:
        """将 SQL 行映射为已脱敏摘要读取模型。"""

        return SessionSummaryRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            summary_text=row["summary_text"],
            summary_version=row["summary_version"],
            contains_sensitive_data=row["contains_sensitive_data"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            covered_through_message_id=row.get("covered_through_message_id"),
            summary_schema_version=row.get(
                "summary_schema_version",
                "career-conversation-summary-v2",
            ),
            compacted_with_profile_id=row.get("compacted_with_profile_id"),
            compacted_input_tokens=row.get("compacted_input_tokens"),
            compacted_output_tokens=row.get("compacted_output_tokens"),
        )

    @staticmethod
    def _to_agent_turn_record(row: RowMapping) -> AgentTurnRecord:
        """将 SQL 行映射为 Turn 生命周期读取模型。"""

        return AgentTurnRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            actor_id=row["actor_id"],
            requested_selection_mode=ModelSelectionMode(row["requested_selection_mode"]),
            requested_model_profile_id=row["requested_model_profile_id"],
            input_kind_codes=tuple(row["input_kind_codes"]),
            status=AgentTurnStatus(row["status"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
