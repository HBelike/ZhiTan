"""把旧版默认求职历史归属到平台唯一管理员。"""

from __future__ import annotations

from alembic import op


revision = "20260828_29"
down_revision = "20260828_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """把旧默认 Actor 的求职会话及关联上下文迁移给唯一管理员。"""

    op.execute(
        r"""
        DO $career_ownership$
        DECLARE
          legacy_actor_id CONSTANT UUID := '00000000-0000-0000-0000-000000000002';
          admin_actor_id UUID;
          admin_organization_id UUID;
          legacy_organization_id UUID;
          admin_count INTEGER;
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM career_assistant.conversations
            WHERE actor_id = legacy_actor_id
          ) THEN
            RETURN;
          END IF;

          SELECT COUNT(*)
          INTO admin_count
          FROM career_assistant.platform_users
          WHERE role = 'admin'
            AND is_active;

          IF admin_count <> 1 THEN
            RAISE EXCEPTION '旧求职历史存在，但无法确定唯一有效管理员';
          END IF;

          SELECT id, organization_id
          INTO STRICT admin_actor_id, admin_organization_id
          FROM career_assistant.platform_users
          WHERE role = 'admin'
            AND is_active
          ORDER BY created_at ASC, id ASC
          LIMIT 1;

          SELECT organization_id
          INTO STRICT legacy_organization_id
          FROM career_assistant.actors
          WHERE id = legacy_actor_id;

          IF admin_organization_id <> legacy_organization_id THEN
            RAISE EXCEPTION '管理员与旧默认用户不在同一组织，拒绝迁移求职历史';
          END IF;

          -- 管理员已存在同名职业空间时复用该空间，避免唯一约束冲突。
          UPDATE career_assistant.conversations AS conversation
          SET career_space_id = admin_space.id
          FROM career_assistant.career_spaces AS legacy_space
          INNER JOIN career_assistant.career_spaces AS admin_space
            ON admin_space.organization_id = legacy_space.organization_id
           AND admin_space.actor_id = admin_actor_id
           AND admin_space.normalized_name = legacy_space.normalized_name
          WHERE conversation.actor_id = legacy_actor_id
            AND conversation.career_space_id = legacy_space.id
            AND legacy_space.actor_id = legacy_actor_id;

          UPDATE career_assistant.career_memory_items AS memory
          SET career_space_id = admin_space.id
          FROM career_assistant.career_spaces AS legacy_space
          INNER JOIN career_assistant.career_spaces AS admin_space
            ON admin_space.organization_id = legacy_space.organization_id
           AND admin_space.actor_id = admin_actor_id
           AND admin_space.normalized_name = legacy_space.normalized_name
          WHERE memory.actor_id = legacy_actor_id
            AND memory.career_space_id = legacy_space.id
            AND legacy_space.actor_id = legacy_actor_id;

          -- 没有同名空间时保留原空间，只调整归属；若管理员已有默认空间则降为普通空间。
          UPDATE career_assistant.career_spaces AS legacy_space
          SET actor_id = admin_actor_id,
              is_default = CASE
                WHEN legacy_space.is_default AND EXISTS (
                  SELECT 1
                  FROM career_assistant.career_spaces AS current_default
                  WHERE current_default.organization_id = admin_organization_id
                    AND current_default.actor_id = admin_actor_id
                    AND current_default.is_default
                ) THEN FALSE
                ELSE legacy_space.is_default
              END,
              updated_at = NOW()
          WHERE legacy_space.actor_id = legacy_actor_id
            AND (
              EXISTS (
                SELECT 1
                FROM career_assistant.conversations AS linked_conversation
                WHERE linked_conversation.actor_id = legacy_actor_id
                  AND linked_conversation.career_space_id = legacy_space.id
              )
              OR EXISTS (
                SELECT 1
                FROM career_assistant.career_memory_items AS linked_memory
                WHERE linked_memory.actor_id = legacy_actor_id
                  AND linked_memory.career_space_id = legacy_space.id
              )
            )
            AND NOT EXISTS (
              SELECT 1
              FROM career_assistant.career_spaces AS same_name_space
              WHERE same_name_space.organization_id = admin_organization_id
                AND same_name_space.actor_id = admin_actor_id
                AND same_name_space.normalized_name = legacy_space.normalized_name
            );

          UPDATE career_assistant.candidate_profiles
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.target_role_profiles
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.job_match_assessments
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.agent_turns
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.conversation_compaction_jobs
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.career_memory_items
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.career_memory_jobs
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.turn_memory_usages
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;

          UPDATE career_assistant.conversations
          SET actor_id = admin_actor_id
          WHERE actor_id = legacy_actor_id;
        END
        $career_ownership$;
        """,
    )


def downgrade() -> None:
    """数据归属迁移不可逆，无法可靠恢复旧记录的真实创建身份。"""

    pass
