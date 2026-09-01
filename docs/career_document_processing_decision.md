# 求职助手文档理解与后续 RAG 决策

## 状态

已确认，作为求职助手后续改造的架构基线。

## 问题

当前 PDF 附件解析仅提取 PDF 内嵌文本层。扫描型 PDF、双栏排版、图片文字、复杂表格和手写内容会导致文本为空或阅读顺序错误，不能直接交给聊天模型。

## 已确认原则

- 先做文档理解，再做模型分析或 RAG；RAG 不能修复 OCR、版面或表格结构错误。
- 简历原文件、页面渲染图和 OCR 原文只允许在当前 Turn 的临时目录中存在；历史对话不保存这些原始材料。
- 通用文档解析能力要可复用于后续知识库；知识库才允许持久化结构化 Chunk 与向量索引。
- 低置信度内容必须给出明确状态与可人工确认的页面/区块，不能笼统提示“未收到文件”。
- 优先使用确定性解析节点；不使用多个自由 LLM Agent 对同一份简历反复猜测内容。
- 每个模块交付或调整都要同步维护本决策文档：记录目标、取舍、调用链、依赖、验证结果和未完成边界，作为未来部署和求职展示的可追溯材料。

## 推荐处理链路

```text
上传 PDF / 图片 / DOCX
  -> 文件安全检查与页数限制
  -> DocumentProbe（文本层、扫描特征、页数、旋转、布局复杂度）
  -> Document Understanding
       -> 简单文本：快速提取
       -> 双栏/复杂布局：版面与阅读顺序恢复
       -> 扫描页：OCR
       -> 低置信度页：备用 OCR 引擎复核
       -> 图片/图表：按需视觉模型复核
  -> Document Graph（页码、区块、坐标、置信度、阅读顺序）
  -> ResumeNormalizer（教育、工作、项目、技能、成果、待确认项）
  -> 脱敏后的当前 Turn 模型分析
  -> 删除所有临时材料
```

## 技术选择

- 主解析器：Docling。用于结构化 PDF、OCR、版面、阅读顺序、表格与 JSON/Markdown 导出。
- OCR 兜底：PaddleOCR PP-StructureV3。用于扫描页、中文 OCR、低置信度复核与表格结构化。
- 不作为当前主依赖：MinerU（许可证有额外条件）、视觉检索模型（成本与资源需求不适合单份临时简历）。
- 编排：LangGraph 的确定性节点图；仅在简历归纳与职业建议阶段调用 LLM。

### 2026-08-07：Office 与图片简历统一解析链路

- 目标：让求职助手实际接收并分析 PDF、Word、Excel 与常见图片简历，而不是仅在前端显示“已上传”。
- 方案：DOCX/XLSX 直接调用本地 Docling `POST /v1/convert/file` 并取得 Markdown；旧版 DOC/XLS 先经本地 Gotenberg（LibreOffice）临时转换为 PDF，再复用既有的 PDF 文本探测、OCR 与结构化解析。Docling 当前原生支持 DOCX/XLSX，不支持二进制 DOC/XLS，因此不把不受支持的格式伪装成可直接解析。
- 图片：支持 JPG、PNG、WebP、BMP、TIFF；BMP/TIFF 在内存中规范为 PNG 后才交给 Vision 模型，避免不同 Provider 对这两类格式的兼容性差异。图片原文件仍在当前 Turn 临时目录，结束后删除。
- 隐私：`privacy.redaction_enabled: false` 为当前个人部署关闭文本脱敏；对话记录按原文写入 PostgreSQL，并以 `is_redacted=false` 如实标记。原始附件、完整解析文本、临时 PDF 均不入库。若部署到公网或多人环境，应改回 `true`。
- 调用链：`CareerChannel` → `TemporaryAttachmentStore` → `AttachmentParser` →（DOCX/XLSX：Docling；DOC/XLS：Gotenberg → PDF → Docling；图片：Vision）→ `ResumeNormalizer` → `CareerResponseRunner`。
- 验证：使用 Docling 官方有效 Office 样本完成实测：DOCX 与 XLSX 均返回非空 Markdown；DOC 与 XLS 均成功经 Gotenberg 转换后被同一 `AttachmentParser` 提取。服务联调脚本改为要求传入真实 Office 样本，避免手工拼接的不完整 OOXML 文件造成假失败。
- 边界：单文件仍限制 10MB，PDF 仍限制 90 页；DOC/XLS 的临时转换后 PDF 限制为 20MB。加密、损坏或超限的文件会提供安全错误提示，不会被误报为“未收到附件”。

