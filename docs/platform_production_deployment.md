# 全平台生产部署基线

## 设计目标

本文件定义当前个人平台的首个公网部署形态：在一台香港 Linux VPS 上完整运行 GitHub 周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台与 PostgreSQL + pgvector，同时不把数据库、FastAPI 或临时附件直接暴露到公网。

平台采用二元角色：固定邮箱管理员负责路由与运行配置，其余账号统一为普通用户。所有业务 API 必须先登录；普通用户按管理员保存的模块开关访问普通页面，管理员专属接口继续由 FastAPI 服务端角色依赖保护。

## 架构与调用链

```text
浏览器
  -> Caddy :443（自动 HTTPS、静态 Web、API 反向代理）
  -> career-web（Vue 静态页面）
  -> career-api（FastAPI：审核、Skill、Career、面经、登录、管理台）
      -> PostgreSQL + pgvector（会话、账号、面经、模型档案、运行配置）
      -> SQLite 共享卷（既有公众号任务、审核、素材索引）
      -> outputs 共享卷（图片、音频、视频）

pipeline-scheduler（唯一副本）
  -> main.py
  -> Application.run()
  -> SchedulerManager
  -> 周五 08:00 内容生产 / 09:00 微信草稿推进
```

## 技术取舍

- **Caddy**：使用 Caddy 2 自动签发/续期 HTTPS，避免宿主机 Nginx + Certbot 的额外维护；它只作为公网入口。Caddy 为 Apache-2.0 许可，适合该单机部署。
- **单一 Scheduler**：既有周榜工作流仍使用 SQLite，因此只能运行一个 `pipeline-scheduler` 副本，并与 API 共用 `application_data` 卷。未来迁移 PostgreSQL 或引入分布式锁后再扩容。
- **执行互斥**：Scheduler 与管理台手动运行共享同一个原子文件锁；发生冲突时 Scheduler 记录跳过，手动执行得到明确失败记录。锁的保守过期时间为六小时，防止容器崩溃留下永久阻塞。
- **双数据库过渡**：Career/账号/RAG 使用 PostgreSQL + pgvector；原公众号工作流继续使用 SQLite。此次上线不改变旧业务数据模型，优先确保稳定发布。
- **媒体与附件**：周榜产物使用 Docker 命名卷持久化；原始简历附件使用 API tmpfs，在 Turn 完成后删除。生产环境必须保持 `CAREER_REDACTION_ENABLED=true`。
- **Skill 持久化**：`deploy/skill-seeds` 是随镜像发布、经过审查的 `SKILL.md` 快照；一次性 `skill-seed` 容器会先将缺失种子复制到独立 `application_skills` 卷并授予 API 进程写入权限。`deploy/skill-seeds/.skill-lock.json` 仅记录已核验的公开 GitHub 来源，用于恢复真实 Star 数据。API 与单一 Scheduler 共用该卷和 `application_data`，Scheduler 启动及每周内容任务会刷新已过期的 GitHub Star 快照。之后 WebUI 的保存结果优先且永不被重新部署覆盖；生产服务器不依赖开发电脑的 `~/.agents`、`~/.codex` 或插件缓存。
- **认证边界**：生产设 `PLATFORM_AUTH_REQUIRED=true`。登录通过 HttpOnly Cookie，认证中间件会把真实平台用户映射为 Career Actor，避免所有登录用户共用默认对话身份；`admin` 仅允许 `admin@example.com` 持有，其余账号统一为 `user`。部署面经管理员权限版本后，健康接口的 `career_runtime_revision` 应为 `2026-08-28-interview-admin-permissions-v2`。

## 部署前置条件

1. Ubuntu 22.04/24.04 或等价 Linux，建议至少 2 vCPU / 4 GB RAM / 60 GB SSD；需要完整视频生产时建议 4 vCPU / 8 GB。
2. 安装 Docker Engine 与 Docker Compose v2；不需要在服务器安装 Python、Node.js 或 PostgreSQL。
3. 一个已解析到服务器固定公网 IPv4 的域名；安全组和 UFW 仅放行 22（限管理 IP）、80、443。
4. 私有仓库使用只读 Deploy Key 拉取；服务器不使用个人 GitHub Token。真实 `.env.production` 在服务器本地创建，权限为 `600`。
5. `application_data` Docker 命名卷必须保留：未显式设置 `CAREER_CREDENTIAL_MASTER_KEY` 时，API 会在此卷自动创建并复用模型凭据主密钥。
6. 微信公众平台的 API IP 白名单加入该服务器的固定出口 IP；当前项目仅主动调用微信接口，不需要配置公众号入站消息回调。
7. 需要启用面经库“全网公开信息收集”时，在服务器 `.env.production` 配置 `FIRECRAWL_API_KEY`；Hosted Firecrawl 使用默认 `FIRECRAWL_API_URL=https://api.firecrawl.dev`。

