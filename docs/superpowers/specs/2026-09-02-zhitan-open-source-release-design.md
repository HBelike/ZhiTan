# ZhiTan 开源发布设计

## 目标

把当前项目的工作区快照整理为独立的公开仓库 `HBelike/ZhiTan`，对外品牌统一为 **ZhiTan**。新仓库以求职者为核心，重点介绍职位发现、简历与 JD 分析、求职 Agent、面经 RAG、面试及笔试辅助；GitHub 周榜与微信公众号内容流水线作为附加自动化模块保留。

原仓库 `<original-worktree>` 在整个过程中保持只读。复制、代码调整、提交和推送只发生在 `<zhitan-worktree>` 的 `master` 分支。

## 仓库隔离与发布顺序

1. 从原工作区复制当前受 Git 管理的文件和未忽略的新文件，使新仓库包含当前尚未提交的功能实现。
2. 不复制原 `.git` 历史，也不复制真实 `.env`、运行数据、日志、生成输出、依赖目录、IDE 缓存和 Firecrawl 调研缓存。
3. 在新目录初始化独立 Git 仓库，分支固定为 `master`，remote 固定为 `git@github.com:HBelike/ZhiTan.git`。
4. 在首次公开推送前完成个人信息泛化、疑似密钥清理和验证，避免敏感值进入公开 Git 历史。
5. 完成测试、构建和扫描后再首次推送；不连接或修改生产服务器。

## 页面路由调整

只解除“简历助手”和“评测中心”的产品页面挂载，不删除其实现。

### 删除的路由表面

- 从 `web-ui/src/App.vue` 移除两页的组件导入、侧栏导航项、页面标题分支、路由样式标记和页面渲染分支。
- 从 `web-ui/src/navigation-access.js` 移除 `/resume-assistant` 与 `/evaluations` 的路由识别，使旧地址和未知地址一样回到 `/career`。
- 从 `src/platform_access/navigation_config.py` 的公开路由目录移除 `resume_assistant` 与 `evaluation_center`，使管理台不再展示这两个模块开关。
- 保留旧配置键的兼容读取：历史 JSON 中出现这两个键时忽略，不让导航配置初始化失败。
- 更新路由与导航测试，明确验证旧地址回到 `/career`、公开目录不再返回两项。

### 明确保留的实现

- `ResumeAssistantPage.vue`、`EvaluationCenterPage.vue` 及其专属样式。
- 简历优化、文件生成、历史归档相关 API、Service、Repository 和 migration。
- 评测数据集、实验、指标、Runner 相关 API、Service、Repository 和 migration。
- 组件级测试、模块设计文档和后续优化所需代码。
- 求职助手现有的简历解析、附件理解、JD 分析与人岗匹配能力。

## 品牌与公开定位

- 产品名、页面标题、README 标题和配置展示名统一为 `ZhiTan`。
- 不再使用中文品牌名；中文文档中也只写 `ZhiTan`。
- 英文定位：`Open-source AI job-search and resume intelligence workbench.`
- 根 `README.md` 使用英文，`README.zh-CN.md` 提供中文说明。
- README 首屏优先展示核心价值、实际能力、截图、Quickstart 和模块边界，不以实现历史或内部里程碑开场。

## 个人信息与硬编码优化

### 管理员身份

- 将固定管理员邮箱改为 `PLATFORM_ADMIN_EMAIL` 环境变量。
- bootstrap、注册保留校验、邮箱绑定、Repository 与测试通过统一配置入口读取管理员邮箱。
- 数据库只约束“最多一个管理员”和合法角色，不在 schema 或 migration 中写死某个私人邮箱。
- 历史归属 migration 按管理员角色定位目标账号，不按私人邮箱定位。
- 示例环境文件使用 `admin@example.com` 等明显的非真实示例值。

### 服务器与部署信息

