# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

个人平台项目，包含四个独立模块：GitHub 周榜公众号自动工作流、AI 求职助手、面经库（RAG）、技能库与管理台。目标部署形态为单机 Docker Compose 生产环境（香港 Ubuntu VPS，Caddy HTTPS → Nginx/Vue → FastAPI → PostgreSQL + SQLite）。

## 常用命令

### 开发环境

```powershell
# 安装 Python 依赖（在 .venv 中）
.\.venv\Scripts\python -m pip install -r requirements.txt

# 安装开发依赖（含 watchfiles 热重载）
.\.venv\Scripts\python -m pip install -r requirements-development.txt

# 启动 Web API 后端（唯一入口，监听 127.0.0.1:18080，自动重载 src/ 和 config/）
scripts\start_dev_backend.ps1

# 运行一次完整周榜工作流（无需 API Key 的免费兜底链路）
.\.venv\Scripts\python main.py

# 启动前端开发服务器
cd web-ui
npm run dev
```

前端开发访问 `http://127.0.0.1:5173/career`（求职助手）或 `http://127.0.0.1:5173/`（审核台）。Vite 代理固定指向 18080 端口。不允许同时启动多个 Web API 实例。

### 本地数据库

```powershell
# 启动求职助手依赖的 PostgreSQL + pgvector
docker compose --env-file .env.career-assistant -f docker-compose.career-assistant.yml up -d

# 启动文档解析服务（Docling + Gotenberg，用于简历 PDF/Office 解析）
docker compose --env-file .env.career-assistant -f docker-compose.document-processing.yml up -d

# 执行数据库迁移
alembic upgrade head
```

### 验证脚本

验证脚本位于 `scripts/verify_*.py`，均可在无外部 API Key 的情况下运行，不会消耗额度：

```powershell
# 核心模块验证
.\.venv\Scripts\python scripts\verify_career_attachment_api.py      # 附件处理 API
.\.venv\Scripts\python scripts\verify_career_intake_graph.py        # 求职对话图（含脱敏/历史写入/临时文件清理）
.\.venv\Scripts\python scripts\verify_career_model_client.py        # 模型客户端契约
.\.venv\Scripts\python scripts\verify_career_model_gateway.py       # 模型网关路由
.\.venv\Scripts\python scripts\verify_career_cloud_vision.py        # 云端图片理解
.\.venv\Scripts\python scripts\verify_interview_library.py          # 面经库 RAG
.\.venv\Scripts\python scripts\verify_video_assembly.py             # FFmpeg 视频合成
.\.venv\Scripts\python scripts\verify_video_frame_sampler.py        # 视频关键帧采样
.\.venv\Scripts\python scripts\verify_doubao_tts_v3_contract.py     # 豆包 TTS 协议（离线）
.\.venv\Scripts\python scripts\verify_platform_first_admin_bootstrap.py  # 管理员引导
.\.venv\Scripts\python scripts\verify_pipeline_execution_lock.py    # 流水线互斥锁
```

### 生产部署

```bash
# 首次部署
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build

# 健康检查
curl -fsS https://<域名>/api/health
# 预期包含 "career_runtime_revision":"2026-08-10-platform-access-v1"

# 备份
pg_dump -Fc $CAREER_POSTGRES_DB > backup_$(date +%Y%m%d).dump
```

## 架构

### 双数据库策略

| 数据库 | 用途 | 模块 |
|--------|------|------|
| SQLite (`data/app.db`) | 周榜工作流、审核、素材索引 | GitHub 周榜公众号（旧模块） |
| PostgreSQL + pgvector | 会话、账号、面经向量、模型档案、运行配置 | 求职助手、面经库、平台管理（新模块） |

周榜 Scheduler 只能单实例运行（受 SQLite 限制），与人工手动流水线通过文件锁互斥。

### 模块边界

四个模块互相独立，不交叉依赖：

1. **GitHub 周榜公众号工作流** (`src/tasks/`, `src/scheduler/`, `src/providers/`)
   - 每周五 08:00 搜索 GitHub 热门项目 → 生成文章/图片/视频 → 09:00 创建微信草稿 → 人工审核 → 发布
   - Provider 降级链：商业 API（Seedream/Seedance/豆包 TTS）→ 免费兜底（GitHub 素材/Pillow/edge-tts/本地 FFmpeg）
   - `video.submit_enabled` 控制是否创建付费 Seedance 任务

2. **求职助手** (`src/career_assistant/`)
   - 独立 Agent 对话系统，支持 PDF/图片/Office 简历解析 + 职位链接分析
   - 附件临时存储，Turn 结束后删除；不持久化原始简历文件
   - 模型路由：免费额度优先；图片理解由平台固定链路（Docling → Qwen Vision）处理，不由用户选择
   - 入口：`/career`

3. **面经库** (`src/career_assistant/interview_library/`)
   - 基于 PostgreSQL + pgvector 的 RAG 系统
   - 支持关键词召回 + 可选向量增强
   - 支持小红书面经导入

4. **平台管理与技能库** (`src/platform_access/`, `deploy/skill-seeds/`)
   - 账号/角色/会话管理（scrypt 密码、HttpOnly Cookie）
   - 运行配置版本快照（JSONB）
   - Claude Code Skills 种子 → 持久卷，WebUI 编辑优先于重新部署

### 周榜流水线任务调用链

```
SearchTask → SummaryTask → ShortVideoPromptTask → ImageTask → VideoClipPlanTask
→ AudioTask → StorageTask → SeedanceClipTask → SeedanceClipStatusTask
→ VideoVisualQualityTask → VideoNarrationTimelineTask → SegmentedAudioTask
→ VideoAssemblyTask → PreviewTask → ArticleLayoutTask → DeliverTask → CatTask
```

关键设计决策：视频先生成，旁白后对齐（`video_first_spec_bound` 模式），避免"口播在讲项目 A，画面已切到项目 B"。

### 关键配置

- `.env` / `.env.career-assistant` / `.env.production`：密钥（API Key、数据库密码）
- `config/app.yaml`：周榜工作流参数
- `config/career_assistant.yaml`：求职助手参数（模型超时、并发限制、文档解析开关、隐私开关）
- 所有 Provider 遵循统一模式：优先读取对应 `*_API_KEY` 环境变量 → `skip_when_api_key_missing: true` 时静默跳过 → `local_fallback_enabled: true` 时使用免费兜底

### Provider 模式

所有外部服务通过 Provider 抽象接入，关键特征：
- 统一配置驱动（`app.yaml` 中的 `*_env` 字段声明 API Key 环境变量名）
- 序列化错误时返回安全回退，不抛原始 Provider 堆栈
- 可替换：业务代码声明能力需求（TEXT、VISION、OCR），不直接依赖具体厂商

## 项目约束

- 回复语言：中文（代码、变量名、技术术语除外）
- 未经用户确认，不一次性输出整个项目；每次实现一个可验证步骤
- 所有密钥只通过 `.env` 或外部配置注入，不写死在代码中
- 微信公众号默认只创建草稿，不自动发布；发布必须经过人工审核
- 云端托管 API 优先；本地 GPU 推理仅可作为开发兜底，不得成为生产唯一依赖
- 每个新模块或重大调整需更新对应决策文档（`docs/*.md`）
- 生产环境：`PLATFORM_AUTH_REQUIRED=true`，`CAREER_REDACTION_ENABLED=true`；普通用户按管理员保存的路由模块配置访问，管理员接口继续由服务端鉴权
