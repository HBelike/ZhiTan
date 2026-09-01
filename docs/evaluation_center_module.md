# 评测中心模块

## 目标

评测中心为面经 RAG、求职 Agent 和简历优化提供可复现的质量评估。它不以单一总分代替判断，而是把真实案例、冻结数据版本、实验配置、运行事实、指标定义和逐项证据连接起来。

首版只实现真实数据准入、确定性指标、实验账本和 PC 端可视化。LLM Judge 与人工标注界面将在基础数据闭环稳定后接入，避免先产生不可解释的模型评分。

## 核心不变量

1. 每条真实案例必须绑定当前组织内可验证的业务记录 UUID、脱敏确认和至少两名复核人；来源不是任意文本标签。
2. 数据版本冻结后不能继续写入案例，并通过所有案例内容哈希生成版本哈希。
3. 实验只允许绑定同组织、同目标的冻结数据版本。
4. 实验配置包含数据版本、Git Commit、Prompt、模型、检索策略、重复次数和稳定配置哈希。
5. 每个得分必须绑定一次实际 Run、指标版本、原始值、通过结果和计算证据。
6. 红线指标独立判定，不能被其他指标平均值抵消。
7. 合成数据可以补充覆盖，但真实案例数量与成绩必须单独展示。

## 调用链

```text
PC 评测中心
  → /api/evaluations/*
  → EvaluationService
  → EvaluationRepository
  → PostgreSQL evaluation schema

实验 Adapter
  → 业务目标实际执行
  → Evaluation Observation
  → Deterministic Evaluators
  → evaluation.runs + evaluation.scores
  → 实验聚合与发布门槛
```

LangSmith 继续作为旁路 Trace。`evaluation.runs.trace_id` 只保存 Trace 引用；评测成绩的事实来源是本项目数据库中的冻结案例、Observation 和版本化指标，不依赖 LangSmith 页面是否可用。

首个可执行 Adapter 为 `InterviewRagExperimentRunner`。管理员在实验详情点击“执行真实 RAG 实验”后，Runner 会逐条读取冻结案例的 `input.query`，调用项目现有 `InterviewRetrievalService`，把实际候选 Chunk ID、排序、检索模式和耗时写入 Run，再由确定性 Evaluator 计算 Recall@6、MRR 与 nDCG@6。已有案例×重复序号会被跳过，因此中断后可以继续收口。

## 数据模型

- `evaluation.datasets`：评测目标和数据范围。
- `evaluation.dataset_versions`：草稿或冻结版本、案例数与版本哈希。
- `evaluation.cases`：输入、期望、来源、标注元数据和案例哈希。
- `evaluation.experiments`：数据、代码、模型、Prompt、检索策略与配置身份。
- `evaluation.runs`：一次案例执行的状态、输出、Observation、耗时、Token 和 Trace。
- `evaluation.metric_definitions`：指标公式、方向、聚合方式、阈值和版本。
- `evaluation.scores`：Run × Metric 的原始得分、是否通过和证据。
- `evaluation.judgments`：为后续 LLM Judge 与人工复核预留的版本化判断记录。

## 首版指标

### 通用指标

- `system.success@1.0`
- `system.structure_valid@1.0`
- `system.latency_ms@1.0`

### 面经 RAG

- `rag.recall_at_6@1.0`
- `rag.mrr@1.0`
- `rag.ndcg_at_6@1.0`

### 求职 Agent

- `agent.required_step_coverage@1.0`
- `agent.unnecessary_step_rate@1.0`

### 简历优化

- `resume.forbidden_claim_match_rate@1.0`

禁止事实命中率只检测金标中已经明确列出的禁止声明，不等价于完整的事实新增率。完整事实一致性需要后续的 Claim 抽取、证据映射和人工校准 Judge，首版不会把简单字符串匹配包装成完整防幻觉能力。

## 管理员 API

```text
GET  /api/evaluations/overview
GET  /api/evaluations/datasets
POST /api/evaluations/datasets
POST /api/evaluations/datasets/{id}/versions
GET  /api/evaluations/dataset-versions/{id}/cases
POST /api/evaluations/dataset-versions/{id}/cases
POST /api/evaluations/dataset-versions/{id}/freeze
GET  /api/evaluations/metric-definitions
GET  /api/evaluations/runtime-config/interview-rag
GET  /api/evaluations/experiments
POST /api/evaluations/experiments
GET  /api/evaluations/experiments/{id}
POST /api/evaluations/experiments/{id}/runs
POST /api/evaluations/experiments/{id}/complete
POST /api/evaluations/experiments/{id}/execute-interview-rag
```

全部接口要求管理员身份，并按 `organization_id` 隔离数据。

真实来源按评测目标做白名单校验：`interview_rag` 绑定 `interview_retrieval_feedback`，`career_agent` 绑定 `agent_turns`，`resume_optimizer` 绑定 `resume_optimization_records`。写入案例时会生成不含业务正文的来源快照哈希，并存入 `metadata.source_binding`；跨组织或不存在的 UUID 会被拒绝。

## PC 页面

路由为 `/evaluations`，包含：

1. 总览：真实案例占比、冻结版本、完成实验和红线状态。
2. 真实数据集：数据集、草稿版本、案例准入和冻结操作。
3. 实验记录：锁定实验配置、查看逐指标聚合和 Run 列表。
4. 指标字典：展示公式、聚合、方向、阈值、Evaluator 和版本。

现阶段只完成 PC 端，不进行手机或平板专项适配。

## 技术取舍

- 复用现有 PostgreSQL 与管理员认证，不引入第二套评测平台或任务队列。
- 使用独立 `evaluation` schema，避免评测实验修改业务历史。
- 指标以 Python 确定性实现为主，并把定义同步写入数据库。
- 实验运行采用 Observation 提交契约；首版已接通真实 RAG Adapter，Agent 与简历 Adapter 后续按同一契约接入。
- 创建 RAG 实验时从运行服务读取不含凭证的检索配置快照；执行前逐项比对，发现参数漂移就拒绝运行。
- 前端不展示虚构示例成绩；没有真实数据时只显示明确空状态。

## 验证结果

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m unittest tests.test_evaluation_module -v
npm --prefix web-ui run build
```

- Alembic 已升级到 `20260820_12`。
- `evaluation` schema 共创建 8 张表，首版写入 9 个指标定义。
- 评测模块 10 个自动化测试通过。
- WebUI Vite 生产构建通过。
- 全量测试中存在一个与本模块无关的既有简历生成测试失败：测试只准备一次模型返回，但当前简历服务质量修复路径会进行第二次模型调用。

## 后续边界

1. 实现求职 Agent 与简历优化 Adapter，自动从真实执行生成 Observation。
2. 为 Citation Precision、Citation Coverage、Faithfulness 和简历事实新增率增加 Claim 级证据映射。
3. 接入人工双标、争议仲裁与 Judge—人工一致性统计。
4. 增加 baseline Pairwise、重复运行方差和 paired bootstrap 置信区间。
5. PC 端流程确认后，再单独规划手机端适配。
