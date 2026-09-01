"""面向中文面经 Markdown 的层级切片器。

实现遵循“先依据文档结构分段、再依据模型 Token 预算细分”的策略：每个切片保留
公司/岗位以及 Markdown 标题路径，避免“项目经历”“高频问题”等短语脱离语境后
无法召回。这里不绑定具体 embedding SDK，后续可用与 Qwen3 Embedding 相同的
tokenizer 替换 ``estimate_tokens``，不改变存储与检索契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re


_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PARAGRAPH_SPLIT_PATTERN = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？；;.!?])\s*")


# 版本写入每条切片，用于后续调整切片策略后的增量重建与检索可追溯。
CHUNKING_VERSION = "hierarchical-markdown-v1"


@dataclass(frozen=True)
class ChunkDraft:
    """尚未嵌入的一条稳定切片。"""

    parent_heading: str | None
    heading_path: str
    chunk_index: int
    content_text: str
    contextual_content: str
    token_estimate: int
    chunk_hash: str


class HierarchicalMarkdownChunker:
    """按标题层级和 Token 预算输出可重建切片。

    输入和输出均为纯文本，线程安全且不访问数据库/网络；因此可被 API 请求、后台
    入库 Job 或离线重建脚本共同复用。
    """

    VERSION = CHUNKING_VERSION

    def __init__(
        self,
        *,
        target_tokens: int = 260,
        max_tokens: int = 340,
        overlap_tokens: int = 36,
    ) -> None:
        """校验预算。默认范围适合面经问答片段并为 prompt 留出上下文空间。"""

        if not 80 <= target_tokens <= max_tokens <= 1_200:
            raise ValueError("切片 Token 预算必须满足 80 <= target <= max <= 1200")
        if not 0 <= overlap_tokens < target_tokens:
            raise ValueError("切片重叠 Token 必须小于目标 Token")
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def split(
        self,
        markdown: str,
        *,
        company_name: str,
        role_name: str,
        job_name: str,
    ) -> list[ChunkDraft]:
        """将面经正文拆成带父级语境的稳定切片。"""

        normalized_markdown = self._normalize(markdown)
        if not normalized_markdown:
            raise ValueError("面经正文不能为空")
        context_prefix = self._context_prefix(company_name, role_name, job_name)
        sections = self._split_sections(normalized_markdown)
        drafts: list[ChunkDraft] = []
        for heading_path, content in sections:
            for piece in self._fit_section(content):
                contextual_content = f"{context_prefix}\n标题路径：{heading_path}\n\n{piece}".strip()
                drafts.append(
                    ChunkDraft(
                        parent_heading=heading_path.split(" > ")[-1] if heading_path else None,
                        heading_path=heading_path or "正文",
                        chunk_index=len(drafts),
                        content_text=piece,
                        contextual_content=contextual_content,
                        token_estimate=self.estimate_tokens(contextual_content),
                        chunk_hash=sha256(contextual_content.encode("utf-8")).hexdigest(),
                    ),
                )
        return drafts

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """提供无模型依赖的保守 Token 估算，用于写入和预算保护。"""

        chinese_characters = len(re.findall(r"[\u3400-\u9fff]", text))
        non_chinese = len(text) - chinese_characters
        return max(1, chinese_characters + (non_chinese + 3) // 4)

    @staticmethod
    def _normalize(value: str) -> str:
        return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").split("\n")).strip()

    def _split_sections(self, markdown: str) -> list[tuple[str, str]]:
        heading_stack: list[tuple[int, str]] = []
        sections: list[tuple[str, list[str]]] = []
        current_content: list[str] = []
        current_path = "正文"

        def flush() -> None:
            content = "\n".join(current_content).strip()
            if content:
                sections.append((current_path, [content]))

        for line in markdown.splitlines():
            match = _HEADING_PATTERN.match(line)
            if match:
                flush()
                current_content.clear()
                level = len(match.group(1))
                heading = match.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading))
                current_path = " > ".join(item[1] for item in heading_stack)
            else:
                current_content.append(line)
        flush()
        return [(path, values[0]) for path, values in sections]

    def _fit_section(self, section: str) -> list[str]:
        paragraphs = [item.strip() for item in _PARAGRAPH_SPLIT_PATTERN.split(section) if item.strip()]
        pieces: list[str] = []
        buffer: list[str] = []
        buffer_tokens = 0
        for paragraph in paragraphs or [section]:
            paragraph_tokens = self.estimate_tokens(paragraph)
            if paragraph_tokens > self._max_tokens:
                for sentence_piece in self._split_oversized_paragraph(paragraph):
                    pieces.extend(self._append_piece(buffer, buffer_tokens, sentence_piece))
                    buffer = []
                    buffer_tokens = 0
                continue
            if buffer and buffer_tokens + paragraph_tokens > self._target_tokens:
                pieces.append("\n\n".join(buffer))
                buffer = self._overlap_from(buffer)
                buffer_tokens = self.estimate_tokens("\n\n".join(buffer)) if buffer else 0
            buffer.append(paragraph)
            buffer_tokens += paragraph_tokens
        if buffer:
            pieces.append("\n\n".join(buffer))
        return [piece for piece in pieces if piece.strip()]

    def _split_oversized_paragraph(self, paragraph: str) -> list[str]:
        sentences = [item.strip() for item in _SENTENCE_SPLIT_PATTERN.split(paragraph) if item.strip()]
        result: list[str] = []
        buffer: list[str] = []
        for sentence in sentences or [paragraph]:
            if self.estimate_tokens(sentence) > self._max_tokens:
                result.extend(self._hard_split(sentence))
                continue
            candidate = "".join(buffer + [sentence])
            if buffer and self.estimate_tokens(candidate) > self._target_tokens:
                result.append("".join(buffer))
                buffer = self._overlap_from(buffer)
            buffer.append(sentence)
        if buffer:
            result.append("".join(buffer))
        return result

    def _hard_split(self, value: str) -> list[str]:
        # 中文简历/面经中单个超长无标点段落很常见；按字符窗口是最后保障。
        maximum_characters = max(120, self._max_tokens)
        step = max(1, maximum_characters - self._overlap_tokens)
        return [value[start : start + maximum_characters] for start in range(0, len(value), step)]

    def _append_piece(self, buffer: list[str], buffer_tokens: int, value: str) -> list[str]:
        if buffer:
            return ["\n\n".join(buffer), value]
        if buffer_tokens:
            return [value]
        return [value]

    def _overlap_from(self, paragraphs: list[str]) -> list[str]:
        if not self._overlap_tokens or not paragraphs:
            return []
        overlap: list[str] = []
        for paragraph in reversed(paragraphs):
            overlap.insert(0, paragraph)
            if self.estimate_tokens("\n\n".join(overlap)) >= self._overlap_tokens:
                break
        return overlap

    @staticmethod
    def _context_prefix(company_name: str, role_name: str, job_name: str) -> str:
        return f"公司：{company_name.strip()}\n岗位：{role_name.strip()}\n面经：{job_name.strip()}"
