"""验证职位链接解析器的 HTML 提取与安全边界，不访问真实招聘网站。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.job_sources import JobPostingExtractor, JobSourceError


def main() -> None:
    """确保可见内容被保留，脚本内容与内网地址被安全拒绝。"""

    snapshot = JobPostingExtractor._parse_html(
        "https://jobs.example.com/position/123",
        """
        <html><head><title>后端工程师</title><meta property="og:title" content="高级后端工程师" />
        <script>secret = 'must not appear'</script></head>
        <body><h1>高级后端工程师</h1><p>负责 Agent 平台与服务端架构。</p></body></html>
        """,
    )
    assert snapshot.source_host == "jobs.example.com"
    assert snapshot.title == "高级后端工程师"
    assert "负责 Agent 平台" in snapshot.visible_text
    assert "must not appear" not in snapshot.visible_text

    try:
        JobPostingExtractor._parse_html(
            "https://www.zhipin.com/job_detail/example.html",
            """
            <html><head><title>请稍候 - BOSS直聘</title></head>
            <body>正在进行安全验证，请完成验证后继续访问。</body></html>
            """,
        )
    except JobSourceError as exc:
        assert "BOSS 直聘" in str(exc)
        assert "复制职位描述" in str(exc)
    else:
        raise AssertionError("未拒绝 BOSS 直聘安全验证页")

    for unsafe_url in (
        "file:///etc/passwd",
        "http://localhost:8000/job",
        "http://127.0.0.1/job",
    ):
        try:
            JobPostingExtractor._validate_public_url(unsafe_url)
        except JobSourceError:
            continue
        raise AssertionError(f"未拒绝不安全链接：{unsafe_url}")

    print("career_job_source_security_ok")


if __name__ == "__main__":
    main()
