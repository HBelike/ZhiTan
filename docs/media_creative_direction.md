# 技术文章配图创作方向

## 当前目标与边界

技术文章配图的首要目标是准确总结项目定位、机制和工程取舍，而不是追求图片模型的随机画面感。当前静态图采用固定 `2048×1152` PC 画布、确定性中文排版和可验证的节点/边；不做自由画布、模板市场、手机或平板专项适配。

Seedream 不再负责带中文文字、节点和箭头的技术总结图。未来若启用无文字概念背景，背景也只能作为装饰层，所有语义文字仍由 HTML 渲染；首版 `concept_background_enabled=false`。

## baoyu 方法的实际使用

项目已安装 `baoyu-article-illustrator`，并采用它的 Type × Style × Palette 方法：Type 先看文章证据再选择总结卡、步骤关系、分层模块、双列比较或阶段演进；Style 固定为克制的 `notion` 编辑式层级；Palette 固定为 `editorial_blue`。

baoyu 只负责规划方法，不作为 Python 服务运行时依赖，也不直接生成最终位图。原因是本地 Agent Skill 不是稳定的线上渲染 API，常规图片生成仍无法保证逐字中文与拓扑方向。

## 图型与容量

| 模板 | 选择条件 | 容量边界 |
| --- | --- | --- |
| `summary_card` | 其他类型证据不足，或历史文章降级 | 定位 180 字；能力 2–4 项，每项描述 150 字；结论 1–3 条、每条 50 字 |
| `flow` | 有明确连续步骤 | 步骤数量按实际证据动态确定，边只能连接相邻步骤 |
| `architecture` | 有可证实模块与依赖 | 3–7 个节点、按真实结构动态分层、2–12 条边 |
| `comparison` | 有两个方案和共同维度 | 2–4 个共同维度 |
| `timeline` | 有明确时间或版本演进 | 3–6 个事件 |

证据不足时选择 `summary_card` 是正常降级，不虚构架构。旧内容只读取 `summary_text` 和文章中的“技术特点、机制拆解、工程启发”，不会读取旧 `visual_brief` 的截断节点或伪拓扑。

三能力 `summary_card` 使用右侧单列三行，主体高度 670 px；能力描述最多 150 字，超过 60 字时按长度切换紧凑或高密度字号，最终排版仍由浏览器溢出校验兜底。冒号和逗号不作为完整句结束符；过长句选择后续首个容量内完整句，找不到则失败，绝不按字符切成残句。底部 takeaway 从项目定位中选择完整句，并与能力描述去重。

动态数量分为三层：管理台 `top_n` 决定当期生成的项目数，`SummaryTask` 对实际周榜项目逐项生成，不再附加静态图数量限制；单个项目的 `flow.steps` 按证据中的真实流程确定；`architecture.layers` 按证据中的真实分层确定，不设置独立数量上限，但每层必须至少包含一个节点，因此实际层数自然受 3–7 个架构节点约束。调用链为 `SummaryTask` 生成规格 → `ArticleVisualSpecValidator` 校验非空步骤、分层引用和严格相邻边 → `ArticleVisualTemplateService` 执行动态布局 → Gotenberg 执行 DOM 与 PNG 验收。架构图达到 5 层时自动切换紧凑排版，达到 6 层及以上时切换高密度排版，所有层、节点和连线均保留。流程边数量始终由步骤顺序推导为 `steps - 1`，无需额外配置。2026-08-28 已用 1、6、12 步规格完成单元测试，并用真实 Gotenberg 截图验证 6、12 步无溢出、越界或节点重叠；超过当前画布文字容量时仍由浏览器质量门失败关闭，不静默删减步骤。

## 禁词、渲染与失败语义

图中禁止出现 `16:9`、`16.9`、`工程架构`、`架构信息图`、`技术信息图`、`流程图`、`对比图`、`示意图`、`无标题` 等描述图片比例或构图类型的元文字。

```text
ArticleVisualSpec v1
  -> ArticleVisualSpecValidator
  -> fixed HTML/CSS/inline SVG + Noto Sans SC
  -> Gotenberg /forms/chromium/screenshot/html
  -> DOM validation + Pillow PNG validation
  -> media_assets
```

