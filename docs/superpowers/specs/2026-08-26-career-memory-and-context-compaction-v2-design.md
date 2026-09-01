# 求职助手长期记忆与动态上下文压缩 V2 设计

## 文档状态

- 状态：已批准，等待实施计划评审
- 批准日期：2026-08-26
- 适用范围：求职助手文字会话、简历与岗位上下文、模型设置、PC 端会话页和用户记忆管理
- 取代文档：`2026-08-22-career-long-conversation-memory-design.md`
- 部署边界：本设计只授权后续本地实现与验证，不包含生产部署

## 一、结论

求职助手采用“会话内滚动记忆 + 跨会话白名单事实 + 简历事实源”的三层记忆架构：

1. 单个会话最多完成 30 个成功用户—助手轮次。模型不直接读取全部 30 轮，而是读取最近完整原文轮次和一份结构化滚动摘要。
2. 每个模型档案配置上下文容量、预留输出 Token、压缩触发比例和压缩目标比例。默认在预计上下文达到 80% 时压缩到不高于 60%，95% 为不可配置的系统硬限制。
3. 跨会话只保留岗位意向、工作经历、学历、奖项、论文和个人优势。简历是除岗位意向外各类履历事实的最高权威来源，长期记忆只保存结构化索引、来源和版本，不复制完整简历。
4. 压缩默认使用当前回答所选模型，温度为 0，使用严格 JSON 输出；每个用户 Turn 最多产生一次压缩模型调用。
5. 首期复用现有 PostgreSQL，不引入新的记忆 SaaS、图数据库或向量数据库。检索先使用作用域、类别、简历版本和全文条件；只有评测证明必要时才增加 pgvector。
6. 普通用户只看到近似的上下文余量和自己的记忆；准确容量与 80%/60% 阈值只对管理员可见和可配置。

## 二、目标与非目标

### 2.1 目标

- 支持单个会话连续完成 30 个成功轮次，并避免固定读取 6 条消息造成的早期遗忘。
- 在模型上下文有限、模型可能切换、附件和 Skill 大小变化的情况下动态控制 Token 成本。
- 保住当前任务、已确认决策、尚未完成的问题、用户纠正和助手承诺，不因压缩破坏任务连续性。
- 让新会话能够安全复用少量求职核心事实，同时阻止闲聊、助手推断和外部材料污染长期记忆。
- 所有被使用的长期记忆都能追溯到用户消息或已确认简历版本，并允许用户查看、纠正、停用和删除。
- 流式和非流式回复使用完全相同的记忆组装、压缩和轮次限制逻辑。

### 2.2 非目标

- 不保存用户的自由聊天画像、情绪、生活偏好、联系方式、地址或与求职无关的个人事实。
- 不把 JD、网页、面经库、工具输出、助手建议或未确认附件写成用户长期事实。
- 不保证无限轮或逐字记忆；30 轮之前的较早内容允许压缩为结构化摘要，原始脱敏消息仍保存在当前会话历史中。
- 不让跨会话长期记忆替代简历解析、候选人档案或岗位档案。
- 首期不引入 Mem0、Letta、Zep/Graphiti、Neo4j、FalkorDB、独立 Embedding 服务或新的外部记忆平台。
- 不在本阶段实施手机端专项适配或平板端适配；遵循项目规则先完成 PC 端。

## 三、现状与问题

### 3.1 当前代码事实

- `CareerResponseRunner._build_prompt()` 读取最近 6 条消息；本轮用户消息先入库后又被排除，因此模型通常只能看到约两个完整旧轮次。
- 每条旧消息还存在 1600 字符截断，可能产生事实残缺。
- `messages` 已保存脱敏历史；`session_summaries` 和 `upsert_redacted_summary()` 已存在，但没有安全覆盖游标、结构版本和正式读取链路。
- 候选人档案和目标岗位档案已经在 PostgreSQL 中版本化，可作为长期事实源复用。
- 模型档案没有上下文容量、输出预留和压缩阈值字段。
- 当前 `model_usage.turn_id` 唯一，只能记录一次模型操作，不能同时区分回答调用与压缩调用。
- LangGraph intake graph 未配置 checkpointer；当前产品已经由自己的 PostgreSQL 会话、Turn 和消息仓储承担持久化。

### 3.2 旧方案为何不再适用

旧设计只解决单会话摘要，使用固定历史 Token 配置，不包含 30 轮上限、按模型容量计算、80%/60% 动态压缩、用户可见余量、跨会话白名单事实、职业方向隔离和来源追踪。V2 因而不是旧方案的小修，而是完整替代。

## 四、调研结论与方案选择

### 4.1 可复用思想

