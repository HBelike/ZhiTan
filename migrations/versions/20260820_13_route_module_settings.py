"""新增按组织保存的顶级路由模块开关。"""

from __future__ import annotations

from alembic import op


revision = "20260820_13"
down_revision = "20260820_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为每个组织保存管理员配置的模块启用状态。"""

    op.execute(
        """
        CREATE TABLE career_assistant.route_module_settings (
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            module_key TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (organization_id, module_key),
            CHECK (length(trim(module_key)) > 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_assistant.route_module_settings")