## 首次部署

```bash
git clone git@github.com:HBelike/ZhiTan.git /opt/zhitan
cd /opt/zhitan
cp .env.production.example .env.production
chmod 600 .env.production
# 编辑 .env.production，填写域名、数据库密码、账号邮件服务、微信与模型凭据。
# 首次部署先保持 VIDEO_SUBMIT_ENABLED=false、AUDIO_ENABLED=false；媒体前置检查通过后再显式开启。
# CAREER_CREDENTIAL_MASTER_KEY 可留空，API 首次启动会在 application_data 卷自动创建。
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml ps
# 仅升级自旧版本时执行：将历史 plaintext_api_key 转为 Fernet 密文。
docker compose --env-file .env.production -f docker-compose.production.yml run --rm career-api python scripts/migrate_career_legacy_credentials.py
```

首次启动后，Caddy 会在 DNS 已生效且 80/443 可达时自动获取证书。迁移服务成功退出后，API、Web 与 Scheduler 才会运行。

`skill-seed` 也必须以退出码 0 完成：它只将 `deploy/skill-seeds` 中目标卷尚不存在的
`SKILL.md` 增量写入 `application_skills`，因此可安全地随每次镜像重建运行。不要将开发机
的整套 `.codex`/插件目录拷贝到服务器；如需增加项目 Skill，应先在开发机审查后通过
`scripts/export_portable_skills.py` 导出到 `deploy/skill-seeds` 并提交。详细规则见
[`deploy/skill-seeds/README.md`](../deploy/skill-seeds/README.md)。

