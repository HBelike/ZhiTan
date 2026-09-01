# 技术文章确定性总结配图设计

日期：2026-08-28

状态：待评审

范围：本地设计与验证，不包含生产部署

## 1. 背景与问题

当前 `SummaryTask -> ImageTask -> SeedreamProvider` 链路把技术文章的项目摘要转换为图片 prompt，再要求 Seedream 同时完成中文文字、节点排版和有向连线。2026-08-28 的真实运行虽然成功生成了 6 张 2048×1152 PNG，但图片质量不可接受，原因不是单一网络错误，而是生成方案本身不适合精确技术图。

已确认的问题包括：

- `config/app.yaml` 把“无标题的16:9工程架构信息图”写入所有图片的统一视觉系统，导致 6 张图都出现或模仿“工程架构”“16:9”等无内容价值的元文字。
- `MediaCreativeBriefService._short_label()` 在进入生成模型前把标签截断到 6 个字符，产生 `Agent运`、`统一LLMA`、`SKILLm` 等残缺文字。
- `SummaryTask` 强制所有项目都使用架构节点和数据流，即使证据只足以支持项目摘要、功能列表或对比关系。
- 布局、节点和边的约束互相冲突；概率式图片模型会复制节点、反向箭头、生成假 Logo 或额外文字。
- `ImageTask` 在图片返回后立即写入有效资产，没有文字、拓扑、溢出和语义校验。
- OCR 只能检查部分文字，不能可靠证明箭头方向、节点唯一性和证据一致性，因此不能把“Seedream + OCR”当作质量保证方案。

此前的网络报错另有明确原因：Windows 系统代理 `127.0.0.1:7897` 被 `requests` 继承，代理断连后触发 `ProxyError`。该问题已通过最多 5 次重试、指数退避和代理失败后的直连回退修复并完成真实验证，但它只解决可用性，不解决图片质量。

## 2. 设计目标

1. 针对中文技术文章，为每个项目或核心章节生成一张可读、可核验的总结图。
2. 中文文字、节点数量、箭头方向和层级必须由程序确定性渲染，不交给图片模型猜测。
3. 图片内容必须能追溯到 GitHub 仓库证据或文章中的已验证结论。
4. 全自动运行；没有人工审核步骤。
5. 生成或验证失败时关闭式失败，不发布低质量兜底图，也不替换已有有效图片。
6. 复用项目已有 Gotenberg、Playwright、`media_assets` 和 `image_prompts_json`，避免新增不必要的服务和数据库表。

## 3. 非目标

- 首版不制作通用海报编辑器、拖拽设计器或模板市场。
- 不让 Seedream 绘制中文标签、流程线、架构节点或对比表。
- 不用 OCR 作为每次渲染的主要正确性证明。
- 不在本次方案中做手机端或平板端专项适配。
- 不连接生产服务器，不部署或重建生产容器。
- 不删除历史图片；旧资产继续作为执行历史保留。

## 4. 已确定的产品决策

| 主题 | 决策 |
| --- | --- |
| 图片密度 | 每个项目或核心章节一张图 |
| 文字与拓扑 | 使用固定模板和确定性渲染 |
| 自动化 | 全自动，无人工审核 |
| Seedream | 仅可用于无文字的概念背景；首版默认关闭 |
| 模板 | 固定模板，不做自由画布 |
| 证据不足 | 降级为总结卡，不虚构架构 |
| 校验失败 | 阻断本次图片任务，不发布低质量回退图 |
| 画布 | PC 优先，固定 2048×1152 PNG |

图片中禁止出现描述图片格式或构图类型的元文字，包括但不限于：

- `16:9`、`16.9`
- `工程架构`、`架构信息图`、`技术信息图`
- `流程图`、`对比图`、`示意图`
- `无标题`

允许显示与文章内容直接相关的短标题，例如项目名、核心机制或结论，但不得用装饰性标题挤占信息空间。

## 5. 总体架构