## RAG 边界

- 求职简历分析：不建立持久向量索引。
- 未来知识库：复用 Document Graph，执行结构感知 Chunk、Embedding、PostgreSQL + pgvector、混合检索与页码/坐标引用。

## 实施顺序

1. 建立可替换的解析器接口与解析质量评分。
2. 接入主解析器，识别扫描 PDF、双栏和低文本覆盖率。
3. 接入 OCR 兜底与按页状态流式反馈。
4. 建立 ResumeNormalizer 和低置信度人工确认界面。
5. 用匿名化测试集对比解析准确性、阅读顺序、字段召回和耗时。
6. 在知识库模块复用该解析结果并增加 RAG 索引。

## 实现记录

### 2026-08-06：DocumentProbe

- 目标：识别扫描型、部分扫描型和原生文本 PDF，避免将空文本误交给聊天模型。
- 实现：新增 `PdfDocumentProbe`，使用页级原生文本覆盖率、字符量和提取异常计算 `DocumentProbeResult`；结果写入当前 Turn 内存中的 `ParsedAttachment`，不落库。
- 调用链：`CareerIntakeGraph._parse_material` → `AttachmentParser._parse_pdf` → `PdfDocumentProbe.probe` → `ParsedAttachment.document_probe`。
- 依赖：仅复用现有 `pypdf`，不下载模型、不调用外部 API。
- 验证：`scripts/verify_career_document_probe.py` 和 `scripts/verify_career_attachment_api.py` 均通过。
- 边界：本步骤只提供路由信号；Docling/OCR 实际解析将在下一步骤接入。

### 2026-08-06：Docling Serve 适配器

- 目标：让扫描型、部分扫描型和复杂布局 PDF 能在不污染 Web 后端依赖的前提下进入真实 OCR 与版面恢复流程。
- 实现：新增 `DoclingServiceDocumentParser` 与 `StructuredDocumentParser` 契约。它按官方 `POST /v1/convert/file` multipart 协议上传匿名文件名 `document.pdf`，请求 Markdown、OCR、强制 OCR 与精确表格模式；只将解析后的 Markdown 留在当前 Turn 内存。
- 调用链：`CareerIntakeGraph._parse_material` → `AttachmentParser._parse_pdf` → `PdfDocumentProbe.probe` → `DoclingServiceDocumentParser.parse_pdf` → `ParsedAttachment.extracted_text` → 脱敏模型上下文。
- 配置：`config/career_assistant.yaml` 的 `document_processing.docling_serve`；默认 `enabled: false`，不会在未部署服务时拦截现有 PDF 文本路径。可选 API Key 仅从 `CAREER_DOCLING_API_KEY` 等环境变量读取。
- 取舍：不将完整 `docling` Python 包安装进 Web 后端。它会引入 PyTorch、OCR 与版面模型，开发机与未来 Web 容器都会变重；改为独立服务便于用 CPU/GPU、队列和限流独立扩展。
- 隐私：不向服务发送原始文件名；解析客户端不记录服务响应正文；`TemporaryAttachmentStore` 仍在成功、失败和超时后删除原始文件。
- 验证：`scripts/verify_career_docling_document_parser.py` 用 `httpx.MockTransport` 验证官方 multipart 字段、匿名文件名、401 安全错误映射，以及扫描件路由到结构化解析器。
- 边界：本步骤提供真实服务调用能力但默认未启用；下一步骤会提供可复现的本地/服务器 Docling 服务部署配置，并做真实扫描简历验收。

