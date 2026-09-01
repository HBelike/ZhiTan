"""面经库公共资料采集模块的离线自检。

不访问互联网、不连接数据库，只验证采集边界、HTML 正文归一化和平台策略。
"""

from __future__ import annotations

from pathlib import Path
import sys

# 允许从项目根目录直接执行该离线校验脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewEvidenceAnalyzer,
    InterviewCollectionService,
    PublicUrlArticleExtractor,
    _ArticleTextParser,
)
from src.career_assistant.interview_library.models import CollectionConnectorKind


def verify_html_parser() -> None:
    """脚本、样式和模板内容不能混入可入库的正文。"""

    parser = _ArticleTextParser()
    parser.feed(
        "<html><head><title>Java 后端一面复盘</title><style>body{display:none}</style>"
        "<script>window.secret = 'nope'</script></head><body><article>"
        "<h1>Java 后端一面</h1><p>面试围绕 JVM、并发和数据库索引展开，"
        "面试官重点追问了线程池参数选择、MySQL 索引失效场景以及线上排障过程。</p>"
        "<template>不应保留</template></article></body></html>"
    )
    parser.close()

    assert parser.title == "Java 后端一面复盘"
    assert "window.secret" not in parser.text
    assert "display:none" not in parser.text
    assert "Java 后端一面" in parser.text
    assert "线上排障过程" in parser.text


def verify_url_guards() -> None:
    """危险或不符合约定的 URL 要在发起网络请求前被拒绝。"""

    extractor = PublicUrlArticleExtractor()
    invalid_urls = (
        "http://example.com/article",
        "https://localhost/article",
        "https://user:password@example.com/article",
        "https://example.com:8443/article",
    )
    for value in invalid_urls:
        try:
            extractor._validate_public_https_url(value)
        except CollectionOperationError:
            continue
        raise AssertionError(f"危险 URL 未被拒绝：{value}")


def verify_platform_policy() -> None:
    """小红书关键词走公开搜索路径，其他受限平台仍不执行自动抓取。"""

    policies = InterviewCollectionService._POLICIES
    xiaohongshu = policies["xiaohongshu"]
    assert xiaohongshu.can_run_keyword_search is True
    assert xiaohongshu.connector_kind == CollectionConnectorKind.URL_IMPORT
    assert "公开搜索页" in xiaohongshu.policy_decision

    for platform in ("nowcoder", "maimai"):
        policy = policies[platform]
        assert policy.can_run_keyword_search is False
        assert policy.connector_kind == CollectionConnectorKind.USER_AUTHORIZED_BROWSER

    public_url = policies["public_url"]
    assert public_url.connector_kind == CollectionConnectorKind.URL_IMPORT
    assert "公开 HTTPS" in public_url.policy_decision


def verify_xiaohongshu_body_cleanup_contract() -> None:
    """候选正文应只保留面经内容，不能把平台外壳和配图地址写进可编辑正文。"""

    raw_markdown = """# 东方财富二面和 HR 面 - 小红书

来小红书，和全世界最有趣的人做朋友

## 公开配图

- 配图 1: https://sns-webpic-qc.xhscdn.com/example-1
- 配图 2: https://sns-webpic-qc.xhscdn.com/example-2

## 配图文字识别

### 配图 1 的文字识别

1. 你做过 AI 相关开发吗？介绍 Agent 场景和技术难点。
2. 怎么实现取数的？

### 配图 2 的文字识别

3. 讲讲 SQL 优化。
"""
    cleaned = InterviewCollectionService._strip_xiaohongshu_collection_wrappers(raw_markdown)

    assert "来小红书" not in cleaned
    assert "公开配图" not in cleaned
    assert "配图文字识别" not in cleaned
    assert "xhscdn.com" not in cleaned
    assert "介绍 Agent 场景和技术难点" in cleaned
    assert "讲讲 SQL 优化" in cleaned

    # 清洗 Agent 的输出约束与本地清洗保持一致：标题、配图 URL、OCR 标签都不属于正文。
    system_prompt = InterviewEvidenceAnalyzer._SYSTEM_PROMPT
    assert "平台口号" in system_prompt
    assert "来源链接" in system_prompt
    assert "不添加标题" in system_prompt


def main() -> None:
    verify_html_parser()
    verify_url_guards()
    verify_platform_policy()
    verify_xiaohongshu_body_cleanup_contract()
    print("面经库公共资料采集离线自检通过")


if __name__ == "__main__":
    main()
