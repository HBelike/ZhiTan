# 线上笔试助手设计

## 目标

在现有求职助手中新增 PC 端“线上笔试助手”。用户在 Chrome 或 Edge 的在线笔试题目标签页点击现有浏览器助手后，扩展立即读取当前视口内可见题面，并在 DOM 信息不足时补充当前视口截图；系统随后自动完成题目结构化、解题、代码生成、公开样例与 AI 边界测试、有限失败修复和结果展示。用户可以重新识别、补充题面、重新生成、编辑、复制和重新测试，但系统不自动写入或提交第三方笔试页面。

结束本次笔试时，用户可以选择把整场笔试归档为一份面经。未归档的题面、截图、代码和测试结果只存在于当前助手窗口的会话状态，关闭窗口后丢失。

## 已确认的产品决策

- 使用场景包含真实企业在线笔试中的参考分析，但首次使用必须提示用户仅在笔试规则允许外部辅助工具时使用。
- 不提供隐身、反检测、自动填写、自动提交或绕过监考能力。
- 复用现有 Chrome MV3 浏览器扩展，不开发第二个扩展，不开发 Electron 客户端。
- 入口属于求职助手，但答题界面使用独立路由 `/career/online-assessment` 和固定命名的小窗口，遵守“单页单任务”。
- 第一阶段只完成 PC 端 Chrome、Edge，不做手机、平板、Firefox 专项适配。
- 打开后自动执行“识别 → 生成 → 测试 → 有限修复”；用户可随时中止当前版本并重新识别。
- 首版支持 Python、JavaScript、Java、C++ 的单文件算法题，包括函数式题目和标准输入输出题目。
- 首版不支持 SQL、前端工程题、多文件项目题、交互式题目和第三方平台隐藏测试。
- 公开样例与 AI 生成测试必须分开展示；本地测试通过不得表述为第三方平台一定通过。
- 保存采用人工确认：整场笔试保存为一份面经，每道题为一个章节。

## 非目标

- 不调用第三方笔试平台的提交接口，不读取或模拟提交结果。
- 不读取 Cookie、登录 Token、密码、聊天记录或页面原始 HTML。
- 不自动滚动、切换或聚焦第三方题目标签页。
- 不在现有 FastAPI、Agent Worker、PostgreSQL 容器中直接运行用户或模型生成的代码。
- 不为所有笔试网站承诺专用适配；首版保证 LeetCode 专用解析、HackerRank 基础解析和通用当前视口回退。
- 不保存原始截图、完整编译日志、模型中间草稿或扩展采集到的页面全文。

## 开源方案复用

### 调研结论

没有一个候选仓库同时具备跨站取题、可嵌入网页、现有模型路由兼容、隔离代码执行、自动测试和面经归档。整仓安装会引入重复的 React、Express、Redis、Electron 和模型 Key 管理，因此采用许可证允许的模块级复用。

| 项目与审查版本 | 许可证 | 采用内容 | 明确不采用 |
|---|---|---|---|
| `RaheesAhmed/LeetCode-AI-Assistant@8368562` | MIT | LeetCode 题面、Monaco/textarea 代码、语言和公开测试用例提取思路 | 自动写入编辑器、扩展直连模型、扩展保存 API Key |
| `harry-the-nerd/open-interview-assistant@cbe239d` | Apache-2.0 | `activeTab` 用户手势、站点选择器逐级回退、最大正文长度和受限消息契约 | Electron、localhost 配对、隐身覆盖层 |
| `LeetBuddyAI/LeetBuddy@8fa99e1` | MIT | 单题会话、边界用例、复杂度、流式回答和图片补充的交互思路 | React 页面、Express、Redis、Gemini SDK 和其占位测试 |
| `zubyj/leetcode-explained@17d2ee0` | MIT | MV3 TypeScript 工程组织和页面变化监听的参考实现 | 远程答案 API、现成答案数据和页面内功能注入 |

`Natively-AI-assistant/natively-cluely-ai-assistant` 使用个人/非商业 Source License，只参考公开交互说明，不复制源码。

### 归属要求

- 改造自 MIT 或 Apache-2.0 实现的文件在文件头记录来源仓库、原文件和许可证。
- 新增 `docs/open_source_attribution.md`，记录采用范围、原版权声明和改造说明。
- 保留上游许可证要求的版权与许可文本。
- 不直接复制未经测试的整文件；先把页面读取逻辑拆成无副作用纯函数，再使用本项目测试固化行为。

