# DeepSeek 付费模型分类修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 DeepSeek 模型从错误的免费额度分类改为付费分类，并确保前端表述与后台路由一致。

**Architecture:** 在模型档案仓储边界统一修正 DeepSeek 的费用分类，使新写入和旧数据读取都得到 `paid`；前端继续依据服务端返回的 `cost_tier` 分组展示，免费自动选择只包含真正的 `free_quota` 档案。数据库迁移同步清理已有错误记录，避免存储层长期保留错误值。

**Tech Stack:** Python 3、FastAPI 领域服务、SQLAlchemy/PostgreSQL、Alembic、Vue 3、Node.js Test Runner

**Spec:** `docs/career_model_connections.md`

## Global Constraints

- 仅本地修改、测试和构建，不部署到生产环境。
- DeepSeek 不得显示“【免费】”，也不得进入免费自动选择。
- 不自动开启 `allow_paid_profiles`；付费调用仍服从现有全局策略。
- 免费目录创建的连接仍保持 `free_quota`，不得影响其他服务商。
- 同步记录设计目标、技术取舍、调用链、依赖、验证结果和后续边界。

---

### Task 1: 统一 DeepSeek 费用分类

**Files:**
- Modify: `src/career_assistant/persistence/model_profile_repository.py`
- Create: `migrations/versions/20260825_21_deepseek_profiles_are_paid.py`
- Test: `tests/test_model_profile_cost_tier.py`

**Interfaces:**
- Consumes: `ModelCostTier`、`ModelProfileDraft`、数据库行中的 `provider_key` 与 `cost_tier`
- Produces: `normalize_model_cost_tier(provider_key: str, cost_tier: ModelCostTier) -> ModelCostTier`

- [x] **Step 1: 编写失败测试**

```python
def test_deepseek_is_always_normalized_to_paid():
    assert normalize_model_cost_tier("deepseek", ModelCostTier.FREE_QUOTA) is ModelCostTier.PAID


def test_other_provider_keeps_declared_cost_tier():
    assert normalize_model_cost_tier("gemini", ModelCostTier.FREE_QUOTA) is ModelCostTier.FREE_QUOTA
```

- [x] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_model_profile_cost_tier.py -q`
Expected: FAIL，原因是 `normalize_model_cost_tier` 尚不存在。

- [x] **Step 3: 实现最小分类规则**

```python
PAID_ONLY_PROVIDER_KEYS = frozenset({"deepseek"})


def normalize_model_cost_tier(provider_key: str, cost_tier: ModelCostTier) -> ModelCostTier:
    if provider_key.strip().lower() in PAID_ONLY_PROVIDER_KEYS:
        return ModelCostTier.PAID
    return cost_tier
```

在写入草稿规范化和数据库行转换两处调用该函数；新增 Alembic 数据迁移，将已有 `provider_key = 'deepseek'` 的档案更新为 `paid`，降级不恢复错误分类。

- [x] **Step 4: 运行测试并确认通过**

Run: `python -m pytest tests/test_model_profile_cost_tier.py -q`
Expected: PASS。

### Task 2: 修正模型下拉与新建默认值

**Files:**
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Modify: `web-ui/src/career-composer-entrypoints.test.js`

**Interfaces:**
- Consumes: 服务端 `profile.cost_tier`
- Produces: 免费与付费两个明确分组；手动连接默认 `costTier = 'paid'`

- [x] **Step 1: 扩充失败测试**

```js
assert.match(source, /costTier: 'paid'/)
assert.match(source, /label="已接入的付费模型"/)
assert.match(source, /【付费】\{\{ modelChoiceLabel\(item\) \}\}/)
assert.doesNotMatch(source, /已配置的其他模型/)
```

- [x] **Step 2: 运行测试并确认失败**

Run: `npm test -- --test-name-pattern="会话工具入口"`
Expected: FAIL，现有模板仍使用“其他模型”且手动连接默认免费。

- [x] **Step 3: 实现下拉表述与默认值**

将手动连接初始 `costTier` 改为 `paid`；将非免费就绪档案分组命名为 `readyPaidModelProfiles`，下拉分组显示“已接入的付费模型”，选项增加“【付费】”前缀。免费目录显式预填仍保留 `free_quota`。

- [x] **Step 4: 运行前端测试并确认通过**

Run: `npm test`
Expected: 全部 PASS。

### Task 3: 文档与整体验证

**Files:**
- Modify: `docs/career_model_connections.md`
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Consumes: Task 1、Task 2 的最终行为
- Produces: 可交接的设计目标、调用链、验证结果与边界说明

- [x] **Step 1: 更新设计和调用链说明**

记录 DeepSeek 的付费归类、旧数据迁移、仓储读取兜底、前端分组，以及“不自动开启付费权限”的边界。

- [x] **Step 2: 运行后端相关测试**

Run: `python -m pytest tests/test_model_profile_cost_tier.py -q`
Expected: PASS。

- [x] **Step 3: 运行前端全量测试与构建**

Run: `npm test`
Expected: 全部 PASS。

Run: `npm run build`
Expected: Vite 构建成功。

- [x] **Step 4: 检查差异范围**

Run: `git diff --check`
Expected: 无空白错误；不包含部署操作。