- 把文档中的真实公网 IP、域名、服务器目录、私人仓库名和个人邮箱替换为 `<server-ip>`、`your-domain.example`、`/opt/zhitan` 等通用示例。
- 保留可复用的部署步骤和架构判断，不保留私人生产环境状态、已部署提交号或实际账号清单。
- 所有 API Key、Token、数据库密码、加密主密钥只通过环境变量注入；示例文件只保留空值或 `replace-with-...`。

### 扫描边界

- 扫描所有将被提交的文件名和内容，检查常见 GitHub Token、云厂商 Key、私钥、Bearer Token、模型 Key、邮箱、IPv4 和真实域名模式。
- 测试中的仿真 Key 使用明显不会被误认成真实凭据的字符串。
- `.gitignore` 明确覆盖本地环境文件、数据库、附件、日志、生成素材、依赖和调研缓存。

## 标准开源文件

参考 DeepSeek Harness、OpenAI Codex、OpenCode 和 Dify 的公开仓库结构，ZhiTan 首版包含：

- `README.md` 与 `README.zh-CN.md`
- `LICENSE`：Apache-2.0
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `SAFETY.md`
- `THIRD_PARTY_NOTICES.md`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- 一条与项目现有测试能力匹配的基础 GitHub Actions CI

第三方说明合并现有 `docs/open_source_attribution.md`、Noto CJK 字体许可证和实际分发依赖，不复制参考项目的 README 文案或品牌素材。

## README 信息架构

1. ZhiTan 一句话定位与语言切换。
2. 核心功能：职位发现、简历/JD 分析、求职 Agent、面经 RAG、面试与笔试辅助。
3. 产品截图与模块概览。
4. Docker Compose 优先的 Quickstart。
5. 可选 Provider 与环境变量说明。
6. 架构与目录导航。
7. 本地开发、测试与验证命令。
8. Responsible Use、隐私边界与已知限制。
9. Contributing、Security、Third-party notices 与 License。

README 只声明代码和现有验证能够支持的能力，不使用无法证明的性能数字、用户规模或“生产级”等营销结论。

## 验证策略

### 路由与前端

- 运行 `web-ui` Node 测试，覆盖导航目录、旧路由回退和现有组件级行为。
- 运行 Vite production build，确认移除页面挂载后不存在未解析导入或模板分支。

### 后端与配置

- 运行导航配置、平台认证、首管理员 bootstrap、管理员 migration 与相关 API 测试。
- 运行完整 Python 测试集；若外部服务测试需要凭据，应保持现有离线替身或明确跳过原因，不使用真实 Key。

### 开源发布

- 对待提交树执行敏感模式扫描和大文件检查。
- 确认新仓库只有 `origin = git@github.com:HBelike/ZhiTan.git`，当前分支为 `master`。
- 确认原仓库 `git status` 没有因本任务新增变化。
- 推送后检查 GitHub 默认分支、README 渲染、License 识别和公开文件列表。

## 不在本次范围

- 不删除简历助手或评测中心的组件、API、Service、Repository、migration 和文档。
- 不拆分公众号流水线与求职平台为多个仓库。
- 不重构现有业务架构，不追加备份、容灾或生产部署设计。
- 不推送、重建或修改现有生产环境。
- 不改写或提交原私有仓库。

## 验收标准

- 原仓库保持不变，所有新提交只存在于 ZhiTan。
- ZhiTan 使用 `master`，并成功推送至 `HBelike/ZhiTan`。
- UI 与管理台均不再出现简历助手和评测中心入口，旧 URL 回到 `/career`。
- 两个模块的实现源码仍完整存在并可继续开发。
- 公开 Git 历史不包含已知个人邮箱、真实服务器 IP/域名或真实 API Key。
- README、许可证、贡献、安全、责任使用、第三方声明和 GitHub 协作模板齐备。
- 后端测试、前端测试与 production build 通过，或对非本次引入且无法离线执行的失败给出明确证据。