## 总体架构

```text
在线笔试题目标签页
  → 现有浏览器扩展 action 点击
  → activeTab 授权并绑定 source_tab_id/source_window_id
  → DOM 结构化候选 + 当前视口截图
  → chrome.storage.session 中的一次性 launch_id
  → 打开 /career/online-assessment 独立窗口
  → 业务页通过现有扩展桥接领取 capture
  → FastAPI ProblemExtractor 生成 AssessmentProblem
  → SolutionGenerator 流式生成 AssessmentSolution
  → TestGenerator 生成公开样例与 AI 边界测试
  → CodeExecutionProvider 调用独立 Piston 执行节点
  → 失败时 SolutionRepairer 最多修复两轮
  → Vue 页面展示、编辑、复制或重测
  → 用户确认后 AssessmentArchiveService 写入面经库并建立索引
```

浏览器扩展负责取得浏览器权限和采集页面；WebUI 负责本次会话编排和展示；FastAPI 负责结构化、模型调用、测试定义、执行适配与归档；Piston 只负责隔离编译和执行。四者通过稳定契约连接，不互相读取内部状态。

## 浏览器扩展设计

### 启动方式

求职助手顶部增加“线上笔试助手”入口。直接从求职助手进入但没有待领取的 `launch_id` 时，页面显示准备说明，引导用户回到题目标签页点击浏览器扩展。

标准启动流程：

1. 用户在题目标签页点击浏览器扩展图标。
2. `chrome.action.onClicked` 取得本次 `activeTab` 授权。
3. Service Worker 在切换焦点前执行 DOM 提取和 `captureVisibleTab(source_window_id)`。
4. 扩展生成 128 位随机 `launch_id`，把采集结果写入 `chrome.storage.session`，有效期五分钟。
5. 扩展创建或聚焦固定命名的助手小窗口，地址为 `/career/online-assessment?launch_id=<id>`。
6. 助手页面通过现有 App 域名 Content Script 请求 `claim_assessment_launch`；扩展返回一次采集并立即删除该 `launch_id`。

`launch_id` 只用于同一浏览器扩展与已允许 App 域名间关联一次采集，不是登录凭据。服务端身份继续使用现有平台 Cookie，不信任 `launch_id` 代表用户身份。

### 扩展动作

在现有 `find-job-job-library-web-v1` 协议中增加：

- `claim_assessment_launch`：业务页领取打开时生成的一次性采集。
- `refresh_assessment_problem`：重新采集并替换当前题目。
- `append_assessment_problem`：采集用户滚动后的新视口，供后端合并题面。
- `ping` 返回新增能力 `online_assessment_capture_v1`，旧版扩展只显示升级入口。

不增加自动写入或提交动作。

### 权限

- 保留已有 `tabs`、`scripting` 和受控 App 域名权限。
- 增加 `activeTab`，确保任意笔试站点只能在用户点击扩展后获得临时页面权限。
- 不增加 `<all_urls>` 持久主机权限。
- `captureVisibleTab` 只在用户启动、重新识别或补充题面时调用，不持续轮询或录屏。

### 页面提取

新增独立 `assessment-page.js`，按以下顺序读取：

1. LeetCode 专用解析器：题目节点、示例、约束、Monaco/textarea 当前代码和语言。
2. HackerRank 基础解析器：`.challenge-body-html`、编辑器可见文本和语言控件。
3. 通用解析器：`question-description`、`problem-statement`、`main`、`article` 等候选节点。
4. 当前视口回退：只保留与视口相交、非隐藏、非透明且具有可见文本的元素。

每个解析器返回同一结构，不直接返回 HTML：

```json
{
  "source_url": "https://example.test/problem/1",
  "source_title": "题目标题",
  "source_platform": "leetcode",
  "visible_text": "清洗后的当前视口文本",
  "problem_candidates": ["候选题面文本"],
  "starter_code": "class Solution: ...",
  "language_hint": "python",
  "public_test_candidates": [],
  "viewport": {"width": 1440, "height": 900, "device_pixel_ratio": 1.25}
}
```

扩展移除 `script`、`style`、`noscript`、导航、隐藏节点和表单控件；正文上限为 25,000 字符。截图为当前视口 PNG，原始数据不写入 `chrome.storage.local`。超过 5 MiB 时由助手页面缩放至最长边 1,920 像素后再上传。

