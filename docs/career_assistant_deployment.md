# 求职助手部署说明

## 目标与边界

本部署方案面向后续个人平台上线：Vue WebUI、FastAPI API、PostgreSQL 与可选的 Docling 文档服务均运行在容器内；原始简历只在 API 容器的临时文件系统中存在，不写入 PostgreSQL。

- PostgreSQL 仅保存会话、消息、任务状态和必要的解析摘要。
- `career-web` 默认只监听宿主机 `127.0.0.1:8080`，应由 Caddy 或 Nginx 统一提供 HTTPS。
- WebUI 通过同源 `/api/` 访问 API；SSE 已关闭 Nginx 缓冲，流式回复不被反向代理攒包。
- API 是单进程容器，保留当前断线恢复与进程内并发闸门的语义；横向扩容前应将并发闸门和任务状态迁移为共享实现。

## 首次部署

1. 准备一台安装 Docker Engine 与 Docker Compose v2 的 Linux 服务器。
2. 在项目根目录复制环境变量模板：

   ```bash
   cp .env.production.example .env.production
   ```

3. 修改 `.env.production`：

   - 为 `CAREER_POSTGRES_PASSWORD` 设置高强度随机密码。
   - 填写已申请的 LLM 网关 Key；模型连接也可后续在页面配置。
   - 如需云端图片理解，填写 `CAREER_DOCUMENT_VISION_API_KEY` 与 `CAREER_DOCUMENT_VISION_BASE_URL`。
   - 保持 `CAREER_REDACTION_ENABLED=true`，避免生产环境历史文本含有可直接识别的个人信息。

4. 构建并启动基础服务：

   ```bash
   docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
   ```

5. 检查状态与健康检查：

   ```bash
   docker compose --env-file .env.production -f docker-compose.production.yml ps
   curl http://127.0.0.1:8080/api/health
   ```

`career-migrate` 成功退出后，`career-api` 才会启动；如果迁移失败，请查看 `career-migrate` 的日志，勿直接删除数据库卷。

## 文档解析策略

基础实例默认启用 Gotenberg：DOC/XLS 会转换成 PDF 后再进入现有解析链。DOCX/XLSX、PDF、图片文件可直接处理。

- 纯文本或结构清晰的 Office/PDF：应用内解析器优先处理，成本最低。
- 双栏、扫描件、复杂表格 PDF：可启用 Docling 服务；CPU 可用但速度较慢，GPU 适合高吞吐场景。
- 图片、扫描页 OCR 置信度不足：使用固定的云端视觉模型兜底；它不是用户可切换的聊天模型。

启用 CPU Docling（无 GPU 服务器的推荐起步方案）：

```bash
# .env.production 内设为 CAREER_DOCLING_ENABLED=true、DOCLING_CPU_THREADS=1
docker compose --env-file .env.production -f docker-compose.production.yml \
  --profile document-processing up -d career-docling career-api pipeline-scheduler
```

CPU Docling 使用单 worker、单线程起步，能处理扫描 PDF、DOCX/XLSX 与图片 OCR；初次启动会下载较大的模型镜像，因此先确认服务器磁盘至少留有 15 GB 空闲空间。复杂长文档的处理时间会明显高于 GPU，应避免同时上传多份扫描件。确认稳定后，可把 `DOCLING_CPU_THREADS` 逐步调到 `2`。

没有启用 Docling 时，纯文本 PDF 仍由应用内解析器提取，DOC/XLS 仍可经 Gotenberg 转 PDF；但扫描件、DOCX/XLSX 与图片的完整识别依赖 Docling 或已配置的云端视觉模型。

启用有 NVIDIA GPU 的 Docling（需宿主机已安装 NVIDIA 驱动与 NVIDIA Container Toolkit）：

```bash
docker compose --env-file .env.production \
  -f docker-compose.production.yml \
  -f docker-compose.production.gpu.yml \
  --profile document-processing up -d career-docling career-api pipeline-scheduler
```

## 容量基线

个人平台起步建议 2 vCPU / 4 GB 内存 / 40 GB SSD。若长期处理复杂扫描 PDF，建议把 API 与 Docling 分开部署，或至少升级到 4 vCPU / 8 GB 内存；GPU Docling 只在有 NVIDIA GPU 且文档量较大时启用。

磁盘主要被 PostgreSQL 的会话历史、容器镜像、日志和运营产物占用。临时附件使用 API 容器的 `tmpfs`，容器重启或任务完成均不会保留原文件。应为数据库卷配置定期备份与监控告警。

## 发布前检查

```bash
python scripts/verify_career_deployment_settings.py
docker compose --env-file .env.production -f docker-compose.production.yml config
```

在本机开发模式中，后端修改由 `.\.venv\Scripts\python.exe preview_server.py` 启动的自动重载进程发现；容器生产环境使用镜像发布，不启用源码热重载。

## 回滚与排障

- 查看 API 日志：`docker compose -f docker-compose.production.yml logs -f career-api`
- 查看数据库迁移：`docker compose -f docker-compose.production.yml logs career-migrate`
- 流式中断时，浏览器重新打开相同会话会读取数据库中最新 Turn 并恢复展示。
- 不要把 `.env.production` 或任何真实 API Key 提交到 Git。

## 2026-08-07 实施验证记录

- 配置展开自检通过：本地默认地址与 Docker 服务地址、脱敏开关、Docling/Gotenberg 启停开关均可由环境变量切换。
- FastAPI 源码在独立 8014 端口完成真实健康检查，返回版本 `2026-08-07-stream-resilience-v3`。
- 求职助手回归通过：会话持久化、附件 API、Docling 降级、图片入口、DOC/DOCX/XLS/XLSX 类型路由、PDF 90 页限制、模型连接、SSE 心跳、重试策略与断线恢复均已验证。
- Vue 生产构建通过。
- Docker Compose 语法与服务依赖图通过 `docker compose config` 验证。实际镜像构建两次均在拉取 Docker Hub 的 Python/Node/Nginx 基础镜像阶段遇到外部 TLS/授权网络超时，尚未进入项目源码或依赖构建；网络恢复后应重新执行一次 `docker compose ... build` 完成最后一层验证。
