# 周榜媒体生成工作流

## 目标与边界

本模块把每周 GitHub 热门项目的图文摘要扩展为可审核的短视频素材，服务于公众号草稿发布。目标是让“文案、动态 N 张技术教学插图、视频分镜、统一旁白、最终视频”在同一个 `content_id` 下可追溯、可重试。N 来自管理台配置形成的当期周榜项目数量。

本次实现不直接调用任何付费生成 API。当前默认配置仍保持 `video.submit_enabled: false`，因此本地可以验证任务编排和视频装配，但不会创建 Seedance 付费任务。

不在本次范围内：自动公开发布、视频内硬字幕烧录、云端对象存储的实际开通。公众号仍保留人工审核后进入草稿箱的边界。

## 内容职责分层

为避免一次 DeepSeek 调用同时生成动态 Top N 长文章、N 张图 Prompt、七段视频脚本和旁白而触发输出截断，当前链路按项目和产物所有权拆分：

- `SummaryTask`：按排名串行执行 N 次单项目生成，再执行 1 次全局综合；代码确定性拼装单篇深度文章，并为每个项目产出经证据校验的 `ArticleVisualSpec v1`。
- `ShortVideoPromptTask`：基于 `ContentBrief` 生成渐进式讲稿、统一旁白与七段视频分镜，并回写 `video_script`、`voiceover_text`。
- `ImageTask`：校验 N 份 `ArticleVisualSpec`，选择固定 HTML/SVG 模板，经 Gotenberg 生成可核验的 `2048×1152` PNG；静态技术图主路径不再调用 Seedream。
- `VideoClipPlanTask`：把分镜翻译为 Seedance 分片计划，不重新总结项目事实。
- `AudioTask`、`SeedanceClipTask`：只消费已生成的旁白与分片 Prompt，不承担内容创作。

因此文章、图片、视频均引用同一份项目事实合同，但每个 Task 只生成自己负责的产物。`content_id` 继续作为全部正文、Prompt、分镜与媒体素材的隔离键。

## 确定性技术配图主链路

- **设计目标**：技术文章中的中文、层级、节点和连线必须由程序确定，不让概率式图片模型猜测；规格或渲染失败时关闭式失败，不发布低质量兜底图。
- **调用链**：`SummaryTask → ArticleVisualSpec v1 → ArticleVisualPlanningService → ArticleVisualSpecValidator → ArticleVisualTemplateService → GotenbergScreenshotProvider → RenderedVisualValidator → media_assets`。
- **图型与容量**：固定支持 `summary_card`、`flow`、`architecture`、`comparison`、`timeline`。证据不足的历史内容只读取 `summary_text` 与文章固定章节并降级为 `summary_card`，不消费旧 `visual_brief` 的截断节点。三能力卡使用单列布局，正文保持 30 px，可承载 validator 允许的 60 字完整描述；句子只按句号、问号、叹号和分号结束，冒号不会生成残句。
- **依赖与取舍**：Gotenberg 使用 `/forms/chromium/screenshot/html`；Noto Sans SC 字体和 OFL 许可证随仓库保存并随请求上传。`baoyu-article-illustrator` 的 Type × Style × Palette 方法只用于规划图型、风格和色板，不作为后端在线位图渲染器。
- **质量门**：渲染前校验证据、禁词、数量、边引用与容量；浏览器等待字体完成后校验文字一致、溢出、越界、节点重叠和边身份；输出后用 Pillow 校验 PNG 与精确尺寸。目标文件先写 `.part` 再原子替换。
- **重试与幂等**：Gotenberg 只对连接/超时以及 HTTP 408、429、500、502、503、504 重试，退避为 1、2、4、8 秒，最多 5 次；普通 4xx 立即失败。`render_key` 由 canonical spec、模板、renderer 和字体版本计算，文件存在且有效资产 key 相同时直接复用。
- **失败保护**：先完成整批规格校验和所有新图渲染，再在单事务中创建新资产并把旧图标记为 `replaced`。第六张失败、孤儿目标冲突或数据库批量写入失败时，旧资产保持有效，新批次不产生半批数据库记录。
- **真实验证（2026-08-28）**：本地 `content_id=20` 的 6 个旧项目真实降级为 6 张 `summary_card`。修复冒号残句与重复 takeaway 后，首轮 `rendered=6/reused=0`，紧接第二轮 `rendered=0/reused=6`；6 张均为 `2048×1152` PNG，metadata 的 `render_key`、模板/renderer 版本、role 与 `validation_result=passed` 完整，无 `.part`。逐张检查未发现中文方框、裁切、重叠、重复节点或禁用元文字。详细结果见本地 `outputs/visual-verification/content-20.json`。