重新识别会替换当前题目；补充题面只合并新视口中未出现的段落。扩展不会自动激活被绑定标签页：若绑定标签页已不再是原窗口的可见活动标签，截图动作返回 `source_tab_not_visible`，页面提示用户切回题目后重试。

## 稳定领域契约

### AssessmentProblem

```text
problem_id: 当前窗口内生成的 UUID
source_url: 来源 URL
source_platform: leetcode | hackerrank | generic
title: 题目标题
statement: 题目正文
constraints: 约束列表
examples: 输入、输出和可选解释列表
starter_code: 初始代码，可为空
language: python | javascript | java | cpp
interface_kind: function | stdin_stdout
function_signature: 函数签名，可为空
confidence: 0.0–1.0
incomplete_reasons: 不完整原因列表
```

当 `confidence < 0.65`、没有题目正文、缺少函数签名且无法判断标准输入输出格式，或模型明确发现题面截断时，系统不自动进入解题，页面显示结构化结果并要求补充题面或人工修正。

### AssessmentSolution

```text
approach_markdown: 解题思路
code: 可编译/运行的最终代码
language: 与题目一致
time_complexity: 时间复杂度
space_complexity: 空间复杂度
assumptions: 对缺失题意所作假设；正常情况下为空
```

### AssessmentTestCase

```text
test_id: 稳定 ID
kind: public | generated
input_payload: stdin 文本或函数参数 JSON
expected_output: 期望输出
explanation: AI 测试必须说明覆盖的边界
```

一次运行最多包含 20 个测试，其中公开样例优先，AI 生成测试补足空输入、最小值、最大值、重复值、顺序和溢出等适用边界。AI 生成的期望输出属于模型推导结果，界面必须标记为“AI 测试”，不得伪装成平台官方用例。

### AssessmentExecutionReport

```text
compile_status: skipped | passed | failed | timeout
tests: 每个测试的 passed/failed/timeout、实际输出和安全错误摘要
passed_count: 通过数量
failed_count: 失败数量
duration_ms: 总执行时间
final_status: passed | partial | failed
```

Provider 原始错误、宿主路径和堆栈不返回浏览器。

## 后端服务

新增 `src/career_assistant/online_assessment/`：

- `contracts.py`：Pydantic 请求、响应和流式事件契约。
- `problem_extractor.py`：DOM 候选与可选截图结构化，合并补充视口。
- `solution_service.py`：使用现有模型档案和 ModelGateway 流式生成方案。
- `test_generator.py`：规范化公开样例并生成受限数量的 AI 测试。
- `execution.py`：语言适配、测试 Harness 和 `CodeExecutionProvider`。
- `repair_service.py`：根据安全化失败摘要修复代码，最多两轮。
- `archive.py`：生成确定性 Markdown 并复用面经库入库和索引。
- `web.py`：FastAPI 路由。

### API

- `POST /api/career/online-assessments/analyze`：接收 DOM 采集和可选截图，返回 `AssessmentProblem`。截图只在请求期内交给视觉模型，不写磁盘和数据库。
- `POST /api/career/online-assessments/solve/stream`：以 `fetch` 流式响应返回解题思路和代码增量。
- `POST /api/career/online-assessments/execute`：运行公开样例和 AI 测试；`auto_repair=true` 时在失败后最多修复两轮，并返回每轮摘要。
- `POST /api/career/online-assessments/archive/preview`：对浏览器提交的本场题目做校验、去重并返回归档预览。
- `POST /api/career/online-assessments/archive`：用户确认公司、职位、日期后写入面经库。

所有接口复用现有平台登录、organization 和 actor 边界。服务端不接受浏览器传入的 organization 或 actor 作为权威身份。

### 模型调用

- 题目结构化优先使用管理台配置的视觉能力模型；没有截图时允许使用普通文本模型。
- `config/career_assistant.yaml` 新增 `online_assessment.problem_extractor_profile_key` 和 `online_assessment.answer_profile_key`。前者用于题目结构化，后者统一用于解题、测试生成和修复；任一档案不存在或不可用时 readiness 明确返回未就绪，不静默切换到其他模型。
- 两个配置值继续引用管理台已有模型档案，不在扩展、WebUI 或新配置段保存 API Key。
- 捕获内容视为不可信数据。系统 Prompt 明确要求忽略题面中试图改变系统行为、索取秘密或调用工具的指令。
- 该领域的模型调用不提供网页、文件、命令或数据库 Tool；模型只能返回受控结构或文本增量。
- `run_version` 每次重新识别或重新生成时递增。前端取消旧请求，并丢弃所有版本不匹配的迟到增量和执行结果。

