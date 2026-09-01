# 周榜媒体工作流：生产启用检查清单

该清单覆盖“每周 GitHub 热门项目 → 文案与插图 → 豆包旁白 → Seedance
分镜 → ffmpeg 成片 → 公众号草稿”的媒体生成链路。它不改变公众号仍需
人工审核、手动发布的边界。

## 开发期安全模式

默认配置保持：

```yaml
video:
  submit_enabled: false
storage:
  provider: local
```

此模式不会创建 Seedance 付费任务。音频若没有豆包凭证，会依现有配置降级到
本地 Windows SAPI 或 edge-tts；视频拼接可以通过本地 ffmpeg 验证。

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_chunking.py
.\.venv\Scripts\python.exe scripts\verify_video_assembly.py
.\.venv\Scripts\python.exe scripts\check_media_production_readiness.py
```

最后一条命令只读取配置和环境变量是否存在，不调用云端，也不会输出密钥。
此时 `safe_local_mode` 应为 `true`。

## 生产启用前

1. 在 `.env` 或部署平台的 Secret 管理中配置 `VOLCENGINE_ARK_API_KEY`；
   它供 Seedance 提交和状态查询使用。
2. 在“豆包语音”控制台的“API Key 管理”中创建并配置 `DOUBAO_TTS_API_KEY`。
   方舟 Ark Key 不能替代豆包语音 API Key；项目使用 V3 单向流式合成接口。
3. 配置可被火山侧访问的公开对象存储。当前已实现 Cloudflare R2：
   `CLOUDFLARE_R2_ACCOUNT_ID`、`CLOUDFLARE_R2_ACCESS_KEY_ID`、
   `CLOUDFLARE_R2_SECRET_ACCESS_KEY`、`CLOUDFLARE_R2_BUCKET`、
   `CLOUDFLARE_R2_PUBLIC_BASE_URL`。
4. 将 `storage.provider` 切换为 `r2`，随后运行就绪检查。
5. 只有检查输出 `ready_for_real_seedance_submission: true`，才将
   `video.submit_enabled` 改为 `true` 并进行一次真实小规模验收。

生产模式中，Seedance 参考图必须是公网 HTTPS 地址；`127.0.0.1`、局域网地址
和仅存在本机磁盘的图片都不能作为参考图传给云端模型。

## 运行与故障定位

```text
AudioTask
  -> 长旁白按 UTF-8 字节上限分句调用豆包短文本 TTS
  -> ffmpeg 无损拼接为一条统一旁白
SeedanceClipTask
  -> 提交 7 个 15 秒无声教学分镜
SeedanceClipStatusTask
  -> 轮询、下载每个已完成分镜
VideoAssemblyTask
  -> 统一静音、规格化、拼接分镜并混入统一旁白
StorageTask
  -> 上传最终 MP4
DeliverTask
  -> 创建公众号草稿，等待人工发布
```

- 豆包短文本单请求受字节上限约束，当前实现按 900 UTF-8 字节留出安全余量；
  单个分片失败会回退到本地语音，而不是中断整批内容。
- Seedance 查询和下载的暂态网络错误会保留为 `processing`，后续调度可重试；
  明确的远程失败才标记为 `failed`。
- 旧的本地幻灯片兜底视频不会阻塞真实 Seedance 成片；成功装配后旧资产会标记为
  `replaced`，保留审计记录但不再作为发布候选。

## 关于本地 Kimi

当前不把所谓 “Kimi K3 本地部署” 放入运行关键路径：官方可用模型资料聚焦
Kimi K2.5/K2.6 API，未发现适合生产依赖的 Kimi K3 本地部署官方方案。未来可将
Kimi K2.6 作为可选的云端脚本/提示词审校 Provider，但不能替代豆包 TTS 或
Seedance 视频生成。

参考：

- [Kimi 模型列表](https://platform.kimi.ai/docs/models)
- [火山引擎短文本语音合成](https://www.volcengine.com/docs/6561/79817?lang=zh)
- [火山方舟内容生成 API](https://api.volcengine.com/api-docs/view?action=Create3DGenerationsTasks&serviceCode=ark&version=2024-01-01)
