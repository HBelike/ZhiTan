from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.config.config_manager import AppConfig


class LoggerManager:
    """负责初始化控制台日志和文件日志。"""

    def __init__(self, project_root: Path, config: AppConfig) -> None:
        self.project_root = project_root
        self.config = config

    def initialize(self, *, extra_handlers: Sequence[logging.Handler] = ()) -> None:
        """根据配置初始化 Python 标准 logging。"""
        log_dir = self.config.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)

        log_level = self._parse_log_level(self.config.log_level)
        log_path = log_dir / self.config.log_file

        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)

        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(log_level)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)
        for handler in extra_handlers:
            root_logger.addHandler(handler)

        logging.getLogger(__name__).info("日志初始化完成：%s", log_path)

    def _parse_log_level(self, value: str) -> int:
        """把配置里的日志级别字符串转换为 logging 常量。"""
        level = getattr(logging, value.upper(), None)
        if not isinstance(level, int):
            raise ValueError(f"不支持的日志级别：{value}")
        return level
