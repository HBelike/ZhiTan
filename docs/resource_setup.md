# 外部资源申请与配置手册

本文档只记录当前项目代码实际会读取的资源，不列“看起来可能有用但暂时没接入”的服务。

## 1. 当前结论

当前系统已经具备一条无需商业图片/视频/语音 API Key 的免费兜底链路：

- 图片：先尝试复用 GitHub 仓库 README/素材目录中的截图、架构图、demo 图；找不到时再用 `Pillow` 本地生成科技教学风 PNG 卡片；
- 语音：优先 Windows 本机 SAPI，失败后使用 `edge-tts` 免费在线语音；
- 视频：`imageio-ffmpeg` 本地合成 MP4；
- 预览：FastAPI + Vue 审核台可以查看图文、图片、音频、视频和 CatTask 健康状态。

因此，想继续把内容推到微信公众号草稿，当前真正必须补齐的是：

- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`

如果你想把生成质量升级到商业级，再补齐 Seedream、豆包 TTS、Seedance、云存储等可选资源。

## 2. 必需资源

### 2.1 微信公众号开发凭证

用途：

- `DeliverTask` 获取 `access_token`；
- 上传封面图、正文内图片、视频素材；
- 调用微信公众号草稿箱接口创建草稿；
- 发布仍然保留人工审核，不会自动群发。

需要申请或获取：

- 公众号后台的 `AppID`
- 公众号后台的 `AppSecret`
- 服务器 IP 白名单：如果微信后台要求，需要把运行本项目机器或服务器的出口 IP 加进去。

写入 `.env`：

```text
WECHAT_APP_ID=你的公众号 AppID
WECHAT_APP_SECRET=你的公众号 AppSecret
WECHAT_AUTHOR=GitHub 技术雷达
WECHAT_CONTENT_SOURCE_URL=
```

说明：

- `WECHAT_AUTHOR` 可以不填，项目会使用 `config/app.yaml` 中的默认作者；
- `WECHAT_CONTENT_SOURCE_URL` 可选，如果你希望草稿标注原文链接再填写；
- 本项目当前创建的是草稿，不直接发布。

官方入口：

- [微信公众平台](https://mp.weixin.qq.com/)
- [获取 access_token](https://developers.weixin.qq.com/doc/offiaccount/Basic_Information/Get_access_token.html)
- [新增草稿](https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html)
- [上传图文消息内图片](https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html)

## 3. 强烈建议修复的资源

### 3.1 GitHub Token

用途：

- `SearchTask` 调 GitHub Search API；
- 降低限流风险；
- 拉取候选项目、star、README 等信息；
- `ImageTask` 会用它读取仓库 README 和常见素材目录，优先复用项目自己的截图、架构图或 demo 图，减少生图费用。

当前状态：

- 如果 `.env` 已配置有效 `GITHUB_TOKEN`，项目会走认证请求以降低限流风险；
- 如果 Token 失效，`SearchTask` 和仓库图检索会尽量降级为未认证请求，但限流更低，不适合长期稳定运行。

建议重新申请：

- 使用 fine-grained personal access token；
- 如果只搜索公开仓库，权限尽量保持最小；
- 不要给删除、写仓库、管理组织等权限。

写入 `.env`：

```text
GITHUB_TOKEN=你的新 GitHub Token
```

官方文档：

- [GitHub personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [GitHub REST API Search repositories](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-repositories)

## 4. 已可运行但可升级的资源

### 4.1 DeepSeek

用途：

- `SummaryTask` 生成公众号标题、摘要、深度正文和共享 `ContentBrief`；
- `ShortVideoPromptTask` 基于 `ContentBrief` 生成渐进式视频讲稿、统一旁白和七段分镜；
- `ImageTask` 基于同一 `ContentBrief` 生成 Ark 最终图片 Prompt；
- 当前代码读取 `DEEPSEEK_API_KEY`；
- 模型名配置为 `deepseek-v4-pro`。

写入 `.env`：

```text
DEEPSEEK_API_KEY=你的 DeepSeek Key
```

官方文档：

- [DeepSeek API Docs](https://api-docs.deepseek.com/)

### 4.2 Seedream 图片生成

用途：

- 替代本地卡片图，生成更强的 AI 科技教学风图片；
- 当前 `ImageTask` 的顺序是：已有可复用图 → GitHub 仓库图 → Seedream → 本地 `Pillow` 卡片图；
- 如果 GitHub 仓库没有合适图片，才会考虑调用 Seedream；
- 如果没有 Key，自动使用本地 `Pillow` 卡片图兜底。

写入 `.env`：

```text
VOLCENGINE_ARK_API_KEY=你的火山方舟 API Key
```

配置位置：

```yaml
image:
  provider: seedream
  local_fallback_enabled: true
