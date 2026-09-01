from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.app.application import Application
from src.config.config_manager import ConfigManager
from src.services.wechat_draft_creation_service import WechatDraftCreationService
from src.tasks.audio_task import AudioTask
from src.tasks.cat_task import CatTask
from src.tasks.segmented_audio_task import SegmentedAudioTask
from src.tasks.storage_task import StorageTask


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MediaChannelConfigTests(unittest.TestCase):
    def test_local_defaults_disable_video_submission_and_audio_generation(self) -> None:
        with patch.dict(
            os.environ,
            {"AUDIO_ENABLED": "", "VIDEO_SUBMIT_ENABLED": ""},
            clear=False,
        ):
            config = ConfigManager(project_root=PROJECT_ROOT).load()

        self.assertFalse(config.audio_enabled)
        self.assertFalse(config.video_submit_enabled)

    def test_audio_environment_override_is_supported(self) -> None:
        with patch.dict(os.environ, {"AUDIO_ENABLED": "true"}, clear=False):
            config = ConfigManager(project_root=PROJECT_ROOT).load()
            self.assertTrue(config.audio_enabled)


class AudioCallGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_run_repository = Mock()
        self.error_event_repository = Mock()
        self.context = SimpleNamespace(
            config=SimpleNamespace(audio_enabled=False),
            database_manager=None,
        )

    def test_audio_task_stops_before_repository_or_provider_calls(self) -> None:
        task = AudioTask(self.task_run_repository, self.error_event_repository)

        result = task.execute(self.context)

        self.assertEqual("audio.enabled=false", result["skip_reason"])
        self.assertTrue(result["disabled_by_config"])
        self.assertFalse(result["network_called"])

    def test_segmented_audio_task_stops_before_repository_or_provider_calls(self) -> None:
        task = SegmentedAudioTask(self.task_run_repository, self.error_event_repository)

        result = task.execute(self.context)

        self.assertEqual("audio.enabled=false", result["skip_reason"])
        self.assertTrue(result["disabled_by_config"])
        self.assertFalse(result["network_called"])


class PipelineChannelSelectionTests(unittest.TestCase):
    def test_manual_pipeline_excludes_all_audio_and_video_tasks_when_disabled(self) -> None:
        config = SimpleNamespace(audio_enabled=False, video_submit_enabled=False)

        task_names = {
            task_class.task_name
            for task_class in Application._once_pipeline_task_classes(config)
        }

        self.assertTrue({"SummaryTask", "ImageTask", "ArticleLayoutTask", "DeliverTask"} <= task_names)
        self.assertTrue(
            {
                "AudioTask",
                "SegmentedAudioTask",
                "ShortVideoPromptTask",
                "VideoClipPlanTask",
                "SeedanceClipTask",
                "SeedanceClipStatusTask",
                "VideoVisualQualityTask",
                "VideoNarrationTimelineTask",
                "VideoAssemblyTask",
                "VideoStatusTask",
            }.isdisjoint(task_names)
        )

    def test_storage_only_keeps_enabled_media_types(self) -> None:
        config = SimpleNamespace(
            storage_asset_types=["image", "audio", "video"],
            audio_enabled=False,
            video_submit_enabled=False,
        )

        self.assertEqual(["image"], StorageTask._enabled_asset_types(config))

    def test_disabled_media_tasks_do_not_block_cat_health(self) -> None:
        config = SimpleNamespace(audio_enabled=False, video_submit_enabled=False)

        self.assertTrue(CatTask._task_channel_disabled(config, "AudioTask"))
        self.assertTrue(CatTask._task_channel_disabled(config, "SeedanceClipTask"))
        self.assertFalse(CatTask._task_channel_disabled(config, "ImageTask"))


class WechatVideoGateTests(unittest.TestCase):
    def test_wechat_draft_does_not_upload_historical_video_when_channel_is_disabled(self) -> None:
        service = WechatDraftCreationService()
        client = Mock()
        config = SimpleNamespace(video_submit_enabled=False)
        layout = SimpleNamespace(title="测试文章", digest="摘要")
        video_asset = SimpleNamespace(
            asset_type="video",
            status="created",
            id=1,
            path="outputs/videos/missing.mp4",
        )

        result = service._try_upload_video_material(
            config=config,
            client=client,
            access_token="token",
            layout=layout,
            media_assets=[video_asset],
        )

        self.assertIsNone(result)
        client.add_permanent_material.assert_not_called()


if __name__ == "__main__":
    unittest.main()
