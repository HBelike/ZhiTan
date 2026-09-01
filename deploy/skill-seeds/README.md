# 生产 Skill 种子

本目录用于把部署方有权分发的 Skill 初始化到持久卷。ZhiTan 的公开源码快照不会携带
从个人电脑、Codex 插件缓存或其他第三方产品导出的 `SKILL.md`；其中部分内容受单独条款
约束，不能由 ZhiTan 重新许可或公开分发。

因此，公开仓库中的 `catalog.manifest.json` 默认是空目录。Skill 管理、检索、编辑和持久化
功能均保留；部署方可以在确认上游许可证允许再分发后，通过下述工具注入自己的目录。
`.skill-lock.json` 只记录已核验的公开 GitHub 来源，不代表 ZhiTan 自动下载或捆绑它们。

## 更新流程

需要按目录手动导出时，在开发机先审查后运行：

```powershell
.\.venv\Scripts\python.exe scripts\export_portable_skills.py `
  --source-root "$env:USERPROFILE\.agents\skills" `
  --destination-root deploy\skill-seeds
```

需要同步本机当前可见的全部 Skill 时，运行：

```powershell
.\.venv\Scripts\python.exe scripts\export_portable_skills.py --local-catalog
```

脚本会按 Skill `name` 去重，优先级是 `~/.agents/skills`、`~/.codex/skills`、插件缓存；
默认不会覆盖已经审查的种子。只有确认上游变更和许可证都允许目标用途时才加
`--overwrite`。无论哪种模式，脚本都只复制 `SKILL.md`，不会复制 `node_modules`、二进制
工具、缓存或包含凭据的本地目录。导出结果默认被 `.gitignore` 排除，供本地开发和私有部署
使用；不要把没有明确再分发授权的 Skill 强制提交到公开仓库。

生产的 `skill-seed` 一次性容器会把这里已有的缺失文件增量复制到 `application_skills` 卷，
绝不会覆盖用户从 WebUI 保存的同名文件。若要将一个种子强制恢复为版本库内容，应先备份并
删除该卷中对应的 `SKILL.md`，再重新运行 `skill-seed`；不能直接用部署覆盖用户修改。
