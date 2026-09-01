# 简历助手模块

> 状态：第一版已于 2026-08-22 投产
> 路由：`/resume-assistant`
> 导航位置：主侧边栏“求职助手”下方，与其同层级
> 最后更新：2026-08-22

## 1. 设计目标

简历助手面向一个明确任务：读取用户已有简历与目标岗位要求，生成有原文依据、可单独确认的优化意见；用户确认后生成新的 Markdown 简历，并由用户显式保存为一条不可更新的历史记录。

模块不修改原始简历，不把分析草稿写入历史，也不改动既有求职助手、面经库、技能库和微信公众号工作流。

## 2. 已实现功能

- 简历仅接收 PDF、DOC、DOCX。
- 岗位信息支持粘贴正文和上传多张截图，截图按上传顺序合并。
- 支持用户填写额外优化要求，并将其加入分析与生成 Prompt。
- 复用现有 `AttachmentParser`、Docling、旧版 Office 转换和图片理解链路。
- 复用现有模型连接库，未指定模型时精确选择 `deepseek-v4-flash`。
- 分析结果为结构化建议，包含简历证据、岗位证据、修改原因和具体改法。
- 建议支持勾选和编辑；用户可重新分析。
- 大画布左侧展示原始简历，右侧展示修改建议或优化后 Markdown。
- 只有点击“保存到历史”才保存原文件副本、优化 Markdown 和数据库记录。
- 历史记录按岗位名称和日期范围查询，PC 端每行四张卡片。
- 普通用户只读取自己的记录，管理员可以读取组织内全部记录。
- 历史详情同时展示原始简历和优化后简历，支持预览与下载。

## 3. 页面流程

```text
/resume-assistant
  → 检索已保存记录
  → 点击“新建优化任务”
  → 上传简历、填写岗位信息和额外要求
  → 开始分析
  → 左侧查看原简历，右侧审核/编辑建议
  → 选择建议并生成优化简历
  → 对照原文与优化结果
  → 显式保存到历史
  → 首页新增一张不可更新的历史卡片
```

分析和生成均为当前页面内的临时状态。刷新页面后，未保存的分析结果不会进入历史库。

## 4. 后端调用链

### 4.1 分析

```text
POST /api/career/resume-optimizations/analyze
  → TemporaryAttachmentStore.save_upload()
  → ResumeOptimizationService.analyze()
  → AttachmentParser.parse(简历)
  → AttachmentParser.parse(岗位截图，按顺序)
  → ResumeOptimizationService._resolve_model()
  → OpenAICompatibleChatClient.complete()
  → 解析结构化建议
  → 清理临时附件
  → 返回原文、岗位信息和建议（不落库）
```

### 4.2 生成

```text
POST /api/career/resume-optimizations/generate
  → 校验用户选择的建议
  → ResumeOptimizationService.generate()
  → OpenAICompatibleChatClient.complete()
  → 清理 Markdown 围栏
  → 校验生成结果是否保留原文事实锚点与文本骨架
  → 首轮偏离时使用严格 Prompt 重试一次；再次偏离则拒绝返回
  → 返回完整优化简历（不落库）
```

### 4.3 保存

```text
POST /api/career/resume-optimizations
  → TemporaryAttachmentStore.save_upload(再次上传原文件)
  → ResumeOptimizationService.save_record()
  → 复制原文件到持久化目录
  → 写入 optimized_resume.md
  → ResumeOptimizationRepository.create()
  → 清理请求临时附件
  → 返回历史记录
```

## 5. Prompt 与模型边界

分析 Prompt 由以下内容组成：

```text
禁止虚构的系统规则
+ 原始简历正文
+ 岗位文本
+ 按顺序解析的岗位截图文字
+ 用户额外要求
+ 结构化 JSON 输出约束
```

生成 Prompt 只接收用户勾选后的建议。模型不得新增原文中不存在的经历、公司、项目、技能、数字或成果，只允许调整结构、顺序和表达。

默认模型通过 `profile_key + display_name + model_id` 归一化匹配 `deepseekv4flash`。若未配置可用连接，接口明确报错，不静默切换其他模型。

## 6. 持久化设计

数据库表：

```text
career_assistant.resume_optimization_records
```

一条记录保存：

- 组织、创建人和所属用户；
- 岗位名称与岗位正文；
- 使用的模型档案信息；
- 原文件名、类型和持久化路径；
- 原始解析 Markdown；
- 最终建议集合；
- 优化后 Markdown 和文件路径；
- 创建时间。

文件目录：

```text
data/resume-assistant/{organization_id}/{record_id}/
  ├─ 原始文件.pdf|doc|docx
  └─ optimized_resume.md
```

仓储只提供新增、列表和详情读取，不提供更新与删除。

## 7. 权限范围

```text
管理员：organization_id 范围内查询全部记录
普通用户：organization_id + owner_id 双重限定
```

列表和详情接口都在服务端追加归属条件，前端只负责展示。

## 8. 关键文件

- `src/career_assistant/resume_assistant/models.py`：模块数据契约。
- `src/career_assistant/resume_assistant/service.py`：分析、生成、保存编排。
- `src/career_assistant/resume_assistant/repository.py`：不可变历史仓储。
- `src/career_assistant/web/router.py`：HTTP 接口与当前用户范围。
- `migrations/versions/20260817_10_resume_assistant.py`：PostgreSQL 表迁移。
- `web-ui/src/components/ResumeAssistantPage.vue`：PC 端页面和交互。
- `web-ui/src/App.vue`：路由与主导航入口。
- `tests/test_resume_assistant.py`：关键行为测试。

## 9. 验证结果

- 生产 Alembic 已升级到 `20260821_16 (head)`。
- 服务测试覆盖：分析不落库、生成不落库、显式保存落库、默认模型精确选择、偏离原文时单次重试、连续偏离时拒绝返回。
- 简历助手仓储与服务已接入 `CareerAssistantServices`；`/api/career/model-profiles` 经后端直连和 Vite 代理访问均返回可用模型列表。
- Python `compileall` 通过。
- Vue 生产构建通过。
- 2026-08-22 全量后端回归为 `116 passed`，生产 API 与 Web 健康检查通过。

## 10. 当前边界与后续

- 当前输出为 Markdown，不生成 DOCX/PDF 优化文件。
- 原始 PDF 只保证文本预览，不承诺像素级版式还原。
- 当前优先完成 PC 端；手机端适配放到后续阶段，不设计 iPad 专属断点。
- 不实现草稿保存、历史更新、删除、版本树和协作审核。
- 当前生产启用登录、闭合运营模式与求职资料脱敏；附件仍只进入 API tmpfs。