```text
GitHub 证据 / 文章项目分析
          │
          ▼
      SummaryTask
          │  结构化事实与 evidence_refs
          ▼
ArticleVisualPlanningService
  ├─ 采用 baoyu 的 Type × Style × Palette 规划方法
  ├─ 选择最匹配的图型
  └─ 产出 ArticleVisualSpec v1
          │
          ▼
ArticleVisualSpecValidator
  ├─ 结构、证据与禁词校验
  ├─ 节点/边引用校验
  └─ 内容长度与模板容量校验
          │
          ▼
ArticleVisualTemplateService
  ├─ summary_card
  ├─ flow
  ├─ architecture
  ├─ comparison
  └─ timeline
          │  单文件 HTML + CSS + inline SVG + 字体
          ▼
GotenbergScreenshotProvider
  └─ /forms/chromium/screenshot/html → 2048×1152 PNG
          │
          ▼
RenderedVisualValidator
  ├─ DOM 文字与规格一致
  ├─ 无溢出、重叠或裁切
  ├─ PNG 可解码且尺寸正确
  └─ 原子写入，无 .part 残留
          │
          ▼
      media_assets
```

## 6. baoyu Skill 的使用方式

已安装并启用 `baoyu-article-illustrator`，项目偏好保存在：

```text
.baoyu-skills/baoyu-article-illustrator/EXTEND.md
```

采用的偏好为中文、`notion` 风格、克制编辑式层级、精确文字、少量蓝色强调、无水印。

该 Skill 在本方案中承担“文章结构分析与视觉类型选择”的设计方法，不作为 Python 服务运行时依赖，也不直接生成最终技术图。原因是本地 Codex Skill 不能被后端任务当作稳定渲染 API 调用，且其常规图片生成路径仍是概率式位图生成，无法保证中文和拓扑精度。

实现时把下列 baoyu 原则固化进项目自己的规划服务和 LLM 输出契约：

- 先理解文章结构，再判断哪里真正需要图。
- 每张图只有一个明确传播目的。
- 根据内容选择 `infographic`、`flowchart`、`comparison`、`framework`、`timeline` 等类型，而不是所有内容都画架构图。
- Style 与 Palette 在整篇文章保持一致。
- 图中信息密度受模板容量约束，超限时改写规格，不缩小到不可读字号。

这既是对 baoyu Skill 的实际使用，也避免把 Agent Skill 错当成线上运行时组件。

## 7. ArticleVisualSpec v1

`SummaryTask` 不再只输出自然语言图片 prompt 和强制性的 `visual_brief`，而是为每个项目输出一个可验证的 `visual_spec`。首版复用 `generated_contents.image_prompts_json`，无需数据库迁移。

示例：

```json
{
  "version": "article_visual_spec_v1",
  "repository_full_name": "owner/project",
  "figure_role": "flow",
  "purpose": "解释规格如何逐步转化为可执行任务",
  "headline": "从规格到可执行任务",
  "evidence_refs": [
    {"kind": "repository_file", "path": "README.md", "claim": "项目采用四阶段工作流"}
  ],
  "steps": [
    {"id": "spec", "label": "规格", "description": "明确目标与约束"},
    {"id": "plan", "label": "计划", "description": "拆分交付步骤"},
    {"id": "task", "label": "任务", "description": "形成可执行清单"}
  ],
  "edges": [
    {"from": "spec", "to": "plan"},
    {"from": "plan", "to": "task"}
  ],
  "takeaways": ["先约束再实现", "每一步都有可验证产物"],
  "art_direction": {
    "style": "notion",
    "palette": "editorial_blue",
    "density": "medium"
  }
}
```

公共字段：

| 字段 | 约束 |
| --- | --- |
| `version` | 固定为 `article_visual_spec_v1` |
| `repository_full_name` | 与文章项目严格对应 |
| `figure_role` | 仅允许已注册模板类型 |
| `purpose` | 一句话说明图片要解释什么 |
| `headline` | 内容相关短标题；不得包含元文字禁词 |
| `evidence_refs` | 至少一条，可回溯到文件、源码、仓库元数据或文章 Claim |
| `takeaways` | 1–3 条，每条为完整短句，不做字符硬截断 |
| `art_direction` | 固定风格系统中的有限枚举，不允许自由 CSS |

模板专属字段：

- `summary_card`：`positioning`、`capabilities`、`takeaways`
- `flow`：`steps`、`edges`
- `architecture`：`nodes`、`edges`、可选 `layers`
- `comparison`：`left`、`right`、`dimensions`
- `timeline`：`events`

