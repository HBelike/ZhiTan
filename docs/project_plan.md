# GitHub 周榜公众号自动化项目方案细节书

## 1. 项目目标

本项目是一个长期运行的 Python 后台服务，用于每周五自动发现 GitHub 上本周 star 表现最强的新技术项目，生成一篇微信公众号周榜合集文章、5 张项目配图和 1 条合集视频，并进入人工审核流程。人工审核通过后，再发布到微信公众号。

项目采用多 Agent / 多 Task 架构。每个核心任务由独立 Agent 负责，任务状态、产物、异常和发布记录全部持久化，便于追踪、重试和后续扩展 Web UI。

## 2. 已确认需求

- 主语言使用 Python。
- 程序入口从 `main.py` 开始。
- 当前目录初始为空，需要从零搭建工程。
- 后台服务每周五早上运行。
- 每周五 08:00 开始内容生产。
- 每周五 09:00 创建微信公众号草稿。
- 默认不自动发布，必须人工审核通过后再发布。
- GitHub 榜单不限定 AI 方向，面向全部技术项目。
- SearchTask 获取 star 数量周榜增速最快、截至周五增加最多的项目。
- 综合考虑当前 star 总量、本周 star 增长量、本周增长率。
- 每周选 Top 5 项目。
- 允许前一到两周因为缺少历史快照导致榜单存在误差。
- 使用 SQLite 存储数据，数据库路径为 `data/app.db`。
- 使用 DeepSeek V4 Pro 生成项目分析、公众号正文、生图 prompt、视频脚本和旁白稿。
- DeepSeek API Key 只放在配置中，不写死在代码里。
- 使用 Seedream 生成 5 张项目图片。
- 使用 Seedance 2.0 生成视频动效片段。
- 使用豆包 TTS 作为主语音合成方案。
- ElevenLabs text-to-speech skill 可作为备用方案。
- 使用 FFmpeg / MoviePy 合成最终视频。
- 一篇公众号文章包含 Top 5 项目分析、5 张图片和 1 条合集视频。
- 需要 Web UI，用于内容审核、任务运行观察、发布记录和系统配置。
- Web UI 使用 FastAPI + Vue 3 + Vite。
- Web UI 风格参考用户提供的原型图：左侧导航、白底、浅灰边界、绿色主色、卡片布局。
- 配置采用 `.env + config/app.yaml`。
- Web UI 只显示密钥是否已配置，不明文展示密钥。

## 3. 参考视频风格结论

用户提供的 Bilibili 视频为技术教学类视频，公开视频元信息如下：

- 标题：使用Python构建RAG系统 —— 用代码还原 RAG系统的每个细节
- 结构：原理回顾、代码地址与工具准备、项目环境搭建、开始写代码
- 时长：约 17 分 31 秒
- 封面风格：深色纹理背景、大号高对比标题、核心技术词高亮、少量副标题

项目不会复制具体视频画面、具体封面或具体真人声音。实现时抽象为科技代码教学科普风：

- 讲解结构清晰，先讲现象，再讲项目解决的问题，再讲技术亮点和适用场景。
- 画面使用深色或干净背景、代码窗口、架构节点、项目指标、流程箭头。
- 字幕清晰，强调项目名、stars、周增长量和一句话价值。
- 旁白使用普通科技科普中文声音，不克隆具体 UP 主声线。

## 4. 总体运行流程

```text
main.py
  -> Application.initialize()
  -> ConfigManager.load()
  -> LoggerManager.initialize()
  -> DatabaseManager.initialize()
  -> SchedulerManager.start()
  -> WeeklyPipeline.run_at_friday_08()
  -> SearchTask.run()
  -> SummaryTask.run()
  -> ImageTask.run()
  -> VideoTask.run()
  -> ArticleLayoutTask.run()
  -> DraftTask.run_at_friday_09()
  -> Web UI 人工审核
  -> PublishTask.run_after_approval()
  -> CatTask.monitor()
```

## 5. Agent 与 Task 分工

### SearchAgent / SearchTask

负责获取 GitHub 候选项目、保存 star 快照、计算周增长指标，并输出 Top 5 榜单。

输入：GitHub Token、搜索策略、上周快照、当前时间窗口。

输出：Top 5 仓库列表、star 快照、榜单评分结果。

失败处理：GitHub API 失败时记录错误、重试、降级使用已有快照，不进入后续生成流程。

