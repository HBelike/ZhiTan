# 公众号技术教学视频生产架构

## 目标与边界

本模块将每周 GitHub 热门项目总结转换为可人工审核的技术教学短视频。视频不是五张静态图片的拼接，也不让语音反向决定画面长度；唯一的时间基准是 **实际生成的视频片段时长**。

本模块当前不替换技能库、求职助手或微信公众号排版/推送模块。它只向既有工作流产出一个带中文硬字幕、可预览的 `video` 媒体资产。

## 关键设计决策

### 1. 视频先生成，旁白后对齐

Seedance 等生成式视频服务无法保证每一段恰好是提示词中写的秒数。若先写总旁白再按预计时长剪视频，容易出现画面跳切、语速失真或人声被截断。

因此流程为：

1. `ShortVideoPromptTask` 生成有事实边界的七段技术教学分镜。
2. `VideoClipPlanTask` 生成纯文生视频提示词。提示词描述教学画面和运动，不要求模型输出旁白、人物口型或内嵌字幕。
3. `SeedanceClipTask` 提交每段视频；`SeedanceClipStatusTask` 下载后用 ffmpeg 探测真实时长。
4. `VideoNarrationTimelineTask` 依据真实时长，为每段改写受字数预算约束的旁白和字幕。
5. `SegmentedAudioTask` 逐段 TTS，将每一段音频精确对齐对应视频片段。
6. `VideoAssemblyTask` 拼接视频、音频和同一时间线生成的中文 SRT，并把字幕烧录进 MP4。

这样旁白的内容仍然来自分镜事实合同，而时间边界来自已生成视频，避免“口播在讲项目 A，画面已经切到项目 B”。

### 2. 音频只允许温和调整，禁止裁掉人声

对每段 TTS 音频：

- 短于视频：在尾部补静音，保留镜头转场空间。
- 略长：最多使用配置项 `max_tts_speed_ratio`（默认 1.08）的温和 `atempo` 加速。
- 超过可接受范围：只对该段旁白再次进行受约束压缩，保留仓库名等不可变标识；仍不满足则进入人工审核。

任何情况下都不会直接裁剪音频末尾的人声。

### 3. 视觉模型只质检，不参与事实创作

`VideoVisualQualityTask` 为每个完成片段均匀抽取关键帧，交给 Qwen-VL 评估：

- 项目/模块标识是否可读；
- 是否体现约定的教学结构和运动；
- 是否出现乱码、无关人物、营销画面、水印或无关品牌；
- 画面是否不足以确认，需要人工审核。

视觉模型不写旁白、不提炼项目事实，避免“看图补故事”。它只会产生 `pass`、`retry` 或 `manual_review`。低分片段仅重生成该片段；达到配置上限后停止自动重试，保留报告给人工审核台。

## 调用链

```text
main
  -> Application.run()
    -> VideoClipPlanTask
    -> SeedanceClipTask
    -> SeedanceClipStatusTask
      -> MediaProbeService.probe_video()
    -> VideoVisualQualityTask
      -> VideoFrameSampler.sample()
      -> QwenVideoQualityProvider.assess()
      -> [仅低分片段] SeedanceClipTask
    -> VideoNarrationTimelineTask
      -> DeepSeekProvider.chat()
    -> SegmentedAudioTask
      -> DoubaoTtsProvider.synthesize()
      -> NarrationAudioTimelineProvider.fit_to_visual_duration()
    -> VideoAssemblyTask
      -> ffmpeg concat + audio mux + SRT burn-in
    -> PreviewTask / ArticleLayoutTask / DeliverTask
```

## 持久化与可恢复性

所有中间产物都通过 `media_assets` 与 `video_clip_plans` 建立可追溯关系：

| 资产类型 | 作用 | 核心元数据 |
| --- | --- | --- |
| `video_clip_task` | Seedance 异步任务 | `task_id`、分镜 ID、提示词 |
| `video_clip` | 下载后的原始片段 | `actual_duration_seconds`、源任务 ID |
| `video_quality_report` | 视觉质检报告 | 评分、结论、重生成指令、原片段 ID |
| `video_narration_timeline` | 旁白与字幕时间线 | 实际时长、字数预算、SRT 路径 |
| `audio` | 分段合并后的旁白 | 片段音频时长、速度比、时间线 ID |
| `video` | 最终成片 | 原片段、音频、字幕时间线、最终时长 |

失败或被质检替换的资产会标记为 `replaced`，不会再被后续任务复用。重跑流水线时，已完成且输入未变化的资产会被幂等复用。

## 超时与重试策略

| 环节 | 策略 |
| --- | --- |
| Seedance 提交/状态查询 | 任务状态持久化，下一次调度继续轮询 |
| 视频下载与 ffmpeg 探测 | 失败即阻止进入旁白阶段，保留错误供 CatTask 记录 |
| 视觉质检 | 单片段失败进入人工审核，不误判为通过 |
| 视觉质量不合格 | 仅重生成失败片段，默认最多 1 次 |
| 豆包 TTS | 瞬时失败重试 1 次；超长旁白仅重写当前段 |
| 最终合成 | ffmpeg 超时即失败，不发布半成品 |

## 本地验证

以下验证均不调用 Seedance、豆包或视觉模型：

```powershell
.\.venv\Scripts\python.exe scripts\verify_video_frame_sampler.py
.\.venv\Scripts\python.exe scripts\verify_video_assembly.py
```

第一条命令生成临时测试视频并验证均匀抽取关键帧；第二条命令验证多段视频、音频与中文 SRT 可合成为 MP4。

## 上线部署要点

线上以容器或服务进程运行同一套 Python 工作流，并把以下资源改为托管服务：

- PostgreSQL：任务、资产元数据和审核状态；
- 对象存储（R2/TOS/COS）：视频、音频、关键帧与最终成片；
- Secret Manager/部署环境变量：Seedance、豆包 TTS、Qwen-VL 等密钥；
- 独立 worker：执行 ffmpeg、视频下载和 TTS，避免阻塞 Web API；
- 审核台：仅在人工通过后调用微信公众号草稿接口。

本地开发和生产使用同一资产状态模型；区别只是本地可用磁盘目录，生产改为对象存储 URL。这样不需要为“上线版”重写视频逻辑。