字段按 Unicode 字符、实际渲染宽度和模板容量校验。禁止用 `label[:6]` 一类静默截断；超过容量时要求规划层重新压缩语义，仍不合格则失败。

## 8. 图型选择与降级规则

选择顺序由证据决定：

1. 存在明确时间、版本或演进阶段时使用 `timeline`。
2. 存在两个可比较方案及共同维度时使用 `comparison`。
3. 存在有序步骤和明确方向时使用 `flow`。
4. 存在可证实的模块、边界和依赖关系时使用 `architecture`。
5. 其他情况使用 `summary_card`。

`summary_card` 是信息结构的安全降级，不是失败兜底图。它只呈现项目定位、经证实的能力和核心结论，不推断不存在的模块或连线。

首版不启用独立 `concept` 模板。未来若启用 Seedream 概念背景，必须满足：

- 背景 prompt 明确禁止文字、Logo、节点和箭头。
- 背景只作为装饰层，所有文字仍由 HTML 渲染。
- 背景失败时允许退回纯色/渐变背景，因为不影响语义。

## 9. 模板与渲染

### 9.1 固定视觉系统

- 画布：2048×1152，16:9 只作为内部尺寸，不显示在图片中。
- 风格：浅色编辑式画布、深蓝主色、浅灰分组、少量高亮色。
- 字号下限：正文不低于 34 px，标签不低于 32 px。
- 所有文本均由浏览器排版，不由图片模型生成。
- 节点和边使用 inline SVG；边根据规格中的 `from`、`to` 计算，箭头只出现一次。
- 模板只接收转义后的结构化数据，不接受任意 HTML 或 CSS。

### 9.2 中文字体

当前仓库没有随包字体，不能依赖开发机或 Gotenberg 镜像恰好安装中文字体。实现阶段引入一份许可证兼容的开源中文字体（优先 Noto Sans SC / Source Han Sans SC，保留 OFL 许可证），作为渲染资源随 multipart 请求上传，并在 HTML 中通过 `@font-face` 使用。

字体属于确保中文稳定显示的必要依赖，不是视觉增强项。验收时需要在没有宿主机中文字体的 Gotenberg 环境中确认无方框字和缺字。

### 9.3 Gotenberg

复用 `docker-compose.production.yml` 已存在的 `career-gotenberg`，调用：

```text
POST /forms/chromium/screenshot/html
```

上传 `index.html` 与字体资源，指定 PNG、2048 宽、1152 高。服务地址从现有 `CAREER_GOTENBERG_SERVICE_BASE_URL` 读取；本地集成测试使用已验证可用的 `http://127.0.0.1:3000`。

按照 Gotenberg 官方 HTML screenshot 契约，请求固定使用：

- `width=2048`、`height=1152`、`clip=true`
- `deviceScaleFactor=1`、`format=png`
- `waitForExpression=window.__visualValidation?.status === 'passed'`
- `failOnConsoleExceptions=true`
- `failOnResourceLoadingFailed=true`

HTML 在字体加载完成后执行同一套 DOM 溢出、重叠、节点和边校验；通过后设置 `window.__visualValidation.status = 'passed'`，失败则抛出包含具体校验项的异常。这样 Gotenberg 会等待显式完成条件，并在脚本异常或字体资源加载失败时返回非成功响应，而不是截取半成品。所有资源都随请求上传，不依赖外部 URL。

Playwright 用于本地模板开发、DOM 校验和自动化测试，不再额外部署浏览器服务。生产渲染仍由 Gotenberg 负责，避免 API 容器自行管理 Chromium。

## 10. 校验与失败语义

### 10.1 渲染前

- 规格版本、字段类型、枚举和必填项正确。
- `evidence_refs` 非空且引用项目分析中存在的证据。
- 标题和正文不含元文字禁词、占位符或残缺标签。
- 节点 ID 唯一；每条边引用已存在节点；不允许自环和重复边。
- 模板数量合同满足，例如流程步骤按实际证据动态确定、架构节点保持 3–7 个。
- 文字长度、预估行数和总信息量在模板容量内。

