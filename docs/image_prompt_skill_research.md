# ImageTask 生图 Prompt 升级记录

本文档记录本项目使用 `$find-skills` 查询生图类 skill 后，沉淀到 `ImageTask` 和 `ShortVideoPromptTask` 的规则。

## 2026-08-28 最终技术结论

旧方案把精确中文、节点排版和箭头方向同时交给 Seedream，实际产生元文字、残缺标签、重复节点和错误拓扑。继续缩短或堆叠 Prompt 不能从根本上解决，因为概率式位图模型无法证明逐字中文和关系方向正确。

当前静态技术总结图已切换为：

```text
SummaryTask
  -> evidence-bound ArticleVisualSpec v1
  -> summary_card / flow / architecture / comparison / timeline
  -> fixed HTML/CSS/inline SVG
  -> Gotenberg + Noto Sans SC
  -> DOM validation + PNG validation
```

项目已安装 `baoyu-article-illustrator`。采用的是它的 Type × Style × Palette 规划方法：先分析文章结构和传播目的，再选图型；整篇保持 `notion` 风格和 `editorial_blue` 色板；内容超容量时重选完整句或关闭式失败，不缩字、不截残句。baoyu 不作为后端运行时依赖，也不直接生成最终技术图，因为 Agent Skill 不是稳定渲染 API，常规生图路径仍不能保证中文与拓扑。

Seedream 只保留两类边界：旧链路兼容；未来可选的无文字概念背景。它不再绘制中文标签、模块框、比较表或流程箭头。旧 `visual_brief` 仅为视频兼容保留，静态图不会消费其中的截断节点。

真实验证使用本地 `content_id=20` 的 6 个历史项目：旧内容全部安全降级为 `summary_card`，首轮真实 Gotenberg 渲染 6 张、第二轮复用 6 张；全部为 `2048×1152` PNG。逐图确认无方框、裁切、重叠、重复节点和禁用元文字。验收中真实发现并修复两项质量缺陷：冒号被误判为句末导致“核心取舍是：”残句；legacy takeaway 直接复制能力卡导致信息重复。

以下 skill 调研和 Prompt 方案保留为历史决策依据；其中 `visual_brief → Seedream` 不再代表当前静态图主路径。

## 已查询并安装的 skill

1. `skills-collective/skills@ai-image-generation`
   - 安装量约 119K。
   - 已安装到 `~/.agents/skills/ai-image-generation`。
   - 被采用的规则：
     - prompt 不要堆 tag，要像给设计师写 brief；
     - Subject first，scene second，modifiers last；
     - Seedream 更适合写实和画面感，不适合复杂图内文字；
     - prompt 过长会稀释重点。

2. `inference-sh/skills@og-image-design`
   - 安装量约 517。
   - 已安装到 `~/.agents/skills/og-image-design`。
   - 被采用的规则：
     - 固定模板比临场发挥更稳；
     - 暗色背景、强对比、清晰安全区更适合社交流和公众号；
     - 少文字、少元素、固定版式能降低混乱概率。

3. `cdeistopened/skill-stack@image-prompt-generator`
   - 安装量约 243。
   - 本次只读取方法，没有安装为核心依赖。
   - 被采用的规则：
     - 一张图最多 2 到 3 个核心元素；
     - 避免灯泡、齿轮、握手、拼图等陈词滥调；
     - 不要让图片模型承担文字排版任务；
     - 先描述概念，再描述构图、颜色、质感和禁忌项。

## 本项目的新生图策略

此前的问题是：最终 Seedream Prompt 实测达到 1456 到 1516 字，同时要求模型处理教学目标、核心判断、版式、节点、关系、阅读顺序、色板和大量负面约束。`SummaryTask` 又把 `visual_brief` 以空值交给确定性兜底，导致不同项目被猜成“输入、核心、处理、输出”或“任务、计划、执行、反馈”等通用流程。Prompt 很长但项目语义很少，最终出现乱码、伪正文和错误节点。

第二轮实图验证又暴露两个问题。抽象的“中心模块连接周边组件”会诱导模型为了对称而镜像节点，同一个“插件安装”“文件安装”被各画两次；旧编译器还会先拼出超长正文再从尾部截断，虽然最终长度正好为 300 字，最后一条箭头关系却已残缺。图片模型收到的并不是完整拓扑。

现在改为两层策略：

1. `SummaryTask` 按管理台项目数量执行 N 次独立项目请求，每次在项目正文之外输出一份结构化 `project_brief`；N 个项目全部通过后再执行 1 次全局综合：
   - `summary_text` 必须依次说明问题、关键机制和使用边界，不能只复述 stars；
   - `visual_brief` 使用正文与 `source_evidence` 中真实出现的 3 到 4 个组件或步骤；
   - 节点 id 与短标签不得重复，关系的起止节点组合也不得重复；
   - 每个项目只保留 2 到 4 条真实关系，关系标签限定为强耦合、弱耦合、同步调用、异步调用、数据流或事件推送；
   - 每张图至少保留一条从入口到输出的主数据流，并同时呈现模块架构；
   - “输入、核心、处理、输出、任务、计划、执行、反馈”等脱离项目证据的通用节点会触发质量修复重试。

2. `ImageTask` 用 `ImagePromptDesignService` 把结构化简报编译成不超过 300 个汉字的最终 Prompt。长度上限来自[火山方舟图片生成教程](https://docs.volcengine.com/docs/82379/1824121)的官方建议：
   - 风格：无标题的 16:9 工程架构信息图，白色画布、深蓝核心卡片、浅灰普通卡片、扁平矢量；
   - 位置：把 `diagram_type`、`reading_order` 和真实入出边编译为“中心、左上、左下、右侧”或明确的单向阅读顺序，不再让模型自行解释抽象版式；
   - 数量：明确节点总数和“每个节点只出现一次”，禁止镜像、复制、新增或为了对称补节点；
   - 关系：完整列出每一条 `A→B`，箭头拓扑优先于画风描述，禁止在截断时损坏最后一条关系；
   - 文字：画面只显示 `visual_brief` 指定的节点名，关系标签只用于编译拓扑而不上屏，避免重复短字和伪文字；
   - 信息分工：详细项目解释放在图片下方的文章图注，图片只承担组件边界、数据方向和耦合关系。

## 为什么不让模型直接生成带文字架构图

Seedream 可以生成中文短标签，但不能保证复杂段落逐字正确。项目因此只保留最多 4 个模块名，并明确禁止标题、正文段落和连线文字：

- 图片模型负责结构图、节点关系和少量短标签；
- 公众号排版层负责项目标题、摘要和完整解释；
- 若未来要求图中文字 100% 准确，应改用 HTML/SVG/Canvas 程序化绘图，而不是继续增加图片 Prompt 长度。
