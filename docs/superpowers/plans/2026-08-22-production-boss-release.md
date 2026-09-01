# 求职助手 BOSS 能力全量投产 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 验证求职助手通过 Chrome 扩展调用 BOSS 职位搜索与详情的完整链路，并将当前工作区全部可发布内容安全部署到既有生产环境。

**Architecture:** 生产 Web 通过 `window.postMessage` 与 Chrome MV3 扩展通信，扩展在用户已登录的 BOSS 页面 MAIN world 发起只读请求；确认岗位后，Web 将标准化职位正文写入 Career API，并绑定到当前会话。发布沿用 Git 仓库与 `docker-compose.production.yml` 的单机 Docker Compose 流程，数据库先迁移，API 健康后再由 Caddy 对外提供服务。

**Tech Stack:** Vue 3、Vite、Chrome MV3、FastAPI、PostgreSQL/pgvector、Alembic、Docker Compose、Caddy

**Spec:** `docs/superpowers/specs/2026-08-21-boss-job-search-ui-design.md`、`docs/superpowers/specs/2026-08-21-career-job-library-import-design.md`

## Global Constraints

- 生产标题、导航与操作名称使用中文。
- BOSS 数据只通过用户浏览器现有登录会话读取，不在服务端保存 Cookie 或登录凭据。
- 扩展只允许 `ping`、`list_cities`、`search_jobs`、`get_job_detail` 四个只读动作。
- 当前工作区已有修改均属于本次发布候选，不覆盖或丢弃任何现有变更。
- 只有所有本地门禁和生产配置校验通过后才允许发布。
- 生产保持 `VIDEO_SUBMIT_ENABLED=false` 与 `AUDIO_ENABLED=false`。

---

### Task 1: 发布范围与资源清单

**Files:**
- Inspect: `git status` 输出与仓库所有未提交文件
- Inspect: `.env.production.example`
- Inspect: `docker-compose.production.yml`

**Interfaces:**
- Consumes: 当前工作区、Git 远端与生产部署文档
- Produces: 可提交文件清单、排除的本地数据/秘密清单、生产依赖清单

- [ ] **Step 1: 检查分支、远端和全部改动**

  Run: `git status --short --branch && git remote -v && git diff --stat && git diff --cached --stat`

  Expected: 位于 `main`，远端为目标私有仓库，所有修改均可解释。

- [ ] **Step 2: 检查未跟踪内容是否包含运行数据或秘密**

  Run: `git status --short | Select-String '^\?\?'`

  Expected: `data/`、临时附件、真实 `.env` 和密钥不进入提交；源码、迁移、测试、文档与浏览器扩展进入发布候选。

- [ ] **Step 3: 校验生产环境变量键与 Compose 展开**

  Run: `python scripts/verify_career_deployment_settings.py`

  Expected: 脱敏、认证、数据库、音视频关闭和容器地址校验全部通过。

### Task 2: BOSS 扩展只读调用链

**Files:**
- Verify: `browser-extension/job-library/manifest.json`
- Verify: `browser-extension/job-library/content-script.js`
- Verify: `browser-extension/job-library/service-worker.js`
- Verify: `browser-extension/job-library/boss-data.js`
- Test: `browser-extension/job-library/tests/boss-data.test.mjs`

**Interfaces:**
- Consumes: 网页动作 `{ action, payload }` 与用户已登录 BOSS 标签页
- Produces: 稳定的城市、岗位列表和岗位详情对象

- [ ] **Step 1: 执行归一化层测试**

  Run: `npm test --prefix browser-extension/job-library`

  Expected: 城市过滤、字段归一化、错误分类相关测试全部 PASS。

- [ ] **Step 2: 校验扩展脚本语法**

  Run: `node --check browser-extension/job-library/service-worker.js` 与 `node --check browser-extension/job-library/content-script.js`

  Expected: 两个命令退出码均为 0。

- [ ] **Step 3: 人工审计权限、来源与动作白名单**

  Run: `rg -n 'host_permissions|externally_connectable|allowed|ping|list_cities|search_jobs|get_job_detail|zhipin.com|your-domain.example' browser-extension/job-library`

  Expected: 生产域名在许可范围内，动作仅为四个只读动作，未读取 Cookie，未包含投递或聊天动作。

### Task 3: 求职助手导入与会话绑定

**Files:**
- Verify: `web-ui/src/components/CareerJobSearchDialog.vue`
- Verify: `web-ui/src/components/JobSearchWorkspace.vue`
- Verify: `web-ui/src/job-library-bridge.js`
- Verify: `web-ui/src/job-library-target-role.js`
- Verify: `src/career_assistant/web/router.py`
- Test: `tests/test_career_job_assessment_api.py`
- Test: `tests/test_career_context_profiles.py`

**Interfaces:**
- Consumes: 扩展返回的完整岗位详情
- Produces: `POST /api/career/target-role-profiles` 与 `POST /api/career/conversations/{id}/context` 成功结果