### 10.2 浏览器内

模板在截图前运行校验脚本，并通过 Gotenberg 的 `waitForExpression` 与 `failOnConsoleExceptions` 把结果转换为请求成败：

- DOM 中每个可见文本项与规格一致。
- `scrollWidth <= clientWidth`、`scrollHeight <= clientHeight`。
- 节点包围盒不重叠、不越过安全边距。
- SVG 边数等于规格边数，每条边保留 `data-from` 和 `data-to` 以供测试核对。
- 字体加载完成后才允许截图。

### 10.3 输出后

- 响应为可解码 PNG。
- 尺寸严格为 2048×1152。
- 文件先写入同目录临时文件，验证成功后原子替换目标路径。
- 任务结束后不存在 `.part` 文件。

OCR 仅用于模板或字体变更时的抽样回归，不作为每次运行的主校验。因为文字来自确定性 DOM，主要证明应来自输入规格、DOM 和像素文件三者的一致性。

任何语义或渲染校验失败时：

1. 当前图片不写入 `media_assets`。
2. 当前任务标记失败并记录具体校验项。
3. 已存在的有效资产保持原状态。
4. 不调用 Seedream 生成有文字的兜底图。

## 11. 幂等、资产替换与可观测性

每个规格计算：

```text
render_key = sha256(canonical_visual_spec + template_version + renderer_version + font_version)
```

`media_assets.metadata_json` 增加：

- `visual_spec`
- `visual_spec_version`
- `figure_role`
- `evidence_refs`
- `render_key`
- `template_version`
- `renderer_version`
- `font_version`
- `width`、`height`
- `validation_result`

当同一项目已有状态有效、文件存在且 `render_key` 一致的图片时直接复用。刷新时先完成新图生成和全部校验，再创建新资产记录，最后把该项目旧图标记为 `replaced`；不得先废弃旧图。

任务结果区分：

- `reused_image_count`
- `rendered_image_count`
- `validation_failed_count`
- `figure_role_counts`
- `renderer=gotenberg_html`

## 12. 模块调整

### 12.1 摘要与规划

- `src/tasks/summary_task.py`
  - 取消“所有项目必须是架构图”的契约。
  - 要求输出事实、Claim、证据引用和 `visual_spec`。
- 新增 `src/services/article_visual_planning_service.py`
  - 固化 baoyu 的图型选择规则。
  - 规范 LLM 输出并执行证据不足降级。
- 新增 `src/services/article_visual_spec_validator.py`
  - 负责渲染前结构与证据校验。
- `src/repositories/generated_content_repository.py`
  - 在现有 JSON 规范化中透传 `visual_spec`，保留旧 `visual_brief` 读取兼容。

### 12.2 模板与渲染

- 新增 `src/services/article_visual_template_service.py`
  - 为五种图型产生单文件安全 HTML、CSS 和 inline SVG。
- 新增 `src/providers/gotenberg_screenshot_provider.py`
  - 负责 multipart 请求、超时、响应和原子文件写入。
- 新增 `src/services/rendered_visual_validator.py`
  - 负责 DOM 校验结果和 PNG 输出校验。
- 调整 `src/tasks/image_task.py`
  - 主路径改为 `visual_spec -> validate -> template -> Gotenberg -> validate -> media_assets`。
  - Seedream 保留为未来无文字概念背景的可选 provider，不参与首版技术图。
  - 取消技术图失败后调用 `local_tech_card` 或 Seedream 文字图的回退。

### 12.3 配置与预览

- `config/app.yaml`
  - 新增确定性 renderer、画布、模板和字体配置。
  - 废止图片主路径的 `visual_system`、`composition_rule`、`text_rule` 和 `style_rule`。
  - 首版 `concept_background_enabled: false`。
- `src/config/config_manager.py`
  - 读取并校验新配置，同时为旧配置提供一次过渡性默认值。
- `src/services/media_preview_service.py`
  - 展示 `visual_spec`、图型、模板版本和校验结果。
  - 不再把确定性规格标记为 Seedream prompt。
- 前端仅调整现有 PC 端媒体预览信息，不新增纵向堆叠的完整功能模块。

## 13. 兼容与迁移

