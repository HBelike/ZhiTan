"""小红书公开面经采集适配器的离线自检。

脚本只使用 ``httpx.MockTransport``，不访问小红书、不读取浏览器 Cookie，也不调用 OCR
或 LLM。它验证公开页面发现、单篇解析、多图下载限制，以及动态/登录/验证码页面的
明确失败语义。
"""

from __future__ import annotations

from pathlib import Path
import sys

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.interview_library.xiaohongshu_collection import (
    XiaohongshuCollectionError,
    XiaohongshuPublicHttpClient,
    XiaohongshuPublicSourceAdapter,
    XiaohongshuSourceKind,
)


LISTING_URL = "https://www.xiaohongshu.com/search_result?keyword=java"
NOTE_ONE_URL = "https://www.xiaohongshu.com/explore/note-one?xsec_token=public-token"
NOTE_TWO_URL = "https://www.xiaohongshu.com/explore/state-note"
IMAGE_ONE_URL = "https://sns-webpic-qc.xhscdn.com/public/note-one-a.jpg"
IMAGE_TWO_URL = "https://sns-webpic-qc.xhscdn.com/public/note-one-b.png"
JSON_LD_IMAGE_URL = "https://sns-webpic-qc.xhscdn.com/public/json-ld-note.jpg"
JSON_LD_NOTE_URL = "https://www.xiaohongshu.com/explore/json-ld-note"


def _html_response(request: httpx.Request, body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "text/html; charset=utf-8"},
        text=body,
        request=request,
    )


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """构造完全离线的小红书公开页面与 CDN 响应。"""

    assert request.headers.get("cookie") is None, "公开采集请求不应携带 Cookie"
    url = str(request.url)
    if url == LISTING_URL:
        return _html_response(
            request,
            """
            <html><head><title>Java 面经搜索</title></head><body>
              <a href="/explore/note-one?xsec_token=public-token">可见笔记</a>
              <script>
                window.__INITIAL_STATE__ = {
                  "feed": [{"noteId": "state-note", "title": "字节后端一面", "desc": "公开状态中的笔记"}]
                };
              </script>
            </body></html>
            """,
        )
    if url == NOTE_ONE_URL:
        return _html_response(
            request,
            f"""
            <html><head><meta property="og:title" content="阿里后端一面复盘" /></head><body>
              <script>
                window.__INITIAL_STATE__ = {{
                  "note": {{
                    "noteId": "note-one",
                    "title": "阿里后端一面复盘",
                    "desc": "面试围绕 JVM、线程池和 MySQL 索引展开。面试官追问线上排障过程。",
                    "imageList": [
                      {{"urlDefault": "{IMAGE_ONE_URL}"}},
                      {{"urlDefault": "{IMAGE_TWO_URL}"}}
                    ],
                    "user": {{"nickname": "公开作者"}}
                  }}
                }};
              </script>
            </body></html>
            """,
        )
    if url == NOTE_TWO_URL:
        return _html_response(
            request,
            """
            <html><head><meta property="og:title" content="字节后端一面" />
            <meta property="og:description" content="公开笔记正文：重点考察缓存、消息队列与系统设计。" /></head>
            <body><article>公开笔记正文：重点考察缓存、消息队列与系统设计。</article></body></html>
            """,
        )
    if url == JSON_LD_NOTE_URL:
        return _html_response(
            request,
            f"""
            <html><head>
              <meta property="og:title" content="小红书 - 你发现生活的指南" />
              <meta property="og:description" content="3亿人的生活经验，都在小红书" />
              <meta property="og:description" content="东方财富暑期实习 AI 应用开发岗凉经。\n1. 请做一个自我介绍。\n2. RAG 检索与重排序如何取舍？\n3. HashMap 为什么要扩容？" />
              <meta property="og:image" content="http://sns-webpic-qc.xhscdn.com/public/json-ld-note.jpg" />
              <script>window.__INITIAL_STATE__ = {{"broken": undefined}};</script>
              <script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article","headline":"东方财富暑期实习 AI 应用开发岗凉经","description":"东方财富暑期实习 AI 应用开发岗凉经。\\n1. 请做一个自我介绍。\\n2. RAG 检索与重排序如何取舍？\\n3. HashMap 为什么要扩容？","author":{{"name":"公开作者"}},"image":"http://sns-webpic-qc.xhscdn.com/public/json-ld-note.jpg"}}</script>
            </head><body>
              <main><h1>东方财富暑期实习 AI 应用开发岗凉经</h1><p>1. 请做一个自我介绍。</p><p>2. RAG 检索与重排序如何取舍？</p><p>3. HashMap 为什么要扩容？</p></main>
            </body></html>
            """,
        )
    if url == IMAGE_ONE_URL:
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"1234",
            request=request,
        )
    if url == IMAGE_TWO_URL:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"123456789",
            request=request,
        )
    if url == JSON_LD_IMAGE_URL:
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"json-ld-image",
            request=request,
        )
    if url.startswith("https://www.xiaohongshu.com/user/profile/dynamic"):
        return _html_response(
            request,
            "<html><body><script>window.__INITIAL_STATE__ = {\"user\": {\"notes\": [[], []]}};</script></body></html>",
        )
    if url.startswith("https://www.xiaohongshu.com/user/profile/login"):
        return _html_response(request, "<html><body>请登录后查看收藏内容</body></html>")
    if url.startswith("https://www.xiaohongshu.com/explore/captcha"):
        return _html_response(request, "<html><body>请完成安全验证后继续访问</body></html>")
    return httpx.Response(404, headers={"content-type": "text/html"}, request=request)