## Seedream 历史网络重试与代理降级

- **设计目标**：本地系统代理或远端服务发生瞬时抖动时，不因单次连接中断终止整批图片生成，同时避免对确定性的请求参数错误反复调用付费接口。
- **技术取舍**：生图请求和签名图片下载的最大尝试次数统一为 5 次，采用最长 6.4 秒的指数退避。连接失败、超时、传输中断、HTTP 429 和 5xx 会重试；普通 4xx 立即失败。首次请求继续尊重系统代理，出现 `ProxyError` 后剩余尝试关闭环境代理并直连，避免持续撞击已失效的本地代理。
- **调用链**：`ImageTask` → `SeedreamProvider.generate_image` → Ark `/images/generations` → 签名图片 URL 下载。重试只封装在 Provider 内，不改变任务幂等、素材复用或数据库写入边界；只有图片完整落盘后才创建 `media_assets` 记录。
- **依赖与边界**：继续使用现有 `requests`，不新增第三方依赖；不重试 API Key、模型、Prompt 等需要人工修正的普通 4xx 响应。
- **历史验证**：曾对 `content_id=20` 验证 Seedream 网络重试，确认代理断连恢复有效。该结果只证明旧 Provider 的网络可用性；当前静态技术总结图主路径已经切换到上节的 Gotenberg 确定性渲染。

## 管理台媒体生成开关

管理台“GitHub 热门”页提供“图片生成”“视频生成”和“语音生成”三个可视化开关，并随项目数量、关键词和提示词一起保存为版本化流水线配置。新字段为 `image_generation_enabled`、`video_generation_enabled` 与 `audio_generation_enabled`；旧配置缺少字段时分别按 `true`、`false` 与 `false` 兼容。

运行时不新增第二套任务状态：`apply_pipeline_config` 将图片开关映射到现有 `image.paid_generation_enabled`，将视频开关映射到现有 `video.submit_enabled`，将语音开关映射到现有 `audio.enabled`。工作台创建的运行快照额外写入仅限本次运行的 `video.runtime_submit_enabled` 与 `audio.runtime_enabled`，优先级分别高于部署环境中的 `VIDEO_SUBMIT_ENABLED` 和 `AUDIO_ENABLED`；常规定时任务没有这些字段，仍保持原有环境变量行为。关闭图片后仍生成文章、项目视觉简报和排版结构，但 `ImageTask` 不调用外部图片生成服务；关闭视频后不进入分镜、Seedance、视觉质检和视频装配任务链；关闭语音后不调用语音合成服务，也不生成旁白音频。每次手动运行使用提交时冻结的配置版本，切换开关不会改写正在运行的任务或历史内容。

前端在 PC 端使用三张并列服务卡展示开关与当前状态，窄屏回退为单列。开关保留原生 checkbox 语义、可见焦点和说明文本，不引入新的前端依赖。后端校验只接受布尔值，避免字符串 `"false"` 被误判为开启。

`SummaryTask` 文章结构为：

```text
开篇总结
  -> 本周主线
  -> N x（项目概述 -> 项目图片 -> 技术特点 -> 机制拆解 -> 工程启发）
  -> 全文末尾工程启发
```

项目调用只看到当前仓库证据，并同时产出正文与 `visual_spec`；视频兼容所需 `visual_brief` 由规划服务派生，不参与静态图模板选择。全局综合只读取已经验证的项目结果，不重写项目事实。某个项目首次失败时只修复该项目；第二次仍失败则任务整体失败，不保存部分文章。

## 运行调用链

