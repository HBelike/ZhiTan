# 音视频链路完全关闭设计

## 目标

本地开发、管理员手动运行和生产 Scheduler 均完全停止音频、视频相关处理，避免 DeepSeek 视频蓝图、豆包 TTS、Seedance、Qwen-VL 视频质检、对象存储音视频上传和微信视频素材上传产生费用；文章、图片、排版、审核和微信公众号图文草稿继续运行。

## 设计

- 视频复用现有 `video.submit_enabled` / `VIDEO_SUBMIT_ENABLED`，本地与生产默认均为 `false`。
- 音频新增 `audio.enabled` / `AUDIO_ENABLED`，本地与生产默认均为 `false`。
- 一次性工作流与 Scheduler 根据两个开关构造任务序列，关闭时不进入对应媒体阶段。
- `AudioTask` 与 `SegmentedAudioTask` 在业务入口再次检查音频开关，关闭时返回 `network_called=false`，不访问 Provider 或本地兜底。
- `StorageTask` 只上传已启用通道的媒体类型；音视频关闭时仍可上传图片。
- 微信草稿创建在视频关闭时不上传历史视频素材；现有历史媒体文件和数据库记录仅保留用于只读预览，不删除。
- `CatTask` 将“由配置主动关闭”视为预期状态，不计入阻塞项。

## 验收标准

1. 本地默认配置的 `audio_enabled` 和 `video_enabled` 均为 `false`。
2. 生产 Compose 中 API 与 Scheduler 的音视频总开关均默认 `false`，`VIDEO_SUBMIT_ENABLED` 也默认 `false`。
3. 关闭状态下直接运行两个音频生成 Task 都不会访问仓储或 Provider，任务元数据包含 `disabled_by_config=true` 和 `network_called=false`。
4. 手动与定时工作流关闭视频时不包含任何视频任务。
5. Storage 只处理图片，微信草稿不上传视频。
6. CatTask 不把主动关闭的音视频任务视为故障或资源阻塞。
7. 现有文章、图片数量、排版和微信草稿测试继续通过。

## 边界

- 不删除音视频 Provider、任务实现、API Key 或历史素材，后续需要恢复时可显式开启总开关。
- 不关闭图片生成，也不改变人工审核和公众号图文草稿流程。
- 不执行真实音频、视频或付费模型请求作为本次验证的一部分。
