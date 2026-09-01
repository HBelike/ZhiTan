# 求职助手运行与模型接入说明

## 已实现的处理链路

```text
WebUI 对话 / PDF / 图片 / 职位链接
  -> 临时附件校验与解析
  -> PDF 文本层探测（按需调用独立 Docling OCR/版面服务）
  -> 图片：Docling OCR 质量门 -> 必要时平台内置视觉模型云端复核
  -> ResumeNormalizer 简历章节归类（仅当前 Turn）
  -> 公开职位页安全读取
  -> 按隐私配置处理后的 LangGraph 输入图
  -> 免费模型路由
  -> OpenAI-compatible Provider 调用
  -> 对话历史与 Turn 状态写入 PostgreSQL（记录是否已脱敏）
  -> 删除原始临时文件
```

- 简历 PDF、JPG、PNG、WebP 单文件上限为 10MB；文件只会放在 `data/career-temporary-attachments` 的单次任务目录。
- PDF 会提取有限文本。图片先经 Docling OCR；OCR 文本不足时由平台内置视觉模型自动理解，再仅把结构化文本交给已选的聊天模型。用户无需、也不能通过模型下拉框决定是否具备图片理解能力。
- 扫描型或文本层不完整 PDF 会先由 `DocumentProbe` 识别；启用 Docling 服务后，系统以匿名文件名上传并取得 Markdown 结构文本。服务与 Web 后端分离部署，原文件仍在本轮结束后删除。
- 简历文本会在按隐私配置处理前由 `ResumeNormalizer` 按工作、项目、教育、技能等标题归类；无法确定的内容会作为“待确认片段”交给模型谨慎追问，而不会被编造为简历事实。
- 职位链接仅接受公开 `http`/`https` 页面，拒绝本机、私网、非标准端口和过大页面；原始 URL 与网页正文不会写入历史。
- PostgreSQL 保存会话、模型档案、运行状态和按 `privacy.redaction_enabled` 处理后的用户/助手消息；当前个人配置关闭脱敏，但仍不保存简历、PDF 或其他附件原件。

## 本地启动

1. 复制 `.env.career-assistant.example` 为 `.env.career-assistant`，设置 PostgreSQL 连接信息与模型额度 Key。
2. 启动数据库：`docker compose --env-file .env.career-assistant -f docker-compose.career-assistant.yml up -d`。
3. 执行既有迁移命令，确保 `career_assistant` schema 已创建。
4. 首次开发前安装热更新依赖：`.\.venv\Scripts\python.exe -m pip install -r requirements-development.txt`。本地后端唯一启动命令是 `scripts\start_dev_backend.ps1`：脚本以前台方式启动 Uvicorn API，同时以隐藏的独立进程启动 `career-agent-worker`，API 退出时自动清理对应 Worker。API 使用 `watchfiles` 监视 `src/**/*.py` 与 `config/**/*.yaml`，并在 Windows 工作区强制使用轮询模式；前端通过 `web-ui/vite.config.js` 代理到固定端口 `18080`。不要直接只运行 `preview_server.py`，否则 Turn 会入队但无人消费；也不要同时启动多个本地后端实例，避免浏览器命中旧进程。
5. 启动前端：在 `web-ui` 下执行 `npm run dev`，访问 `http://127.0.0.1:5173/career`。

### 长回复配置

`config/career_assistant.yaml` 中的 `response_generation` 用于控制求职助手的单次回复：默认允许模型生成 `4096` Token，模型请求超时为 `90` 秒，完整回复最多持久化 `30000` 字符。使用 `preview_server.py` 启动时，`src/**/*.py` 与 `config/**/*.yaml` 变更会自动重载；新增或修改 `.env.career-assistant` 后应重启一次开发后端，以重新读取 Secret。

### 开发后端版本核验

当页面行为与当前代码不一致时，先访问 `http://127.0.0.1:18080/api/health`。当前版本必须包含：

```json
{"career_runtime_revision":"2026-08-10-platform-access-v1"}
```

