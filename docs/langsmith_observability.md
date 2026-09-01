# LangSmith 可观测性设计

## 目标

项目通过 LangSmith 观测微信公众号内容工作流、求职 Agent、文档/视觉解析、面经采集与 RAG 检索中的关键决策链路。观测的目标是定位失败节点、比较模型和 Provider 表现、统计调用耗时及结果规模，不用于保存业务正文，也不改变任何任务的执行顺序、重试策略或模型路由。

代码统一通过 `src/observability/langsmith_runtime.py` 接入 LangSmith 的 `traceable`。业务模块使用 `trace_operation`、`trace_stream` 或 `trace_llm_call`，避免各模块重复处理隐私过滤、禁用降级和异常语义。LangGraph 继续使用原生 tracing，并由同一组环境变量控制。

## 配置与依赖

基础工作流和求职助手都可能产生 Trace，因此 `requirements.txt` 与 `requirements-career-assistant.txt` 均声明 `langsmith>=0.6.0,<1.0.0`。样例配置如下：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=replace-with-your-langsmith-api-key
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=your-langsmith-project
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

只有同时提供 `LANGSMITH_API_KEY` 且 `LANGSMITH_TRACING=true` 时才启用上传；缺少配置时业务照常执行。由于 LangGraph state 会携带简历、职位、OCR 和聊天正文，运行时会强制将 `LANGSMITH_HIDE_INPUTS` 与 `LANGSMITH_HIDE_OUTPUTS` 设为 `true`，即使运维误配为 `false` 也不会放宽线上隐私边界。

真实密钥只能写入未纳入 Git 的本地或生产环境文件，并通过部署系统 Secret 注入。样例文件仅保留空值和占位符。

## Span 层级

### 微信内容工作流

```text
wechat.content_pipeline                         # 一次完整内容流水线
└─ wechat.task.<TaskName>                       # Search/Summary/Image/Video/Deliver 等 Task 生命周期
   ├─ wechat.summary.generate                   # DeepSeek 内容总结
   ├─ wechat.video_storyboard.generate          # 视频脚本与分镜
   ├─ media.image.seedream.generate             # Seedream 生图
   ├─ media.audio.doubao_tts.synthesize          # 豆包语音合成
   ├─ media.audio.edge_tts.synthesize            # Edge TTS 语音合成
   ├─ media.video.seedance.submit                # Seedance 视频任务提交
   └─ media.video.qwen_quality.assess            # 视频质量评估
```

父 Span 记录一次流水线或 Task 的成功、失败和耗时；LLM/多模态 Provider 作为叶子 Span 记录 Provider、模型、输入输出字符数、Token 用量等脱敏指标。视频轮询、下载、数据库 CRUD 和静态文件读取不单独建 Span，避免噪声及成本膨胀。

### 求职 Agent 与文件理解

```text
career.turn                                     # 一轮用户请求（流式输出仅记录事件规模）
├─ career.intake.graph                          # LangGraph 意图识别与路由
│  ├─ career.model.route                         # 模型档案选择与可用性决策
│  └─ career.vision.route
│     └─ career.vision.provider_call            # 简历图片/文件的视觉模型调用
├─ career.document.docling_parse                # Docling 文档解析边界
│  └─ career.document.legacy_office_convert      # 旧 Office 文件转 PDF
├─ career.job_source.fetch                      # 职位公开页面获取边界
├─ interview.rag.retrieve                       # 面经检索决策
│  └─ interview.embedding.batch                 # Embedding 批处理
└─ career.chat.completion | career.chat.stream   # 最终回答模型
```

### Skill 检索

```text
skills.search                                   # 一次 Skill 搜索父链
├─ 本地索引与关键词规范化                         # 确定性处理，不单独建 Span
├─ GitHub 公开 Skill 检索                        # 由父链记录耗时与结果规模
└─ LLM 相关性排序                                # 作为已有 Provider 子 Span
```

Skill Trace 只记录查询长度、候选数量、缓存命中与状态，不记录原始查询、仓库 URL 或 Skill 正文。