def _create_adapter(*, max_image_bytes: int = 5) -> XiaohongshuPublicSourceAdapter:
    client = XiaohongshuPublicHttpClient(
        transport=httpx.MockTransport(_mock_handler),
    )
    return XiaohongshuPublicSourceAdapter(
        client,
        max_images_per_note=2,
        max_image_bytes=max_image_bytes,
        max_total_image_bytes=8,
    )


def verify_public_listing_and_note_extraction() -> None:
    """列表页只采集实际公开暴露的 href/state noteId，并逐篇读取。"""

    adapter = _create_adapter()
    try:
        result = adapter.collect(LISTING_URL, requested_limit=2)
        assert result.source_kind is XiaohongshuSourceKind.SEARCH
        assert len(result.discovered_notes) == 2
        assert len(result.notes) == 2
        assert not result.note_failures

        first = result.notes[0]
        assert first.note_id == "note-one"
        assert first.title == "阿里后端一面复盘"
        assert "JVM" in first.body_text
        assert first.author_name == "公开作者"
        assert first.image_urls == (IMAGE_ONE_URL, IMAGE_TWO_URL)
        assert "## 公开配图" in first.markdown_content

        second = result.notes[1]
        assert second.note_id == "state-note"
        assert "缓存" in second.body_text

        direct = adapter.extract_note(NOTE_ONE_URL)
        assert direct.canonical_url == "https://www.xiaohongshu.com/explore/note-one"
    finally:
        adapter.close()


def verify_multimedia_limits_and_partial_failure() -> None:
    """单图超限不能让同篇其他图片丢失，失败信息必须可展示。"""

    adapter = _create_adapter(max_image_bytes=5)
    try:
        note = adapter.extract_note(NOTE_ONE_URL)
        result = adapter.download_images(note)
        assert len(result.images) == 1
        assert result.images[0].data == b"1234"
        assert result.total_bytes == 4
        assert len(result.failures) == 1
        assert result.failures[0].index == 2
        assert result.failures[0].error_code == "response_too_large"
        assert adapter.download_image(result.images[0]) == b"1234"
    finally:
        adapter.close()


def verify_json_ld_and_repeated_meta_prefer_real_note_evidence() -> None:
    """平台口号、坏初始状态与 data 占位图不能吞掉公开题目正文和真实 CDN 图。"""

    adapter = _create_adapter(max_image_bytes=32)
    # 默认总大小限制仅为 8 字节，用于另一个多图超限回归；本案例需验证
    # 可信 HTTP CDN 图被规范化为 HTTPS 后可以正常下载。
    adapter._image_downloader._max_total_bytes = 32
    try:
        note = adapter.extract_note(JSON_LD_NOTE_URL)
        assert note.title == "东方财富暑期实习 AI 应用开发岗凉经"
        assert note.body_source == "json_ld"
        assert "RAG 检索与重排序" in note.body_text
        assert "3亿人的生活经验" not in note.body_text
        assert note.author_name == "公开作者"
        assert note.image_urls == (JSON_LD_IMAGE_URL,)
        images = adapter.download_images(note)
        assert len(images.images) == 1
        assert images.images[0].source_url == JSON_LD_IMAGE_URL
    finally:
        adapter.close()