### 2026-08-06：Docling GPU 服务部署基线

- 目标：让 OCR 与版面模型独立于 FastAPI/WebUI 运行，并为未来个人网站部署保留可扩展边界。
- 实现：新增 `docker-compose.document-processing.yml`，固定到经清单验证的官方 `ghcr.io/docling-project/docling-serve-cu128:v1.21.0` 镜像，仅绑定 `127.0.0.1:5001`，限制本机单个解析 worker、90 页和 10MB 文件；使用 Docker NVIDIA runtime 与 RTX 5070。

## 2026-08-06：PDF 页数上限统一为 90 页

- Web 后端通过 `document_processing.max_pdf_pages` 在文件进入 OCR 前校验页数，默认 90 页；Docling 容器通过 `DOCLING_SERVE_MAX_NUM_PAGES` 使用相同上限，避免两层规则不一致。
- 90 页是单份附件的硬上限，不改变单文件 10MB 限制、单 worker 并发和临时文件自动清理策略。扫描件在 90 页内仍可能因 OCR 复杂度而接近超时；此时应优先拆分文档，而不是提高 Web 服务并发。
- Word（DOCX）与 Excel（XLSX）目前不在 API 白名单中，因此不会出现“上传成功但未解析”的假象。后续接入会复用 Docling 的通用文档转换接口，并分别增加版面、表格和公式的质量检测。
- 调用链：浏览器 → 求职助手 FastAPI → `http://127.0.0.1:5001/v1/convert/file` → Docling 本地计算引擎（CUDA）→ Markdown → 当前 Turn 模型上下文。
- 取舍：开发环境用单机 Local engine，降低资源占用和运维复杂度；后续公网部署/高并发再切 Redis/RQ worker，使 API 与解析 worker 独立扩容。
- 验证：Docker 引擎、NVIDIA runtime 与 `docker compose config` 已检测通过；官方 GPU 镜像 `v1.21.0` 标签已通过 manifest 校验。2026-08-06 已确认容器持续运行、本地 `/health` 和 `/openapi.json` 均返回 200、`/v1/convert/file` 存在，容器内可见 RTX 5070（驱动 591.86）。`scripts/verify_career_docling_live_service.py` 使用匿名中文扫描 PDF 成功完成 OCR；`scripts/verify_career_attachment_api.py` 成功走通真实 FastAPI 附件上传、Docling、ResumeNormalizer、脱敏历史和临时文件清理。
- 当前边界：服务仅绑定 `127.0.0.1`，不在容器或 PostgreSQL 保存用户简历原件与 OCR 正文；生产的模型缓存策略、限流和 Redis/RQ worker 扩容将在服务器部署阶段确定。

### 2026-08-06：ResumeNormalizer

- 目标：把原生 PDF 或 Docling 输出的 Markdown 简历归纳为工作经历、项目经历、教育经历、技能等稳定区块，使后续求职分析有明确输入结构，而不是把无序 OCR 文本直接交给模型猜测。
- 方案来源与取舍：参考 JSON Resume 的稳定字段模型（MIT，仓库已迁移/归档，因此仅借鉴 Schema 思想）与 Hiring Agent 的“解析、归一化、分析分层”做法；不复用其多轮 LLM JSON 提取，因为会增加 API 成本、延迟与简历隐私暴露面。`pyresume` 也未作为依赖引入：它以英文 PDF 文本模式为主，维护状态与中文复杂版面适配不足。
- 实现：新增无状态 `ResumeNormalizer`，只按 Markdown 标题与中英文常见章节别名做确定性分类，保留无法识别的内容为“待确认片段”，同时给出识别置信度。不会联网、不会调用模型、不会写数据库。
- 调用链：`AttachmentParser`（原生文本或 Docling Markdown）→ `CareerIntakeGraph._build_model_context` → `ResumeNormalizer.normalize` → `SensitiveDataRedactor` → `ModelTurnContext.redacted_resume_outline` → `CareerResponseRunner._build_prompt`。
- 隐私与边界：结构提纲仅存活在本轮内存；原始 PDF、Docling Markdown、提纲和附件路径均不持久化。Normalizer 只完成章节归类，不判断工作年限、不计算岗位匹配分、不替代 OCR/版面恢复；图片简历仍需后续 Vision 节点。
- 验证：新增 `scripts/verify_career_resume_normalizer.py`，覆盖中文 Markdown、英文标题、缺失教育经历和未归类片段；并扩展 Prompt 验证，确认结构提纲会进入模型上下文。