浏览器在字体加载后确认文字逐项一致、无溢出、无越界、节点不重叠、SVG 边身份与规格一致。只有整批新图全部通过，数据库才在一个事务中创建新资产并替换旧资产；失败不会调用 Seedream 或本地卡片生成带文字兜底图，也不会提前废弃旧资产。

2026-09-02 移除 `architecture.layers` 的 4 层数量上限，生成提示改为按仓库证据动态分层；模板按层数切换标准、紧凑和高密度排版。本地使用真实 Gotenberg 对 5 层与 7 层架构规格完成截图验收，两张图片均一次通过 DOM、文字、边界、重叠与 `2048×1152` PNG 校验。本次未执行生产部署。

2026-08-28 本地对 `content_id=20` 的 6 个历史项目完成真实验收：6 项全部按证据降级为 `summary_card`；修复残句和重复 takeaway 后，首轮实际渲染 6 张，第二轮按 `render_key` 复用 6 张。所有图片均为 `2048×1152` PNG，逐张检查中文无方框、无裁切、无重叠、无重复节点和禁用元文字；输出目录无 `.part`。本次未执行生产部署。

---

# 历史方案记录：周榜内容创作质量升级

以下内容记录旧 `visual_brief → Seedream` 方案的背景，不再代表当前静态技术图主路径。

## 目标与边界

本次升级把“项目摘要 → 动态 N 张插图 → 动态教学视频”的过程改为一份可追溯的创作合同，避免项目间共用泛化提示词、图片同质化和视频片段在提交前被重新压缩。N 来自管理台配置形成的当期周榜数量。

- 每个项目保留独立的 `visual_brief`、`video_brief`、原始摘要意图和最终 Ark 图片 Prompt。
- 图片始终由火山方舟直接输出原始图像；禁止本地叠字、遮罩、拼贴、覆盖或把其他图片合成到模型输出上。
- 成片按开场、N 个项目段和结尾组织。每段是逐步讲解的动态信息图，不把 N 张静态图直接硬切成视频。
- Seedance 负责动态视觉片段；TTS、中文字幕、转场与合成由 HyperFrames 蓝图在人工审核通过后再确定性装配。
- 当前不执行付费生图、视频提交、视频渲染或公众号发布；`video.submit_enabled` 保持为 `false`。

## 技术取舍

| 组件 | 作用 | 采用原因 |
| --- | --- | --- |
| ByteDance DeerFlow 的结构化视觉思想 | 将摘要约束为结构、关系、阅读顺序和色板 | 避免模型只收到一句抽象描述后生成同质化画面。 |
| 火山方舟 Seedream | 生成文章的 N 张原始插图 | 保留现有已配置的服务与审核流程；按 16:9、32 对齐配置为 `2048x1152`，首次受控真实调用仍需验收提供方返回的实际尺寸。 |
| Seedance 2.0 Prompt 规则 | 为每段生成分时动作、运镜、入场和离场状态 | 使七段片段可按连续叙事衔接，而不是依赖笼统总 Prompt。 |
| HyperFrames | 保留后期装配蓝图 | HTML 时间轴可确定性地处理配音、字幕、转场和验收；不替代 Seedance 的原始动态生成。 |

本地已存在 Seedance 2.0 和 HyperFrames Skill，因此本次不重复下载未经审查的同名包。调研时排除了 GPL/AGPL 的本地 ComfyUI、A1111 路线，避免把生产视频能力绑到服务器 GPU 与强 copyleft 依赖上。

## 数据合同

`SummaryTask` 按排名串行执行 N 次单项目调用，每次生成项目概述、三个教学分点和一份 `project_brief`。项目结果全部通过后，再执行 1 次全局综合并由代码确定性拼装 `article_markdown`。任务将每份事实摘要和结构关系规范化为同序共享 `ContentBrief`；它仍不生成 Seedream 供应商 Prompt：

```text
repository_full_name / rank
summary_text / project_summary_text
project_analysis_markdown
prompt_stage = content_brief_v2
visual_brief
  -> diagram_type / teaching_goal / visual_thesis
  -> nodes / relationships / reading_order / chinese_labels
  -> palette_key / negative_constraints
video_brief
  -> narrative_claim / mechanism / motion_metaphor
  -> camera / transition / audio_directive
```