模型凭据迁移脚本只输出数量；看到 `pending_after=0` 后，确认 `.env.production` 中 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS=false`（或不设置）。若输出 `legacy_unknown` 大于 0，代表早期没有可验证格式的旧密文，不能强行转换，必须在模型设置页重新填写这些 API Key。

## 验证与验收

```bash
curl -fsS https://<你的域名>/api/health
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 career-migrate
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 skill-seed
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 pipeline-scheduler
docker compose --env-file .env.production -f docker-compose.production.yml ps
docker compose --env-file .env.production -f docker-compose.production.yml exec career-api printenv VIDEO_SUBMIT_ENABLED AUDIO_ENABLED
docker compose --env-file .env.production -f docker-compose.production.yml exec pipeline-scheduler printenv VIDEO_SUBMIT_ENABLED AUDIO_ENABLED
docker compose --env-file .env.production -f docker-compose.production.yml exec career-api python scripts/check_media_production_readiness.py
```

验收顺序：`skill-seed` 日志显示已导入或保留种子 → HTTPS 页面可访问 → 邮箱 bootstrap 创建首个管理员 → 登录后打开技能库并确认 `find-skills` 等项目本地 Skill 可见且可编辑 → 访问工作台与 Career → 验证 PostgreSQL 会话持久化 → 查看 Scheduler 日志中的下次周五执行时间 → 运行不发起外部请求的媒体前置检查 → 创建微信草稿 → 确认首次备份可恢复。

## 备份与后续边界

- 每日对 PostgreSQL 执行 `pg_dump -Fc`，并备份 `application_data`（SQLite）、`application_outputs`（媒体）与 `application_skills`（编辑后的 Skill）到异地对象存储；保留至少 7 个日备份与 4 个周备份。
- Caddy 的 `caddy_data`、`caddy_config` 卷必须保留，否则会丢失证书账户状态。
- 首发不启用 GPU Docling；有 NVIDIA GPU 的服务器再使用 `document-processing` profile。
- 仓库和本地默认配置保持 `video.submit_enabled=false`、`audio.enabled=false`，避免开发调试产生费用。当前生产环境显式设置 `VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`，运行完整音视频链路；每次调整后必须重建 API 与 Scheduler 并执行上面的容器内检查。
- 旧公众号 SQLite 工作流、Skill 文件持久化、更细粒度的用户资源授权、Docker secrets 与 CI/CD 是下一阶段演进项，不应在未验证的情况下横向扩容。

## 2026-08-26 全量生产部署记录

- **发布目标**：将求职助手招呼语学历/成果链接强化、多岗位逐岗触发发送、面经归属编辑、输入框 Skill/面经内联高亮、空指标过滤、路由标题盒与面经导入操作盒统一发布到 `your-domain.example`。
- **发布版本**：业务代码提交 `5618965`，生产仓库从 `0afb857` 快进更新；本地 `data/`、临时 PDF、缓存、本机 `.agents/skills` 和 `src/test.py` 未进入提交或生产镜像。
- **发布前验证**：Python 全量 `244 passed`，WebUI 全量 `138 passed`，浏览器扩展全量 `21 passed`；Service Worker 语法检查、求职助手部署配置检查、Vite production build 和扩展 `0.2.7` 确定性打包通过。
- **部署调用链**：生产 `.env.production` 配置展开通过 → `git pull --ff-only origin main` → `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 重建完成。
- **数据库与运行状态**：Alembic 为 `20260825_21 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；`VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`、`CAREER_ALLOW_PAID_PROFILES=true` 保持不变。
- **公网验收**：`/api/health`、首页、职位库和浏览器助手教程均返回 `200`；匿名面经树与招呼语生成接口均返回 `401`。线上主 JS/CSS 已确认包含逐岗触发文案、面经公司/岗位编辑、上传入口和 Skill/面经高亮样式。
- **扩展验收**：`find-job-boss-helper-v0.2.7.zip` 返回 `200`，线上与本地文件均为 `15249` 字节，SHA-256 均为 `59E3DDE5133F172621EEBB3A3DE811CA63E784F793F5EE4D1CDCD1C6B81C9172`。
- **外部调用边界**：本次未主动调用付费模型、未触发微信草稿、Seedance 或 TTS 任务，也未使用真人 BOSS 账号发送招呼语。

## 2026-08-27 全量生产部署记录

- **发布目标**：发布求职助手上下文治理与用户记忆停用、PC 响应式首道门禁、全网公开面经采集、小红书浏览器采集、登录首页和面经库交互更新，以及浏览器助手 `0.2.9`。
- **发布版本**：业务代码提交 `e7ff69b`；生产仓库从 `e3a169f` 快进更新。本机 `.agents/skills`、个人 `data/`、临时测试文件、Playwright 产物、重复 pnpm 锁文件和旧版扩展 `0.2.8` 未进入发布。
- **发布前验证**：Python `368 passed`，WebUI `184 passed`，浏览器扩展 `28 passed`，Playwright Chromium/Firefox 响应式验收 `7 passed`；Vite production build、部署设置、Compose 配置、扩展语法、公开面经与小红书编排合约均通过。
- **部署调用链**：生产 Compose 配置展开通过 → `git pull --ff-only origin main` → `docker compose ... up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260825_21` 连续迁移到 `20260827_26 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；Scheduler 已注册下一次周五 08:00 生产与 09:00 草稿任务。
- **Firecrawl**：生产 `.env.production` 已以不回显方式注入 `FIRECRAWL_API_KEY`，API 容器读取成功；本地与生产容器各执行一次最小 `/search` + `/scrape` smoke test，均返回 `public_web_firecrawl_smoke_ok`，未写业务数据库。
- **公网验收**：健康接口、首页、求职助手、面经库、职位库、扩展教程和 `0.2.9` ZIP 均返回 `200`；匿名记忆与公开面经任务接口均返回 `401`。线上扩展为 `21948` 字节，SHA-256 为 `060FDB174E668B0E15B461DB176FF77871EC71A4760FCE4F6424B15904528D0F`，与本地一致。
- **外部调用边界**：除上述两次 Firecrawl 最小验证外，未调用聊天模型、未触发微信草稿、Seedance、TTS 或真人 BOSS 招呼语发送。

## 2026-08-27 动态内容全量生产部署记录