- [ ] **Step 1: 执行前端协议与映射测试**

  Run: `npm test --prefix web-ui`

  Expected: bridge、城市目录、岗位正文、职位画布和评估视图测试全部 PASS。

- [ ] **Step 2: 执行 Career 后端相关测试**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_career_job_assessment.py tests/test_career_job_assessment_api.py tests/test_career_job_assessment_repository.py tests/test_career_context_profiles.py tests/test_career_context_profiles_v2.py tests/test_career_optional_context_repository.py -q`

  Expected: 目标岗位创建、上下文绑定、替换岗位、四项评估和失败回滚全部 PASS。

- [ ] **Step 3: 构建生产 Web**

  Run: `npm run build --prefix web-ui`

  Expected: Vite 构建成功，无缺失依赖或编译错误。

### Task 4: 全项目投产门禁

**Files:**
- Verify: `tests/`
- Verify: `migrations/versions/`
- Verify: `deploy/skill-seeds/`
- Verify: `docker-compose.production.yml`

**Interfaces:**
- Consumes: 当前全部后端、前端、迁移、Skill 和配置改动
- Produces: 可复现的测试与构建结果

- [ ] **Step 1: 执行完整 Python 测试集**

  Run: `.venv/Scripts/python.exe -m pytest -q`

  Expected: 全部测试 PASS；真实第三方服务测试仅在明确标记为可选时允许 SKIP。

- [ ] **Step 2: 校验数据库迁移链**

  Run: `.venv/Scripts/alembic.exe heads`

  Expected: 只有一个 head，包含 `20260821_16_job_match_assessments`。

- [ ] **Step 3: 校验 Skill 种子清单与发布资源**

  Run: `.venv/Scripts/python.exe scripts/verify_skill_portability.py`

  Expected: manifest、Skill 路径和可移植性检查全部通过。

- [ ] **Step 4: 展开生产 Compose 配置**

  Run: `docker compose --env-file <生产环境文件> -f docker-compose.production.yml config --quiet`

  Expected: 配置成功展开，生产秘密已设置且未输出到日志。

### Task 5: 修复、提交与发布

**Files:**
- Modify: 仅修改门禁发现的阻塞文件
- Update: 与对应模块相关的 `docs/*.md`

**Interfaces:**
- Consumes: Tasks 1-4 的失败证据
- Produces: 无阻塞的发布提交与生产容器

- [ ] **Step 1: 对每个阻塞问题先补充可复现测试**

  Run: 对应的单测命令并确认修复前 FAIL、修复后 PASS。

  Expected: 每个代码修复都有自动化回归证据。

- [ ] **Step 2: 检查最终提交内容**

  Run: `git diff --check && git status --short`

  Expected: 无空白错误、无秘密、无运行数据，发布候选内容完整。

- [ ] **Step 3: 创建并推送发布提交**

  Run: `git add <已审计的发布文件> && git commit -m "feat: release career boss job workflow" && git push origin main`

  Expected: 远端 `main` 指向本地发布提交。

- [ ] **Step 4: 在既有生产服务器拉取并重建**

  Run: `git pull --ff-only origin main && docker compose --env-file .env.production -f docker-compose.production.yml up -d --build`

  Expected: `career-migrate` 和 `skill-seed` 退出码为 0，其余常驻服务 healthy/running。

### Task 6: 线上验收

**Files:**
- Verify: 生产容器日志与正式域名页面

**Interfaces:**
- Consumes: 已发布的生产服务与已重新加载的 Chrome 扩展
- Produces: 求职助手 BOSS 搜索、详情、确认岗位、上下文绑定的线上证据

- [ ] **Step 1: 检查服务与健康接口**

  Run: `docker compose --env-file .env.production -f docker-compose.production.yml ps` 与 `curl -fsS https://your-domain.example/api/health`

  Expected: API 返回 200，Web/API/PostgreSQL/Gotenberg/Scheduler/Caddy 正常。

- [ ] **Step 2: 检查迁移、Skill 与 Scheduler 日志**

  Run: `docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 career-migrate skill-seed pipeline-scheduler`

  Expected: 迁移和 Skill 初始化成功，Scheduler 无启动异常。

- [ ] **Step 3: 检查生产音视频开关**

  Run: `docker compose --env-file .env.production -f docker-compose.production.yml exec career-api sh -lc 'test "$VIDEO_SUBMIT_ENABLED" = false && test "$AUDIO_ENABLED" = false'`

  Expected: 命令退出码为 0。

- [ ] **Step 4: 在 Chrome 重新加载扩展并完成真实 BOSS 冒烟**

  操作：重新加载 `browser-extension/job-library`，登录 BOSS，在正式站点选择城市与岗位，执行搜索、查看详情、在求职助手中确认岗位并发送一轮消息。

  Expected: 状态显示已连接；岗位城市正确；详情完整；目标岗位成功保存并绑定；下一轮助手回答使用该岗位上下文。
