# Disable Audio And Video Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让本地和生产工作流完全停止音频与视频处理，同时保持图文和微信公众号草稿链路正常运行。

**Architecture:** 视频复用现有 `video.submit_enabled` / `VIDEO_SUBMIT_ENABLED`，音频新增 `audio.enabled` / `AUDIO_ENABLED`。Application 根据开关构造任务序列，两个音频任务在真实调用入口二次拦截；存储、微信草稿和健康摘要同步忽略已关闭通道。

**Tech Stack:** Python 3.11、PyYAML、unittest、Docker Compose、FastAPI 工作流任务体系

**Spec:** `docs/superpowers/specs/2026-08-21-disable-audio-video-pipeline-design.md`

## Global Constraints

- 本地和生产默认完全关闭音频、视频通道。
- 文章、图片、排版、审核和微信公众号图文草稿必须继续运行。
- 关闭状态下不得调用 DeepSeek 视频蓝图、豆包 TTS、Seedance、Qwen-VL 视频质检、音视频对象存储上传或微信视频上传。
- 不删除现有 Provider、API Key、历史素材或数据库记录。
- 保留工作树中与本需求无关的既有修改。

---

### Task 1: 配置与生产默认值

**Files:**
- Modify: `config/app.yaml`
- Modify: `src/config/config_manager.py`
- Modify: `.env.production.example`
- Modify: `docker-compose.production.yml`
- Test: `tests/test_media_channel_disable.py`

**Interfaces:**
- Produces: `AppConfig.audio_enabled: bool`
- Consumes: 既有 `AppConfig.video_submit_enabled: bool`

- [x] **Step 1: 写入失败测试并确认现有音频无开关**

- [x] **Step 2: 实现 `audio.enabled=false` 与 `AUDIO_ENABLED` 覆盖**

```python
@property
def audio_enabled(self) -> bool:
    override = _optional_env_bool("AUDIO_ENABLED")
    return bool(self.raw["audio"].get("enabled", False)) if override is None else override
```

- [x] **Step 3: 将 API、Scheduler 和生产环境模板的两个开关设为 false**

### Task 2: 工作流与音频调用入口

**Files:**
- Modify: `src/app/application.py`
- Modify: `src/tasks/audio_task.py`
- Modify: `src/tasks/segmented_audio_task.py`
- Test: `tests/test_media_channel_disable.py`

**Interfaces:**
- Produces: `Application._once_pipeline_task_classes(config) -> tuple[type[BaseTask], ...]`
- Produces: 音频关闭元数据 `disabled_by_config=true`、`network_called=false`

- [x] **Step 1: 测试关闭状态的任务序列不含任何音视频 Task**

- [x] **Step 2: 手动任务和两个 Scheduler Job 按开关跳过音视频阶段**

- [x] **Step 3: `AudioTask` 与 `SegmentedAudioTask` 在仓储和 Provider 调用前返回**

### Task 3: 下游资产过滤和健康状态

**Files:**
- Modify: `src/tasks/storage_task.py`
- Modify: `src/services/wechat_draft_creation_service.py`
- Modify: `src/tasks/cat_task.py`
- Test: `tests/test_media_channel_disable.py`

**Interfaces:**
- Produces: `StorageTask._enabled_asset_types(config) -> list[str]`
- Produces: `CatTask._task_channel_disabled(config, task_name) -> bool`

- [x] **Step 1: 音视频关闭时 Storage 只保留图片候选**

- [x] **Step 2: 微信草稿不上传历史视频素材**

- [x] **Step 3: CatTask 不把已关闭媒体任务的历史失败计入当前健康状态**

### Task 4: 文档与完整验证

**Files:**
- Modify: `docs/platform_production_deployment.md`
- Modify: `docs/weekly_media_pipeline.md`
- Modify: `docs/global_memory.md`
- Modify: `scripts/check_media_production_readiness.py`
- Modify: `web-ui/src/components/ManualPipelinePanel.vue`

**Interfaces:**
- Documents: 生产关闭状态、恢复方法和图文链路边界

- [x] **Step 1: 更新页面说明、生产文档和就绪检查**

- [x] **Step 2: 运行 Python 回归测试**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [x] **Step 3: 校验生产 Compose**

Run: `docker compose --env-file .env.production.example -f docker-compose.production.yml config --quiet`

Expected: exit code 0，API 与 Scheduler 的 `AUDIO_ENABLED`、`VIDEO_SUBMIT_ENABLED` 均解析为 false。

- [x] **Step 4: 构建 PC 端前端**

Run: `npm --prefix web-ui run build`

Expected: 构建成功。
