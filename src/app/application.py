from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.app.pipeline_execution_lock import PipelineAlreadyRunningError, PipelineExecutionLock
from src.config.config_manager import AppConfig, ConfigManager
from src.platform_access.runtime_config import apply_pipeline_config
from src.database.database_manager import DatabaseManager
from src.logging.logger_manager import LoggerManager
from src.observability.langsmith_runtime import trace_operation
from src.repositories.error_event_repository import ErrorEventRepository
from src.repositories.task_run_repository import TaskRunRepository
from src.scheduler.scheduler_manager import SchedulerManager
from src.services.skill_library_service import SkillLibraryService
from src.tasks.article_layout_task import ArticleLayoutTask
from src.tasks.audio_task import AudioTask
from src.tasks.base_task import BaseTask
from src.tasks.cat_task import CatTask
from src.tasks.deliver_task import DeliverTask
from src.tasks.image_task import ImageTask
from src.tasks.preview_task import PreviewTask
from src.tasks.search_task import SearchTask
from src.tasks.seedance_clip_task import SeedanceClipTask
from src.tasks.seedance_clip_status_task import SeedanceClipStatusTask
from src.tasks.segmented_audio_task import SegmentedAudioTask
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.startup_self_check_task import StartupSelfCheckTask
from src.tasks.storage_task import StorageTask
from src.tasks.summary_task import SummaryTask
from src.tasks.task_context import TaskContext
from src.tasks.task_result import TaskResult
from src.tasks.video_clip_plan_task import VideoClipPlanTask
from src.tasks.video_assembly_task import VideoAssemblyTask
from src.tasks.video_narration_timeline_task import VideoNarrationTimelineTask
from src.tasks.video_status_task import VideoStatusTask
from src.tasks.video_task import VideoTask
from src.tasks.video_visual_quality_task import VideoVisualQualityTask


