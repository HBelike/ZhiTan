"""面经库领域模块。

模块独立于微信公众号工作流，复用求职助手的 PostgreSQL、临时附件解析与模型网关。
它的长期事实来源是规范化 Markdown；向量索引可按部署配置重建，不作为唯一数据源。
"""

from src.career_assistant.interview_library.chunking import HierarchicalMarkdownChunker
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.service import InterviewLibraryService

__all__ = [
    "HierarchicalMarkdownChunker",
    "InterviewLibraryRepository",
    "InterviewLibraryService",
]