```text
Application
  -> SearchTask
  -> SummaryTask
       -> N 次项目生成（概述 + 三分点 + ArticleVisualSpec）
       -> 1 次全局综合（标题 + 摘要 + 开篇 + 主线 + 结尾）
       -> 确定性拼装文章 + N 份 ContentBrief
  -> ShortVideoPromptTask（讲稿 + 旁白 + 分镜）
  -> ImageTask（N 份规格校验 + 固定 HTML/SVG 模板 + Gotenberg PNG）
  -> VideoClipPlanTask
  -> AudioTask
  -> StorageTask
  -> SeedanceClipTask
  -> SeedanceClipStatusTask
  -> VideoAssemblyTask
  -> PreviewTask
  -> ArticleLayoutTask
  -> DeliverTask
  -> CatTask
```

周五 08:00 的内容生产 Job 负责生成文章、图片、分镜、旁白并提交 Seedance 分片任务。周五 09:00 的草稿 Job 先轮询分片状态；所有片段完成后再装配最终视频、刷新预览并推送公众号草稿箱。

`VideoStatusTask` 仅为历史单段视频资产保留兼容入口；新链路不再依赖它完成分片视频。

`ArticleLayoutService` 按仓库名、`项目 N` 或 `第 N` 识别项目标题，在项目概述后插入对应图片。它不再用 `Top N` 判断单个项目，避免“Top N 项目拆解”总标题误触发排名 N 的图片。排版和手动 GitHub 图片升级都遍历完整 `image_prompts`，不对动态 N 做前 5 项截断。

## 视频状态流转

```text
video_clip_plan: planned
  -> SeedanceClipTask
video_clip_task: submitted / processing
  -> SeedanceClipStatusTask
video_clip: created
  -> VideoAssemblyTask
video: created
  -> StorageTask / PreviewTask / DeliverTask
```

- 单个 Seedance 分片查询或下载失败时，只更新为 `processing` 并记录原因，下一次调度会继续重试。
- 明确的远端失败状态才会把该分镜与任务标记为 `failed`，供 CatTask 和人工审核定位。
- 已下载的片段会按 `clip_plan_id` 复用，重复运行不会再次下载或重复创建成片。
- 旧的本地幻灯片兜底视频不会阻止新生成的 Seedance 成片进入装配流程。

## 工作台生命周期展示边界

- **设计目标**：五阶段主任务生命周期只在 `/review/pipeline` 详情页展示，避免工作台及素材、历史等页面重复出现同一组状态。
- **技术取舍**：删除 `App.vue` 审核路由壳层中的顶部资源状态栏，在任务流程页以“内容 → 图片 → 音频 → 视频 → 审核”的横向流程集中展示；原有底层 Task 列表保留为“执行任务明细”，不新增接口。
- **调用链**：预览与素材 API → `content` / `mediaAssets` / `approval` → `lifecycleStages` → `/review/pipeline` 主任务生命周期；任务运行记录 API → `tasks` → `taskCards` → 工作台任务流程预览 / 详情页执行任务明细。

## 提示词预览视口

- **设计目标**：`/review/prompts` 在 PC 端固定在工作台当前视口内，避免生图与视频提示词将整页无限向下撑开。
- **交互取舍**：生图提示词和视频提示词保持左右两列，两列内容各自独立纵向滚动并保留可见滚动槽；手机端回退为单列页面自然滚动。
- **信息边界**：页面仅保留“生图提示词”“视频提示词”两个中文标题，删除只读状态说明和英文装饰眉题，不改变提示词数据及接口调用链。

## 工作台首屏收敛

- **设计目标**：`/review` 在 PC 端固定在当前工作区高度内，由工作台内容容器独立滚动；健康概览、管理员运行区和模块入口采用紧凑间距，避免大卡片把常用入口推到多屏之后。
- **信息取舍**：删除 CatTask、Manual Run、Article、Pipeline、Assets、Archive 等英文装饰眉题，以及“健康”“管理员操作”“ready”“只读归档”等重复状态胶囊；保留真实计数、错误状态和运行中状态。
- **模块布局**：宽屏五个入口等高并排，较窄 PC 自动切换三列或两列；模块预览限制高度，手动运行记录在卡片内滚动。手机端回退为单列页面自然滚动。
- **短视频蓝图入口**：当前蓝图仅作为后端视频生成合同使用，独立页面不能编辑、审批或改变后续流程，因此工作台卡片和审核导航暂时隐藏；直接访问 `/review/storyboard` 回到审核总览。页面模板、API、数据与音视频任务均保留，前端开关可随未来人工审核需求恢复。
- **依赖与边界**：不修改后端任务、状态存储、审核动作和媒体资源数据，只收敛前端展示位置；其他业务页面不再展示主任务生命周期。
- **验证方式**：前端测试与生产构建通过，确认五阶段生命周期只在 `/review/pipeline` 模板分支渲染，并确认蓝图入口不进入可见路由集合。