### 2026-08-06：文档理解服务故障降级

- 目标：避免 Docling 镜像下载、服务重启或瞬时网络故障导致整轮求职对话失败，更不能把“增强解析服务未完成”误告知为“没有收到简历”。
- 实现：`AttachmentParser` 仅捕获可安全展示的 `DocumentUnderstandingError`；保留已提取的原生文本与 `DocumentProbe` 结果，并将服务状态作为当前 Turn 的 `document_processing_notices` 传给模型和 SSE 最终负载。加密 PDF、无效附件和本地读取错误仍会严格失败，不被降级掩盖。
- 调用链：`DoclingServiceDocumentParser.parse_pdf` 异常 → `AttachmentParser._parse_pdf` 降级 → `ParsedAttachment.document_understanding_error` → `ModelTurnContext.document_processing_notices` → `CareerResponseRunner._build_prompt` / WebUI 反馈。
- 取舍：当前不自动切到未部署的 PaddleOCR，避免在失败时额外消耗 CPU/GPU 或隐藏第二个服务依赖；PaddleOCR 作为明确配置的后续兜底节点接入。
- 验证：新增 `scripts/verify_career_document_parser_degradation.py`，使用空文本 PDF 和不可用解析器验证本轮可继续、错误提示安全且附件状态不丢失。

### 2026-08-06：文档解析可观测性

- 目标：让用户知道扫描件是否实际经过 Docling OCR，而非只看到“提取了若干字符”的模糊反馈；同时为后续低置信度人工确认界面提供稳定、无敏感的 API 数据。
- 实现：新增 `AttachmentProcessingSummary`，将附件类型、页数、处理路径、解析器名称/状态、原生文本质量分和问题码加入 `ModelTurnContext` 与附件接口/SSE 最终负载。WebUI 在 Docling 成功时明确展示“Docling OCR 已完成”。
- 调用链：`AttachmentParser` → `ParsedAttachment` → `CareerIntakeGraph._build_attachment_processing_summaries` → `ModelTurnContext` → `router._attachment_processing_summary_payload` → `CareerAssistantPage`。
- 隐私：摘要不包含原始文件名、路径、OCR/简历正文或个人身份字段，可作为实时状态展示；当前不写入 PostgreSQL。
- 验证：扩展真实附件 API 验证，断言匿名扫描 PDF 的处理路径为 `structured_document`、解析器为 `docling-serve`、状态为 `success`。

### 2026-08-07：图片材料 OCR 优先与 Vision 复核路由