- 不新增数据库表或迁移；规格保存在现有 `image_prompts_json`，渲染信息保存在资产 metadata。
- 旧 `visual_brief` 和旧 Seedream 图片可读取、可预览，但新运行不会复用缺少匹配 `render_key` 的旧图片。
- 用户明确刷新某个历史内容时，新图全部生成并验证成功后才逐项目替换旧图。
- 视频链路若仍读取 `visual_brief`，过渡期由规划服务同时派生兼容字段；该字段不再影响静态图片模板选择。

## 14. 测试与真实验证

### 14.1 单元测试

- 图型选择：时间线、对比、流程、架构和总结卡覆盖。
- 证据不足自动降级为 `summary_card`。
- 禁词、占位符、空证据、重复节点、悬空边和超容量全部拒绝。
- 中文标签完整保留，不允许静默字符截断。
- canonical JSON 与 `render_key` 稳定。
- 旧 `visual_brief` 读取兼容。

### 14.2 模板测试

- 每种模板验证可见文字、节点数、边数和 `data-from/data-to`。
- 使用极限允许长度验证无溢出、重叠和裁切。
- 验证 HTML 转义，规格内容不能注入脚本或样式。
- 验证中文字体加载完成。

### 14.3 Gotenberg 集成测试

- 调用真实 `/forms/chromium/screenshot/html`。
- 生成 2048×1152 PNG 并解码。
- 检查图片中无方框字，DOM 校验通过，无 `.part` 残留。
- 模拟服务错误，确认最多 5 次有界重试后失败，且不会创建资产记录。

### 14.4 端到端验收

以当前 `content_id=20` 的 6 个项目为真实样本重新执行：

1. 重新生成 6 份 `ArticleVisualSpec`。
2. 至少覆盖 `summary_card` 和一种关系型模板；若证据不足，不为覆盖率虚构图型。
3. 真实生成 6 张 2048×1152 PNG。
4. 核对每张图的完整中文、项目专属结论、节点唯一性和边方向。
5. 确认图片内不出现 `16:9`、`16.9`、`工程架构`、`无标题` 等元文字。
6. 确认 6 个有效新资产均有 `render_key`、模板版本和验证结果。
7. 再运行一次，确认 6 张图按 `render_key` 全部复用，不重复渲染。
8. 注入一个非法规格，确认任务失败且已有 6 张有效图片不被替换。

随后运行相关测试集和全量测试。全量测试若存在与本模块无关的工作区既有失败，需单独列明，不能把它们误报为本方案通过或失败。

## 15. 文档同步

实现阶段同步更新：

- `docs/weekly_media_pipeline.md`：新调用链、失败语义、重试和真实验证结果。
- `docs/media_creative_direction.md`：Type × Style × Palette 选择规则、模板容量和禁词。
- `docs/image_prompt_skill_research.md`：记录 baoyu Skill 的适用边界及不作为运行时渲染器的原因。

每完成一个模块，文档记录设计目标、技术取舍、调用链、依赖、验证结果和后续边界。

## 16. 风险与边界

- LLM 仍可能产出错误事实：通过 `evidence_refs` 和规格校验降低风险，但不能替代上游仓库证据质量。
- 固定模板牺牲自由度：这是换取中文、拓扑和自动化可靠性的主动取舍。
- Gotenberg 是运行依赖：项目已经部署该服务，不新增基础设施；服务不可用时任务明确失败。
- 字体会增加镜像或仓库体积：实现前核对许可证并选择满足中文覆盖的最小合理字重集合。
- 未来加入概念背景时不得改变首版的语义层原则：背景模型只负责无文字装饰，文字和关系始终由确定性渲染层负责。

## 17. 验收标准

方案实施完成需同时满足：

- 技术图主路径不调用 Seedream。
- 每张图都来自通过校验的 `ArticleVisualSpec v1`。
- 中文、节点和边由 HTML/SVG 确定性渲染。
- 图片中没有格式/图型元文字和截断标签。
- 真实 6 图运行、幂等复跑和失败保护均通过。
- 校验失败不会产生有效资产，也不会替换旧图。
- 相关文档与测试同步完成。
- 仅完成本地修改和验证，生产部署需用户另行明确授权。
