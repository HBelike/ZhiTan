# 小红书面经导入与信息收集

## 目标

面经库当前提供三种互不混用的入口：

- “小红书URL读取”沿用服务端公开读取，只处理无登录请求实际可访问的笔记和列表。
- “公开信息收集”输入关键词和 5–10 条数量后，通过 Firecrawl 搜索全网无需登录的公开网页，完成地址/正文去重、解析、Agent 甄别和自动入库，不依赖浏览器扩展或小红书登录态。
- 小红书浏览器关键词接口仍保留为兼容能力，但不再是 PC 面经库“公开信息收集”的默认实现。

“公开信息收集”默认且最多分析 10 条，实际 `/scrape` 调用不会超过本批配置。只有高置信度、公司/岗位/具体问题齐全的完整面经自动入库；每条有效面经分析通过后立即保存，其他条目失败不回滚已入库结果。其余网页保留为候选或过滤结果。跨关键词先按组织与规范 URL 去重，再按规范正文 SHA-256 去重。

面经库不会保存原始小红书图片、浏览器 Cookie、`xsec_token` 或搜索卡片的临时签名地址。候选记录只保存规范化来源 URL、正文、OCR 文本、分析结果、失败原因和最终入库的面经 ID。图片字节仅进入现有临时 OCR 流程，解析结束即清理。

## 调用链

```text
POST /api/career/interview-library/xiaohongshu-imports
  → InterviewCollectionService.create_xiaohongshu_import_job()
  → FastAPI BackgroundTasks.run_xiaohongshu_import()
    → XiaohongshuPublicSourceAdapter.collect()
    → XiaohongshuPublicSourceAdapter.download_images()
    → TemporaryAttachmentStore.save_bytes()
    → AttachmentParser.parse()
    → InterviewEvidenceAnalyzer.analyze()
    → InterviewLibraryService.ingest()

POST /api/career/interview-library/collection-jobs (platform_key=xiaohongshu)
  → InterviewCollectionService.create_xiaohongshu_keyword_import_job()
  → FastAPI BackgroundTasks.run_xiaohongshu_import()
  → 兼容旧调用的公开搜索导入链路

POST /api/career/interview-library/xiaohongshu-browser-imports
  → 创建 user_authorized_browser 任务
  → Web UI 调用浏览器助手 search_xiaohongshu_notes
  → POST .../{job_id}/xiaohongshu-discoveries
  → 逐篇调用浏览器助手 get_xiaohongshu_note
  → POST .../{job_id}/xiaohongshu-notes
    → BackgroundTasks 复用 OCR 与 InterviewEvidenceAnalyzer
    → 符合自动入库条件时 InterviewLibraryService.ingest()
  → POST .../{job_id}/complete

POST /api/career/interview-library/public-web-imports
  → Firecrawl /v2/search 发现公开 URL
  → interview_web_documents 排除已处理地址
  → Firecrawl /v2/scrape 提取新页面 Markdown
  → 正文不足时临时 OCR；不保存图片
  → SHA-256 正文去重
  → InterviewEvidenceAnalyzer + InterviewLibraryService.ingest()
  → interview_experience_sources 记录主来源和来源别名
```

浏览器信息收集展示“连接浏览器 → 读取卡片 → 逐篇解析 → OCR 图片 → 写入面经库”。`0.2.9` 起，详情读取从搜索结果页点击真实卡片并在完成后恢复搜索上下文，不再直接导航卡片中的临时签名地址。Web 端 `300031`、“当前笔记暂时无法浏览”或“请打开小红书 App 扫码查看”属于单篇内容限制：候选会保存限制原因、跳过当前笔记并继续；登录失效、安全验证或访问频率限制才会暂停整批并切换小红书标签页，用户处理后可从未完成笔记继续。任务编号保存在浏览器 `localStorage`，不保存登录信息或详情 token。

FastAPI `BackgroundTasks` 不是持久化队列：若整个服务进程在执行中被强制终止，运行中的任务不会自动续跑。此类“进程崩溃后恢复”需要另行接入持久化 worker / 租约恢复机制，当前功能不会伪造已完成结果。

## 输入与边界

- 公开 URL 入口接受小红书单篇笔记、个人页、收藏页、搜索页链接，每次 1–50 条；能否导入取决于页面是否向无登录服务端请求暴露内容。
- 关键词信息收集入口接受 5–50 条，默认 20 条，只支持图文笔记，不采集评论和视频内容。
- 单篇笔记：当页面返回可解析的公开 HTML 或公开初始状态时，读取标题、正文和公开配图。
- 列表页：只读取其 HTML/公开初始状态中实际暴露的笔记链接，再逐篇读取。
- 公开 URL 链路遇到登录、验证码或动态列表时以可读错误结束；浏览器信息收集链路使用用户已有登录页面，但不会自动填写账号、处理验证码或绕过访问限制。
- 小红书部分旧搜索路径会先返回同站 `http` 跳转。适配器会在内存中直接升级回同站 HTTPS，不会真的请求中间 HTTP 地址。
- 图片字节只进入临时附件和 OCR 流程，任务结束后清理；数据库只保存 OCR/分析后的文本和轻量元数据。

## 自动入库规则

模型只有在同时满足下列条件时才自动写入面经库：

1. 判定为有效面经；
2. 置信度不低于 0.60；
3. 可以抽取公司、岗位和至少一个具体问题。

未通过的内容不会丢失：无效内容标记为“已过滤”，模型暂不可用或字段不全的内容保留为候选，供后续检查或手动处理。不同来源笔记会使用来源内容哈希区分，避免同公司同岗位的多篇面经互相覆盖。

## 验证命令

在项目根目录运行：

```powershell
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_collection.py
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_browser_collection.py
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_orchestration.py
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_api.py
& .\.venv\Scripts\python.exe scripts\verify_interview_collection_metadata_contract.py
cd web-ui
npm run build
```

其中回归覆盖：浏览器能力门禁、5–50 条限制、卡片与详情数据归一化、从搜索页点击真实卡片及恢复上下文、`300031`/App 扫码限制单篇跳过、跨关键词去重、单篇失败不中断、账号级错误暂停、OCR 复用、有效内容自动入库、临时图片清理，以及 token/图片地址不进入持久化元数据。