## 媒体素材展示边界

- **设计目标**：`/review/assets` 只展示用户可直接查看或播放的图片、音频、成片和视频片段，避免任务句柄与 JSON 报告占用大尺寸预览卡片。
- **数据取舍**：`/api/media-assets` 的 `items` 与计数、工作台媒体概览以及执行历史素材数只包含 `image`、`audio`、`video`、`video_clip`；`video_clip_task`、`video_quality_report`、`video_narration_timeline` 继续保存在 `media_assets` 中供任务追溯，但不进入素材库。
- **前端兜底**：素材页对接口结果再次按上述四种类型过滤，兼容旧服务或缓存返回的流程记录；标题统计使用“媒体文件”，不再把数据库记录数描述为实际文件数。
- **图片查看交互**：素材库图片缩略图、素材卡片的“打开预览”和正文排版图片均进入同一个应用内全屏查看器；查看器固定显示“下载原图”和“关闭预览”，同时支持 `Esc`、点击画布空白处关闭，不再依赖新标签页返回。
- **调用链与依赖**：`decorateOpenableImages` 为正文图片保留原图地址并标记预览入口，`App.vue` 将正文和素材库点击统一写入预览状态，`ImagePreviewDialog.vue` 只负责展示、关闭、焦点恢复和下载链接；继续复用 `asset.preview_url`，不新增第三方依赖或后端接口。
- **边界**：本次只调整 PC 端图片交互；音频、视频、媒体文件存储、鉴权和响应头保持原样，下载仅在用户点击查看器中的“下载原图”后触发。
- **验证方式**：单元测试覆盖正文图片链接装饰、应用内预览请求和非图片点击忽略；前端 191 项测试与生产构建通过。PC 端浏览器验收确认查看器图片适配、关闭按钮、`Esc`、焦点状态与下载链接均可用。

## 历史归档文章与媒体联查

- **设计目标**：每个历史 `content_id` 都能联查生成时保存的原文章与对应媒体资源，历史列表同时显示精确到分钟的生成时间。
- **调用链**：`/api/execution-history` 构建归档索引；历史卡片进入 `/review/assets?content_id=<id>`，`refreshDashboard` 通过 `/api/execution-history/{content_id}` 读取该批次正文，并通过 `/api/media-assets?content_id=<id>` 读取该批次媒体；资源页的“文章原文”入口进入 `/review/article?content_id=<id>`，继续复用同一历史详情负载。
- **技术取舍**：不把完整正文塞进历史列表响应，也不复制文章到媒体表；文章继续以 `generated_contents.article_markdown` 和可选的 `article_layouts.article_html` 为唯一来源。历史文章页保持只读，避免误触旧批次审核或重新生成。
- **页面边界**：历史列表负责选择归档，资源页负责汇总同批次文章入口与媒体，文章页负责阅读全文；三页分别保持单一主要任务。
- **验证结果**：前端回归覆盖原文入口、`content_id` 路由、历史只读、时间戳和蓝白主题；PC 端本地页面确认历史卡片、六张媒体卡片、原文入口与完整文章按同一 `content_id` 串联。

## 音频与画面一致性

Seedance 每段只生成教学风动态画面：提示词明确禁止旁白、口型与内嵌字幕。`AudioTask` 只生成一条完整中文旁白，`VideoAssemblyTask` 通过 ffmpeg 统一静音、规范化、拼接七段画面，再混入这一条旁白。