没有该字段，或字段不是这个值，表示端口仍由旧后端占用。先关闭旧服务终端，再只执行一次 `scripts\start_dev_backend.ps1`；前端 Vite 进程无需因 Python 源码变更而重启。`web-ui/vite.config.js` 已固定从 `web-ui` 自身读取 `.env.local`，避免从项目根目录执行 npm 时丢失代理配置。

## 免费模型档案配置

模型档案只存元数据，不存 API Key。页面“模型设置”可创建或切换档案；Provider 的 Key 只能放在服务端环境变量中。

| Provider | 环境变量 | 默认兼容端点 | 用途 |
| --- | --- | --- | --- |
| Groq | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | 文本或 Vision（以平台实际模型能力为准） |
| Mistral AI | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | Free mode 文本模型 |
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | 选择带免费额度的文本或 Vision 模型 |
| Gemini | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` | 文本或 Vision |

创建档案时：

- 费用属性选择“免费额度”；付费档案会被全局策略拦截。
- PDF/纯文本只需勾选“文本理解”。
- 图片简历的识别由平台固定的“Docling 优先、Qwen 自动复核”链路完成；聊天档案只需具备“文本理解”，不再以图片能力作为路由前提。
- 若 Provider 不在内置表中，可填写其 OpenAI-compatible `API Base URL`，同时在 `config/career_assistant.yaml` 中登记对应的 Key 环境变量名后再启用。

## 2026-08-07：材料格式与个人隐私开关

- 支持格式：PDF、DOC、DOCX、XLS、XLSX、JPG、PNG、WebP、BMP、TIFF；单文件最大 10MB。
- PDF 最多 90 页。DOCX/XLSX 由 Docling 直接解析，DOC/XLS 由 Gotenberg 中的 LibreOffice 临时转为 PDF 后解析；容器端口仅绑定到本机 `127.0.0.1`。
- 图片材料先进入 Docling OCR；低质量时自动进入平台视觉模型复核。BMP/TIFF 会在内存规范化为 PNG，原始图片不会持久化。
- DeepSeek `deepseek-v4-pro` 保存为已就绪的“免费额度”档案后，会参与“免费额度优先”和“指定模型”两种路由；它只处理解析后的文本。为避免默认思考模式吞掉常规求职问答的输出预算，常规聊天请求会发送 `thinking: {"type": "disabled"}`，复杂推理模式将在独立产品开关中提供。
- 2026-08-07 使用真实图片简历完成端到端验收：`qwen3.6-flash` 走云端图片理解并提取 3437 字，`deepseek-v4-pro` 生成 787 字分析回复，Turn 成功；原始附件与测试会话均已删除。浏览器请使用 `http://127.0.0.1:5173/career`，旧的 `5175` 开发实例仍会指向旧后端，不应用于验收。
- 当前个人配置为 `privacy.redaction_enabled: false`：聊天历史保存原始对话文本，数据库字段 `is_redacted` 会标记为 `false`。附件原件、转换后的 PDF、完整 OCR/Markdown 仍不会入库，并在当前 Turn 清理。公网部署前应开启脱敏。

启动文档解析依赖：

```powershell
docker compose --env-file .env.career-assistant -f docker-compose.document-processing.yml up -d
```

使用真实 Office 样本进行服务联调：

```powershell
.venv\Scripts\python.exe scripts\verify_career_docling_office_live_service.py --fixtures-dir C:\path\to\fixtures
.venv\Scripts\python.exe scripts\verify_career_gotenberg_live_service.py --fixtures-dir C:\path\to\fixtures
```

第一个目录需要含有 `word_sample.docx`、`xlsx_01.xlsx`，第二个目录需要含有 `legacy_sample.doc`、`legacy_sample.xls`。它们只用于本机联调，不是运行时依赖。

## 验证命令

