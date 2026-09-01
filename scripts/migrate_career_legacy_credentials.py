"""将求职助手的历史明文模型 API Key 转为 Fernet 密文。

运行前必须已完成 Alembic ``20260810_08`` 迁移。本脚本会复用 API 启动时自动
创建的持久化主密钥；若部署者显式配置 ``CAREER_CREDENTIAL_MASTER_KEY``，则仍
优先使用该值。本脚本不输出、导出或备份任何 Key 原文。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.persistence import CareerDatabase, CareerModelProfileRepository
from src.career_assistant.persistence.credential_cipher import (
    CredentialCipherError,
    ensure_credential_master_key,
)


def main() -> int:
    """执行一次幂等旧明文迁移，并仅报告不含敏感信息的统计结果。"""

    try:
        ensure_credential_master_key(PROJECT_ROOT)
    except CredentialCipherError as exc:
        print(f"凭据迁移未执行：{exc}", file=sys.stderr)
        return 2

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        print("凭据迁移未执行：缺少 CAREER_DATABASE_URL", file=sys.stderr)
        return 2

    database = CareerDatabase(database_url)
    repository = CareerModelProfileRepository(database)
    try:
        pending_before = repository.count_legacy_plaintext_credentials()
        migrated_count = repository.migrate_legacy_plaintext_credentials()
        pending_after = repository.count_legacy_plaintext_credentials()
        legacy_unknown_count = repository.count_legacy_unknown_credentials()
    except CredentialCipherError as exc:
        print(f"凭据迁移未执行：{exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(
            "凭据迁移未执行：请先确认 Alembic 已升级到 20260810_08，且数据库可访问",
            file=sys.stderr,
        )
        return 2
    finally:
        database.close()

    print(
        "career_legacy_credential_migration_ok "
        f"pending_before={pending_before} migrated={migrated_count} "
        f"pending_after={pending_after} legacy_unknown={legacy_unknown_count}",
    )
    if pending_after:
        print("仍有旧明文凭据未完成迁移，请不要在生产环境启用该版本", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