为兼容现有数据库，`ContentBrief` 暂时仍保存于 `generated_contents.image_prompts_json`，但它不是可直接提交给图片模型的最终 Prompt。它只承载文章、图片和视频共同依赖的事实、机制与视觉意图，避免三个任务各自重新理解项目后产生内容漂移。

`ImageTask` 读取 `ContentBrief`，将其编译成不超过 300 个汉字的 `ark_final_v3` Prompt，并把最终 Prompt 与同一 `content_id` 的媒体素材 metadata 一起保存。预览页仅回填该 `content_id` 下的最终 Prompt；GitHub 或本地兜底素材不会伪装成 Ark 最终提示词。

`ShortVideoPromptTask` 读取同一 `ContentBrief`，独立生成渐进式讲稿、统一旁白、七段 `scene_contract` 和每段可直接交给 Seedance 的 `seedance_scene_prompt`，再把讲稿与旁白回写当前内容记录。`VideoClipPlanTask` 必须原样保留这份详细 Prompt，只追加连续性与全局负面约束，不能再次压缩成旧的通用模板。

## 调用链

```text
main.py
  -> SummaryTask
     -> DeepSeek x N（项目概述、三分点、结构化项目简报）
     -> DeepSeek x 1（标题、摘要、开篇、本周主线、结尾）
     -> 确定性拼装深度文章
     -> MediaCreativeBriefService.normalize_visual_brief()
     -> MediaCreativeBriefService.normalize_video_brief()
     -> GeneratedContentRepository
  -> ShortVideoPromptTask
     -> DeepSeek（渐进式讲稿与七段分镜）
     -> GeneratedContentRepository.update_media_plan()
     -> MediaCreativeBriefService.build_hyperframes_blueprint()
     -> VideoStoryboardRepository
  -> ImageTask
     -> ContentBrief
     -> ImagePromptDesignService.build_project_architecture_prompt()
     -> Ark Seedream（仅在显式运行图片任务时）
     -> MediaAssetRepository
  -> VideoClipPlanTask
     -> VideoClipPlanRepository
  -> 人工审核通过后
     -> Seedance 片段生成 / TTS / HyperFrames 装配 / 微信草稿箱
```

## 质量门槛

1. 每张图都使用无标题的 16:9 工程架构信息图语言；结构类型必须随项目机制变化，不能回退为通用“输入—核心—输出”模板。
2. 每张图最多四个真实模块、四条关系；节点标签和关系起止组合不得重复。最终 Prompt 明确节点总数、唯一位置和完整 `A→B` 拓扑，不超过 300 个汉字，禁用镜像节点、对称补位、标题区、装饰点、空白占位卡片、仓库 URL、真实产品 Logo、乱码与伪文字。
3. 每段视频明确时长、进入状态、三拍动作、离开状态、运镜和转场；文本不要求在视频里逐字上屏。
4. `content_id` 是素材、最终 Prompt、文章和视频蓝图的隔离键；不可混用其他任务的媒体资产。
5. HyperFrames 只在审核后进行 `lint → check → snapshot → 人工确认 → render`，确保付费视频和发布前可查看。

## 离线验证

以下验证不读取密钥、不请求 DeepSeek、Ark、Seedance、HyperFrames 或微信：

```powershell
.\.venv\Scripts\python.exe -m compileall src scripts
.\.venv\Scripts\python.exe scripts\verify_media_creative_contract.py
Set-Location web-ui
npm run build
```

验证脚本会检查五个项目的最终图片 Prompt 不相同、无 URL、包含原始图像约束，并检查七段分镜、详细 Seedance Prompt 与待审核 HyperFrames 蓝图完整保留。

## 后续人工验收

在首次真实生图前，需要在审核台确认一份五图预览，重点看中文短标签的可读性和 16:9 横版安全区。确认后再执行一次受控的 Ark 调用，确认服务端接受 `2048x1152` 并返回预期横版尺寸；随后再启用 Seedance 片段任务，而不是直接打开全链路定时发布。