- [LangGraph Memory](https://docs.langchain.com/oss/python/concepts/memory) 将 thread 内短期记忆与跨 thread 长期记忆分开，适合本设计的作用域划分；其滚动摘要模式也证明“最近原文 + 摘要”是主流实现。
- [OpenAI Agents SDK Sessions](https://openai.github.io/openai-agents-python/sessions/) 提供 Session 后端与 compaction 会话思路，说明压缩应作为会话存储层能力，而不是散落在 Prompt 中。
- [MemGPT](https://arxiv.org/abs/2310.08560) 把有限上下文视作受管理的工作记忆，为阈值触发和分层存储提供理论参考。
- [LongMemEval](https://arxiv.org/abs/2410.10813) 将长期对话能力拆成信息抽取、多会话推理、时间推理、知识更新和拒答，直接用于本项目验收集设计。
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) 说明长上下文并不等于稳定利用全部信息，因此不能简单把 30 轮全部塞给模型。
- [AgentPoison](https://papers.nips.cc/paper_files/paper/2024/file/eb113910e9c3f6242541c1652e30dfd6-Paper-Conference.pdf) 说明长期记忆和检索层可能成为持久化污染入口，因此外部材料必须作为不可信数据，不能自动晋升为用户事实或工具指令。

### 4.2 开源方案取舍

| 方案 | 可借鉴点 | 首期是否引入 | 原因 |
| --- | --- | --- | --- |
| LangGraph/LangMem 思路 | thread/store 分层、摘要与记忆操作 | 借鉴，不替换现有持久化 | 现有 PostgreSQL 已覆盖运行时状态，替换会增加迁移成本 |
| [Mem0](https://github.com/mem0ai/mem0) | 事实抽取、更新、来源化记忆 | 不引入 | Apache-2.0 可复用，但当前白名单窄，自建确定性规则更易审计且依赖更少 |
| [Letta](https://github.com/letta-ai/letta) | 完整有状态 Agent 运行时 | 不引入 | Apache-2.0，但会与现有 AgentLoop、模型网关和 PostgreSQL Turn 运行时重叠 |
| [Graphiti](https://github.com/getzep/graphiti) | 时间有效性、事实关系、来源 | 只借鉴时间字段 | 需要图存储和更复杂检索，30 轮与六类事实不需要图数据库 |
| OpenAI compaction | Provider 原生压缩 | 不引入 | 当前产品支持多 Provider，不能把核心可用性绑定到单一 Provider |
| pgvector | 语义召回 | 评测后再决定 | 六类白名单事实可先通过结构化过滤与全文检索完成，向量会增加成本和污染面 |

结论是增强现有 PostgreSQL 架构，复用成熟方案的分层、增量压缩、来源与时间有效性思想，不替换现有运行时。

## 五、总体架构

```text
已确认简历/岗位档案 ──────────────┐
                                  │
跨会话白名单事实 ── 作用域过滤 ────┤
                                  ├─ ContextBudgetService ── Prompt ── 回答模型
会话滚动摘要 ────── 游标后原文 ────┤            │
                                  │            ├─ 80%：压缩到 <=60%
当前输入/附件/Skill/检索证据 ─────┘            └─ 95%：阻止调用并引导拆分

成功回答 ──> 完成轮次计数 ──> PostgreSQL 后台任务
                           ├─ 会话压缩预判
                           └─ 长期事实候选/更新
```

核心服务边界：

- `ContextBudgetService`：按模型档案估算完整 Prompt、计算余量、选择保留和降级内容。
- `ConversationMemoryService`：完整轮次分组、滚动摘要校验、游标与乐观锁、压缩任务。
- `CareerMemoryService`：白名单抽取、候选确认、冲突更新、作用域检索和 Prompt 数据封装。
- `CareerResponseRunner`：解析回答模型并编排上述服务，不内嵌记忆算法。
- PostgreSQL Worker：复用现有数据库租约/worker 模式执行回答后的压缩与事实写入任务；请求线程只负责可靠入队。

## 六、三层记忆与生命周期

### 6.1 第一层：当前工作上下文

每次模型调用实时构建，包含系统规则、激活 Skill、当前输入、当前简历/JD 证据、面经检索证据和附件解析摘要。它不是长期记忆，不独立持久化为用户事实。

### 6.2 第二层：会话内滚动记忆

- 作用域仅为一个 conversation。
- 内容为一份结构化摘要和摘要游标之后的最近完整原文轮次。
- 可以暂时记录当前任务、决策、开放问题、纠正、上下文和助手承诺。
- 摘要是可重建派生数据，不能晋升为跨会话事实。
- 删除会话时与消息一起级联删除。

### 6.3 第三层：跨会话白名单事实

只允许以下 `memory_type`：

| 类型 | 中文 | 默认作用域 | 主要事实源 |
| --- | --- | --- | --- |
| `job_intention` | 岗位意向 | 职业空间 | 用户当前明确表达或纠正 |
| `work_experience` | 工作经历 | 全局 | 已确认简历；聊天内容先成为候选 |
| `education` | 学历 | 全局 | 已确认简历；聊天内容先成为候选 |
| `award` | 奖项 | 全局 | 已确认简历；聊天内容先成为候选 |
| `publication` | 论文 | 全局 | 已确认简历；聊天内容先成为候选 |
| `personal_advantage` | 个人优势 | 全局 | 已确认简历及其可追溯证据 |

“全局”仍限定同一 `organization_id + actor_id`，只表示可供该用户的多个职业空间按相关性检索，不表示跨用户共享。

### 6.4 职业空间隔离

- 新增 `career_spaces`，例如“Java 后端”“AI 应用工程”。
- 每个会话必须绑定一个职业空间；未选择时使用系统创建的“默认求职方向”。
- `job_intention` 必须带 `career_space_id`，只在同一空间读取和更新。
- 全局履历事实可在不同空间复用，但 `personal_advantage` 只有与当前问题和岗位相关时才读取。
- 不允许把一个空间的岗位偏好、薪资方向或目标公司自动带入另一个空间。

## 七、30 轮上限

### 7.1 轮次定义

一个计数轮次必须同时满足：

1. 同一 `agent_turns.id` 下有一条用户消息；
2. 有一条成功持久化的助手最终消息；
3. `agent_turns.status = 'succeeded'`。

失败、取消、尚未完成和重试失败的 Turn 不计数。流式回答只有在最终消息持久化并把 Turn 标记为 succeeded 后才计数。

### 7.2 并发与限制

- 每个会话只允许一个 queued/running Turn，提交时在事务内锁定会话行。
- 锁内统计 succeeded Turn；达到 30 时返回业务错误 `conversation_turn_limit_reached`，不创建新 Turn、不调用模型。
- 第 28 个成功轮次结束后显示“还可继续 2 轮”，第 29 个后显示“还可继续 1 轮”。
- 第 30 个成功轮次正常展示回答，随后输入区变为只读，并提供“开启新对话”入口。
- 新对话只继承用户批准的长期白名单事实和选定职业空间，不继承旧会话摘要、闲聊、当前任务或助手承诺。

## 八、上下文预算模型

### 8.1 模型档案配置

在 `model_profiles` 增加：

| 字段 | 默认值 | 规则 | 可见性 |
| --- | ---: | --- | --- |
| `context_window_tokens` | 8192 | 管理员应按 Provider 官方容量校正；必须大于预留输出 | 仅管理员精确可见 |
| `reserved_output_tokens` | 4096 | 预留本次最大回答空间，不大于上下文容量的 50% | 仅管理员精确可见 |
| `compression_trigger_percent` | 80 | 50—90 的整数 | 仅管理员 |
| `compression_target_percent` | 60 | 30—75 且必须小于触发比例 | 仅管理员 |
| `context_window_source` | `fallback` | `fallback`、`built_in` 或 `admin` | 仅管理员 |

系统常量：

- `hard_limit_percent = 95`，不可由管理员修改。
- `token_safety_factor = 1.15`，首期为服务端常量，防止本地估算低于 Provider 实际计费。
- 迁移后的旧模型档案使用保守的 8192 Token fallback，并在管理页显示“容量待核对”；管理员确认后改为 `admin`。

### 8.2 完整核算口径

以下全部计入预计输入：

- 系统 Prompt 与隐私/安全规则；
- 激活 Skill 的受控说明；
- 跨会话长期记忆；
- 会话滚动摘要；
- 最近完整原文轮次；
- 当前相关简历、JD、面经检索结果；
- 当前用户输入与附件解析摘要；
- ChatMessage 角色、分隔符、tool schema 和 Provider 包装开销。

计算公式：

```text
estimated_input_tokens = ceil(local_estimated_input_tokens × 1.15)
projected_total_tokens = estimated_input_tokens + reserved_output_tokens
used_percent = ceil(projected_total_tokens / context_window_tokens × 100)
remaining_percent = max(0, 100 - used_percent)
```

优先使用对应 Provider tokenizer；没有稳定 tokenizer 时使用统一字符估算器，并为中文、英文、JSON、tool schema 分别设置保守系数。Provider 返回的真实 usage 用于离线校准估算器，但不在请求中临时改变阈值。

### 8.3 为什么是 80%/60%

80% 是产品默认安全阈值，不宣称是所有模型的行业标准。预留 20% 用于 tokenizer 误差、下一条用户输入、tool schema 和 Provider 包装差异。压缩到 60% 形成 20 个百分点的滞回区间，避免每轮在 80% 附近反复压缩。

## 九、动态压缩状态机

```text
NORMAL (<80%)
  └─ 达到80% ─> COMPACTION_QUEUED/RUNNING
                    ├─ 成功且<=60% ─> NORMAL
                    ├─ 成功但>60% ─> DEGRADED_TRIMMING
                    └─ 失败 ───────> DEGRADED_TRIMMING

DEGRADED_TRIMMING
  ├─ 降级后<95% ─> 可调用回答模型
  └─ 仍>=95% ───> HARD_BLOCKED（不调用模型）
```

### 9.1 回答后的后台预压缩

成功回答持久化后，服务重新估算“当前已完成历史 + 当前使用模型 + 预留输出”的预计占用：

- 小于 80%：不创建压缩任务。
- 大于等于 80%：在同一收口事务中可靠创建压缩任务，响应无需等待压缩模型。
- 后台任务使用刚完成回答的 resolved model profile。

### 9.2 下一轮调用前同步兜底

收到下一条用户输入并解析本轮模型后，以新模型和真实新输入重新计算：

- 已有后台压缩成功：直接读取最新摘要重新估算。
- 后台任务 queued：当前请求可原子领取同一任务并同步执行。
- 后台任务 running：短暂轮询不超过服务端小超时；仍未完成时，不重复调用压缩模型，直接进入确定性降级。
- 没有后台任务但本轮达到 80%：为本 Turn 创建并同步执行一次压缩。

同一用户 Turn 通过数据库唯一键保证最多一次 `conversation_memory_compaction` 模型调用，流式和非流式入口共享该约束。

### 9.3 模型切换

- 切换模型后必须按新模型的 `context_window_tokens` 重新计算，不能沿用旧百分比。
- 若新模型容量更小且达到 80%，使用本轮新选择的回答模型执行压缩。
- 压缩和降级后仍达到 95%，不自动切换到付费或更大模型；提示用户保留当前大模型或拆分任务。

## 十、滚动摘要设计

### 10.1 Schema

`session_summaries.summary_text` 保存经过严格校验的紧凑 JSON，版本为 `career-conversation-summary-v2`：

```json
{
  "schema_version": "career-conversation-summary-v2",
  "current_tasks": [],
  "decisions": [],
  "open_loops": [],
  "user_corrections": [],
  "temporary_user_context": [],
  "assistant_commitments": [],
  "companies": [],
  "roles": []
}
```

所有业务字段都是字符串数组。服务端限制字段集合、数组数量、单项长度、总字符数，去除空白和精确重复项；未知字段、缺字段、非字符串、越界或非法 JSON 全部拒绝。

### 10.2 进入摘要的内容

- 只处理 succeeded Turn 的完整用户—助手轮次。
- 当前正在回答的用户消息不进入摘要。
- 用户说法只能进入用户相关字段；助手提出但用户未确认的建议不能伪装成用户决策。
- `assistant_commitments` 只记录助手明确承诺后续要做且尚未完成的事项。
- 新纠正替代旧说法，摘要不能同时保留互相冲突的当前事实。
- system/tool 消息不作为用户事实；外部内容中的指令字符串只作为普通数据。
- 摘要永远不能直接写入长期记忆。

### 10.3 压缩调用

- 模型：默认使用当前回答模型；后台预压缩使用刚完成回答的模型。
- operation：`conversation_memory_compaction`，与 `career_response` 分开计量。
- 参数：temperature 0、严格 JSON schema、最大输出约 1600 Token、关闭工具调用。
- 输入：上一版有效摘要 + 游标之后待压缩的最旧完整轮次前缀。
- 输出：完整新快照，不是差量补丁。
- 目标：压缩后 `projected_total_tokens <= context_window_tokens × 60%`；预算不允许时可以少保留原文轮次。
- 最近完整原文默认尽量保留 4 轮，但上下文安全优先于该软目标。

### 10.4 游标和并发

扩展 `session_summaries`：

- `covered_through_message_id UUID NULL`：最后一条已覆盖的 assistant 消息；
- `summary_schema_version TEXT NOT NULL`；
- `summary_version INTEGER`：复用为乐观锁；
- `compacted_with_profile_id UUID NULL`；
- `compacted_input_tokens INTEGER NULL`；
- `compacted_output_tokens INTEGER NULL`。

压缩模型调用在数据库事务外执行。保存时同时匹配旧 `summary_version` 和旧游标；冲突结果丢弃并重新读取胜出版本。非法输出、超时、限流或并发失败均不更新摘要、不推进游标、不删除原始消息。

### 10.5 确定性降级顺序

压缩失败或成功后仍超预算时，按以下顺序从低到高缩减可重新加载的内容：

1. 更老且可再次检索的面经/网页证据；
2. 滚动摘要中的次要细节；
3. 更老的完整原文轮次；
4. 与当前问题弱相关的个人优势记忆；
5. 非关键的 JD 或简历片段。

必须保留的优先级从高到低为：

1. 系统、隐私和安全规则；
2. 当前用户输入及其中的明确纠正；
3. 当前激活 Skill 的必要规则；
4. 当前问题所需的简历/JD 关键证据；
5. 相关的已确认长期事实；
6. 最近完整轮次；
7. 旧摘要细节；
8. 可重新检索内容。

不得静默截断当前问题、用户纠正、安全/隐私规则或生成结论所必需的事实证据。降级后仍达到 95% 时返回 `context_hard_limit_reached`，不调用回答模型。

## 十一、长期事实写入机制

### 11.1 数据模型

新增 `career_memory_items`：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `organization_id`、`actor_id` | 强制租户与用户隔离 |
| `career_space_id` | 岗位意向必填；全局履历事实可为空 |
| `memory_type` | 六类白名单枚举 |
| `normalized_value_json` | 服务端按类型校验的结构化值 |
| `display_text` | 面向用户的简洁事实，不保存完整简历段落 |
| `source_kind` | `explicit_user_statement`、`explicit_user_correction`、`confirmed_resume`、`user_confirmed_candidate` |
| `source_message_id` | 来源为对话时指向用户消息 |
| `candidate_profile_id`、`candidate_profile_version` | 来源为简历时记录版本 |
| `source_conversation_id` | 支持“删除会话并遗忘由此产生的记忆” |
| `status` | `candidate`、`active`、`disabled`、`superseded` |
| `supersedes_memory_id` | 指向被新事实取代的记录 |
| `valid_from`、`valid_to` | 表达事实有效时间 |
| `created_at`、`updated_at` | 审计时间 |

删除操作物理删除记忆内容；使用记录的外键设为 NULL，避免已经展示过的回答记录反向阻止删除。

### 11.2 写入准入表

| 输入来源 | 岗位意向 | 其他五类事实 | 自动成为 active |
| --- | --- | --- | --- |
| 当前用户明确陈述/纠正 | 可以 | 先写 candidate | 岗位意向可以 |
| 用户在记忆页确认 candidate | 可以 | 可以 | 可以 |
| 已保存的确认简历版本 | 不推断 | 可以生成简洁索引 | 可以 |
| 助手回答或推断 | 禁止 | 禁止 | 禁止 |
| JD、网页、面经库、工具输出 | 禁止 | 禁止 | 禁止 |
| 未确认附件 | 禁止 | 禁止 | 禁止 |
| 会话滚动摘要 | 禁止 | 禁止 | 禁止 |

已保存的候选人档案视为确认简历版本。聊天中出现的新工作、学历、奖项、论文或优势，只有用户在记忆页确认或更新简历后才能参与跨会话检索。

### 11.3 后台写入流程

1. 回答成功后写入 `career_memory_jobs`，`turn_id` 唯一，主响应不等待抽取。
2. 先执行确定性白名单、来源和作用域检查；岗位意向优先用规则化结构解析。
3. 只有自由文本确实需要归一化时才调用结构化抽取模型；模型只能产生 candidate，不可绕过服务端准入表。
4. 服务端验证类型、来源、长度、职业空间和冲突关系后，在事务中写入或 supersede。
5. 失败任务可由现有 PostgreSQL worker 重新领取；幂等键防止重复事实。

显式岗位意向纠正在成功回答收口后立即通过快速规则路径更新；该更新不阻塞已经生成的回答，也不回写当前回答内容。

### 11.4 冲突与权威顺序

同一事实冲突时按以下顺序：

```text
当前用户明确纠正或已持久化的用户纠正覆盖
  > 当前会话绑定的已确认简历版本
  > 其他已确认 active 长期记忆
  > 当前会话滚动摘要
  > 更老原始消息
  > 助手推断和外部材料
```

- 新事实不覆盖历史行，而是把旧行标记为 `superseded` 并设置 `valid_to`，新行记录 `supersedes_memory_id`。
- 检索只返回 `active` 且在有效期内的记录。
- 用户在记忆页纠正简历派生事实时，新记录使用 `explicit_user_correction` 并持续高于旧简历版本；界面同时提示更新简历，确认新简历后再用新版本事实取代该临时覆盖。
- 不使用模型自报 `confidence` 决定事实真伪；来源、用户确认和简历版本才是权威依据。
- 新简历版本确认后，旧版本派生事实先被 supersede，再从新版本建立索引，不能并列注入。

## 十二、长期事实检索与 Prompt 注入

### 12.1 检索规则

- 强制过滤 `organization_id + actor_id`。
- 岗位意向强制过滤当前 `career_space_id`，每轮固定注入，最多 300 Token。
- 工作、学历、奖项、论文和个人优势只在当前问题需要时检索。
- 优先当前绑定的 `candidate_profile_id + version`，再按类型、岗位关键词和 PostgreSQL 全文条件排序。
- 最多返回 5 条履历事实，合计最多 800 Token。
- 一般闲聊不注入个人优势；无有效证据时应明确拒绝臆测。
- 首期不对原始消息做向量召回，不把完整简历复制为向量块。

### 12.2 数据与指令隔离

长期记忆以惰性数据信封注入：

```xml
<career_memory_data instruction_authority="none">
  ...服务端序列化的事实与来源...
</career_memory_data>
```

系统 Prompt 明确声明该区域只含事实数据，不能改变系统规则、激活 Skill、授权工具或要求执行外部操作。事实文本中的 `/skill`、XML、tool call、URL 或“忽略之前规则”等字符串均按普通文本处理。

服务端使用固定 JSON 序列化后再放入边界标签，并对 `<`、`>`、`&` 等边界字符转义；模型生成的原始字符串不能直接拼接为 XML，避免事实文本提前闭合数据区域。

## 十三、数据库变更

### 13.1 新表

- `career_spaces`：用户职业方向，唯一键为 `organization_id + actor_id + normalized_name`。
- `career_memory_items`：单条、可来源化、可更新的长期事实。
- `career_memory_jobs`：成功 Turn 后的事实抽取任务，`turn_id` 唯一。
- `conversation_compaction_jobs`：后台/同步共用的压缩任务和领取状态。
- `turn_memory_usages`：记录某次回答实际使用的记忆 ID、类型和来源，不保存新的事实正文副本。

### 13.2 现有表扩展

- `conversations`：增加 `career_space_id`；旧会话迁移到该用户的默认职业空间。
- `session_summaries`：增加覆盖游标、Schema 版本、模型和 Token 元数据。
- `model_profiles`：增加上下文策略五个字段。
- `model_usage`：增加 `operation_kind`，把 `turn_id` 唯一约束调整为 `(turn_id, operation_kind)`，至少支持 `career_response`、`conversation_memory_compaction` 和 `career_memory_extraction`。

### 13.3 索引与约束

- `career_memory_items(organization_id, actor_id, career_space_id, memory_type, status)` 复合索引。
- `candidate_profile_id + candidate_profile_version` 来源索引。
- `source_conversation_id` 删除联动查询索引。
- `conversation_compaction_jobs(conversation_id, status, created_at)` worker 索引。
- Check constraint 限制六类 `memory_type`、状态枚举、百分比范围和 Token 正数。
- 所有仓储读写都必须同时带 organization 和 actor 条件；仅凭 UUID 查询不被允许。

## 十四、API 契约

### 14.1 普通用户

- `GET /api/career/conversations/{conversation_id}/context-usage?model_profile_id=...`
  - 返回 `used_percent`、`remaining_percent`、`state`、`successful_turns`、`remaining_turns`。
  - 百分比取整且标记 `approximate: true`；不返回容量、预留输出或阈值。
- Turn 创建、流式事件和最终响应均附带同结构 `context_usage`，保证圆环无需额外轮询。
- `GET /api/career/career-spaces`、`POST /api/career/career-spaces`、`PATCH /api/career/career-spaces/{id}`。
- `GET /api/career/memories?career_space_id=...&status=...`。
- `POST /api/career/memories/{id}/confirm`、`PATCH /api/career/memories/{id}`、`POST /api/career/memories/{id}/disable`、`DELETE /api/career/memories/{id}`。
- `GET /api/career/turns/{turn_id}/memory-usages` 返回本次回答使用了哪些事实和来源。
- `DELETE /api/career/conversations/{id}` 默认保留 active 长期事实；显式 `forget_derived_memories=true` 时，同时物理删除来源于该会话消息的长期事实。简历版本派生事实不因会话删除而删除。

所有用户 API 只能操作当前登录用户的数据；candidate 默认不注入模型。

### 14.2 管理员

- `GET /api/admin/career/model-profiles/{profile_key}/context-policy`
- `PUT /api/admin/career/model-profiles/{profile_key}/context-policy`

两个接口使用现有 `require_admin`，返回或更新准确容量、预留输出、80%/60% 阈值、来源和最后修改时间。普通 `/api/career/model-profiles` 不返回这些精确字段。

## 十五、PC 端交互

### 15.1 上下文余量圆环

位置：求职助手输入框底部、模型选择器旁，沿用用户示意图的紧凑圆环，不新增独立大面板。

- 默认显示剩余比例的圆环，不持续显示数字；hover/focus 显示“上下文余量约 37%”。
- 使用率 0—59%：普通灰/绿色。
- 使用率 60—79%：橙色，提示上下文开始变长。
- 使用率达到 80% 且正在整理：蓝色轻量旋转，文案“正在整理上下文”。
- 压缩完成：圆环按新比例回落，不弹打扰式提示。
- 无法降到 95% 以下：红色，输入区阻止发送并说明如何拆分任务。
- 圆环百分比始终按当前选中的模型重算；切换模型时显示短暂计算态。

普通用户看不到“80%、60%、95%”的准确配置值。无障碍文本必须包含状态和近似余量，不能只依赖颜色。

### 15.2 30 轮提示

- 轮次 0—27 不打扰。
- 第 28、29 个成功轮次后，在输入区上方显示剩余轮次。
- 第 30 个成功轮次后保留历史浏览，禁用继续发送，主操作为“基于当前求职记忆开启新对话”。

### 15.3 我的求职记忆

新增独立子路由，遵守“单页单任务”：

- 按职业空间和六类事实分组。
- 展示 active、candidate、disabled 视图；disabled 由服务端状态映射，不进入检索。
- 每条事实展示来源类型、简历版本或对话日期。
- candidate 可确认、修正或删除；active 可修正、停用或删除。
- 删除会话时提供两个清晰选项：“仅删除对话”和“删除对话并遗忘由本对话产生的求职记忆”。

### 15.4 管理员配置

在管理台增加“模型上下文”子页面，逐个模型编辑准确容量、预留输出、压缩触发比例和目标比例。硬限制 95% 只读展示。保存前在前后端同时校验范围，并标识使用 fallback 的模型。

## 十六、调用链

```text
提交用户输入
  -> 锁定会话，校验单飞与30轮上限
  -> 创建 Turn，持久化脱敏用户消息
  -> 解析回答模型和职业空间
  -> 检索当前简历/JD、白名单事实、有效摘要和游标后完整轮次
  -> ContextBudgetService 估算
       -> <80%：直接组装
       -> >=80%：领取/创建压缩任务，至多调用一次当前回答模型
       -> 再估算并执行确定性降级
       -> >=95%：终止，不调用回答模型
  -> 生成回答（流式/非流式共用同一准备结果）
  -> 持久化助手消息并把 Turn 标记 succeeded
  -> 记录 turn_memory_usages 和 career_response usage
  -> 返回回答、近似上下文余量和剩余轮次
  -> PostgreSQL 后台任务
       -> 预判并执行下一轮所需压缩
       -> 提取/更新长期事实候选
```

## 十七、失败与恢复

- 压缩超时、限流或网络错误：保留旧摘要和原文，不推进游标；按降级顺序组装。
- 压缩 JSON 非法：拒绝整个新摘要，不能部分保存。
- 乐观锁冲突：丢弃落后结果，读取胜出摘要重新估算。
- 后台 worker 未及时处理：下一轮同步领取同一任务；不会产生第二次压缩调用。
- 事实抽取失败：不影响回答和会话摘要；任务保留失败码，可重试且幂等。
- 长期记忆检索失败：继续使用当前简历/JD 与会话记忆回答，并在元数据中标记 degraded。
- Token 估算后 Provider 仍返回 context length error：记录估算偏差，确定性再缩减一次后允许回答调用重试一次；不能再次调用压缩模型。
- 任何降级都不能把 assistant、外部资料或旧摘要提升为用户事实。

## 十八、可观测性与成本

每次回答或压缩记录以下元数据，不记录正文、摘要正文或长期事实正文：

- conversation、turn、model profile、operation kind；
- 各上下文组件的估算 Token 数；
- 压缩前后 used percent、摘要版本、覆盖轮次数；
- 是否触发、排队、同步兜底、成功、失败或硬阻止；
- 实际 input/output Token、耗时、错误类别；
- 注入的记忆 ID 和类型通过 `turn_memory_usages` 关联。

成本约束：

- 普通轮次不额外调用摘要模型。
- 单个用户 Turn 最多一次压缩模型调用。
- 长期事实先走确定性抽取；只有必要时后台调用结构化抽取模型。
- 压缩默认复用当前回答模型，不自动升级为付费模型。
- 管理员可按 operation kind 汇总回答、压缩和事实抽取的 Token 成本。

## 十九、测试与验收

### 19.1 确定性功能测试

1. 连续 30 个成功轮次可完成，第 31 次提交被阻止。
2. 失败、取消和重试失败不占 30 轮额度；并发提交不能突破上限。
3. 前 5 轮建立的开放问题在后续压缩后仍存在，完成后从 `open_loops` 移除。
4. 预计使用率达到 80% 触发压缩，成功后降到不高于 60%。
5. 只压缩完整 succeeded 轮次，游标只能指向 assistant 消息。
6. 非法摘要、模型失败和乐观锁冲突均不推进游标、不删除原文。
7. 每个 Turn 至多一次压缩模型调用；流式与非流式行为一致。
8. 用户纠正使旧事实 superseded，后续 Prompt 只出现新事实。
9. 新会话只继承六类 active 白名单事实，不继承旧摘要、闲聊和助手建议。
10. JD、网页、面经、tool 输出和带指令的附件不能创建 active 用户事实。
11. 没有简历或确认事实时，模型对经历问题会说明证据不足而非编造。
12. 切换到更小模型会重新计算并按新模型触发压缩或硬阻止。
13. 普通用户 API 不返回准确上下文容量和阈值；管理员 API 才能读写。
14. 删除会话默认保留长期事实；选择遗忘时只删除由该会话消息产生的事实。
15. 跨组织、跨 actor、跨职业空间读取全部返回不可见。
16. 当前问题、用户纠正、系统规则和关键简历/JD 证据不会被静默截断。

### 19.2 长对话评测集

建立不调用真实 Provider 的 30 轮确定性模拟，以及调用测试模型的语义评测，覆盖 LongMemEval 对应能力：

- 信息抽取：能否写入正确类型和来源；
- 多轮推理：能否结合早期任务与当前问题；
- 时间更新：能否让新纠正替代旧事实；
- 多会话检索：新会话是否只召回允许事实；
- 拒答：无证据时是否避免编造。

硬性验收指标：

- 确定性白名单写入准确率不低于 98%；
- active 记忆来源可追溯率 100%；
- 外部材料污染 active 记忆成功率为 0；
- 普通未触发轮次额外摘要调用数为 0；
- 压缩后 Prompt 预计使用率不高于 60%，除非进入有明确原因码的降级状态；
- 所有 Prompt 均低于 95% 硬限制才允许发给 Provider；
- 30 轮基准中的已确认纠正、开放任务和岗位意向召回率 100%。

### 19.3 决定是否增加 pgvector

只有同时满足以下条件才进入单独设计：

1. 结构化摘要和全文检索通过安全测试；
2. 长对话评测持续出现“知道主题但找不到必要旧细节”；
3. 问题无法通过改进摘要字段、来源索引或关键词检索解决；
4. 新方案仍能保证 actor/职业空间隔离、来源追踪和记忆删除。

## 二十、实施分期

1. 模型上下文配置、Token 估算服务、管理员 API 与硬限制。
2. PC 端上下文余量圆环和模型切换重算。
3. 会话滚动摘要 V2、压缩任务、usage 分操作记录和失败降级。
4. 30 轮上限、28/29 轮提示和新会话引导。
5. 职业空间、长期事实表、后台写入和用户记忆管理。
6. 相关事实检索、惰性数据注入、来源抽屉和每回答使用记录。
7. 30 轮与多会话评测、成本校准；依据结果决定是否另行设计 pgvector。

每一期都先完成后端与 PC 端验证，再进入下一期；手机端适配在 PC 端验收后作为独立任务处理。

## 二十一、受影响模块与文档同步

后续实施预计涉及：

- `src/career_assistant/response_runner.py`：改为消费统一上下文准备结果。
- `src/career_assistant/conversation_memory.py`：会话摘要与压缩。
- `src/career_assistant/context_budget.py`：Token 核算、阈值和降级选择。
- `src/career_assistant/career_memory.py`：长期事实准入、冲突、检索与注入。
- `src/career_assistant/persistence/`：模型档案、会话摘要、职业空间、事实、任务和 usage 仓储。
- `src/career_assistant/web/router.py` 与 `src/platform_access/web.py`：用户和管理员 API。
- `web-ui/src/components/CareerAssistantPage.vue`：PC 端圆环、轮次提示和来源入口。
- 新的独立“我的求职记忆”与“模型上下文”子路由组件。
- Alembic 迁移、后端测试、前端交互测试和求职助手模块说明文档。

每完成一个阶段，必须同步记录设计目标、调用链、依赖、验证结果、配置边界和未进入本期的能力。

## 二十二、最终决策摘要

- 30 个成功完整轮次是单会话硬上限，不等于把 30 轮原文全部送给模型。
- 80% 自动触发、60% 压缩目标、95% 系统硬限制；计算包含预留输出和完整 Prompt。
- 压缩默认使用当前回答模型，普通轮次零额外摘要调用，每 Turn 最多一次压缩。
- 跨会话只保存六类求职事实，简历是履历事实权威源，滚动摘要不得晋升长期记忆。
- PostgreSQL 是首期唯一记忆基础设施；不先上向量库、图数据库或第三方记忆框架。
- 用户拥有查看、确认、纠正、停用和删除权；管理员只管理模型策略，不默认查看其他用户的记忆正文。
- 所有记忆按组织、用户和职业空间隔离，外部材料永远没有事实晋升和指令授权能力。
