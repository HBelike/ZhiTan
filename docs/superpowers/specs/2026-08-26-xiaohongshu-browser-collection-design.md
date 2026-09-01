# 小红书关键词浏览器采集设计

## 目标

在面经库顶部增加“信息收集”入口。用户输入关键词和采集数量后，系统复用其当前 Chrome 中已登录的小红书会话，读取搜索卡片并逐篇取得正文与图片地址；后端复用现有临时图片下载、OCR、`InterviewEvidenceAnalyzer`、面经入库和 RAG 索引链路。满足自动入库门槛的资料直接进入面经库，其余资料保留为可人工编辑的候选。

## 已确认范围

- 首期只支持 PC Chrome；手机端不实现浏览器采集。
- 默认采集 20 条，允许 5–50 条；不做无限滚动、组合关键词、筛选器或定时任务。
- 只采集图文笔记的标题、作者、发布时间、正文、标签和图片 OCR 文本；不采集评论，不解析视频。
- 单篇失败继续处理；登录失效、验证码或明确限流时暂停整批。
- 按当前组织和小红书 `note_id` 去重；已经入库的资料不覆盖人工编辑结果。
- `xsec_token` 只存在于浏览器助手和 Web UI 当前调用内存，不提交后端、不写数据库。
- 图片只进入现有临时下载与 OCR 流程，结束后清理；数据库只保存解析后的文本、状态和错误原因。
- 合格项自动入库：有效面经、置信度不低于 0.60、来源完整、公司和岗位非空、至少包含一个具体问题。
- 本次只做本地实现与验证，不部署生产。

## 方案取舍

采用现有 Chrome MV3 浏览器助手，不引入 OpenCLI 守护进程、Firecrawl 云端 Profile、服务器 Playwright 登录池或新的第三方运行依赖。浏览器助手只承担登录态页面读取；Web UI 承担串行调度；后端继续作为任务、候选、解析状态和入库结果的唯一事实源。

## 总体调用链

```text
面经库“信息收集”
  -> POST /xiaohongshu-browser-imports 创建 user_authorized_browser 任务
  -> 浏览器助手 search_xiaohongshu_notes
       -> 打开/复用非激活小红书标签页
       -> 搜索并滚动到目标数量
       -> 返回 note_id、临时签名 URL、标题、作者、点赞数
  -> POST /{job_id}/discoveries 提交不含 token 的卡片元数据
  -> 对未完成卡片串行调用 get_xiaohongshu_note
       -> 浏览器助手导航到临时签名 URL
       -> 从页面初始状态或 DOM 返回正文与图片地址
  -> POST /{job_id}/notes 提交单篇规范化结果
       -> 立即返回候选并用 FastAPI BackgroundTasks 处理
       -> 临时图片下载 -> 现有 OCR -> Agent 清洗 -> 条件自动入库
  -> Web UI 轮询候选进入终态后再处理下一篇
  -> POST /{job_id}/complete 完成批次
```

## 浏览器助手边界

扩展新增 `search_xiaohongshu_notes` 和 `get_xiaohongshu_note` 两个只读动作，并在 `ping` 中声明 `xiaohongshu_keyword_collection` 能力。扩展增加 `https://www.xiaohongshu.com/*` 主机权限，但不读取 `chrome.cookies`，不增加 Cookie 或密码权限。

搜索动作生成普通 `/search_result?keyword=...` 页面，不依赖 `/search_result_ai` 的 AI 总结和排序。扩展只选择非激活的小红书标签页；没有合适标签页时新建后台标签页。登录、验证码或限流发生时激活该标签页并返回带 `stopBatch=true` 的明确错误。

详情动作优先从 `window.__INITIAL_STATE__` 中按 `note_id` 查找笔记实体，再降级到笔记 DOM、JSON-LD 和 meta。返回给 Web UI 的对象包含 `noteId`、`canonicalUrl`、`title`、`bodyText`、`authorName`、`publishedAt`、`tags` 和 `imageUrls`。临时签名详情 URL不进入后端请求。