def verify_same_site_http_redirect_is_upgraded() -> None:
    """站点旧路径的 HTTP 中间跳转应在本地升级回 HTTPS，而非误报链接无效。"""

    source_url = "https://www.xiaohongshu.com/search_result?keyword=java"
    https_target = "https://www.xiaohongshu.com/search_result/?keyword=java"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == source_url:
            return httpx.Response(
                301,
                headers={
                    "location": "http://www.xiaohongshu.com/search_result/?keyword=java",
                    "content-type": "text/html",
                },
                request=request,
            )
        if str(request.url) == https_target:
            return _html_response(request, "<html><body>公开搜索页</body></html>")
        raise AssertionError(f"unexpected redirect request: {request.url}")

    client = XiaohongshuPublicHttpClient(transport=httpx.MockTransport(handler))
    try:
        fetched = client.fetch_html(source_url)
        assert fetched.final_url == https_target
    finally:
        client.close()


def verify_login_redirect_is_explicit() -> None:
    """收藏页跳到登录页时，任务应报告登录限制而不是内容为空。"""

    source_url = "https://www.xiaohongshu.com/user/profile/requires-login?tab=fav"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == source_url:
            return httpx.Response(
                302,
                headers={
                    "location": "/login?redirect=%2Fuser%2Fprofile%2Frequires-login",
                    "content-type": "text/html",
                },
                request=request,
            )
        if request.url.path == "/login":
            return _html_response(request, "<html><body>请登录</body></html>")
        raise AssertionError(f"unexpected login redirect request: {request.url}")

    client = XiaohongshuPublicHttpClient(transport=httpx.MockTransport(handler))
    adapter = XiaohongshuPublicSourceAdapter(client)
    try:
        _assert_error("login_required", lambda: adapter.discover(source_url))
    finally:
        adapter.close()


def verify_restricted_note_redirect_is_explicit() -> None:
    """笔记被站点重定向到 300031 限制页时，应给出访问限制而非误报 URL 无效。"""

    source_url = "https://www.xiaohongshu.com/explore/restricted-note"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == source_url:
            return httpx.Response(
                302,
                headers={
                    "location": "/404?error_code=300031&error_msg=note-unavailable",
                    "content-type": "text/html",
                },
                request=request,
            )
        if request.url.path == "/404":
            return _html_response(request, "<html><body>当前笔记暂时无法浏览</body></html>")
        raise AssertionError(f"unexpected restricted note request: {request.url}")

    client = XiaohongshuPublicHttpClient(transport=httpx.MockTransport(handler))
    adapter = XiaohongshuPublicSourceAdapter(client)
    try:
        _assert_error("access_restricted", lambda: adapter.extract_note(source_url))
    finally:
        adapter.close()


def _assert_error(expected_code: str, callback) -> None:  # type: ignore[no-untyped-def]
    try:
        callback()
    except XiaohongshuCollectionError as exc:
        assert exc.code == expected_code, f"期望 {expected_code}，实际 {exc.code}"
        assert exc.message
        return
    raise AssertionError(f"应抛出 {expected_code}")


def verify_dynamic_login_and_verification_boundaries() -> None:
    """动态空壳、登录和验证码都要明确失败，不能伪造成抓取成功。"""

    adapter = _create_adapter()
    try:
        _assert_error(
            "source_content_unavailable",
            lambda: adapter.discover("https://www.xiaohongshu.com/user/profile/dynamic?tab=fav"),
        )
        _assert_error(
            "login_required",
            lambda: adapter.discover("https://www.xiaohongshu.com/user/profile/login?tab=fav"),
        )
        _assert_error(
            "verification_required",
            lambda: adapter.extract_note("https://www.xiaohongshu.com/explore/captcha"),
        )
    finally:
        adapter.close()


def main() -> None:
    verify_public_listing_and_note_extraction()
    verify_multimedia_limits_and_partial_failure()
    verify_json_ld_and_repeated_meta_prefer_real_note_evidence()
    verify_same_site_http_redirect_is_upgraded()
    verify_login_redirect_is_explicit()
    verify_restricted_note_redirect_is_explicit()
    verify_dynamic_login_and_verification_boundaries()
    print("小红书公开面经采集离线自检通过")


if __name__ == "__main__":
    main()
