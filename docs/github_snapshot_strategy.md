# GitHub 快照读取策略

## 设计目标

技能搜索和热门项目搜索优先读取已有快照，避免每次打开页面或重复输入关键词都请求 GitHub。只有用户明确点击刷新，或周五定时生产任务启动时，系统才主动更新远端数据。

## 技能搜索

- 存储位置：`data/skill_search_cache.json`。
- 查询键：对关键词执行去首尾空格、合并连续空格、转小写后形成统一键。
- 新关键词：没有任何快照时允许请求一次 GitHub，并保存结果。
- 6 小时内：直接返回新鲜快照。
- 超过 6 小时：继续返回过期快照，普通搜索不会自动请求 GitHub。
- 明确刷新：用户点击“刷新 GitHub”后强制请求 GitHub；成功后覆盖该关键词快照，失败时保留并返回旧快照。
- 页面信息：展示数据来源、快照状态和更新时间。

调用链：

```text
技能库搜索
  -> POST /api/skills/search
  -> SkillLibraryService.search_skills()
  -> 读取 skill_search_cache.json
  -> 命中快照：直接返回
  -> 首次查询或明确刷新：请求 GitHub -> 写入快照 -> 返回
```

## GitHub 热门项目

- 存储位置：SQLite 的 `repositories`、`star_snapshots`、`weekly_rankings` 表。
- 周五定时生产：先执行 `SearchTask` 更新当周榜单，再进入总结和素材生成。
- 手动完整流程：不执行 `SearchTask`，从 `SummaryTask` 开始，消费最近一次周榜快照。
- 管理员刷新：管理台“刷新 GitHub 热门项目”只执行 `SearchTask`。
- 无快照边界：工作台禁止启动手动完整流程，并提示先到管理台刷新。
- 页面信息：展示快照周区间、项目数量和更新时间。

调用链：

```text
周五定时任务
  -> SearchTask
  -> weekly_rankings 快照
  -> SummaryTask -> 后续内容流水线

手动完整流程
  -> 读取 weekly_rankings 快照
  -> SummaryTask -> 后续内容流水线

管理台明确刷新
  -> POST /api/admin/github-snapshot/refresh
  -> Application.refresh_github_snapshot()
  -> SearchTask
  -> weekly_rankings 快照
```

## 验证与边界

- `scripts/verify_skill_search_performance.py` 验证关键词归一化、新鲜快照、过期快照复用和明确刷新。
- `scripts/verify_github_snapshot_strategy.py` 验证周榜快照元数据、手动流程不访问 GitHub、周五流程仍先执行 `SearchTask`。
- 本策略只优化读取时机和页面可见性，不改变 GitHub 排名算法、现有数据库结构或定时任务时间。