## 后端契约

新增接口：

- `POST /api/career/interview-library/xiaohongshu-browser-imports`
  - 输入：`keyword`、`requested_limit`。
  - 输出：`user_authorized_browser` 类型任务。
- `POST /api/career/interview-library/collection-jobs/{job_id}/xiaohongshu-discoveries`
  - 输入：不含图片和 token 的卡片数组。
  - 行为：创建或更新 `discovered` 候选；已入库来源标记为重复并关联既有面经。
- `POST /api/career/interview-library/collection-jobs/{job_id}/xiaohongshu-notes`
  - 输入：单篇正文、标签和临时图片地址，不含 `xsec_token`。
  - 行为：返回 `202`，后台复用现有 `_build_xiaohongshu_markdown()`、OCR、分析和条件入库。
- `POST /api/career/interview-library/collection-jobs/{job_id}/pause`
  - 输入：受控错误代码和说明。
  - 行为：进入 `needs_user_interaction`，保留已处理候选。
- `POST /api/career/interview-library/collection-jobs/{job_id}/complete`
  - 行为：确认不存在仍在处理的候选后进入 `succeeded`。

不新增数据库表。卡片使用现有候选表，`metadata_json` 只保存 `note_id`、作者、点赞数、分析结果和 OCR 统计，不保存图片地址、原始 HTML 或临时 token。跨批次去重通过当前组织下已入库面经的规范来源 URL `/explore/{note_id}` 完成。

## 任务状态与恢复

- 新建：`queued`，等待浏览器连接。
- 已发现卡片或开始恢复：`running`。
- 登录、验证码、限流、页面关闭后重新进入：`needs_user_interaction`。
- 所有卡片进入 `imported`、`fetched`、`empty` 或 `failed`：`succeeded`。
- 用户主动停止：`cancelled`。

Web UI 将当前任务 ID 写入 `localStorage`，不写临时 token。页面重载后读取任务；若尚有 `discovered` 候选，则重新执行同一关键词搜索，用新获得的签名 URL按 `note_id` 继续未完成项。已经入库、待人工确认、空内容或失败的候选不自动重复处理。

## 界面设计

“信息收集”与“小红书 URL 读取”并列。弹窗只展示关键词和数量，不暴露连接器、Cookie、内部接口或模型参数。启动后显示真实阶段轨：连接浏览器、读取卡片、逐篇解析、图片识别、写入面经库。

卡片显示标题、作者、来源链接和状态：待读取、解析中、已入库、待人工确认、重复跳过、失败。自动入库项提供“打开面经”；待人工项复用现有可编辑正文和保存入口。视觉继续使用面经库现有蓝绿色证据工作台，不新增装饰性英文眉题。

## 异常规则

- 单篇正文、单张图片、OCR 或模型失败：记录在候选并继续下一篇。
- 登录失效、验证码、明确 429/频率限制：暂停整批并激活小红书标签页。
- 图片全部失败但有正文：保留正文，标记来源不完整，不自动入库。
- 正文为空但 OCR 成功：允许进入分析与人工候选。
- 浏览器页面关闭：已提交后台的单篇继续；未提交项在恢复时重新搜索。
- 服务进程在 BackgroundTasks 执行中终止：候选保留为 `discovered`，恢复时可重新提交，不宣称已完成。

## 验证范围

- 扩展纯函数：URL 编码、卡片规范化、`note_id` 与 canonical URL、登录/验证/限流分类、详情字段和图片过滤。
- 扩展协议：两个动作、能力门禁、非激活标签页、无 Cookie 权限、Service Worker 语法。
- 后端服务：任务类型、发现去重、token 拒绝、单篇后台处理、OCR 文本入库、自动入库门槛、暂停与完成。
- Web UI：按钮位置、扩展能力检查、默认 20/范围 5–50、串行处理、暂停、恢复任务 ID、状态文案。
- 回归：现有公开 URL 导入、候选人工保存、面经树、扩展 BOSS 职位与招呼语能力不退化。
