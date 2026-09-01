# GitHub 项目文章 Skill 最小集成设计

> 状态说明（2026-08-21）：本文记录首次接入设计。后续已按人工审核流程的要求，取消标题、摘要、全文、项目章节和标签解释的全部输出长度限制；现行合同以 `docs/summary_writing_skill_research.md` 为准。

## 目标

在不改变现有周榜文章流水线的前提下，用一个自包含的 `github-project-blog` Skill 替换 `SummaryTask` 内部写死的写作方法，改善动态 Top N 文章的技术密度、教学表达和 AI 腔问题。

本次保留 `SummaryTask` 的任务名称、一次性模型生成、质量修复重试、JSON 输出、`ContentBrief` 编译、入库、审核、图片、视频、排版和公众号草稿链路。项目数量始终来自当期 `rankings`，不得写死为 5。

## Skill 形态

`github-project-blog` 是一个可独立挂载的 Skill，不在运行时依赖或继续调用其他 Skill。创建时只提炼以下已有能力：

- `blog-post-writer` 的开篇钩子、技术文章结构、具体例子、段落节奏和有效结尾；
- `humanizer` 的去套话、去营销腔、减少机械排比、保持事实不变和自然中文表达；
- 本项目现有的 GitHub 周榜事实约束、动态项目数量和公众号文章输出合同。

为适配当前普通 ChatCompletions 调用，运行时所需规则必须完整写入一个 `SKILL.md`。Skill 不使用外部引用文件、脚本或 Agent Tool Calling，也不要求生产环境另外安装 `blog-post-writer` 或 `humanizer`。

开发时 Skill 位于 `.agents/skills/github-project-blog/SKILL.md`；生产快照位于 `deploy/skill-seeds/github-project-blog/SKILL.md`。生产的既有增量种子策略保持不变。

## 调用链

```text
工作台配置的动态 Top N
  -> SummaryTask 读取周榜与现有 GitHub README 证据
  -> ArticleSkillPromptLoader 按固定名称加载 github-project-blog
  -> SummaryTask 将 Skill 正文挂载到 system message
  -> DeepSeek 单次生成 title / digest / article_markdown
  -> 现有质量修复重试
  -> 现有结构与事实校验
  -> 确定性编译 ContentBrief
  -> generated_contents 入库
  -> 既有审核及媒体流水线
```

`SummaryTask` 仍是业务编排入口。Skill 只接管“模型如何分析和写作”，不接管数据库、GitHub 请求、模型客户端、任务状态或下游媒体生成。

## Prompt 组合

模型消息按以下优先级组合：

1. 固定的角色、事实边界和 JSON 输出要求；
2. `github-project-blog` 的完整正文，以明确边界包裹；
3. 管理员运行时摘要指令和审核重生成反馈；
4. 当期动态 Top N 数据及 `source_evidence`。

旧的 `_build_writing_playbook_prompt()` 不再作为另一套写作规范保留，避免与 Skill 冲突。若指定 Skill 缺失、无正文或超出允许长度，任务应明确失败，不能静默回退到旧写法。

Skill 必须要求模型：

- 先解释项目解决的问题、技术机制和工程取舍，再给评价；
- 把 README 事实翻译为开发者能复用的工程认知，不机械复述 description；
- 信息不足时明确降级表述，不虚构架构、模块、API、性能、用户量或 benchmark；
- 使用中文讲师式技术表达，保持专业判断，但避免营销话术和模板化排比；
- 覆盖项目定位、问题、关注价值、技术栈、架构、模块、流程、值得学习的实现、关键源码线索、运行方式、优点、不足和适合人群；证据不足的项必须说明依据有限；
- 保持当前 JSON 字段和动态项目标题格式，供现有解析与下游继续使用。

## 字数规则调整

取消以下硬性限制：

- 每个项目章节至少 500 个中文字符；
- 由 `项目数量 × 500 + 固定开销` 推导出的全文最低中文字符数；
- Prompt 和质量修复 Prompt 中所有“项目正文不少于 500 字”的要求。

保留以下约束：

- 动态项目章节必须齐全且顺序与当期排名一致；
- 每个项目必须包含规定的分析标签、真实 stars 和本周增长数字；
- 标签不能只有空话，继续使用较小的标签内容下限防止空壳输出；
- 保留每个项目和全文的最大长度，防止模型失控堆砌；
- `ContentBrief` 继续从通过校验的项目章节确定性生成。

质量判断从“用字数逼深度”转为“结构、事实、技术机制、边界和可学习实现是否完整”。

## 错误处理

- Skill 缺失或读取失败：`SummaryTask` 失败并记录明确错误；
- 首次模型输出不满足结构或事实合同：沿用现有一次质量修复重试；
- 重试仍失败：不写入半成品文章；
- Skill 不得引导模型执行仓库脚本或访问额外网络资源；当前证据采集范围保持不变。

## 验证

- 验证加载器能按固定名称读取 Skill、去除 frontmatter，并拒绝缺失或空正文；
- 验证主 Prompt 和修复 Prompt 都包含 Skill 正文；
- 验证项目数量来自输入列表，且不存在固定 Top 5 假设；
- 验证不足 500 个中文字符但结构、标签和数字完整的项目章节可以通过；
- 验证缺少项目、标签或精确榜单数字仍会失败；
- 验证最大长度、JSON 字段、`ContentBrief` 和下游视频证据合同保持有效；
- 更新周榜流水线和 SummaryTask 写作能力记录，说明 Skill 来源、调用链、验证结果和当前 README 证据边界。

## 不在本次范围

- 不把一个项目拆成一次独立模型调用；
- 不增加第二次 humanizer 模型润色；
- 不扩展 GitHub 源码抓取、浅克隆或代码执行；
- 不修改工作台项目数量配置方式；
- 不重命名 `SummaryTask`，不修改数据库结构或下游任务接口；
- 不进行手机端、平板端或新的前端页面改造。