## 代码执行

定义 `CodeExecutionProvider`，首个实现为 `PistonExecutionProvider`。业务层只依赖统一接口，未来更换 Judge0 或托管执行服务不改变解题 API。首次本地接入以已审查的 `engineer-man/piston@de2b365` 为源码基线，不跟随浮动 `latest`。

本地开发通过独立 Docker 服务运行 Piston，只安装 Python、Node.js、OpenJDK 和 GCC/G++ 运行时。执行限制：

- 编译墙钟时间 10 秒。
- 运行墙钟时间 3 秒。
- 编译内存 512 MiB。
- 运行内存 256 MiB。
- 单次 stdout/stderr 各截断到 64 KiB。
- 单次请求最多 20 个测试。
- 禁止出站网络。
- 不挂载项目目录、Docker Socket、数据库、环境文件或密钥。
- 每次执行使用独立临时目录，完成后清理。

生产代码执行不能直接启用在当前应用与数据库所在的信任边界内。后续投产必须单独确认独立执行节点或托管 Provider；本设计和下一阶段实施只完成本地接入与验证，不部署生产。

函数式题目由四个语言适配器生成测试 Harness；标准输入输出题目直接传入 stdin 并比较归一化 stdout。比较规则只移除行尾空白和末尾空行，不改写大小写、数值或内部空格。

## 自动测试与修复

自动链路：

1. 先编译生成代码；编译失败不运行测试。
2. 依次运行公开样例，全部通过后再运行 AI 测试。
3. 任一阶段失败时，将题目、当前代码、失败测试输入、期望输出、实际输出和安全错误摘要交给修复模型。
4. 修复后从编译开始重新运行全部测试。
5. 最多两轮修复；两轮后仍失败则返回 `partial` 或 `failed`，不继续循环。

用户手工编辑代码后可以单独点击“重新测试”；手工编辑不会自动触发模型修复，除非用户明确点击“修复并重测”。

## WebUI

新增 `OnlineAssessmentPage.vue`，不嵌入现有聊天页。PC 布局：

- 顶部工具栏：来源网站、采集状态、当前语言、补充题面、重新识别、结束本次笔试。
- 左侧题目区：结构化题目、约束、公开样例和可编辑确认状态。
- 右侧答案区：解题思路、代码编辑器、复杂度、复制和重新生成。
- 底部测试区：公开样例、AI 测试、编译结果、失败摘要和重新测试。

状态机：

```text
waiting_for_capture
  → capturing
  → analyzing
  → generating
  → testing
  → repairing
  → ready | partial | failed
```

打开页面领取有效采集后自动进入 `analyzing`。没有采集、扩展版本过旧或题目标签不可见时停在准备态并给出明确恢复动作。

页面在关闭前仅使用 Vue 内存状态和 `sessionStorage` 保存本窗口问题列表，避免刷新时立即丢失；`sessionStorage` 不跨窗口恢复，结束或明确丢弃时清除。截图在完成分析后立即从前端状态释放。

## 面经库归档

现有 `InterviewSourceType` 和数据库 CHECK 增加 `online_assessment`。归档使用现有 `IngestionTriggerType.API_SYNC`、`InterviewLibraryService`、切片和索引链路，不新增独立题库。

结束弹窗要求填写公司、职位和笔试日期，展示题数和前五题。整场笔试保存为一份面经；同一公司、职位和日期再次归档时，按题面规范化哈希追加未出现的题目，不重复已有章节。

归档正文：

```markdown
# 示例公司 · Java 后端线上笔试

- 笔试日期：2026-09-01
- 来源：线上笔试助手

## 第 1 题：两数之和

### 题目
...

### 解题思路
...

### 最终代码
...

### 复杂度
...

### 公开样例结果
...

### AI 测试摘要
...
```

保存字段包括结构化题面、用户最后确认的代码、思路、复杂度、公开样例结果和 AI 测试摘要。不保存截图、原始页面文本、模型草稿、完整运行日志、Cookie 或 Token。标签固定包含“线上笔试”和来源平台；`source_platform` 保存主要题目页面的 hostname。