- 目标：让职位截图、简历图片和面经截图优先经本机 Docling OCR 转成可审计的文本，而不是把所有图片强制交给云端视觉模型；当 OCR 没有产出足够文字时，才由平台固定的云端 Vision 连接复核，聊天模型仍只需处理文本。
- 调研与取舍：Docling 已在本项目以 GPU 服务部署，代码为 MIT 许可并支持图片与 OCR；PaddleOCR PP-StructureV3 对中文、多栏、表格和阅读顺序是后续本地兜底候选，但此步不引入第二个重型服务。云端复核优先采用可配置的 Qwen `qwen-vl-ocr`（文字、表格和手写内容）或 Qwen-VL（图文语义）；DeepSeek 只消费提取后的文本做求职分析。
- 实现：`DoclingServiceDocumentParser` 支持常见图片 MIME；但经匿名实测，当前 GPU 镜像直接处理中文图片会使用不适配的 OCR 默认语言。`AttachmentParser._parse_image` 因此将图片临时转为单页匿名 PDF，再复用已经验收的扫描 PDF OCR 路径。OCR 文本达到最小可用阈值时仅提交文本模型；OCR 不可用或文本过少时才保留图片 Base64 并声明 `VISION` 能力需求。
- 调用链：`CareerChannel` → `TemporaryAttachmentStore` → `AttachmentParser._parse_image` → 临时单页 PDF → `DoclingServiceDocumentParser.parse_document` → `ParsedAttachment` → `CareerIntakeGraph._build_model_context` → 文本模型或 Vision 模型 → `CareerResponseRunner`。
- 隐私：图片原件、OCR 原文和 Base64 仍仅在当前 Turn 存活。OCR 成功时不再把图片 Base64 发送给模型；历史中只保存最终对话文本。
- 验证：`scripts/verify_career_office_and_image_attachments.py` 覆盖图片 OCR 成功与 Vision 降级；`scripts/verify_career_docling_image_live_service.py` 以匿名中文岗位截图直连本机 Docling 服务验收。

### 2026-08-07：图片理解改为“Docling 优先、平台视觉模型自动复核”

- 目标：图片上传后必然具备理解能力，但不把该能力绑到用户当前选择的聊天模型，也不让每张图片都产生云端模型费用。
- 调用链：`TemporaryAttachmentStore` → `AttachmentParser._parse_image` → 单页匿名 PDF → `DoclingServiceDocumentParser` → OCR 质量门（最小有效文字量与字符多样性）→ 成功则将 OCR 文本交给任意文本聊天模型；失败或低质量则自动调用平台 `CloudVisionRouter` → 将结构化图片理解文本交给任意文本聊天模型 → 删除原图与临时 PDF。
- 成本与隐私：大部分文本截图、简历图片会在已部署的 Docling 中完成，不产生云端 Vision 调用；只有低质量、无文本或 Docling 不可用的图片会通过 HTTPS 发给配置的视觉服务。云端调用只存在于当前 Turn，原图、Base64、API 响应与密钥都不进入 PostgreSQL、浏览器或历史对话。
- 模型边界：视觉模型不再写死为某个 Qwen 版本。`cloud_vision.active_connection_key` 决定生产连接，默认使用已实测的 `qwen3.6-flash`；百炼已公告 `qwen3-vl-flash` 将于 2026-10-10 下线，并指定 `qwen3.6-flash` 为替代。连接按 OpenAI-compatible 图片输入协议抽象，未来可切换 Gemini 或其他 Provider；模型变化不影响附件、Docling、聊天模型档案或历史会话。`qwen3.7-plus` 保留为默认关闭的复杂图表/高准确率候选，防止日常每张图片使用高成本模型。
- 回退策略：只有配置中的 `fallback_connection_keys` 显式列出的已启用连接，才会在主连接发生超时、网络异常、限流、5xx 或模型/接口不存在时尝试；认证、权限和请求格式错误直接反馈管理员，避免无意掩盖配置问题或产生额外费用。
- 部署：生产环境把每条连接的 Key 与 API Host 分别注入 Secret 管理器；API Host 必须使用供应商控制台提供的 HTTPS OpenAI-compatible 根地址。Web 容器不要求 GPU；Docling 可作为 CPU/GPU 独立服务按预算和吞吐弹性部署。
- 验证：`scripts/verify_career_cloud_vision.py` 使用 MockTransport 验证可配置连接、Base64 仅在服务端生成、缺失 API Host 的安全错误和连接池路由；`scripts/verify_career_office_and_image_attachments.py` 覆盖 Docling 成功、未配置失败及云端自动复核三条路径。另以真实百炼 `qwen3.6-flash` 对最小有效 PNG 完成一次调用验收。

### 2026-08-07：运行时模型路由与图片理解回归修复

