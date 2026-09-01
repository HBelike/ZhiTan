from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _optional_env_bool(name: str) -> bool | None:
    """读取可选布尔环境变量；未配置时返回 ``None``。"""

    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"环境变量 {name} 必须是 true/false、1/0、yes/no 或 on/off"
    )


@dataclass(frozen=True)
class AppConfig:
    """应用配置快照，供启动后的各模块只读使用。"""

    project_root: Path
    config_path: Path
    raw: dict[str, Any]

    @property
    def app_name(self) -> str:
        return str(self.raw["app"]["name"])

    @property
    def run_mode(self) -> str:
        configured = os.getenv("APP_RUN_MODE", "").strip() or str(self.raw["app"].get("run_mode", "once"))
        return configured.strip().lower()

    @property
    def timezone_name(self) -> str:
        return str(self.raw["app"].get("timezone", "Asia/Shanghai"))

    @property
    def log_level(self) -> str:
        return str(self.raw["logging"]["level"])

    @property
    def log_dir(self) -> Path:
        return self.project_root / str(self.raw["logging"]["dir"])

    @property
    def log_file(self) -> str:
        return str(self.raw["logging"]["file"])

    @property
    def database_path(self) -> Path:
        configured_path = Path(str(self.raw["database"]["path"]))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def database_timeout_seconds(self) -> float:
        return float(self.raw["database"].get("timeout_seconds", 30))

    @property
    def production_day(self) -> str:
        return str(self.raw["schedule"]["production_day"])

    @property
    def production_time(self) -> str:
        return str(self.raw["schedule"]["production_time"])

    @property
    def draft_time(self) -> str:
        return str(self.raw["schedule"]["draft_time"])

    @property
    def scheduler_loop_interval_seconds(self) -> int:
        return int(self.raw["schedule"].get("loop_interval_seconds", 60))

    @property
    def pipeline_execution_lock_stale_seconds(self) -> int:
        """返回单机跨容器流水线锁的最长保留时间。"""

        return int(self.raw["schedule"].get("pipeline_execution_lock_stale_seconds", 21_600))

    @property
    def github_api_base_url(self) -> str:
        return str(self.raw["github"]["api_base_url"])

    @property
    def github_search_endpoint(self) -> str:
        return str(self.raw["github"]["search_endpoint"])

    @property
    def github_api_version(self) -> str:
        return str(self.raw["github"].get("api_version", "2022-11-28"))

    @property
    def github_token_env(self) -> str:
        return str(self.raw["github"].get("token_env", "GITHUB_TOKEN"))

    @property
    def github_search_query(self) -> str:
        return str(self.raw["github"].get("search_query", "fork:false archived:false"))

    @property
    def github_min_stars(self) -> int:
        return int(self.raw["github"].get("min_stars", 1))

    @property
    def github_pushed_within_days(self) -> int:
        return int(self.raw["github"].get("pushed_within_days", 30))

    @property
    def github_sort(self) -> str:
        return str(self.raw["github"].get("sort", "stars"))

    @property
    def github_order(self) -> str:
        return str(self.raw["github"].get("order", "desc"))

    @property
    def github_candidate_limit(self) -> int:
        return int(self.raw["github"].get("candidate_limit", 100))

    @property
    def github_per_page(self) -> int:
        return int(self.raw["github"].get("per_page", 50))

    @property
    def github_timeout_seconds(self) -> float:
        return float(self.raw["github"].get("timeout_seconds", 30))

    @property
    def ranking_top_n(self) -> int:
        return int(self.raw["ranking"].get("top_n", 5))

    def runtime_prompt(self, name: str) -> str:
        """读取管理员为一次运行覆盖的提示词；未配置时保持系统模板。"""

        prompts = self.raw.get("runtime_prompts", {})
        if not isinstance(prompts, dict):
            return ""
        return str(prompts.get(name, "")).strip()

    @property
    def ranking_growth_weight(self) -> float:
        return float(self.raw["ranking"].get("growth_weight", 0.7))

    @property
    def ranking_growth_rate_weight(self) -> float:
        return float(self.raw["ranking"].get("growth_rate_weight", 0.2))

    @property
    def ranking_star_weight(self) -> float:
        return float(self.raw["ranking"].get("star_weight", 0.1))

    @property
    def llm_provider(self) -> str:
        return str(self.raw["llm"].get("provider", "deepseek"))

    @property
    def llm_base_url(self) -> str:
        return str(self.raw["llm"]["base_url"])

    @property
    def llm_chat_completions_endpoint(self) -> str:
        return str(self.raw["llm"].get("chat_completions_endpoint", "/chat/completions"))

    @property
    def llm_api_key_env(self) -> str:
        return str(self.raw["llm"].get("api_key_env", "DEEPSEEK_API_KEY"))

    @property
    def llm_model(self) -> str:
        return str(self.raw["llm"]["model"])

    @property
    def llm_temperature(self) -> float:
        return float(self.raw["llm"].get("temperature", 0.4))

    @property
    def llm_max_tokens(self) -> int:
        return int(self.raw["llm"].get("max_tokens", 4096))

    @property
    def llm_timeout_seconds(self) -> float:
        return float(self.raw["llm"].get("timeout_seconds", 90))

    @property
    def llm_response_format_json(self) -> bool:
        return bool(self.raw["llm"].get("response_format_json", True))

    @property
    def image_provider(self) -> str:
        return str(self.raw["image"].get("provider", "seedream"))

    @property
    def image_paid_generation_enabled(self) -> bool:
        return bool(self.raw["image"].get("paid_generation_enabled", True))

    @property
    def image_base_url(self) -> str:
        return str(self.raw["image"]["base_url"])

    @property
    def image_generations_endpoint(self) -> str:
        return str(self.raw["image"].get("generations_endpoint", "/images/generations"))

    @property
    def image_api_key_env(self) -> str:
        return str(self.raw["image"].get("api_key_env", "VOLCENGINE_ARK_API_KEY"))

    @property
    def image_model(self) -> str:
        return str(self.raw["image"]["model"])

    @property
    def image_size(self) -> str:
        return str(self.raw["image"].get("size", "2048x1152"))

    @property
    def image_n(self) -> int:
        return int(self.raw["image"].get("n", 1))

    @property
    def image_response_format(self) -> str:
        return str(self.raw["image"].get("response_format", "url"))

    @property
    def image_watermark(self) -> bool:
        return bool(self.raw["image"].get("watermark", True))

    @property
    def image_timeout_seconds(self) -> float:
        return float(self.raw["image"].get("timeout_seconds", 180))

    @property
    def image_renderer(self) -> str:
        return str(self._image_renderer_raw().get("name", "gotenberg_html"))

    @property
    def image_gotenberg_base_url(self) -> str:
        environment_url = os.getenv("CAREER_GOTENBERG_SERVICE_BASE_URL", "").strip()
        if environment_url:
            return environment_url
        return str(
            self._image_renderer_raw().get(
                "gotenberg_base_url",
                "http://127.0.0.1:3000",
            )
        )

    @property
    def image_gotenberg_timeout_seconds(self) -> float:
        return float(self._image_renderer_raw().get("timeout_seconds", 60))

    @property
    def image_renderer_max_attempts(self) -> int:
        return int(self._image_renderer_raw().get("max_attempts", 5))

    @property
    def image_canvas_width(self) -> int:
        return int(self._image_renderer_raw().get("width", 2048))

    @property
    def image_canvas_height(self) -> int:
        return int(self._image_renderer_raw().get("height", 1152))

    @property
    def image_template_version(self) -> str:
        return str(self._image_renderer_raw().get("template_version", "article_visual_v1"))

    @property
    def image_renderer_version(self) -> str:
        return str(self._image_renderer_raw().get("renderer_version", "gotenberg_html_v1"))

    @property
    def image_font_path(self) -> Path:
        configured_path = Path(
            str(
                self._image_renderer_raw().get(
                    "font_path",
                    "assets/fonts/NotoSansSC-VF.ttf",
                )
            )
        )
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def image_font_version(self) -> str:
        return str(
            self._image_renderer_raw().get(
                "font_version",
                "noto-cjk-2.004-523d033d",
            )
        )

    @property
    def image_concept_background_enabled(self) -> bool:
        return bool(
            self._image_renderer_raw().get(
                "concept_background_enabled",
                False,
            )
        )

    @property
    def image_output_dir(self) -> Path:
        configured_path = Path(str(self.raw["image"].get("output_dir", "outputs/images")))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def image_skip_when_api_key_missing(self) -> bool:
        return bool(self.raw["image"].get("skip_when_api_key_missing", True))

    @property
    def image_refresh_existing_assets_on_run(self) -> bool:
        return bool(self.raw["image"].get("refresh_existing_assets_on_run", True))

    @property
    def image_github_asset_fallback_enabled(self) -> bool:
        return bool(self.raw["image"].get("github_asset_fallback_enabled", True))

    @property
    def image_github_asset_timeout_seconds(self) -> float:
        return float(self.raw["image"].get("github_asset_timeout_seconds", 20))

    @property
    def image_github_asset_max_bytes(self) -> int:
        return int(self.raw["image"].get("github_asset_max_bytes", 5 * 1024 * 1024))

    @property
    def image_github_asset_max_candidate_attempts(self) -> int:
        return int(self.raw["image"].get("github_asset_max_candidate_attempts", 5))

    @property
    def image_github_asset_allowed_extensions(self) -> list[str]:
        raw_extensions = self.raw["image"].get(
            "github_asset_allowed_extensions",
            [".png", ".jpg", ".jpeg", ".webp", ".gif"],
        )
        if not isinstance(raw_extensions, list):
            return [".png", ".jpg", ".jpeg", ".webp", ".gif"]

        normalized: list[str] = []
        for item in raw_extensions:
            extension = str(item).strip().lower()
            if not extension:
                continue
            if not extension.startswith("."):
                extension = "." + extension
            normalized.append(extension)
        return normalized or [".png", ".jpg", ".jpeg", ".webp", ".gif"]

    @property
    def image_local_fallback_enabled(self) -> bool:
        return bool(self.raw["image"].get("local_fallback_enabled", True))

    @property
    def image_chinese_overlay_enabled(self) -> bool:
        return bool(self.raw["image"].get("chinese_overlay_enabled", True))

    @property
    def image_prompt_max_length(self) -> int:
        return int(self._image_prompt_raw().get("max_length", 900))

    @property
    def image_prompt_visual_system(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "visual_system",
                "无标题的16:9工程架构信息图，白色画布，深蓝核心卡片，浅灰普通卡片，扁平矢量，精细对齐，克制留白。",
            )
        )

    @property
    def image_prompt_composition_rule(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "composition_rule",
                "严格按指定数量和位置绘制节点，每个节点只出现一次；正交箭头只连接指定起止节点，禁止镜像、复制或补齐对称节点。",
            )
        )

    @property
    def image_prompt_text_rule(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "text_rule",
                "只显示指定的节点名称，连线不生成文字；禁止标题区、正文段落、仓库名和额外文字。",
            )
        )

    @property
    def image_prompt_style_rule(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "style_rule",
                "核心节点使用深蓝底白字，普通节点使用浅灰底深色字；突出组件边界和数据流向，不使用空白占位卡片。",
            )
        )

    @property
    def image_prompt_safe_zone_rule(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "safe_zone_rule",
                "主体位于画面中心 80% 安全区，四周保留足够边距。",
            )
        )

    @property
    def image_prompt_negative_prompt(self) -> str:
        return str(
            self._image_prompt_raw().get(
                "negative_prompt",
                "不要标题区、装饰点、重复节点、镜像节点、空白占位卡片、曲线、交叉线、logo、水印、伪文字或乱码。",
            )
        )

    def _image_prompt_raw(self) -> dict[str, Any]:
        """读取 image.prompt 配置段；缺失时返回空配置。"""

        raw_prompt = self.raw["image"].get("prompt", {})
        if not isinstance(raw_prompt, dict):
            return {}
        return raw_prompt

    def _image_renderer_raw(self) -> dict[str, Any]:
        """读取确定性图片渲染配置段；缺失时返回空配置。"""

        raw_renderer = self.raw["image"].get("renderer", {})
        if not isinstance(raw_renderer, dict):
            return {}
        return raw_renderer

    @property
    def audio_provider(self) -> str:
        return str(self.raw["audio"].get("provider", "doubao_tts"))

    @property
    def audio_enabled(self) -> bool:
        """是否允许生成音频；工作台快照优先，其他运行可由环境变量控制。"""

        runtime_override = self.raw["audio"].get("runtime_enabled")
        if runtime_override is not None:
            return bool(runtime_override)
        environment_override = _optional_env_bool("AUDIO_ENABLED")
        if environment_override is not None:
            return environment_override
        return bool(self.raw["audio"].get("enabled", False))

    @property
    def audio_api_url(self) -> str:
        return str(self.raw["audio"]["api_url"])

    @property
    def audio_api_key_env(self) -> str:
        """新版豆包语音 API Key 的环境变量名称。"""
        return str(self.raw["audio"].get("api_key_env", "DOUBAO_TTS_API_KEY"))

    @property
    def audio_resource_id(self) -> str:
        """新版豆包语音 V3 的资源标识。"""
        return str(self.raw["audio"].get("resource_id", "seed-tts-2.0"))

    @property
    def audio_voice_type_env(self) -> str:
        return str(self.raw["audio"].get("voice_type_env", "DOUBAO_TTS_VOICE_TYPE"))

    @property
    def audio_default_voice_type(self) -> str:
        return str(self.raw["audio"].get("default_voice_type", "zh_female_vv_uranus_bigtts"))

    @property
    def audio_encoding(self) -> str:
        return str(self.raw["audio"].get("encoding", "mp3"))

    @property
    def audio_speed_ratio(self) -> float:
        return float(self.raw["audio"].get("speed_ratio", 1.0))

    @property
    def audio_rate(self) -> int:
        return int(self.raw["audio"].get("rate", 24000))

    @property
    def audio_max_input_utf8_bytes(self) -> int:
        """单次豆包短文本 TTS 请求允许的最大 UTF-8 字节数。"""
        return int(self.raw["audio"].get("max_input_utf8_bytes", 900))

    @property
    def audio_timeout_seconds(self) -> float:
        return float(self.raw["audio"].get("timeout_seconds", 120))

    @property
    def audio_output_dir(self) -> Path:
        configured_path = Path(str(self.raw["audio"].get("output_dir", "outputs/audio")))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def audio_skip_when_api_key_missing(self) -> bool:
        return bool(self.raw["audio"].get("skip_when_api_key_missing", True))

    @property
    def audio_local_fallback_enabled(self) -> bool:
        return bool(self.raw["audio"].get("local_fallback_enabled", True))

    @property
    def video_provider(self) -> str:
        return str(self.raw["video"].get("provider", "seedance2"))

    @property
    def video_submit_enabled(self) -> bool:
        runtime_override = self.raw["video"].get("runtime_submit_enabled")
        if runtime_override is not None:
            return bool(runtime_override)
        environment_override = _optional_env_bool("VIDEO_SUBMIT_ENABLED")
        if environment_override is not None:
            return environment_override
        return bool(self.raw["video"].get("submit_enabled", False))

    @property
    def video_base_url(self) -> str:
        return str(self.raw["video"]["base_url"])

    @property
    def video_generation_endpoint(self) -> str:
        return str(self.raw["video"].get("generation_endpoint", "/v1/videos/generations"))

    @property
    def video_task_status_endpoint_template(self) -> str:
        return str(self.raw["video"].get("task_status_endpoint_template", "/v1/videos/generations/{task_id}"))

    @property
    def video_api_key_env(self) -> str:
        return str(self.raw["video"].get("api_key_env", "SEEDANCE_API_KEY"))

    @property
    def video_model(self) -> str:
        return str(self.raw["video"]["model"])

    @property
    def video_aspect_ratio(self) -> str:
        return str(self.raw["video"].get("aspect_ratio", "16:9"))

    @property
    def video_resolution(self) -> str:
        return str(self.raw["video"].get("resolution", "720p"))

    @property
    def video_clip_duration_seconds(self) -> int:
        return int(self.raw["video"].get("clip_duration_seconds", 8))

    @property
    def video_min_clip_duration_seconds(self) -> int:
        """Seedance 单个视觉片段允许的最短请求时长。"""
        return int(self.raw["video"].get("min_clip_duration_seconds", 4))

    @property
    def video_max_clip_duration_seconds(self) -> int:
        """Seedance 单个视觉片段允许的最长请求时长。"""
        return int(self.raw["video"].get("max_clip_duration_seconds", 15))

    @property
    def video_duration_seconds(self) -> int:
        return int(self.raw["video"].get("duration_seconds", 60))

    @property
    def video_required_image_count(self) -> int:
        return int(self.raw["video"].get("required_image_count", 5))

    @property
    def video_generation_type(self) -> str:
        return str(self.raw["video"].get("generation_type", "reference-to-video"))

    @property
    def video_reference_images_enabled(self) -> bool:
        """是否允许当前视频生成链路向 Seedance 提交参考图。"""
        return bool(self.raw["video"].get("reference_images_enabled", False))

    @property
    def video_generate_audio(self) -> bool:
        return bool(self.raw["video"].get("generate_audio", False))

    @property
    def video_watermark(self) -> bool:
        return bool(self.raw["video"].get("watermark", False))

    @property
    def video_web_search(self) -> bool:
        return bool(self.raw["video"].get("web_search", False))

    @property
    def video_return_last_frame(self) -> bool:
        return bool(self.raw["video"].get("return_last_frame", False))

    @property
    def video_seed(self) -> int:
        return int(self.raw["video"].get("seed", -1))

    @property
    def video_timeout_seconds(self) -> float:
        return float(self.raw["video"].get("timeout_seconds", 120))

    @property
    def video_output_dir(self) -> Path:
        configured_path = Path(str(self.raw["video"].get("output_dir", "outputs/videos")))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def video_skip_when_material_missing(self) -> bool:
        return bool(self.raw["video"].get("skip_when_material_missing", True))

    @property
    def video_skip_when_api_key_missing(self) -> bool:
        return bool(self.raw["video"].get("skip_when_api_key_missing", True))

    @property
    def video_local_fallback_enabled(self) -> bool:
        return bool(self.raw["video"].get("local_fallback_enabled", True))

    @property
    def video_assembly_enabled(self) -> bool:
        """是否启用 Seedance 分片下载后的本地无损装配。"""
        return bool(self.raw["video"].get("assembly", {}).get("enabled", True))

    @property
    def video_assembly_require_voiceover(self) -> bool:
        """装配最终视频前是否强制要求已经生成统一旁白。"""
        return bool(self.raw["video"].get("assembly", {}).get("require_voiceover", True))

    @property
    def video_assembly_timeout_seconds(self) -> float:
        """本地 ffmpeg 片段规范化和拼接的最大执行时间。"""
        return float(self.raw["video"].get("assembly", {}).get("timeout_seconds", 900))

    @property
    def video_assembly_timing_mode(self) -> str:
        """最终装配的时序策略。"""
        return str(self.raw["video"].get("assembly", {}).get("timing_mode", "legacy"))

    @property
    def video_assembly_burn_subtitles(self) -> bool:
        """是否把受时间线约束的中文字幕直接烧录进最终视频。"""
        return bool(self.raw["video"].get("assembly", {}).get("burn_subtitles", True))

    @property
    def video_narration_characters_per_second(self) -> float:
        """按真实视频时长生成中文旁白时使用的保守字符速率。"""
        return float(self.raw["video"].get("narration", {}).get("characters_per_second", 3.3))

    @property
    def video_narration_max_tts_speed_ratio(self) -> float:
        """音频略长时允许的最大无感加速比例。"""
        return float(self.raw["video"].get("narration", {}).get("max_tts_speed_ratio", 1.08))

    @property
    def video_narration_tail_padding_seconds(self) -> float:
        """每段旁白结束后预留的静音，避免紧贴切镜。"""
        return float(self.raw["video"].get("narration", {}).get("tail_padding_seconds", 0.2))

    @property
    def video_narration_max_refit_attempts(self) -> int:
        """旁白真实音频超出镜头时允许触发的自动精简次数。"""
        return int(self.raw["video"].get("narration", {}).get("max_refit_attempts", 1))

    @property
    def video_quality_assurance_enabled(self) -> bool:
        """是否对下载完成的视频片段执行抽帧视觉质检。"""
        return bool(self.raw["video"].get("quality_assurance", {}).get("enabled", True))

    @property
    def video_quality_assurance_provider(self) -> str:
        """视频视觉质检服务商标识。"""
        return str(self.raw["video"].get("quality_assurance", {}).get("provider", "aliyun_qwen_vl"))

    @property
    def video_quality_assurance_model(self) -> str:
        """用于视频关键帧理解的视觉模型标识。"""
        return str(self.raw["video"].get("quality_assurance", {}).get("model", "qwen3-vl-plus"))

    @property
    def video_quality_assurance_base_url(self) -> str:
        """视觉模型 OpenAI-compatible API Base URL。"""
        return str(
            self.raw["video"].get("quality_assurance", {}).get(
                "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
        )

    @property
    def video_quality_assurance_api_key_env(self) -> str:
        """保存视觉质检 API Key 的环境变量名。"""
        return str(self.raw["video"].get("quality_assurance", {}).get("api_key_env", "DASHSCOPE_API_KEY"))

    @property
    def video_quality_assurance_sample_frame_count(self) -> int:
        """每个片段的均匀抽帧数。"""
        return int(self.raw["video"].get("quality_assurance", {}).get("sample_frame_count", 3))

    @property
    def video_quality_assurance_minimum_score(self) -> int:
        """自动通过质检的最低综合分。"""
        return int(self.raw["video"].get("quality_assurance", {}).get("minimum_score", 72))

    @property
    def video_quality_assurance_max_regeneration_attempts(self) -> int:
        """单个片段因视觉不合格允许重新生成的最大次数。"""
        return int(self.raw["video"].get("quality_assurance", {}).get("max_regeneration_attempts", 1))

    @property
    def video_quality_assurance_timeout_seconds(self) -> float:
        """视觉模型单次质检请求的超时秒数。"""
        return float(self.raw["video"].get("quality_assurance", {}).get("timeout_seconds", 90))

    @property
    def video_clip_prompt_max_length(self) -> int:
        return int(self._video_clip_prompt_raw().get("max_length", 2400))

    @property
    def video_clip_prompt_visual_system(self) -> str:
        return str(
            self._video_clip_prompt_raw().get(
                "visual_system",
                "所有 clip 必须保持同一套中文技术教学视觉系统：浅纸本或低饱和纹理背景、"
                "清晰模块、箭头和留白表达工程逻辑；各项目按其视觉合同使用不同的克制色板，"
                "不统一回退为蓝绿色霓虹或深色海报。",
            )
        )

    @property
    def video_clip_prompt_continuity_rule(self) -> str:
        return str(
            self._video_clip_prompt_raw().get(
                "continuity_rule",
                "本段不是独立广告片，要像连续课程的一页动态课件；画面必须承接上一段，并为下一段留下自然过渡。",
            )
        )

    @property
    def video_clip_prompt_motion_rule(self) -> str:
        return str(
            self._video_clip_prompt_raw().get(
                "motion_rule",
                "请让画面跟随旁白逐步展开，节点按讲解顺序高亮，流程线自然推进。",
            )
        )

    @property
    def video_clip_prompt_audio_rule(self) -> str:
        """读取单段视频和统一旁白协作的约束。"""
        return str(
            self._video_clip_prompt_raw().get(
                "audio_rule",
                "本片段不要生成旁白、人物口型或内嵌字幕；画面节奏需适配后期统一配入的中文旁白与字幕。",
            )
        )

    @property
    def video_clip_prompt_reference_image_rule(self) -> str:
        return str(
            self._video_clip_prompt_raw().get(
                "reference_image_rule",
                "如果使用参考图，只继承其架构图风格、色彩和模块布局，不要照搬水印或无关文字。",
            )
        )

    @property
    def video_clip_prompt_negative_prompt(self) -> str:
        return str(
            self._video_clip_prompt_raw().get(
                "negative_prompt",
                "避免随机抽象画面、错误 UI、乱码或伪文字、仓库地址、真实 Logo、代码截图、"
                "满屏蓝绿色霓虹、夸张电影镜头和跳切感很重的 slideshow。",
            )
        )

    def _video_clip_prompt_raw(self) -> dict[str, Any]:
        """读取 video.clip_prompt 配置段；缺失时返回空配置。"""

        raw_clip_prompt = self.raw["video"].get("clip_prompt", {})
        if not isinstance(raw_clip_prompt, dict):
            return {}
        return raw_clip_prompt

    @property
    def storage_provider(self) -> str:
        return str(self.raw["storage"].get("provider", "local"))

    @property
    def storage_asset_types(self) -> list[str]:
        raw_asset_types = self.raw["storage"].get("asset_types", ["image", "audio", "video"])
        if not isinstance(raw_asset_types, list):
            return ["image", "audio", "video"]
        return [str(item).strip() for item in raw_asset_types if str(item).strip()]

    @property
    def storage_skip_when_no_assets(self) -> bool:
        return bool(self.raw["storage"].get("skip_when_no_assets", True))

    @property
    def storage_skip_when_unconfigured(self) -> bool:
        return bool(self.raw["storage"].get("skip_when_unconfigured", True))

    @property
    def storage_local_upload_dir(self) -> Path:
        configured_path = Path(str(self.raw["storage"].get("local", {}).get("upload_dir", "outputs/public")))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def storage_local_public_base_url_env(self) -> str:
        return str(self.raw["storage"].get("local", {}).get("public_base_url_env", "STORAGE_LOCAL_PUBLIC_BASE_URL"))

    @property
    def storage_r2_account_id_env(self) -> str:
        return str(self.raw["storage"].get("r2", {}).get("account_id_env", "CLOUDFLARE_R2_ACCOUNT_ID"))

    @property
    def storage_r2_access_key_id_env(self) -> str:
        return str(self.raw["storage"].get("r2", {}).get("access_key_id_env", "CLOUDFLARE_R2_ACCESS_KEY_ID"))

    @property
    def storage_r2_secret_access_key_env(self) -> str:
        return str(self.raw["storage"].get("r2", {}).get("secret_access_key_env", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"))

    @property
    def storage_r2_bucket_env(self) -> str:
        return str(self.raw["storage"].get("r2", {}).get("bucket_env", "CLOUDFLARE_R2_BUCKET"))

    @property
    def storage_r2_public_base_url_env(self) -> str:
        return str(self.raw["storage"].get("r2", {}).get("public_base_url_env", "CLOUDFLARE_R2_PUBLIC_BASE_URL"))

    @property
    def preview_enabled(self) -> bool:
        return bool(self.raw["preview"].get("enabled", True))

    @property
    def preview_output_dir(self) -> Path:
        configured_path = Path(str(self.raw["preview"].get("output_dir", "outputs/preview")))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def preview_media_route_prefix(self) -> str:
        return str(self.raw["preview"].get("media_route_prefix", "/api/media-assets")).rstrip("/")

    @property
    def preview_expose_local_media(self) -> bool:
        return bool(self.raw["preview"].get("expose_local_media", True))


class ConfigManager:
    """负责加载 `.env` 和 `config/app.yaml`，并做最小必要校验。"""

    REQUIRED_SECTIONS = (
        "app",
        "schedule",
        "logging",
        "database",
        "github",
        "ranking",
        "llm",
        "image",
        "audio",
        "video",
        "storage",
        "preview",
    )

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.config_path = project_root / "config" / "app.yaml"
        self.env_path = project_root / ".env"

    def load(self) -> AppConfig:
        """加载配置文件，失败时抛出异常交给 Application 统一处理。"""
        self._load_env_if_exists()
        raw = self._load_yaml()
        self._validate(raw)
        return AppConfig(
            project_root=self.project_root,
            config_path=self.config_path,
            raw=raw,
        )

    def _load_env_if_exists(self) -> None:
        """如果存在 `.env`，就把密钥加载到环境变量。"""
        if not self.env_path.exists():
            return

        try:
            from dotenv import load_dotenv
        except ImportError as exc:
            raise RuntimeError("检测到 .env，但缺少 python-dotenv 依赖") from exc

        load_dotenv(dotenv_path=self.env_path, override=False)

    def _load_yaml(self) -> dict[str, Any]:
        """读取 YAML 配置文件。"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在：{self.config_path}")

        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("缺少 PyYAML 依赖，无法读取 config/app.yaml") from exc

        with self.config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file)

        if not isinstance(loaded, dict):
            raise ValueError("config/app.yaml 必须是 YAML 对象")

        return loaded

    def _validate(self, raw: dict[str, Any]) -> None:
        """校验启动所需的最小配置项。"""
        missing_sections = [name for name in self.REQUIRED_SECTIONS if name not in raw]
        if missing_sections:
            joined = ", ".join(missing_sections)
            raise ValueError(f"config/app.yaml 缺少配置段：{joined}")

        if "name" not in raw["app"]:
            raise ValueError("config/app.yaml 缺少 app.name")

        if "level" not in raw["logging"]:
            raise ValueError("config/app.yaml 缺少 logging.level")

        if "dir" not in raw["logging"]:
            raise ValueError("config/app.yaml 缺少 logging.dir")

        if "file" not in raw["logging"]:
            raise ValueError("config/app.yaml 缺少 logging.file")

        if "path" not in raw["database"]:
            raise ValueError("config/app.yaml 缺少 database.path")

        if "production_day" not in raw["schedule"]:
            raise ValueError("config/app.yaml 缺少 schedule.production_day")

        if "production_time" not in raw["schedule"]:
            raise ValueError("config/app.yaml 缺少 schedule.production_time")

        if "draft_time" not in raw["schedule"]:
            raise ValueError("config/app.yaml 缺少 schedule.draft_time")

        pipeline_execution_lock_stale_seconds = int(
            raw["schedule"].get("pipeline_execution_lock_stale_seconds", 21_600),
        )
        if pipeline_execution_lock_stale_seconds < 900:
            raise ValueError("schedule.pipeline_execution_lock_stale_seconds 不能小于 900")

        if "api_base_url" not in raw["github"]:
            raise ValueError("config/app.yaml 缺少 github.api_base_url")

        if "search_endpoint" not in raw["github"]:
            raise ValueError("config/app.yaml 缺少 github.search_endpoint")

        github_candidate_limit = int(raw["github"].get("candidate_limit", 100))
        if github_candidate_limit <= 0:
            raise ValueError("github.candidate_limit 必须大于 0")

        github_per_page = int(raw["github"].get("per_page", 50))
        if github_per_page <= 0 or github_per_page > 100:
            raise ValueError("github.per_page 必须在 1 到 100 之间")

        github_min_stars = int(raw["github"].get("min_stars", 1))
        if github_min_stars < 0:
            raise ValueError("github.min_stars 不能小于 0")

        github_pushed_within_days = int(raw["github"].get("pushed_within_days", 30))
        if github_pushed_within_days < 0:
            raise ValueError("github.pushed_within_days 不能小于 0")

        ranking_top_n = int(raw["ranking"].get("top_n", 5))
        if ranking_top_n <= 0:
            raise ValueError("ranking.top_n 必须大于 0")

        for key in ("growth_weight", "growth_rate_weight", "star_weight"):
            value = float(raw["ranking"].get(key, 0))
            if value < 0:
                raise ValueError(f"ranking.{key} 不能小于 0")

        if "base_url" not in raw["llm"]:
            raise ValueError("config/app.yaml 缺少 llm.base_url")

        if "model" not in raw["llm"]:
            raise ValueError("config/app.yaml 缺少 llm.model")

        llm_max_tokens = int(raw["llm"].get("max_tokens", 4096))
        if llm_max_tokens <= 0:
            raise ValueError("llm.max_tokens 必须大于 0")

        if "base_url" not in raw["image"]:
            raise ValueError("config/app.yaml 缺少 image.base_url")

        if "model" not in raw["image"]:
            raise ValueError("config/app.yaml 缺少 image.model")

        image_n = int(raw["image"].get("n", 1))
        if image_n <= 0:
            raise ValueError("image.n 必须大于 0")

        image_paid_generation_enabled = raw["image"].get("paid_generation_enabled", True)
        if not isinstance(image_paid_generation_enabled, bool):
            raise ValueError("image.paid_generation_enabled 必须是布尔值")

        image_chinese_overlay_enabled = raw["image"].get("chinese_overlay_enabled", True)
        if not isinstance(image_chinese_overlay_enabled, bool):
            raise ValueError("image.chinese_overlay_enabled 必须是布尔值")

        image_timeout_seconds = float(raw["image"].get("timeout_seconds", 180))
        if image_timeout_seconds <= 0:
            raise ValueError("image.timeout_seconds 必须大于 0")

        image_renderer = raw["image"].get("renderer", {})
        if not isinstance(image_renderer, dict):
            raise ValueError("image.renderer 必须是 YAML 对象")

        renderer_name = str(image_renderer.get("name", "gotenberg_html")).strip()
        if renderer_name not in {"gotenberg_html", "seedream"}:
            raise ValueError("image.renderer.name 仅支持 gotenberg_html 或 seedream")

        gotenberg_base_url = str(
            image_renderer.get("gotenberg_base_url", "http://127.0.0.1:3000")
        ).strip()
        if not gotenberg_base_url:
            raise ValueError("image.renderer.gotenberg_base_url 不能为空")

        renderer_timeout_seconds = float(image_renderer.get("timeout_seconds", 60))
        if renderer_timeout_seconds <= 0:
            raise ValueError("image.renderer.timeout_seconds 必须大于 0")

        renderer_max_attempts = int(image_renderer.get("max_attempts", 5))
        if renderer_max_attempts != 5:
            raise ValueError("image.renderer.max_attempts 必须为 5")

        renderer_width = int(image_renderer.get("width", 2048))
        if renderer_width != 2048:
            raise ValueError("image.renderer.width 必须为 2048")

        renderer_height = int(image_renderer.get("height", 1152))
        if renderer_height != 1152:
            raise ValueError("image.renderer.height 必须为 1152")

        for version_key in ("template_version", "renderer_version", "font_version"):
            if not str(image_renderer.get(version_key, "")).strip():
                raise ValueError(f"image.renderer.{version_key} 不能为空")

        renderer_font_path = Path(str(image_renderer.get("font_path", "")).strip())
        if not str(renderer_font_path):
            raise ValueError("image.renderer.font_path 不能为空")
        resolved_font_path = (
            renderer_font_path
            if renderer_font_path.is_absolute()
            else self.project_root / renderer_font_path
        )
        if not resolved_font_path.is_file():
            raise ValueError(f"image.renderer.font_path 不存在：{resolved_font_path}")

        concept_background_enabled = image_renderer.get(
            "concept_background_enabled",
            False,
        )
        if not isinstance(concept_background_enabled, bool):
            raise ValueError("image.renderer.concept_background_enabled 必须是布尔值")

        image_github_asset_timeout_seconds = float(raw["image"].get("github_asset_timeout_seconds", 20))
        if image_github_asset_timeout_seconds <= 0:
            raise ValueError("image.github_asset_timeout_seconds 必须大于 0")

        image_github_asset_max_bytes = int(raw["image"].get("github_asset_max_bytes", 5 * 1024 * 1024))
        if image_github_asset_max_bytes <= 0:
            raise ValueError("image.github_asset_max_bytes 必须大于 0")

        image_github_asset_max_candidate_attempts = int(raw["image"].get("github_asset_max_candidate_attempts", 5))
        if image_github_asset_max_candidate_attempts <= 0:
            raise ValueError("image.github_asset_max_candidate_attempts 必须大于 0")

        image_github_asset_allowed_extensions = raw["image"].get(
            "github_asset_allowed_extensions",
            [".png", ".jpg", ".jpeg", ".webp", ".gif"],
        )
        if not isinstance(image_github_asset_allowed_extensions, list) or not image_github_asset_allowed_extensions:
            raise ValueError("image.github_asset_allowed_extensions 必须是非空数组")

        image_prompt = raw["image"].get("prompt", {})
        if image_prompt is not None and not isinstance(image_prompt, dict):
            raise ValueError("image.prompt 必须是 YAML 对象")
        if isinstance(image_prompt, dict):
            image_prompt_max_length = int(image_prompt.get("max_length", 900))
            if image_prompt_max_length <= 0:
                raise ValueError("image.prompt.max_length 必须大于 0")

        if "model" not in raw["video"]:
            raise ValueError("config/app.yaml 缺少 video.model")

        if "base_url" not in raw["video"]:
            raise ValueError("config/app.yaml 缺少 video.base_url")

        video_submit_enabled = raw["video"].get("submit_enabled", False)
        if not isinstance(video_submit_enabled, bool):
            raise ValueError("video.submit_enabled 必须是布尔值")

        audio_enabled = raw["audio"].get("enabled", False)
        if not isinstance(audio_enabled, bool):
            raise ValueError("audio.enabled 必须是布尔值")

        video_duration_seconds = int(raw["video"].get("duration_seconds", 60))
        if video_duration_seconds <= 0:
            raise ValueError("video.duration_seconds 必须大于 0")

        video_clip_duration_seconds = int(raw["video"].get("clip_duration_seconds", 8))
        if video_clip_duration_seconds <= 0:
            raise ValueError("video.clip_duration_seconds 必须大于 0")

        video_min_clip_duration_seconds = int(raw["video"].get("min_clip_duration_seconds", 4))
        video_max_clip_duration_seconds = int(raw["video"].get("max_clip_duration_seconds", 15))
        if video_min_clip_duration_seconds <= 0:
            raise ValueError("video.min_clip_duration_seconds 必须大于 0")
        if video_max_clip_duration_seconds < video_min_clip_duration_seconds:
            raise ValueError("video.max_clip_duration_seconds 不能小于 video.min_clip_duration_seconds")

        video_required_image_count = int(raw["video"].get("required_image_count", 5))
        if video_required_image_count < 0:
            raise ValueError("video.required_image_count 不能小于 0")

        video_timeout_seconds = float(raw["video"].get("timeout_seconds", 120))
        if video_timeout_seconds <= 0:
            raise ValueError("video.timeout_seconds 必须大于 0")

        video_assembly = raw["video"].get("assembly", {})
        if video_assembly is not None and not isinstance(video_assembly, dict):
            raise ValueError("video.assembly 必须是 YAML 对象")
        if isinstance(video_assembly, dict):
            assembly_timeout_seconds = float(video_assembly.get("timeout_seconds", 900))
            if assembly_timeout_seconds <= 0:
                raise ValueError("video.assembly.timeout_seconds 必须大于 0")

        video_narration = raw["video"].get("narration", {})
        if video_narration is not None and not isinstance(video_narration, dict):
            raise ValueError("video.narration 必须是 YAML 对象")
        if isinstance(video_narration, dict):
            characters_per_second = float(video_narration.get("characters_per_second", 3.3))
            max_tts_speed_ratio = float(video_narration.get("max_tts_speed_ratio", 1.08))
            tail_padding_seconds = float(video_narration.get("tail_padding_seconds", 0.2))
            max_refit_attempts = int(video_narration.get("max_refit_attempts", 1))
            if characters_per_second <= 0 or characters_per_second > 8:
                raise ValueError("video.narration.characters_per_second 必须在 0 到 8 之间")
            if max_tts_speed_ratio < 1 or max_tts_speed_ratio > 1.25:
                raise ValueError("video.narration.max_tts_speed_ratio 必须在 1 到 1.25 之间")
            if tail_padding_seconds < 0 or tail_padding_seconds >= video_min_clip_duration_seconds:
                raise ValueError("video.narration.tail_padding_seconds 配置无效")
            if max_refit_attempts < 0 or max_refit_attempts > 3:
                raise ValueError("video.narration.max_refit_attempts 必须在 0 到 3 之间")

        video_quality_assurance = raw["video"].get("quality_assurance", {})
        if video_quality_assurance is not None and not isinstance(video_quality_assurance, dict):
            raise ValueError("video.quality_assurance 必须是 YAML 对象")
        if isinstance(video_quality_assurance, dict):
            if not str(video_quality_assurance.get("provider", "aliyun_qwen_vl")).strip():
                raise ValueError("video.quality_assurance.provider 不能为空")
            if not str(video_quality_assurance.get("model", "qwen3-vl-plus")).strip():
                raise ValueError("video.quality_assurance.model 不能为空")
            if not str(video_quality_assurance.get("base_url", "")).strip():
                raise ValueError("video.quality_assurance.base_url 不能为空")
            if not str(video_quality_assurance.get("api_key_env", "DASHSCOPE_API_KEY")).strip():
                raise ValueError("video.quality_assurance.api_key_env 不能为空")
            sample_frame_count = int(video_quality_assurance.get("sample_frame_count", 3))
            minimum_score = int(video_quality_assurance.get("minimum_score", 72))
            max_regeneration_attempts = int(video_quality_assurance.get("max_regeneration_attempts", 1))
            quality_assurance_timeout_seconds = float(video_quality_assurance.get("timeout_seconds", 90))
            if sample_frame_count < 1 or sample_frame_count > 8:
                raise ValueError("video.quality_assurance.sample_frame_count 必须在 1 到 8 之间")
            if minimum_score < 0 or minimum_score > 100:
                raise ValueError("video.quality_assurance.minimum_score 必须在 0 到 100 之间")
            if max_regeneration_attempts < 0 or max_regeneration_attempts > 3:
                raise ValueError("video.quality_assurance.max_regeneration_attempts 必须在 0 到 3 之间")
            if quality_assurance_timeout_seconds <= 0:
                raise ValueError("video.quality_assurance.timeout_seconds 必须大于 0")

        video_clip_prompt = raw["video"].get("clip_prompt", {})
        if video_clip_prompt is not None and not isinstance(video_clip_prompt, dict):
            raise ValueError("video.clip_prompt 必须是 YAML 对象")
        if isinstance(video_clip_prompt, dict):
            clip_prompt_max_length = int(video_clip_prompt.get("max_length", 1800))
            if clip_prompt_max_length <= 0:
                raise ValueError("video.clip_prompt.max_length 必须大于 0")

        if "default_voice_type" not in raw["audio"]:
            raise ValueError("config/app.yaml 缺少 audio.default_voice_type")

        if "api_url" not in raw["audio"]:
            raise ValueError("config/app.yaml 缺少 audio.api_url")

        if "resource_id" not in raw["audio"]:
            raise ValueError("config/app.yaml 缺少 audio.resource_id")

        if not str(raw["audio"].get("api_key_env", "")).strip():
            raise ValueError("audio.api_key_env 不能为空")

        if not str(raw["audio"].get("resource_id", "")).strip():
            raise ValueError("audio.resource_id 不能为空")

        audio_speed_ratio = float(raw["audio"].get("speed_ratio", 1.0))
        if audio_speed_ratio < 0.5 or audio_speed_ratio > 2.0:
            raise ValueError("audio.speed_ratio 必须在 0.5 到 2.0 之间")

        audio_rate = int(raw["audio"].get("rate", 24000))
        if audio_rate not in {8000, 16000, 22050, 24000, 32000, 44100, 48000}:
            raise ValueError("audio.rate 必须是豆包 TTS V3 支持的采样率")

        audio_encoding = str(raw["audio"].get("encoding", "mp3")).strip()
        if audio_encoding not in {"mp3", "wav", "pcm", "ogg_opus"}:
            raise ValueError("audio.encoding 仅支持 mp3、wav、pcm 或 ogg_opus")

        audio_max_input_utf8_bytes = int(raw["audio"].get("max_input_utf8_bytes", 900))
        if audio_max_input_utf8_bytes <= 0 or audio_max_input_utf8_bytes > 1024:
            raise ValueError("audio.max_input_utf8_bytes 必须在 1 到 1024 之间")

        audio_timeout_seconds = float(raw["audio"].get("timeout_seconds", 120))
        if audio_timeout_seconds <= 0:
            raise ValueError("audio.timeout_seconds 必须大于 0")

        storage_provider = str(raw["storage"].get("provider", "")).strip()
        if not storage_provider:
            raise ValueError("storage.provider 不能为空")

        storage_asset_types = raw["storage"].get("asset_types", [])
        if not isinstance(storage_asset_types, list) or not storage_asset_types:
            raise ValueError("storage.asset_types 必须是非空数组")

        supported_storage_providers = {"local", "r2", "tos", "cos"}
        if storage_provider not in supported_storage_providers:
            supported = ", ".join(sorted(supported_storage_providers))
            raise ValueError(f"storage.provider 只支持：{supported}")

        preview_media_route_prefix = str(raw["preview"].get("media_route_prefix", "")).strip()
        if not preview_media_route_prefix.startswith("/"):
            raise ValueError("preview.media_route_prefix 必须以 / 开头")