线程安全：第一版串行执行，不共享可变状态；数据库写入通过事务保护。

任务监控：写入 `task_runs` 和 `error_events`。

### SummaryAgent / SummaryTask

负责调用 DeepSeek V4 Pro，生成项目分析、公众号正文草稿、图片 prompt、视频脚本、标题和摘要。

输入：Top 5 仓库结构化数据、README 摘要、star 增长指标。

输出：结构化内容 JSON、公众号正文 Markdown、生图 prompt、视频脚本、旁白稿。

失败处理：模型调用失败时重试；JSON 解析失败时要求模型修复；仍失败则记录异常。

线程安全：第一版串行执行；模型客户端无共享写状态。

任务监控：记录 token 使用、耗时、失败原因。

### ImageAgent / ImageTask

负责调用 Seedream，为每个项目生成 1 张科技教学风图片，共 5 张。

输入：5 个图片 prompt、统一视觉风格配置、输出路径。

输出：5 张图片文件路径、图片元数据。

失败处理：单张图片失败可重试；最终不足 5 张时任务标记为部分失败，不进入草稿生成。

线程安全：第一版串行，后续可并发生成。

任务监控：记录每张图片生成状态。

### VideoAgent / VideoTask

负责调用 Seedance 2.0 生成动效片段，并使用 FFmpeg / MoviePy 合成 1 条合集视频。

输入：5 张图片、视频脚本、旁白音频、字幕文本。

输出：最终视频文件路径、封面图、字幕文件。

失败处理：片段生成失败时重试；合成失败时释放临时文件并记录异常。

线程安全：合成过程使用独立临时目录，避免文件冲突。

任务监控：记录片段状态、合成状态和最终视频状态。

### TTSAgent

负责使用豆包 TTS 生成中文科技科普旁白。

输入：旁白稿、声音配置、语速、音高、音量。

输出：音频文件路径。

失败处理：豆包失败时可切换 ElevenLabs 备用方案。

线程安全：输出文件按 run_id 隔离。

任务监控：记录 TTS provider、耗时和错误。

### ArticleLayoutAgent / ArticleLayoutTask

负责将结构化内容、5 张图片和视频组织成微信公众号兼容 HTML 与草稿 payload。

输入：项目分析、图片路径或 media_id、视频路径或 media_id、标题、摘要。

输出：微信 HTML、文章标题、digest、封面图、草稿 payload。

失败处理：排版失败不进入 DraftTask；记录错误并等待人工处理。

线程安全：第一版串行。

任务监控：记录排版结果和草稿 payload 状态。

### DraftAgent / DraftTask

负责在每周五 09:00 上传素材并创建微信公众号草稿。

输入：排版结果、图片、视频、微信公众号配置。

输出：微信草稿 ID、media_id、草稿状态。

失败处理：上传失败回滚当前草稿流程；素材已上传但草稿失败时记录孤立素材。

线程安全：涉及外部接口，第一版同一时间只允许一个 DraftTask 运行。

任务监控：记录微信接口响应和错误。

### PublishAgent / PublishTask

负责人工审核通过后的发布动作。

输入：审核通过状态、草稿 ID、微信公众号配置。

输出：发布记录、发布状态。

失败处理：发布失败不重复盲发，记录错误，等待人工重试。

线程安全：同一草稿只允许发布一次，通过数据库状态约束保护。

任务监控：更新 `publish_records`。

### CatAgent / CatTask

负责全局异常监控、失败原因汇总和任务状态分析。

输入：task_runs、error_events、日志文件。

输出：异常摘要、失败原因、建议处理动作。

失败处理：自身失败时写入兜底日志。

线程安全：只读为主，写入 error_events 时使用事务。

任务监控：影响全局健康状态。

## 6. 存储设计

第一版使用 SQLite，数据库文件为 `data/app.db`。程序启动时自动初始化。

核心表：

- `repositories`：保存 GitHub 仓库基础信息。
- `star_snapshots`：保存每周 star 快照。
- `weekly_rankings`：保存每周 Top 5 榜单和评分。
- `generated_contents`：保存文章、prompt、脚本、标题和摘要。
- `media_assets`：保存图片、音频、视频等素材路径和状态。
- `article_layouts`：保存微信公众号 HTML 和草稿 payload。
- `draft_records`：保存微信公众号草稿状态。
- `publish_records`：保存发布状态。
- `task_runs`：保存每次任务运行状态。
- `error_events`：保存异常事件和 CatTask 分析结果。
- `approval_records`：保存人工审核动作。