## 失败与恢复

- `extension_unavailable`：显示现有扩展安装或更新入口。
- `source_tab_not_visible`：提示切回题目标签页后再次点击扩展或重新识别。
- `page_access_denied`：说明该页面未获得 `activeTab` 权限，不自动扩大权限。
- `capture_too_large`：在前端缩放一次；仍超限则只提交 DOM。
- `problem_incomplete`：停止自动解题，要求补充题面或人工修改。
- `unsupported_problem_type`：保留识别内容，不调用执行器。
- `model_unavailable`：保留已识别题面和用户编辑内容，允许重试。
- `compile_failed`、`execution_timeout`、`memory_limit_exceeded`：展示安全中文摘要并进入有限修复。
- `runner_unavailable`：展示已生成代码但标记“尚未测试”，不得显示通过状态。
- `archive_failed`：不清除当前窗口内容，允许再次保存。

## 文件边界

计划新增或调整的主要文件职责：

- `browser-extension/job-library/assessment-page.js`：纯页面读取与平台适配。
- `browser-extension/job-library/service-worker.js`：一次性启动采集、绑定来源标签和扩展动作分发。
- `browser-extension/job-library/content-script.js`：App 页桥接动作白名单。
- `browser-extension/job-library/manifest.json`：`activeTab` 权限和版本升级。
- `web-ui/src/components/OnlineAssessmentPage.vue`：独立 PC 页面和本地会话状态。
- `web-ui/src/online-assessment-client.js`：扩展、分析、流式解题、执行和归档客户端。
- `src/career_assistant/online_assessment/*`：后端领域模块。
- 新 Alembic 迁移：扩展面经来源 CHECK，不创建笔试会话表。
- `docs/modules/online-assessment-assistant.md`：实现后的目标、取舍、调用链、依赖、验证和边界。
- `docs/open_source_attribution.md`：第三方许可证与改造归属。

不对现有面试大师模块做职责合并；两者只复用模型档案、鉴权、临时图片处理模式和面经库归档基础设施。

## 验证策略

### 扩展

- 使用静态 HTML Fixture 覆盖 LeetCode、HackerRank、通用题面、隐藏节点、视口外节点、Monaco 文本、语言判断和公开测试提取。
- 覆盖一次性 `launch_id`、五分钟过期、领取后删除、旧扩展能力门禁和来源标签不可见错误。
- 覆盖不调用自动写入、自动提交和后台轮询。
- 运行扩展 Node 测试、`node --check` 和版本化 ZIP 打包校验。

### 后端

- 契约测试覆盖字符上限、置信度门禁、四种语言和不支持题型。
- 使用 Fake 模型覆盖结构化、流式生成、测试生成、迟到版本和最多两轮修复。
- 使用 Fake 执行器覆盖编译失败、部分测试失败、超时、内存限制、输出截断和 Runner 不可用。
- 使用真实本地 Piston 覆盖四种语言至少各一个通过、编译失败和超时用例。
- 归档测试覆盖 actor/organization 隔离、同日追加去重、Markdown 内容、来源类型和索引触发。

### 前端

- 覆盖准备态、自动链路、补充题面、重新识别、旧版本结果丢弃、手工编辑、重新测试和两轮修复展示。
- 覆盖公开样例与 AI 测试的视觉和文案区分。
- 覆盖结束归档、本次不保存、归档失败保留内容和成功跳转面经库。
- 运行 WebUI 全部 Node 测试和 Vite production build。

### 联调验收

- 本地 Chrome/Edge 从模拟笔试页面点击扩展，验证打开即识别。
- 在真实 LeetCode 页面验证题面、语言、初始代码和公开样例读取。
- 使用本地模拟长题验证“补充题面”只追加、不覆盖。
- 验证关闭未归档窗口后服务端和 PostgreSQL 没有题面、截图或代码记录。
- 验证保存后面经树可见、正文可编辑、RAG 切片完成且截图不存在。

## 实施顺序

1. 扩展只读取题、一次性启动采集和静态页面 Fixture。
2. 后端题目结构化、Vue PC 页面和重新识别/补充题面。
3. 解题、测试生成、Piston 本地执行与有限修复。
4. 面经归档、来源迁移、RAG 索引、开源归属和模块文档。

每个阶段必须形成可独立测试的结果；不得提前加入手机端适配或生产部署。