这样避免七段视频各自生成不同音色、语速或文案。当前 `VideoClipPlanTask` 会生成 7 段、每段 15 秒的计划，最终理论时长约 105 秒；这是此前采用“七段十五秒”方案的结果。配置中的 `video.duration_seconds: 60` 仍是旧版整体目标时长，不参与当前装配裁剪，后续应由产品决定统一为 60 秒或 105 秒后再收敛配置。

本地开发继续默认关闭音视频生成，避免调试时产生费用。2026-08-22 起生产环境已显式设置 `VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`，Scheduler 会进入视频蓝图、分镜、Seedance、视觉质检、旁白、TTS 和装配阶段；`AudioTask` 与 `SegmentedAudioTask` 在真实调用入口仍会再次检查音频开关。

## 本地开发与上线配置

| 场景 | 推荐配置 | 行为 |
| --- | --- | --- |
| 本地开发 | `video.submit_enabled=false`、`audio.enabled=false` | 只生成文章、图片、排版和图文草稿，不生成或上传音视频。 |
| 生产云端 | `VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true` | API 与 Scheduler 运行完整音视频链路，会产生 Seedance、视频质检和 TTS 调用。 |

生产环境最小必需变量：

```text
VOLCENGINE_ARK_API_KEY=
DOUBAO_TTS_API_KEY=
# 可选；未填则使用 app.yaml 的默认音色
DOUBAO_TTS_VOICE_TYPE=

# 仅在 video.reference_images_enabled=true 时需要；R2 是当前已实现的对象存储 Provider
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```

本地 `storage.provider=local` 只有在设置 `STORAGE_LOCAL_PUBLIC_BASE_URL` 且该地址实际能被火山方舟访问时，才能作为 Seedance 的图片引用来源。当前 `generation_type=text-to-video` 且 `reference_images_enabled=false`，因此它不是视频提交前置条件；生产 Compose 仍会由 Caddy 只读服务 `outputs/public`，供媒体预览与未来参考图模式使用。

公众号草稿的正文图片数量不再固定为 5。`ArticleLayoutTask` 会把本次有效项目数写入 `layout_stats.expected_image_count`，`DeliverTask` 的前置检查和真实上传共同读取这个值；旧排版记录则由 `embedded_image_count + missing_image_count` 推导。因此 Top1 内容只要求 1 张项目图，Top5 内容仍要求 5 张，且报错会显示本次真实目标数。

## Kimi 的评估结论

没有把“Kimi K3 本地部署”加入运行链路：截至本次调研，Moonshot 官方模型列表明确提供的是 Kimi K2.5 / K2.6 API，未找到可作为生产依赖的 Kimi K3 本地部署官方资料。RTX 5070 的本地模型也不应成为未来个人网站上线时的硬依赖。

若后续需要提高分镜、标题或提示词质量，可把 Kimi K2.6 作为可选的云端“脚本审校 Provider”，而不是替代豆包 TTS 或 Seedance 视频生成。官方文档：

- [Kimi 模型列表](https://platform.kimi.ai/docs/models)
- [Kimi K2.6 快速开始](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart)
- [Kimi API 概览](https://platform.kimi.ai/docs/api/overview)

## 验证结果

已在本地完成以下无外部 API 验证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_video_assembly.py
```

该脚本生成 3 个测试片段和一条测试音频，验证 ffmpeg 可以规范化、拼接并混音，输出 MP4 文件。配置加载与新任务模块的 Python 编译检查也已通过。

豆包短文本旁白的分段与拼接可额外通过以下命令验证；它只生成本地测试音频，不会读取或消耗豆包凭证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_chunking.py
```

豆包语音改为新版 API Key 后，还可先执行下列离线协议验证。它会模拟 V3 NDJSON
响应，不会发送真实旁白或消耗语音额度：

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_v3_contract.py
```

生产开启音视频前必须先以环境覆盖方式运行 `scripts/check_media_production_readiness.py`，确认 Seedance、豆包 TTS、存储与视频装配没有阻塞项；随后设置 `.env.production` 的两个开关为 `true` 并重建 `career-api` 与 `pipeline-scheduler`。2026-08-22 验收结果为 `ready_for_real_seedance_submission=true`，未主动触发付费生成任务。
