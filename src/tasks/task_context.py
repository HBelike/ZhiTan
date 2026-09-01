from __future__ import annotations

from dataclasses import dataclass

from src.config.config_manager import AppConfig
from src.database.database_manager import DatabaseManager


@dataclass(frozen=True)
class TaskContext:
    """Task 执行时共享的只读上下文。"""

    config: AppConfig
    database_manager: DatabaseManager