```

官方入口：

- [火山方舟](https://www.volcengine.com/product/ark)
- [火山引擎控制台](https://console.volcengine.com/)

#### 4.2.1 手动复用 GitHub 仓库图

审核台的“媒体素材”区域提供了“尝试复用 GitHub 项目图”按钮。

这个按钮会：

- 读取当前内容的 5 个 GitHub 项目；
- 优先从项目 README、`docs`、`assets`、`images`、`screenshots` 等位置查找截图、架构图、demo 图；
- 下载后统一转成本地 PNG；
- 新增 `provider=github_repository_asset` 的 `media_assets` 记录；
- 不删除旧图，不覆盖旧图；
- 不调用 Seedream，不产生生图费用。

如果同一个项目同时存在本地兜底图和 GitHub 仓库图，公众号排版会优先使用 GitHub 仓库图。

### 4.3 豆包 TTS

用途：

- 替代 `edge-tts`，生成更自然、更稳定的中文旁白；
- 当前 `AudioTask` 优先使用豆包；
- 如果没有凭证，自动尝试本机 SAPI，再尝试 `edge-tts`。

写入 `.env`：

```text
DOUBAO_TTS_API_KEY=你的豆包语音 API Key
DOUBAO_TTS_VOICE_TYPE=zh_female_vv_uranus_bigtts
```

配置位置：

```yaml
audio:
  provider: doubao_tts_v3
  api_url: https://openspeech.bytedance.com/api/v3/tts/unidirectional
  resource_id: seed-tts-2.0
  local_fallback_enabled: true
```

鉴权与调用规则：

- API Key 必须在“豆包语音 → API Key 管理”创建；方舟 Ark API Key 无法用于该接口；
- `AudioTask` 调用 V3 HTTP 单向流式接口，使用 `X-Api-Key`、`X-Api-Resource-Id` 和每次请求独立的 `X-Api-Request-Id`；
- 音频响应为 NDJSON，系统只落盘 Base64 音频分片，不会将 API Key 或音频 Base64 写入任务日志、数据库元数据。

官方入口：

- [火山引擎语音技术](https://www.volcengine.com/product/voice-tech)
- [火山引擎控制台](https://console.volcengine.com/)

### 4.4 Seedance 视频生成与统一装配

用途：

- 替代本地幻灯片 MP4，生成动态感更强的 AI 视频；
- 当前链路由 `VideoClipPlanTask`、`SeedanceClipTask`、`SeedanceClipStatusTask` 和 `VideoAssemblyTask` 组成；
- 先将参考图片上传为公开 URL，再提交每个 Seedance 分片；
- 分片完成后下载到本地，统一去除片段原音轨并混入豆包 TTS 旁白；
- 如果没有 Key、没有公网图片 URL，自动使用本地图片 + 本地音频生成审核版 MP4。

写入 `.env`：

```text
VOLCENGINE_ARK_API_KEY=你的火山方舟 API Key
```

配置位置：

```yaml
video:
  provider: seedance_ark
  submit_enabled: false # 本地验证默认不产生付费任务；生产验收后再设为 true
  local_fallback_enabled: true
```

说明：

- 远程 Seedance 需要公网可访问图片 URL；
- 如果只使用本地免费视频合成，不需要公网图片 URL。

官方入口：

- [火山方舟](https://www.volcengine.com/product/ark)
- [火山引擎控制台](https://console.volcengine.com/)

完整链路、状态流转和本地/生产切换说明见 [周榜媒体生成工作流](weekly_media_pipeline.md)。

## 5. 云存储是否必须

短答案：当前不是必须。

本项目现在有两条路径：

1. 免费本地链路：
   - 图片存在本地；
   - 音频存在本地；
   - 视频存在本地；
   - 微信草稿创建时，`DeliverTask` 可以直接把本地图片/视频上传到微信公众号素材接口；
   - 不强制需要云存储。

2. 远程 Seedance 链路：
   - Seedance 需要能从公网读取参考图片；
   - 这时需要 Cloudflare R2、火山 TOS、腾讯 COS 或其它对象存储；
   - 上传后写入 `media_assets.metadata.remote_url`。

免费优先推荐：

- 短期：不配云存储，先用本地视频审核版；
- 中期：如果要稳定跑 Seedance，优先用 Cloudflare R2；
- 长期：如果部署在火山生态内，可以考虑火山 TOS，服务链路更统一。

Cloudflare R2 `.env`：

```text
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```

官方文档：

- [Cloudflare R2](https://developers.cloudflare.com/r2/)
- [R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)

## 6. 当前本地验证命令

安装依赖：

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
```

运行一次完整任务：

```powershell
.\.venv\Scripts\python main.py
```

启动审核台后端：

```powershell
.\.venv\Scripts\python preview_server.py
```

该命令是 Web API（审核台、技能库、求职助手、面经库、管理台）的唯一开发入口，固定监听 `127.0.0.1:18080` 并自动重载 `src`、`config` 下的源码和配置。`main.py` 仍只负责原有的公众号定时工作流，两者职责互不替代。

构建审核台前端：

```powershell
cd web-ui
npm run build
```

## 7. 当前环境变量清单

必需：

```text
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

强烈建议：

```text
GITHUB_TOKEN=
```

可选增强：

```text
DEEPSEEK_API_KEY=
VOLCENGINE_ARK_API_KEY=
DOUBAO_TTS_API_KEY=
DOUBAO_TTS_VOICE_TYPE=
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```