class Application:
    """应用生命周期对象，负责串起配置、日志、数据库、调度和任务。"""

    def __init__(
        self,
        project_root: Path,
        *,
        runtime_config: dict[str, object] | None = None,
        extra_log_handlers: tuple[logging.Handler, ...] = (),
    ) -> None:
        self.project_root = project_root
        self.runtime_config = runtime_config
        self.extra_log_handlers = extra_log_handlers
        self.config: AppConfig | None = None
        self.logger: logging.Logger | None = None
        self.database_manager: DatabaseManager | None = None
        self.task_run_repository: TaskRunRepository | None = None
        self.error_event_repository: ErrorEventRepository | None = None
        self.scheduler_manager: SchedulerManager | None = None

    def initialize(self) -> None:
        """初始化应用基础设施。"""
        self.config = ConfigManager(project_root=self.project_root).load()
        if self.runtime_config is not None:
            self.config = apply_pipeline_config(self.config, self.runtime_config)
        LoggerManager(project_root=self.project_root, config=self.config).initialize(
            extra_handlers=self.extra_log_handlers,
        )
        self.logger = logging.getLogger(__name__)
        self.database_manager = DatabaseManager(config=self.config)
        self.database_manager.initialize()
        self.task_run_repository = TaskRunRepository(database_manager=self.database_manager)
        self.error_event_repository = ErrorEventRepository(database_manager=self.database_manager)
        self.scheduler_manager = SchedulerManager(config=self.config)
        self.logger.info("应用基础设施初始化完成")

    def run(self) -> int:
        """启动应用，根据 run_mode 选择一次性流水线或常驻调度。"""
        try:
            self.initialize()
            assert self.config is not None
            assert self.logger is not None

            self.logger.info("应用启动成功：%s run_mode=%s", self.config.app_name, self.config.run_mode)
            self._run_startup_self_check()
            self._preview_scheduler()

            if self.config.run_mode == "scheduler":
                self._run_scheduler_forever()
            else:
                self._run_once_pipeline()

            return 0
        except KeyboardInterrupt:
            if self.logger is not None:
                self.logger.info("收到退出信号，应用准备退出")
            return 0
        except Exception:
            logging.basicConfig(level=logging.ERROR)
            logging.getLogger(__name__).exception("应用启动失败")
            return 1
        finally:
            if self.logger is not None:
                self.logger.info("应用退出")

    def run_manual_pipeline(self) -> list[TaskResult]:
        """手动消费最近 GitHub 快照并生成内容，不在每次运行时重复搜索 GitHub。"""

        self.initialize()
        self._run_startup_self_check()
        return self._run_exclusive_pipeline(
            owner="manual_pipeline",
            handler=self._run_once_pipeline_unlocked,
        )

    def refresh_github_snapshot(self) -> TaskResult:
        """显式刷新 GitHub 周榜快照；仅由定时任务或管理员刷新入口调用。"""

        self.initialize()
        results = self._run_exclusive_pipeline(
            owner="github_snapshot_refresh",
            handler=lambda: [self._run_task(SearchTask)],
        )
        return results[0]

    def _run_once_pipeline(self) -> list[TaskResult]:
        """手动执行一次内容流水线，复用最近一次 GitHub 周榜快照。"""

        return self._run_exclusive_pipeline(
            owner="once_pipeline",
            handler=self._run_once_pipeline_unlocked,
        )

    def _run_once_pipeline_unlocked(self) -> list[TaskResult]:
        """在已持有流水线锁时从 SummaryTask 开始消费现有周榜快照。"""

        assert self.config is not None
        task_classes = self._once_pipeline_task_classes(self.config)
        results = [self._run_task(task_class) for task_class in task_classes]
        assert self.logger is not None
        self.logger.info("一次性流水线执行完成")
        return results

    @staticmethod
    def _once_pipeline_task_classes(config: AppConfig) -> tuple[type[BaseTask], ...]:
        """根据媒体开关构建一次性流水线，关闭时不进入对应任务阶段。"""

        task_classes: list[type[BaseTask]] = [SummaryTask]
        if config.video_submit_enabled:
            task_classes.append(ShortVideoPromptTask)
        task_classes.append(ImageTask)
        if config.audio_enabled:
            task_classes.append(AudioTask)
        if config.video_submit_enabled:
            task_classes.extend(
                [
                    VideoClipPlanTask,
                    StorageTask,
                    SeedanceClipTask,
                    SeedanceClipStatusTask,
                    VideoVisualQualityTask,
                    SeedanceClipTask,
                    SeedanceClipStatusTask,
                    VideoNarrationTimelineTask,
                    SegmentedAudioTask,
                    VideoAssemblyTask,
                ]
            )
        task_classes.extend([StorageTask, PreviewTask, ArticleLayoutTask, DeliverTask, CatTask])
        return tuple(task_classes)

    def _run_weekly_content_production_job(self) -> None:
        """周五 08:00 内容生产 Job：采集、总结、生成素材、生成审核预览。"""

        self._run_scheduled_pipeline_if_available(
            owner="weekly_content_production",
            handler=self._run_weekly_content_production_job_unlocked,
        )

    def _run_weekly_content_production_job_unlocked(self) -> None:
        """在已持有流水线锁的前提下执行周五 08:00 内容生产。"""

        assert self.config is not None
        self._refresh_skill_stars()
        self._run_task(SearchTask)
        self._run_task(SummaryTask)
        if self.config.video_submit_enabled:
            self._run_task(ShortVideoPromptTask)
        self._run_task(ImageTask)
        if self.config.audio_enabled:
            self._run_task(AudioTask)
        if self.config.video_submit_enabled:
            self._run_task(VideoClipPlanTask)
        self._run_task(StorageTask)
        if self.config.video_submit_enabled:
            self._run_task(SeedanceClipTask)
        self._run_task(PreviewTask)
        self._run_task(CatTask)

    def _run_weekly_draft_creation_job(self) -> None:
        """周五 09:00 草稿推进 Job：刷新素材状态、排版、创建公众号草稿。"""

        self._run_scheduled_pipeline_if_available(
            owner="weekly_draft_creation",
            handler=self._run_weekly_draft_creation_job_unlocked,
        )

    def _run_weekly_draft_creation_job_unlocked(self) -> None:
        """在已持有流水线锁的前提下执行周五 09:00 草稿推进。"""

        assert self.config is not None
        if self.config.audio_enabled:
            self._run_task(AudioTask)
        self._run_task(StorageTask)
        if self.config.video_submit_enabled:
            self._run_task(SeedanceClipStatusTask)
            self._run_task(VideoVisualQualityTask)
            self._run_task(SeedanceClipTask)
            self._run_task(SeedanceClipStatusTask)
            self._run_task(VideoNarrationTimelineTask)
            self._run_task(SegmentedAudioTask)
            self._run_task(VideoAssemblyTask)
            self._run_task(StorageTask)
            self._run_task(VideoStatusTask)
        self._run_task(PreviewTask)
        self._run_task(ArticleLayoutTask)
        self._run_task(DeliverTask)
        self._run_task(CatTask)

    def _run_scheduler_forever(self) -> None:
        """进入常驻调度模式。"""
        assert self.scheduler_manager is not None
        self._refresh_skill_stars()
        self.scheduler_manager.run_forever(
            handlers={
                "weekly_content_production": self._run_weekly_content_production_job,
                "weekly_draft_creation": self._run_weekly_draft_creation_job,
            }
        )

    def _refresh_skill_stars(self) -> None:
        """刷新已过期的 Skill Star 快照；失败不阻断内容 Scheduler。"""

        assert self.config is not None
        assert self.logger is not None
        try:
            summary = SkillLibraryService(self.config).refresh_stale_star_snapshots()
        except Exception:
            self.logger.exception("Skill Star 快照刷新失败")
            return
        self.logger.info(
            "Skill Star 快照刷新完成：repositories=%s refreshed=%s unchanged=%s failed=%s",
            summary["repositories"],
            summary["refreshed"],
            summary["unchanged"],
            summary["failed"],
        )

    def _run_startup_self_check(self) -> None:
        """运行基础设施自检 Task，用于验证 task_runs 写入链路。"""
        self._run_task(StartupSelfCheckTask)

    def _preview_scheduler(self) -> None:
        """预览已注册 Job 的下次运行时间。"""
        assert self.scheduler_manager is not None
        self.scheduler_manager.preview_registered_jobs()

    def _run_task(self, task_class: type[BaseTask]) -> TaskResult:
        """创建并运行一个 Task，统一注入任务状态仓储和上下文。"""
        task = self._create_task(task_class)
        result = task.run(self._build_task_context())
        assert self.logger is not None
        self.logger.info("%s 完成：run_id=%s", result.task_name, result.run_id)
        return result

    def _run_exclusive_pipeline(
        self,
        *,
        owner: str,
        handler: Any,
    ) -> Any:
        """为手动或一次性运行取得跨容器互斥锁；冲突交给调用方记录失败。"""

        lock = self._create_pipeline_execution_lock()
        with lock.hold(owner):
            return trace_operation(
                run_name="wechat.content_pipeline",
                run_type="chain",
                inputs={"trigger": owner},
                metadata={
                    "component": "wechat_content_pipeline",
                    "trigger": owner,
                    "privacy_mode": "metadata_only",
                },
                tags=("wechat", "multi-agent", "pipeline"),
                execute=handler,
                summarize=self._summarize_pipeline_result,
            )

    @staticmethod
    def _summarize_pipeline_result(result: Any) -> dict[str, Any]:
        """只向 LangSmith 返回任务数量，不发送文章、Prompt 或媒体地址。"""

        if isinstance(result, list):
            return {
                "completed": True,
                "task_count": len(result),
            }
        return {"completed": True}

    def _run_scheduled_pipeline_if_available(self, *, owner: str, handler: Any) -> None:
        """调度任务遇到手动运行时安全跳过，保持常驻 Scheduler 进程继续工作。"""

        try:
            self._run_exclusive_pipeline(owner=owner, handler=handler)
        except PipelineAlreadyRunningError as exc:
            assert self.logger is not None
            self.logger.warning("跳过 %s：%s", owner, exc)

    def _create_pipeline_execution_lock(self) -> PipelineExecutionLock:
        """把锁文件放在与旧 SQLite 同一共享数据目录，保证跨容器可见。"""

        assert self.config is not None
        return PipelineExecutionLock(
            self.config.database_path.parent / ".pipeline-execution.lock",
            stale_after_seconds=self.config.pipeline_execution_lock_stale_seconds,
        )

    def _create_task(self, task_class: type[BaseTask]) -> BaseTask:
        """创建 Task 实例。"""
        assert self.task_run_repository is not None
        assert self.error_event_repository is not None
        return task_class(
            task_run_repository=self.task_run_repository,
            error_event_repository=self.error_event_repository,
        )

    def _build_task_context(self) -> TaskContext:
        """创建 Task 执行时共享的只读上下文。"""
        assert self.config is not None
        assert self.database_manager is not None
        return TaskContext(
            config=self.config,
            database_manager=self.database_manager,
        )
