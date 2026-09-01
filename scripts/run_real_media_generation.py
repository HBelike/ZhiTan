"""执行一次不触发公众号投递的真实媒体生成验证。

该脚本只用于人工明确授权后的本地验证：它会真实调用 GitHub、DeepSeek、
Seedream、豆包语音和 Seedance，并在视频片段完成后进行本地装配；不会执行
ArticleLayoutTask 或 DeliverTask，因此不会创建微信公众号草稿、更不会发布。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.tasks.audio_task import AudioTask
from src.tasks.cat_task import CatTask
from src.tasks.image_task import ImageTask
from src.tasks.preview_task import PreviewTask
from src.tasks.search_task import SearchTask
from src.tasks.seedance_clip_status_task import SeedanceClipStatusTask
from src.tasks.seedance_clip_task import SeedanceClipTask
from src.tasks.segmented_audio_task import SegmentedAudioTask
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.storage_task import StorageTask
from src.tasks.summary_task import SummaryTask
from src.tasks.video_assembly_task import VideoAssemblyTask
from src.tasks.video_clip_plan_task import VideoClipPlanTask
from src.tasks.video_narration_timeline_task import VideoNarrationTimelineTask
from src.tasks.task_result import TaskResult


STAGE_TASKS: dict[str, tuple[type, ...]] = {
    "storyboard": (ShortVideoPromptTask,),
    "images": (ImageTask,),
    "audio": (AudioTask,),
    "video-plan": (VideoClipPlanTask,),
    "video-submit": (SeedanceClipTask,),
    "video-status": (SeedanceClipStatusTask,),
    "narration": (VideoNarrationTimelineTask,),
    "segmented-audio": (SegmentedAudioTask,),
    "assembly": (VideoAssemblyTask,),
    "storage": (StorageTask,),
    "preview": (PreviewTask,),
    "health": (CatTask,),
}


def parse_arguments() -> argparse.Namespace:
    """读取轮询参数，默认值适合一次人工观察的真实测试。"""

    parser = argparse.ArgumentParser(description="运行真实 Seedance 媒体生成测试，不投递微信公众号。")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seedance 状态轮询间隔（秒）。")
    parser.add_argument("--timeout", type=int, default=1_200, help="等待全部视频片段完成的最长时间（秒）。")
    parser.add_argument(
        "--resume-existing-content",
        action="store_true",
        help="复用最新已生成内容，从短视频蓝图开始继续，不重复调用 GitHub 和 SummaryTask。",
    )
    parser.add_argument(
        "--stage",
        choices=("all", *STAGE_TASKS.keys()),
        default="all",
        help=(
            "只执行一个可独立恢复的阶段。用于受限终端逐段验证真实媒体链路；"
            "stage 模式始终复用最新 content，不会重复搜索或生成摘要。"
        ),
    )
    return parser.parse_args()


def main() -> int:
    """按受控顺序执行真实媒体任务，并将简洁状态写入本地结果文件。"""

    args = parse_arguments()
    if args.poll_interval < 5:
        raise ValueError("--poll-interval 不能小于 5 秒")
    if args.timeout < args.poll_interval:
        raise ValueError("--timeout 必须不小于 --poll-interval")

    application = Application(PROJECT_ROOT)
    application.initialize()
    assert application.config is not None

    # 只修改当前进程的只读配置快照，绝不写回 config/app.yaml。
    # 这是用户已明确授权的一次真实视频生成开关，不会影响后续定时任务。
    raw_config = deepcopy(application.config.raw)
    raw_config.setdefault("video", {})["submit_enabled"] = True
    application.config = replace(application.config, raw=raw_config)

    output_path = PROJECT_ROOT / "outputs" / "real-media-test" / "latest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "mode": "real_media_generation_no_wechat_delivery",
        "stage": args.stage,
        "video_submission_enabled_in_memory": True,
        "task_results": [],
        "final_status": "running",
    }

    try:
        with application._create_pipeline_execution_lock().hold("real_media_generation_test"):
            if args.stage != "all":
                return _run_single_stage(
                    application=application,
                    stage=args.stage,
                    report=report,
                    output_path=output_path,
                )

            # 到 Seedance 提交为止是真实外部生成链路；刻意排除排版和公众号投递。
            preparation_tasks = (
                ShortVideoPromptTask,
                ImageTask,
                AudioTask,
                VideoClipPlanTask,
                StorageTask,
                SeedanceClipTask,
            ) if args.resume_existing_content else (
                SearchTask,
                SummaryTask,
                ShortVideoPromptTask,
                ImageTask,
                AudioTask,
                VideoClipPlanTask,
                StorageTask,
                SeedanceClipTask,
            )
            for task_class in preparation_tasks:
                result = application._run_task(task_class)
                _append_result(report, result)
                _write_report(output_path, report)
                if task_class is SeedanceClipTask and bool(result.metadata.get("skipped")):
                    report["final_status"] = "video_submission_not_started"
                    _write_report(output_path, report)
                    _print_event("SeedanceClipTask", result.metadata)
                    return 2

            deadline = time.monotonic() + args.timeout
            while True:
                status_result = application._run_task(SeedanceClipStatusTask)
                _append_result(report, status_result)
                _write_report(output_path, report)
                _print_event("SeedanceClipStatusTask", status_result.metadata)

                if bool(status_result.metadata.get("all_completed")):
                    break
                if int(status_result.metadata.get("failed_count", 0) or 0) > 0:
                    report["final_status"] = "video_clip_failed"
                    _write_report(output_path, report)
                    return 2
                if time.monotonic() >= deadline:
                    report["final_status"] = "video_clip_wait_timeout"
                    _write_report(output_path, report)
                    return 2
                time.sleep(args.poll_interval)

            # 片段完成后才生成与真实时长对齐的旁白、字幕和最终 MP4。
            for task_class in (
                VideoNarrationTimelineTask,
                SegmentedAudioTask,
                VideoAssemblyTask,
                StorageTask,
                PreviewTask,
                CatTask,
            ):
                result = application._run_task(task_class)
                _append_result(report, result)
                _write_report(output_path, report)

            report["final_status"] = "completed"
            _write_report(output_path, report)
            return 0
    except Exception as exc:
        report["final_status"] = "failed"
        report["error_type"] = exc.__class__.__name__
        report["error_message"] = str(exc)[:600]
        _write_report(output_path, report)
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        _write_report(output_path, report)


def _run_single_stage(
    *,
    application: Application,
    stage: str,
    report: dict[str, Any],
    output_path: Path,
) -> int:
    """执行一个可恢复阶段，避免受限前台终端中断长媒体工作流。

    每次进程只持有锁到单一 Task 返回为止。任务产生的内容、素材和远程
    Seedance task_id 都已落库，因此下一阶段可通过最新 content 幂等续跑。
    该函数不调用排版或 DeliverTask，也不改写持久化 video.submit_enabled 配置。
    """

    task_classes = STAGE_TASKS[stage]
    for task_class in task_classes:
        result = application._run_task(task_class)
        _append_result(report, result)

        if task_class is SeedanceClipTask and bool(result.metadata.get("skipped")):
            report["final_status"] = "video_submission_not_started"
            _write_report(output_path, report)
            return 2

        if task_class is SeedanceClipStatusTask:
            if int(result.metadata.get("failed_count", 0) or 0) > 0:
                report["final_status"] = "video_clip_failed"
                _write_report(output_path, report)
                return 2
            if not bool(result.metadata.get("all_completed")):
                report["final_status"] = "video_clips_processing"
                _write_report(output_path, report)
                return 0

    report["final_status"] = "completed"
    _write_report(output_path, report)
    return 0


def _append_result(report: dict[str, Any], result: TaskResult) -> None:
    """只保存可复核的任务摘要，不把远程响应、URL 或任何密钥写进测试报告。"""

    metadata = result.metadata
    report["task_results"].append(
        {
            "task_name": result.task_name,
            "run_id": result.run_id,
            "content_id": metadata.get("content_id"),
            "skipped": bool(metadata.get("skipped")),
            "skip_reason": metadata.get("skip_reason"),
            "submitted_clip_count": metadata.get("submitted_clip_count"),
            "completed_count": metadata.get("completed_count"),
            "processing_count": metadata.get("processing_count"),
            "failed_count": metadata.get("failed_count"),
            "video_asset_id": metadata.get("video_asset_id"),
        }
    )
    _print_event(result.task_name, metadata)


def _print_event(task_name: str, metadata: dict[str, Any]) -> None:
    """向控制台输出一次简洁进度，便于前台轮询和人工定位。"""

    payload = {
        "task": task_name,
        "content_id": metadata.get("content_id"),
        "skipped": bool(metadata.get("skipped")),
        "skip_reason": metadata.get("skip_reason"),
        "submitted_clip_count": metadata.get("submitted_clip_count"),
        "completed_count": metadata.get("completed_count"),
        "processing_count": metadata.get("processing_count"),
        "failed_count": metadata.get("failed_count"),
        "video_asset_id": metadata.get("video_asset_id"),
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """原子写入本次测试摘要，防止轮询时读取到半截 JSON。"""

    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
