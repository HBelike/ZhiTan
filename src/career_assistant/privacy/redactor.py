"""面向持久化边界的基础敏感信息脱敏器。"""

from __future__ import annotations

import re


class SensitiveDataRedactor:
    """在文本写入对话历史前隐藏常见直接标识符。

    该类不声称可以覆盖所有隐私信息；它是数据库写入前的基础规则层。未来视觉/OCR
    解析节点还会提供结构化字段级脱敏，模型提示词中也会要求不复述个人信息。
    本类无可变状态，可安全地被多个 Agent Turn 并发复用。
    """

    _EMAIL_PATTERN = re.compile(
        r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.+-])",
    )
    _CHINA_MOBILE_PATTERN = re.compile(
        r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)",
    )
    _CHINA_ID_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

    def __init__(self, *, enabled: bool = True) -> None:
        """创建脱敏器；关闭时只规范化文本，不替换其中的身份信息。"""

        if not isinstance(enabled, bool):
            raise ValueError("脱敏开关必须是布尔值")
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """返回当前实例是否实际执行替换，供持久化层标记消息状态。"""

        return self._enabled

    def redact(self, content_text: str) -> str:
        """返回可持久化文本；输入为空时拒绝，避免静默丢失消息。"""

        normalized_content = content_text.strip()
        if not normalized_content:
            raise ValueError("待脱敏文本不能为空")

        if not self._enabled:
            return normalized_content

        redacted_content = self._EMAIL_PATTERN.sub("【已隐藏邮箱】", normalized_content)
        redacted_content = self._CHINA_MOBILE_PATTERN.sub(
            "【已隐藏手机号】",
            redacted_content,
        )
        return self._CHINA_ID_CARD_PATTERN.sub("【已隐藏证件号】", redacted_content)