## 7. 配置设计

`.env` 存储密钥：

```text
GITHUB_TOKEN=
DEEPSEEK_API_KEY=
VOLCENGINE_ARK_API_KEY=
DOUBAO_TTS_API_KEY=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

`config/app.yaml` 存储普通配置：

```text
app:
  timezone: Asia/Shanghai
  data_dir: data
  output_dir: outputs

schedule:
  production_time: "Friday 08:00"
  draft_time: "Friday 09:00"

github:
  top_n: 5
  candidate_limit: 100
  snapshot_weeks: 2

llm:
  provider: deepseek
  model: deepseek-v4-pro

image:
  provider: seedream

video:
  provider: seedance

tts:
  provider: doubao

wechat:
  auto_publish: false
```

## 8. Web UI 设计

Web UI 使用 FastAPI + Vue 3 + Vite。

核心页面：

- 内容审核：查看本周文章、5 张图片、视频、标题、摘要，执行通过或驳回。
- 任务运行：查看各 Task 状态、耗时、日志和错误。
- 项目榜单：查看候选项目、Top 5、star 增长量和评分。
- 素材库：查看图片、音频、视频。
- 发布记录：查看草稿、审核、发布状态。
- 系统配置：查看配置项是否已填写，不明文展示密钥。

预留导航：

- 新建 Agent
- 技能库
- 消息通知
- 知识库
- 历史会话

UI 风格：

- 左侧固定导航。
- 主区域顶部标题栏。
- 白色背景，浅灰边界。
- 绿色为主色。
- 卡片式内容展示。
- 搜索框和新建按钮靠右。

## 9. 外部服务

GitHub：

- 使用 GitHub REST API。
- 使用 `GITHUB_TOKEN` 降低限流风险。

DeepSeek：

- 使用 OpenAI-compatible ChatCompletions 接口。
- 模型配置为 `deepseek-v4-pro`。

Seedream：

- 生成 5 张项目图片。

Seedance 2.0：

- 基于图片生成短视频片段。

豆包 TTS：

- 生成中文科技科普旁白。

微信公众号：

- 上传图片、视频素材。
- 创建草稿。
- 人工审核通过后发布。

## 10. 工程目录规划

```text
.
  main.py
  requirements.txt
  .env.example
  config/
    app.yaml
  docs/
    project_plan.md
  data/
    app.db
  outputs/
    images/
    audio/
    video/
    articles/
  logs/
  src/
    app/
    config/
    logging/
    database/
    scheduler/
    agents/
    tasks/
    providers/
    repositories/
    services/
    web/
  frontend/
```

## 11. 分步施工计划

每一步都必须从真实调用链出发，只完成一个可验证小步骤。

第一步：创建最小 Python 工程骨架，从 `main.py` 启动，加载配置和日志。

第二步：接入 SQLite 初始化，创建数据库表。

第三步：实现 Task 运行状态记录。

第四步：实现 SchedulerManager 和每周五时间策略。

第五步：实现 SearchTask 的 GitHub 客户端和快照存储。

第六步：实现周榜评分和 Top 5 计算。

第七步：实现 DeepSeekProvider 和 SummaryTask。

第八步：实现 ImageTask 的 Seedream provider 配置位。

第九步：实现 TTSAgent 和豆包 TTS provider 配置位。

第十步：实现 VideoTask 和 FFmpeg / MoviePy 合成骨架。

第十一步：实现 ArticleLayoutTask。

第十二步：实现微信公众号 DraftTask。

第十三步：实现 Web UI 后端 API。

第十四步：实现 Vue 审核后台。

第十五步：实现人工审核后 PublishTask。

第十六步：实现 CatTask 全局异常分析。

第十七步：端到端联调。

## 12. 当前控制原则

- 未经用户确认，不一次性输出整个项目。
- 每次只实现一个清晰、可验证的小步骤。
- 每一步结束后等待用户回复“继续”。
- 不省略异常处理、日志、资源释放、边界检查和任务状态记录。
- 所有密钥只通过 `.env` 或外部配置注入。
- 微信公众号默认只创建草稿，不自动发布。
- 发布动作必须经过人工审核。
