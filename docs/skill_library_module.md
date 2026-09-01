# 技能库模块说明

## 目标与边界

- 技能库用于查看本机已安装 Skill、借助 `find-skills` 搜索 GitHub 开放 Skill，并查看或编辑对应的 `SKILL.md`。
- 该模块不改动微信公众号内容生成和审核发布链路；求职助手通过只读适配层复用已安装 Skill 的摘要与 `SKILL.md`，不会从聊天页保存或覆盖 Skill。
- 保存行为会更新已有项目本地 Skill，或以用户填写的名称创建项目本地副本；前端按钮统一使用“保存并更新”表达这两种结果。

## 视口布局

- 页面沿用全局固定视口：左侧已安装 Skill 列表独立滚动，右侧由搜索区与预览编辑区组成。
- 右侧预览编辑区会占满搜索区以下的可用高度；`SKILL.md` 编辑器仅在自身区域内部滚动，保存栏保持在该面板底部。
- 此布局避免已选 Skill 内容较短时，面板下方留下大块无意义空白；窗口高度不足时，右侧主面板内部滚动，不扩张全局页面。

## 2026-08-07：预览区填充调整

- `skills-main-panel` 明确继承工作区高度，`skill-editor-panel` 从固定 720px 改为弹性填满剩余高度。
- 编辑器使用可用高度而不是固定内容高度，确保预览和保存区域形成完整的单一面板。

## 2026-08-20：求职助手调用适配

- `SkillLibraryService` 仍是 Skill 发现和读取的唯一文件层入口；`CareerSkillRuntime` 不自行扫描第二套目录，避免名称优先级和去重规则分叉。
- `/api/career/skills/mentions` 只返回候选元数据。首次挂载必须由输入中的精确 `/skill-name` 触发；`@` 仅用于求职助手引用面经。`selected_skill_ids` 中已经从文字删除的旧选择会被忽略；挂载成功后，该 Skill 会在当前会话后续轮次持续生效，直到用户显式调用另一个 Skill 或开启新会话。
- 所有 Skill 统一按文件语义挂载：移除 YAML frontmatter 后，将正文作为独立 `system` 上下文交给模型，并把命令后正文展开到 `$ARGUMENTS` / `${ARGUMENTS}`，把 Skill 目录展开到 `${SKILL_DIR}` / `${CLAUDE_SKILL_DIR}`。
- 不再存在 `find-skills → skill_search` 的名称硬编码。`/find-skills` 与 `/tech-resume-optimizer` 使用完全相同的加载路径，因此模型收到的行为规则始终以用户选中的 `SKILL.md` 为准。
- `SkillToolRegistry` 以通用能力而不是 Skill 名称绑定工具：skills.sh 实时搜索、GitHub Skill 仓库检查、项目级完整目录安装。仓库检查优先识别标准 `skills/`、`.agents/skills/`、`.claude/skills/`，避免把仓库维护用内部 Skill 与公开分发目录混装。
- 项目安装从 GitHub 默认分支归档中只提取选中的完整 Skill 目录，单次上限 2000 个文件、80 MB，目标固定为 `.agents/skills/<name>`；已有目录跳过，不覆盖用户本地修改。新目录会被 `SkillLibraryService.list_skills()` 立即扫描，无需另一套缓存刷新。
- 最终响应中的 `activated_skills[].status = mounted` 表示文件已真实进入本轮模型上下文；会话继承轮次的 `invocation_source` 为 `session`。`skill_executions` 则证明模型通过 Agent Loop 实际执行了哪些工具、状态和结果数。挂载或安装不等于执行新 Skill 自带脚本。

## 2026-08-25：GitHub Star 快照恢复

- Star 仓库识别优先读取 `SKILL.md` 的 `repository_full_name`、`github_repository`、`repository`、`homepage` 等字段；缺失时读取项目、用户目录或 `deploy/skill-seeds/.skill-lock.json` 中经过核验的 GitHub 安装来源。生产种子只记录公开仓库地址，不携带凭据。
- 列表和详情接口仍只读取 `data/skill_star_cache.json`，不会因打开技能库而同步等待 GitHub。首次快照会立即展示总 Star 数；积累下一期快照后再计算新增量和增长率。
- 生产 `pipeline-scheduler` 与 API 共用 `application_skills` 和 `application_data`：Scheduler 启动时刷新一次过期快照，周五内容任务开始时再次检查；七天内的新快照直接复用，不重复请求 GitHub。
- 无法关联公开 GitHub 仓库的项目自建或本地 Skill 明确显示“本地 Skill”，不再显示“暂无 Star”圆环；这类 Skill 不伪造仓库和 Star 数据。
- `scripts/refresh_skill_stars.py` 继续保留为独立手动诊断入口，使用同一服务方法和七天有效期规则。

## 2026-08-28：grill-me 工作流修复

- `grill-me` 改为自包含的分轮追问工作流，不再通过正文中的 `/grilling` 间接引用另一个 Skill；当前运行时只解析用户消息中的显式 `/skill-name`，不会递归激活 Skill 正文里的 Slash Command。
- 每轮只询问当前已经具备前置条件的决策，并附推荐答案；模型必须等待用户回答后再展开下一轮，在用户确认形成共同理解前不得直接实施方案。
- 项目种子与本机当前生效副本同步更新；未调整求职助手前端，也未新增 Skill 状态展示。