- 问题：已保存且已就绪的 DeepSeek `deepseek-v4-pro` 档案，在“免费额度优先”路由中被错误地限制为静态公共模型目录中的 Provider，导致页面虽显示“可调用”，附件分析却返回“当前没有可用模型连接”。旧 Uvicorn 进程还会继续返回早已移除的 `vision_pending` 状态，造成图片看似仍在“等待 Vision”。
- 修复：免费额度路由改为仅以已保存档案的 `enabled`、`cost_tier=free_quota`、能力集与 `readiness=ready` 为准，不再依赖公共目录是否收录该 Provider。DeepSeek V4 请求显式关闭默认思考模式，确保常规求职问答的有限输出 Token 用于最终回答。图片路径保持“Docling OCR → 低质量才 CloudVisionRouter → 文本聊天模型”，不会要求聊天模型具备 Vision 能力。
- 可观测性：`/api/health` 增加不含敏感信息的 `career_runtime_revision`。开发环境必须通过 `preview_server.py` 启动唯一的 Uvicorn 自动重载实例；它用于发现端口仍指向旧服务的情况。
- 前端配置：`web-ui/vite.config.js` 固定从自身目录读取 `.env.local`，并将开发代理统一到 `18080`；解决了从项目根目录启动 npm 时 Vite 回退到旧端口、页面实际调用旧 API 的问题。
- 验证：`scripts/verify_career_image_intake.py` 与 `scripts/verify_career_model_gateway.py` 在真实 PostgreSQL 和服务器环境变量下通过；使用已保存 DeepSeek 档案的真实 Chat Completions 非流式与流式最小调用均返回有效内容。另以真实图片简历通过开发后端联调，结果为 `cloud_vision` / `qwen3.6-flash`、提取文本 3437 字、`deepseek-v4-pro` 生成 787 字回复，Turn 状态为 `succeeded`。所有验证会话均在验证结束后永久删除。

### 2026-08-11：本机 Docling 直连与 CUDA 运行修复

- 目标：修复页面“附件已上传但文档解析服务异常”的实际阻断，让本机 RTX 5070 的 Docling GPU 服务稳定处理 PDF、Word 等材料。
- 根因与取舍：FastAPI 进程中的 `httpx` 默认继承系统代理，导致指向 `127.0.0.1` 的 Docling/Gotenberg 请求被错误转发；历史 CUDA 日志也来自未带正确 GPU 请求的旧容器状态。修复后不更换业务链路，仍使用同一个 Docling 服务和同一份 Markdown 输出；仅让本机内网客户端显式绕过代理，并让 Compose 默认申请 GPU。CPU 仍可通过 `DOCLING_DEVICE=cpu` 作为明确、临时的故障降级，而不是主链路替代。
- 实现：`DoclingServiceDocumentParser` 和 `GotenbergOfficeConverter` 创建 `httpx.Client` 时设置 `trust_env=False`；`docker-compose.document-processing.yml` 使用 `docling-serve-cu128`、`DOCLING_DEVICE=${DOCLING_DEVICE:-cuda}` 与 `gpus: all`。
- 调用链：浏览器上传 → FastAPI `AttachmentParser` → `DoclingServiceDocumentParser`（直连 `127.0.0.1:5001`）→ Docling CUDA OCR/版面恢复 → Markdown → `ResumeNormalizer` → 当前 Turn 的模型上下文。
- 依赖：Docker Desktop 的 NVIDIA runtime、RTX 5070 驱动、Docling Serve `v1.21.0`；不新增云服务、模型额度或持久化原始附件。
- 验证：正式 GPU 容器中 `torch.cuda.is_available()` 返回 `true`；`scripts/verify_career_docling_live_service.py` 以匿名中文扫描 PDF 返回 `docling-serve / success`；`/api/career/interview-library/parse-file` 经本地 18080 API 返回 200 且识别器为 `docling_ocr`；文档解析契约与降级回归脚本均通过。
- 边界：此修复不保存原始文件、不改变历史对话、模型连接或线上部署。若未来 Docker GPU runtime 不可用，应先显式设置 `DOCLING_DEVICE=cpu` 并重建该单一服务，而不是让浏览器端静默失败。