```powershell
.venv\Scripts\python.exe scripts\verify_career_attachment_api.py
.venv\Scripts\python.exe scripts\verify_career_image_intake.py
.venv\Scripts\python.exe scripts\verify_career_job_source.py
.venv\Scripts\python.exe scripts\verify_career_model_client.py
.venv\Scripts\python.exe scripts\verify_career_cloud_vision.py
.venv\Scripts\python.exe scripts\verify_career_resume_normalizer.py
.venv\Scripts\python.exe scripts\verify_career_docling_live_service.py
.venv\Scripts\python.exe scripts\verify_career_attachment_api.py
.venv\Scripts\python.exe scripts\verify_career_pdf_page_limit.py
```

这些验证不会调用真实模型额度；数据库验证会自动删除其测试会话。

## 启用扫描 PDF / 复杂简历解析

1. 部署 Docling Serve 服务。本项目提供 [docker-compose.document-processing.yml](../docker-compose.document-processing.yml)，用于 RTX 5070 的本机 GPU 开发环境；默认只运行一个解析 worker、单份 PDF 最多 90 页，并且端口仅绑定到 `127.0.0.1`。生产环境应将服务与 Web 后端拆分部署，并按吞吐量选择本地线程池或 Redis/RQ worker。
2. 在 `config/career_assistant.yaml` 中将 `career_assistant.document_processing.docling_serve.enabled` 改为 `true`，并将 `service_base_url` 指向服务根地址；本机默认示例为 `http://127.0.0.1:5001`。
3. 若 Docling 服务配置了 `DOCLING_SERVE_API_KEY`，在 `.env.career-assistant` 设置 `CAREER_DOCLING_API_KEY`，并在 YAML 的 `api_key_env` 填 `CAREER_DOCLING_API_KEY`。
4. 在项目根目录执行：

   ```powershell
   docker compose --env-file .env.career-assistant -f docker-compose.document-processing.yml up -d
   ```

   首次启动需要拉取官方 CUDA 镜像和下载解析模型，镜像约 11GB；确认 `http://127.0.0.1:5001/docs` 可访问后，再将 YAML 中的 `enabled` 改为 `true`。
5. 上传扫描 PDF。系统会在当前 SSE 任务中先显示“正在解析附件”，再把 Docling 返回的 Markdown 交给模型分析；若图片 OCR 不足，系统会自动调用服务端配置的视觉模型，不要求切换聊天模型。

部署基线、接口字段和异步服务能力以 [Docling 官方 REST API](https://docling-project.github.io/docling/usage/api_server/rest_api/) 与 [官方部署文档](https://docling-project.github.io/docling/usage/api_server/deployment/) 为准。当前适配器使用同步上传接口，后续在高并发部署阶段切换为异步任务轮询或 Redis/RQ worker。

## 平台内置云端图片理解

该能力不出现在用户模型选择框中。部署时只需在服务器 Secret 中提供：

- `CAREER_DOCUMENT_VISION_API_KEY`：阿里云百炼 API Key；
- `CAREER_DOCUMENT_VISION_BASE_URL`：创建 Key 时控制台展示的 Workspace API Host，格式为 `https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。

视觉模型由 `config/career_assistant.yaml` 的 `document_processing.cloud_vision` 管理，而不是由聊天页下拉框决定：

- `active_connection_key`：当前生产主模型。默认是实测可用的 `aliyun-qwen-primary` / `qwen3.6-flash`；百炼已将 `qwen3-vl-flash` 标为旧模型并公告于 2026-10-10 下线。
- `connections`：可预先登记多条 OpenAI-compatible 图片理解连接；每条连接只保存 Key 与 API Host 的环境变量名称。
- `fallback_connection_keys`：默认空。只有管理员显式启用并添加的备用项，才会在主连接发生可恢复故障时调用，避免隐式消耗另一个供应商的额度。

例如，要将复杂图表复核切换到已预置的高准确率连接，只需在服务器配置中设置：

```yaml
cloud_vision:
  active_connection_key: aliyun-qwen-high-accuracy
```

之后重新加载服务配置即可；不需要改附件、对话模型或数据库结构。平台不会将任何 Key 返回给浏览器或写进 PostgreSQL。
