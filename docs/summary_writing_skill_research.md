# GitHub 项目文章 Skill 与 SummaryTask 接入记录

## 目标

工作台需要把动态 Top N 周榜生成一篇结构稳定的中文技术教学文章。文章由开篇总结、N 个项目章节和末尾工程启发组成；每个项目章节依次呈现项目概述、一张架构加数据流图片、技术特点、机制拆解和工程启发。

项目数量继续来自管理台配置形成的当期 `weekly_rankings`。代码不假定 Top 5 或 Top 6。N 个项目对应 N 次独立项目生成和 N 份生图简报，最终仍只保存一篇文章。

本模块只使用公开 GitHub 仓库证据。私有仓库、需要 Token 才能读取的仓库、非 GitHub 地址和无法确认公开性的地址不在处理范围内。

## 技术取舍

- `SummaryTask` 按排名串行执行 N 次单项目 DeepSeek 调用。每次请求只包含一个仓库的榜单数据和 `source_evidence`，避免项目之间互相污染。
- N 个项目全部通过后，再执行 1 次全局综合调用，生成标题、摘要、开篇总结、本周主线和末尾工程启发。正常调用数为 N+1。
- 项目或全局结果首次未通过 JSON、事实或结构合同，只重试当前失败单元 1 次。已经通过的项目不重放；第二次仍失败则 `SummaryTask` 整体失败，不写入半篇文章。
- 标题层级、项目顺序和三个分点由代码确定性拼装。模型不再负责输出整篇 `article_markdown`，因此不能遗漏项目标题或改变项目顺序。
- 不引入并发、缓存、断点续跑或部分成功持久化。串行方式先保证调用追踪、限流行为和失败定位稳定。
- 不新增数据库字段。最终仍写入 `title`、`digest`、`article_markdown` 和兼容字段 `image_prompts_json`；其中 `image_prompts_json` 保存的是 N 份共享 `ContentBrief`。
- `DeepSeekProvider` 对 `deepseek-v4-*` 的结构化任务继续显式关闭 thinking，避免推理内容消耗 JSON 输出预算。

## github-project-blog Skill

`.agents/skills/github-project-blog` 提供两种工作台嵌入模式：

### 单项目生成

项目概述必须写明本周新增 stars、总 stars、项目作用和使用场景。两个数字保留数据库中的精确值，不改写成模糊量级。

正文只显示三个分点：

- `技术特点`：项目定位、解决的问题、整体架构、模块划分和职责；
- `机制拆解`：入口到输出的数据流、证据明确的关键类或文件、入口函数、依赖和调用关系；
- `工程启发`：技术难点、设计取舍、坑点、使用边界和源码阅读顺序。

用户要求的七项教学分析被收进这三个分点，不再展开成七个整齐小标题。这样保留技术覆盖面，同时避免文章像模板问答。

每次单项目调用还生成一份 `project_brief`。它包含事实摘要和一张图的 `visual_brief`。图内同时表达 3 到 4 个真实模块或步骤，以及一条入口到输出的主数据流。强弱耦合、同步异步和事件推送只有在证据明确时才能写入。

### 全局综合

全局调用只读取已经验证的项目结果，生成标题、摘要、开篇、本周主线和结尾。它不能重写项目章节、视觉简报或添加新项目事实。

开篇使用具体数字、开发者问题或有证据的共同变化。本周主线可以分类和比较项目，但不能制造不存在的行业趋势。结尾提炼可以迁移到其他工程实践中的判断，不逐项复述正文。

### 自然表达

Skill 吸收了 `humanizer` 的表达规则，但线上不依赖该 Skill。文章删除销售话术、匿名权威、宏大结论、聊天残留、标题复述、连续“不是 X，而是 Y”和戏剧化碎句；润色不得改变名称、数字、排名、限定词或技术结论。

## 调用链

```text
管理台配置项目数量 N
  -> SearchTask 生成 N 条 weekly_rankings
  -> SummaryTask 获取 N 份公开仓库证据
  -> SkillLibraryService / ArticleSkillPromptLoader 加载 github-project-blog
  -> 按排名串行执行 N 次单项目生成
       -> 项目概述
       -> 技术特点
       -> 机制拆解
       -> 工程启发
       -> project_brief.visual_brief
  -> 1 次全局综合
       -> title / digest
       -> opening_markdown / weekly_theme_markdown / closing_markdown
  -> SummaryTask 确定性拼装单篇 article_markdown
  -> N 份 ContentBrief
  -> N 次项目生图与后续视频任务
```

每次模型 trace 记录 `phase`、尝试次数和项目数。项目调用另外记录仓库全名与排名；阶段值为 `project_generation`、`project_repair`、`global_synthesis` 或 `global_repair`。

## 图片与排版

每个项目只生成一张图。`MediaCreativeBriefService` 规范化真实节点、关系和阅读顺序，`ImagePromptDesignService` 再把简报编译成 Seedream 最终 Prompt。`SummaryTask` 不写供应商参数或画风说明。

`ArticleLayoutService` 在项目标题后的第一段概述之后插入对应图片，再继续渲染三个教学分点。实现过程中发现 `Top N 项目拆解` 会被旧的 `Top {rank}` 规则误判成项目标题，因此项目匹配已收敛为仓库名、`项目 N` 和 `第 N`，避免排名 N 的图片提前出现在项目正文之前。排版和手动 GitHub 图片升级均遍历完整 `image_prompts`，管理台设置 N=6 至 12 时也不会截断为前 5 张。

## 事实与失败边界

- 当前证据主要是周榜元数据和 README 摘录。没有提供真实路径、类名、函数名、运行命令或调用关系时，正文必须明确说明证据没有展开。
- `visual_brief` 禁止使用“输入、核心、处理、输出、任务、计划、执行、反馈、结果”等通用占位节点。
- 每份 `visual_brief` 必须包含 3 到 4 个节点、2 到 4 条关系和至少一条 `数据流`。
- Skill 缺失、正文为空或超过 32,000 个字符时直接失败，不静默回退旧提示词。
- 管理员摘要指令和人工重生成反馈会传给项目调用及全局调用，但不能覆盖 JSON、事实和项目顺序合同。

## 验证结果

- `github-project-blog` 通过 `skill-creator` 的 UTF-8 `quick_validate.py`。
- 新增编排测试覆盖 N=1、N=3、N=6、单项目证据隔离、失败项目选择性重试、修复失败时停止全局综合，以及文章确定性拼装。
- 视觉测试确认每份图片简报包含真实节点和主数据流，通用占位节点仍会被拒绝。
- 排版测试确认图片位于项目概述之后、`技术特点` 之前，并覆盖 N=6 时六张图片全部进入正文。
- `verify_summary_depth_contract.py` 离线验证动态 N+1 调用、精确 stars、三分点、无长度合同、N 份 ContentBrief 和短视频证据传递。
- `verify_media_creative_contract.py` 验证 N 份 ContentBrief 继续进入图片和视频链路。

## 后续边界

如果真实运行显示 N+1 串行调用耗时不可接受，应根据 trace 数据单独设计有限并发与限流方案。本次不在没有运行数据的情况下提前引入并发，也不改变生产调度或部署配置。
