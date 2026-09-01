# 求职助手模型连接配置

“模型与连接”使用一张可滚动的配置页：先选择服务商，再向下填写连接名称、模型 ID、官网地址、API Key、请求地址和模型能力。

## 预置服务商

已内置 DeepSeek、Groq、OpenRouter、Google Gemini、阿里云百炼 Qwen、SiliconFlow、ModelScope、NVIDIA NIM、腾讯云 TokenHub、百度千帆、腾讯混元、智谱 AI、MiniMax 与火山方舟。选择预置项会自动填充官网地址和 OpenAI-compatible API Base URL；也可以选择“自定义兼容服务商”手动填写。

DeepSeek 的两个地址用途不同：

- 官网地址：`https://platform.deepseek.com`
- 请求地址：`https://api.deepseek.com`

请求地址必须填写到 API Base URL 层级，不能拼接 `/chat/completions`；生产环境必须使用 HTTPS。

## 2026-08-27：模型上下文容量自动识别

- 设计目标：创建或更新模型连接时按 `provider_key + model_id` 自动写入上下文容量，避免所有模型沿用历史统一 `8192 Token` 兜底值而在调用 Provider 前被错误拦截。
- 识别规则：DeepSeek 官方资料确认 `deepseek-v4-flash`、`deepseek-v4-pro` 和实验视觉版本支持 1 Mi Token，写入 `1,048,576` 并标记为 `built_in`；未命中内置规则的模型按产品约定写入 `1,000,000`，标记为 `fallback`，方便管理员后续识别和覆盖。
- 覆盖边界：管理员保存的 `admin` 策略优先级最高，普通模型连接更新不会覆盖它；`fallback` 和 `built_in` 档案在模型 ID 变化时重新计算。输出预留继续保持 `4096 Token`，不再因为上下文窗口只有 8192 而固定占用一半容量。
- 调用链：`保存模型连接 → CareerModelProfileRepository.upsert_profile() → infer_model_context_policy() → PostgreSQL → ContextBudgetService`。迁移 `20260827_27` 对已有非 `admin` 档案执行同一规则，并把数据库未来默认容量调整为 1M。
- 资料依据：[DeepSeek V4 Preview Release](https://api-docs.deepseek.com/news/news260424/) 明确两款 V4 API 模型均支持 1M 上下文；官方 Codex 接入配置给出的窗口值为 `1,048,576`。未知模型的 1M 是本产品兜底策略，不代表 Provider 官方承诺。
- 验证：容量推导、未知模型兜底、模型档案写入、管理员覆盖 SQL 和迁移回填均有回归测试；本地 Alembic 已升级到 `20260827_27`，DeepSeek Flash/Pro 为 `1,048,576 built_in`，现有 Qwen 为 `1,000,000 fallback`。未部署生产。

### 对话更新模型时同步上下文限制

- 设计目标：新建对话、恢复历史对话或切换模型下拉框时，立即作废上一模型的占用展示，并按当前实际模型重新估算；编辑并保存当前模型连接时，即使档案 ID 不变，也会重新读取服务端刚更新的上下文容量。
- 技术取舍：前端使用“会话 ID + 解析后的模型档案 ID + 模型连接修订号”作为刷新键。每次刷新先清空旧值，再携带 `model_profile_id` 请求；递增请求号保证较早发出的慢响应不能覆盖后来选择的模型结果。
- 调用链：`模型下拉框/历史对话/连接保存 → 失效旧占用 → GET context-usage?model_profile_id=... → ModelGateway SPECIFIC_PROFILE → ContextBudgetService → 仅接收最新响应`。普通用户仍只看到近似百分比，不暴露准确 Token 容量和阈值。
- 依赖与验证：没有新增运行时依赖。前端回归测试覆盖刷新键、旧值失效、同档案连接更新和请求号保护；本地 Web UI 全量 `191 passed`，Vite production build 成功。后端路由及上下文容量策略相关测试 `14 passed`，确认指定档案 ID 会原样进入 `SPECIFIC_PROFILE` 解析。未部署生产。

## API Key 与连通性

API Key 在页面中显式填写，但仅用于当前测试和服务端加密保存：

1. 点击“测试连接”后，服务端发起一次最小真实推理请求，验证地址、模型 ID 和 API Key。
2. 测试成功才会启用“保存模型连接”。
3. 保存时会再次验证，避免在测试后修改字段导致不可用连接入库。
4. Key 在进入 PostgreSQL 前由服务端 `pyca/cryptography` 的 Fernet 加密；任何列表、读取接口、日志和编辑页都不会返回明文。

### 2026-08-27：API Key 显示与隐藏

- 设计目标：管理员填写 API Key 时可以通过输入框右侧的小眼睛临时核对内容，同时默认继续使用掩码显示。
- 技术取舍：仅在当前浏览器表单内切换输入框的 `text/password` 类型，不新增依赖、不改变测试或保存请求，也不缓存、复制或回显服务端已保存的 Key。按钮使用可访问的“显示 API Key / 隐藏 API Key”名称和按下状态。
- 交互边界：新建连接、编辑连接、切换服务商或关闭弹窗都会恢复隐藏状态；编辑已有连接时仍要求重新填写 Key，服务端不会把旧明文返回浏览器。
- 调用链：`点击小眼睛 → showModelApiKey 切换 → 输入框 type 更新`；后续 `测试连接 → 保存模型连接 → 服务端加密入库` 链路保持不变。
- 依赖与验证：没有新增运行时依赖。`career-api-key-visibility.test.js` 覆盖显示/隐藏、无障碍名称和上下文切换后的重置行为；2026-08-27 本地 Web UI 全量 `188 passed`，Vite production build 成功。浏览器自动检查因本地页面没有可用登录态而未进入配置弹窗，未尝试读取或使用登录凭据，也未部署生产。

默认无需手动配置主密钥：服务首次启动会自动在 `data/career_credential_master.key` 创建一把 Fernet 主密钥，并在后续本地重启时复用。生产 Docker 环境中该文件位于持久化的 `application_data` 卷，因此容器更新不会使已经保存的模型 API Key 失效。

如果后续接入云 Secret 管理服务，也可以显式设置下列环境变量；显式值优先于自动托管文件：

```dotenv
CAREER_CREDENTIAL_MASTER_KEY=<Fernet.generate_key() 生成的 URL-safe Base64 值>
```

不要把任意口令、数据库密码或模型 API Key 直接填入此变量。无论使用自动托管文件还是显式 Secret，遗失、删除或更换主密钥后，旧 API Key 都无法解密，只能从页面重新输入。

### 旧明文凭据迁移边界

`20260810_08` 以前，本机数据库的 `plaintext_api_key` 可能仍保存了旧 Key。新版本不会再写入该列：只要页面保存了 API Key，数据库会写入 `encryption_scheme=fernet_v1` 和 Fernet token，并把旧明文列清空。

上线前按以下顺序处理：

1. 执行 `alembic upgrade head`。
2. 执行 `python scripts/migrate_career_legacy_credentials.py`；脚本会复用或自动创建同一把持久化主密钥，只输出迁移数量，不输出 Key、档案名或组织信息。
3. 脚本提示剩余旧明文为 `0` 后，确认 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS` 未设置或为 `false`，再启动 API 服务。

本地临时排障时可显式设置 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS=true` 读取旧明文，但它不是生产配置，不能替代迁移。迁移 `20260806_02` 曾预留但未定义格式的历史 `encrypted_api_key` 会标记为 `legacy_unknown`；系统不会将其误当成 Key 使用，必须在页面重新填写并保存。

调用链：`请求求职模块 → ensure_credential_master_key() → 保存模型连接 → CareerModelProfileRepository.upsert_profile() → CredentialCipher.encrypt() → PostgreSQL`；实际对话则通过 `ModelGateway → read_stored_credential() → CredentialCipher.decrypt()` 在进程内短暂读取，随后交给同一个模型调用适配器。Fernet 密钥不存 PostgreSQL、不传浏览器，也不进入可观测性日志；自动托管模式只保存在服务端持久化目录。

## 支持范围

当前连接层统一使用 OpenAI-compatible Chat Completions 协议。服务商若不兼容该协议，不能直接添加为求职助手模型连接。

## 官方免费模型选择

聊天输入框旁的模型下拉只展示 `readiness=ready` 的真实可调用连接；免费连接额外以“【免费】”标记。尚未接入的候选模型不会再混入聊天下拉，而是统一放在“申请免费模型”目录中。每张候选模型卡片都提供对应服务商的“获取 API Key”官方入口，管理员也可从服务商卡片底部打开官方接入文档和费用说明，再一键预填连接参数；只有完成真实连通性测试并保存后，连接才会进入聊天下拉。免费不代表匿名调用：云端 API 仍需由平台管理员注册账号并申请 API Key。

模型主标签统一使用“`Provider · Model ID`”，连接的自定义显示名称仅作为辅助说明。因此，即使两个连接都被命名为“DeepSeek 模型连接”，`deepseek-v4-pro` 与 `deepseek-v4-flash` 也会被明确区分。免费目录请求失败时会单独提示并支持重试，不会阻断会话历史和已配置连接的加载。

管理员把 Key 测试并保存后，Key 仅保留在服务端，访客不需要自行配置 Key。免费优先路由只会选择状态为“可调用”的免费连接，不会把 DeepSeek、Qwen Plus 等按量付费模型误当作免费模型。

当前目录与官方接入入口：

| 服务商 | 免费方式 | API Base URL | Key 申请 | 接入/额度说明 | 费用与实时目录 |
| --- | --- | --- | --- | --- | --- |
| Google Gemini | `gemini-3.7-flash`、`gemini-3.6-flash` 标准 Free Tier；地区和项目级限额以 AI Studio 为准 | `https://generativelanguage.googleapis.com/v1beta/openai` | [创建 Key](https://aistudio.google.com/app/apikey) | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) | [Pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Groq | `openai/gpt-oss-120b`、`openai/gpt-oss-20b` 提供 Free Plan 限额，按组织共享 | `https://api.groq.com/openai/v1` | [创建 Key](https://console.groq.com/keys) | [生产模型目录](https://console.groq.com/docs/models) | [Free Plan 限额](https://console.groq.com/docs/rate-limits) |
| Mistral AI | Free mode 提供有限月度用量，无需信用卡 | `https://api.mistral.ai/v1` | [创建 Key](https://console.mistral.ai/api-keys) | [Free mode 接入](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key) | [用量与限制](https://docs.mistral.ai/admin/billing-usage/usage-limits) |
| 阿里云百炼 Qwen | 中国（北京）符合条件的新用户按模型获得通常 90 天有效的额度 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | [创建 Key](https://bailian.console.aliyun.com/cn-beijing/?tab=app#/api-key) | [新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota/) | [模型定价](https://help.aliyun.com/zh/model-studio/model-pricing) |
| OpenRouter | `openrouter/free` 动态路由；未购额度账号为 50 次/日、20 RPM | `https://openrouter.ai/api/v1` | [创建 Key](https://openrouter.ai/settings/keys) | [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) | [Pricing](https://openrouter.ai/pricing) |
| ModelScope | 使用魔力值兑换 API-Inference 体验；需关联已实名认证的阿里云账号 | `https://api-inference.modelscope.cn/v1` | [Access Token](https://modelscope.cn/my/myaccesstoken) | [API-Inference 文档](https://modelscope.cn/docs/model-service/API-Inference/intro) | [魔力值与实时目录](https://modelscope.cn/docs/model-service/API-Inference/limits) |
| 硅基流动 SiliconFlow | 仅预置价格页当前明确免费的 `THUDM/GLM-Z1-9B-0414` | `https://api.siliconflow.cn/v1` | [创建 Key](https://cloud.siliconflow.cn/account/ak) | [快速开始](https://docs.siliconflow.cn/cn/userguide/quickstart) | [模型定价](https://siliconflow.cn/pricing) |
| NVIDIA NIM | Build 提供受限开发试用，非长期免费生产额度 | `https://integrate.api.nvidia.com/v1` | [创建 Key](https://build.nvidia.com/settings/api-keys) | [NIM LLM API](https://docs.api.nvidia.com/nim/reference/llm-apis) | [实时模型目录](https://build.nvidia.com/explore/discover?api-key=true) |

目录中的模型 ID 是 2026-08-27 通过官方资料核对的调用标识，但“有这个模型”不等于“当前账号仍有免费额度”。ModelScope、OpenRouter 和 NVIDIA 的可用模型会动态变化，管理员保存连接前必须以官方实时目录为准并执行真实连通性测试。Gemini 3.7/3.6 Flash 标记为 Vision；Groq 的 Llama 端点当前属于 Enterprise，因此免费目录只保留 Free Plan 限额表明确列出的 GPT-OSS；硅基流动已删除转为收费或无法证明仍免费的旧 Qwen、GLM-4 条目；NVIDIA 暂保留当前 Build 页面仍存在的 `meta/llama-3.3-70b-instruct`。

### 2026-08-27：免费模型目录实时审计

- 设计目标：目录优先展示主流、热门且能从官方资料证明当前存在免费层、免费路由或可用体验额度的模型；删除仅存在模型仓库但已无法证明可通过对应免费 API 调用的条目。
- 技术取舍：长期或周期免费层作为主要候选；百炼新用户额度、ModelScope 魔力值和 NVIDIA 开发试用继续保留，但在卡片中明确资格、有效期和控制台复核要求。Cerebras 只有要求验证付款方式的 30 天 5 美元一次性额度，GitHub Models 已退役，因此不加入目录。
- 调用链：`官方价格/限额/模型目录 → FREE_MODEL_PROVIDERS → /api/career/free-model-catalog → 免费模型弹窗 → 填写 Key 并真实测试 → 保存为 free_quota 连接`。目录从不代表连接已可调用，也不会绕过服务商账号条件。
- 依赖与边界：调研使用 Firecrawl 抓取公开官方页面，结果写入已被 Git 忽略的 `.firecrawl/`；运行时没有新增依赖。目录不尝试在后台匿名探测服务商，也不承诺第三方未来仍维持免费政策。
- 验证：`verify_career_free_model_catalog.py` 固定供应商集合、当前模型 ID、删除项和 HTTPS 官方链接；`verify_career_model_client.py` 覆盖新增 Mistral 默认 OpenAI-compatible 地址；`verify_career_deployment_settings.py` 与 Python `compileall` 通过，Web UI 全量 `191 passed`，Vite production build 成功。未调用任何第三方模型 Key，也未部署生产。

### 2026-08-27：上线优先的云端 Vision 选择

求职助手的生产请求不会依赖开发电脑的 NVIDIA GPU 或本地 Ollama。图片简历、职位截图与面经图片的云端语义理解默认推荐使用 `qwen3.6-flash`：它支持图文输入和多轮求职对话，可通过百炼 OpenAI-compatible Chat Completions 接口接入。

在“模型与连接”中选择“阿里云百炼 Qwen”后填写：

- 模型 ID：`qwen3.6-flash`
- API Key：从百炼控制台创建的 `DASHSCOPE_API_KEY`
- API Base URL：优先使用百炼当前工作空间在控制台显示的 OpenAI-compatible 地址；旧版通用地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 能力：勾选“文字与 PDF 文本”和“图片简历”。

`qwen3.5-ocr` 是更适合文本、表格和证照字段抽取的专用 OCR 模型。它不作为普通会话模型列出：官方文档说明其使用固定内部 System Message，且不适合承接通用连续聊天。后续会由独立的云端 OCR 复核节点调用；这样不会让用户误把 OCR 模型选成聊天模型。

百炼的免费额度具有账户、区域、模型准入和有效期条件，不能被标注为“永久免费”。系统只会在真实连接测试成功后，将其标记为平台可用。

DeepSeek、腾讯云 TokenHub、腾讯混元、百度千帆、火山方舟与 MiniMax 继续保留在“模型与连接”的手动配置项中。Groq Free Plan、Mistral Free mode 和百炼新用户免费额度已进入免费目录，但只有管理员真实测试成功并明确保存为 `free_quota` 的连接才会进入免费自动选择和显示“【免费】”。

### 2026-08-25：DeepSeek 付费分类纠正

- 设计目标：DeepSeek API 模型统一按付费模型展示和路由，不能因为旧版手动连接表单的默认值而显示“【免费】”或进入免费自动选择。
- 技术取舍：费用属性在服务端仓储边界统一规范化，而不是只在 WebUI 改字。`normalize_model_cost_tier()` 会把 DeepSeek 的新写入和旧记录读取都纠正为 `paid`；迁移 `20260825_21` 同步把 PostgreSQL 中已有的 DeepSeek 错误记录更新为 `paid`。
- 调用链：`模型连接保存/读取 → CareerModelProfileRepository → normalize_model_cost_tier() → ModelGateway 策略检查 → WebUI 按 cost_tier 分组`。免费自动选择仍只接收 `free_quota`；付费连接显示在“已接入的付费模型”下，并使用“【付费】”前缀。
- 权限边界：本次修正不修改 `allow_paid_profiles`。如果当前环境禁用付费模型，DeepSeek 会在“模型连接”中显示“策略已拦截”和付费属性，不会为了维持可用状态而绕过费用策略。
- 依赖与验证：没有新增运行时依赖；后端通过 `tests/test_model_profile_cost_tier.py` 覆盖分类函数、写入规范化与旧记录读取兜底。2026-08-25 本地验证结果为后端 `232 passed`、前端 `109 passed`、Vite 构建成功，Alembic head 为 `20260825_21`；未执行数据库迁移，也未连接生产环境。

科大讯飞 Spark Lite 使用 WebSocket 鉴权（AppID、APIKey、APISecret），不属于当前统一的 OpenAI-compatible 调用器；在专用 Spark 客户端完成前，不会把它伪装成可用的通用模型连接。

GitHub Models 不在目录中：GitHub 已于 2026-07-30 停止其模型目录和推理 API，现有 GitHub Token 仅继续服务于 GitHub 项目检索。

## 2026-08-30：模型最大输出与动态回答额度

- 管理台原“预留输出”字段现显示为“模型最大输出”。数据库和 API 暂时继续使用兼容字段名 `reserved_output_tokens`，其值代表具体 `Provider + Model ID` 的最大输出能力，不再代表每轮固定请求额度。
- 求职助手每轮真实 `max_tokens` 取模型能力、系统 `100000 Token` 上限和 95% 上下文安全边界内剩余空间三者的最小值；压缩前后都会重新估算输入。
- 已核对的 DeepSeek V4 Flash、Pro 和实验视觉版本使用 `1,048,576 Token` 上下文、`384,000 Token` 最大输出。迁移 `20260830_32` 只回填未被管理员覆盖的档案。
- 未识别模型仍显示“容量待核对”，管理员必须按准确 Model ID 查阅 Provider 官方资料后填写上下文容量和最大输出；系统不会从 Provider 名称猜测输出能力。
- 达到长度上限的普通或流式正文会显示未完成提示；被截断的 Tool Calling 不会执行。本次不自动续写，也不把未知结束原因推断成长度截断。