- **发布目标**：发布动态 Top N 的 N+1 周榜文章编排、逐项目视觉简报与图片数量、自适应模型上下文容量、管理台图片/视频/语音开关，以及 PC 端归档原文、图片预览和 API Key 显隐交互。
- **发布版本**：业务代码提交 `51446e6`，生产仓库从 `84988ba` 快进更新；本地插件 Skill、个人 `data/`、临时 `src/test.py`、重复 pnpm 文件、Playwright 产物和旧版扩展 ZIP 未进入发布。
- **发布前验证**：Python `395 passed`，WebUI `195 passed`，浏览器扩展 `28 passed`；Vite production build、变更契约脚本、部署设置、Alembic 单一 head、源码编译、补丁密钥扫描与 Compose 配置均通过。
- **部署调用链**：推送 GitHub `main` → 生产 `git pull --ff-only origin main` → `docker compose ... up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260827_26` 升级到 `20260827_27 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；Scheduler 已注册次日周五 08:00 生产与 09:00 草稿任务，启动日志未出现 `ERROR`、`Traceback` 或 `Exception`。
- **Skill 持久卷**：`skill-seed` 按既有规则跳过 57 个已存在 Skill；哈希确认 `github-project-blog` 持久副本与上一版种子完全一致且没有线上编辑后，仅将该文件更新到本次种子，更新后两者 SHA-256 均为 `EE675749BF08783A1EE51E29FC25A81E4C7610CE3256EDD92B7B122425DF8997`。
- **公网验收**：健康接口与首页、求职助手、面经库、职位库、工作台均返回 `200`；匿名会话和面经树接口均返回 `401`。线上主资源 `/assets/index-BG2ekzHc.js` 已确认包含“显示 API Key”“文章原文”“图片生成”。
- **生产开关与外部调用边界**：API 的 `VIDEO_SUBMIT_ENABLED`、`AUDIO_ENABLED`、`CAREER_ALLOW_PAID_PROFILES`、`PLATFORM_AUTH_REQUIRED`、`CAREER_REDACTION_ENABLED` 以及 Scheduler 的音视频开关均保持 `true`；本次未主动调用聊天模型、Firecrawl、微信草稿、Seedance、TTS 或真人 BOSS 发送动作。

## 2026-08-28 全量生产部署记录

- **发布目标**：发布技术文章确定性图稿规格、HTML 模板与 Gotenberg 截图链路，面经创建者归属、管理员维护与删除能力，旧默认求职历史归属修复，登录后认证路由刷新修复，Provider 实际模型标识留存，以及求职对话自动滚动、公开面经采集上限和审核台生成方案展示更新。
- **发布版本**：业务代码提交 `60253cd`，同时包含确定性图稿设计与实施文档提交 `d4789e0`、`e024079`；生产仓库从 `38534fe` 快进更新。个人 `data/`、本机新增插件目录、临时 `src/test.py` 和旧版扩展 `0.2.8` 未进入发布。
- **发布前验证**：Python 全量 `517 passed`，WebUI 全量 `205 passed`，浏览器扩展全量 `28 passed`；Vite production build、求职助手部署设置、Alembic 单一 head、受控源码编译、补丁凭据扫描与生产 Compose 配置均通过。
- **部署调用链**：推送 GitHub `main` → 生产 `git pull --ff-only origin main` → `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler 与 Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260827_27` 连续迁移到 `20260828_30 (head)`；旧默认 Actor 的求职会话剩余 `0` 条，固定管理员仍为唯一有效管理员；两张面经表的创建者字段与模型用量表的 Provider 模型字段均已落库。API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行，启动后日志未出现 `ERROR`、`Traceback` 或 `Exception`。
- **确定性图稿与 Skill 持久卷**：API 镜像内字体文件和确定性渲染模块加载通过，媒体生产 readiness 无 blocker。`github-project-blog` 与 `grill-me` 的线上持久副本均与上一版种子哈希一致，确认没有线上编辑后才同步到新种子；同步后 API 与 Scheduler 读取哈希一致。
- **公网验收**：健康接口返回 `career_runtime_revision=2026-08-28-interview-admin-permissions-v2`；首页、求职助手、面经库和审核台均返回 `200`，匿名面经树、删除面经与创建公开采集任务均返回 `401`。线上主资源 `/assets/index-M0V9WIBS.js` 已确认包含图片总结方案、实际调用模型、面经删除、维护权限与默认 10 条采集文案。
- **生产开关与外部调用边界**：API 的 `VIDEO_SUBMIT_ENABLED`、`AUDIO_ENABLED`、`CAREER_ALLOW_PAID_PROFILES`、`PLATFORM_AUTH_REQUIRED`、`CAREER_REDACTION_ENABLED` 以及 Scheduler 的音视频开关均保持 `true`；本次只执行无付费请求的 readiness 检查，未主动调用聊天模型、Firecrawl、微信草稿、Seedream、TTS 或真人 BOSS 发送动作。