`career.intake.graph` 使用 LangGraph 原生 Trace 展示 Node 的运行关系；Graph state 包含简历、职位和历史上下文，所以输入输出必须全局隐藏。流式回答只记录分片序号和字符数，原始 chunk 原样返回给调用方但不会进入 Trace。

### 面经采集与入库

```text
career.interview_collection.xiaohongshu_import  # 一次公开链接采集与分析
├─ career.vision.route/provider_call             # 需要时执行图片文字理解
├─ career.interview_evidence.analyze              # 有效性与结构化语义分析
└─ career.interview_library.ingest               # 清洗后的面经入库
   └─ interview.embedding.batch                  # 可选向量化

career.interview_collection.public_url_import    # 通用公开 URL 导入父链
```

采集 Trace 只记录发现数量、有效数量、写入数量、来源类型和耗时，不记录小红书 URL、图片地址、OCR 正文、公司/岗位文本或用户编辑内容。

## 隐私边界

以下内容禁止上传到 LangSmith：

- Prompt、模型完整回复、聊天历史和 LangGraph state；
- 简历、职位描述、面经正文、OCR 文本、RAG query 与命中文档；
- 文件名、文件路径、图片 Base64、签名 URL 和公开内容原链接；
- API Key、Access Token、Cookie、Authorization Header、邮箱和姓名；
- Provider 原始错误响应中可能包含的请求体或凭证。

允许记录的内容限于：固定的 Span 名称、Provider/模型标识、任务类型、布尔能力标记、批次数量、字符数、向量维度、Token 用量、状态与耗时。运行时会递归清洗 metadata、tags、input summary 和 output summary；业务 ID、文件名、路径、邮箱、敏感字段名及疑似密钥值会被替换为 `[redacted]`。

内容总结与视频分镜的模型 Span 额外记录 `attempt_index`、`phase` 和固定枚举 `reason_code`，用于区分首轮生成与质量修复；这些字段不包含失败原文、Prompt 或用户反馈正文。

## 调用和失败语义

```text
业务入口
→ trace_operation / trace_stream / trace_llm_call
→ 检查 LANGSMITH_TRACING 与 API Key
→ 设置隐藏输入输出的隐私默认值
→ 通过 @traceable 建立父子 Span
→ 只提交清洗后的摘要
→ 返回原业务结果
```

可观测性是旁路能力：LangSmith 未安装、未配置、网络失败或 Trace 写入失败时，业务调用仍只执行一次。运行时不会因 Trace 异常重放付费的 LLM、生图、TTS 或视频请求；业务自身抛出的异常保持原类型和调用栈，而 Trace 端只看到异常类别。流式包装器即使预取了多个分片后失败，也会按原顺序补发并继续消费同一个迭代器，既不丢失已产生分片，也不重新请求模型。

## 验证

本地验证命令：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_langsmith_runtime -v
.\.venv\Scripts\python.exe -m compileall src tests
git diff --check
```

运行时测试覆盖：配置开关、隐私默认值、metadata/tag 清洗、Token 指标保留、Trace 初始化失败降级、Trace 结束阶段失败、业务异常不重试、流式分片不泄露及中途失败续传。真实环境验证仅需执行一个不调用模型的 metadata-only smoke Span，然后在对应 LangSmith Project 中确认名称、父子关系和隐藏的 Inputs/Outputs；不要用真实简历或面经作为 smoke 数据。

## 后续边界

- 不为高频轮询、文件下载和普通数据库访问添加 Span；如需性能监控应接入 Metrics/APM。
- 新增模型 Provider 时必须复用统一 runtime，并为输出定义仅含计数和状态的摘要函数。
- 新增 Agent/Graph 时先建立一个业务父 Span，再让 Node、RAG 与模型调用自然嵌套，禁止把原始 state 放入 metadata。
- 若后续需要质量评测，应使用专门脱敏的离线数据集和独立 Project，不能直接将线上用户内容复制为 Dataset。
- 生产部署需在变更审批后单独注入 Secret；本文档和本次配置调整不执行生产部署。
