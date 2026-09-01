from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text
from src.repositories.generated_content_repository import GeneratedContentForStoryboard, GeneratedContentRepository
from src.repositories.video_storyboard_repository import VideoStoryboardInput, VideoStoryboardRepository
from src.services.media_creative_brief_service import MediaCreativeBriefService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class ShortVideoPromptTask(BaseTask):
    """生成短视频制作蓝图，让文本、图片和视频提示词层层递进。"""

    task_name = "ShortVideoPromptTask"
    opening_duration_seconds = 5
    closing_duration_seconds = 5
    target_duration_seconds = 60
    _repository_token_pattern = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w.-])")

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新 Summary 内容，生成可审查的 Seedance 视频蓝图。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_storyboard_generation()
        if content is None:
            raise RuntimeError("没有可生成短视频蓝图的 generated_contents，请先运行 SummaryTask")
        project_count = len(content.image_prompts)
        if project_count < 1:
            raise RuntimeError(f"content_id={content.id} 至少需要 1 条 image_prompt")

        provider = DeepSeekProvider(config=context.config, run_name="wechat.video_storyboard.generate")
        raw_response_model = ""
        fallback_used = False
        try:
            response, normalized = self._generate_normalized_storyboard(
                provider=provider,
                content=content,
                video_instruction=context.config.runtime_prompt("video"),
            )
            raw_response_model = response.model
        except Exception as exc:
            fallback_used = True
            self.logger.warning(
                "DeepSeek 短视频口播生成失败，将使用本地结构化兜底：content_id=%s error=%s",
                content.id,
                exc,
            )
            normalized = self._build_fallback_storyboard(content)

        record = storyboard_repository.upsert(
            VideoStoryboardInput(
                content_id=content.id,
                title=normalized["video_title"],
                progressive_script=normalized["progressive_script"],
                seedance_prompt=normalized["seedance_master_prompt"],
                architecture_image_prompts=normalized["architecture_image_prompts"],
                storyboard=normalized,
                status="ready",
            )
        )
        content_repository.update_media_plan(
            content.id,
            video_script=normalized["progressive_script"],
            voiceover_text=self._build_voiceover_text(normalized),
        )

        self.logger.info(
            "短视频蓝图已生成：content_id=%s storyboard_id=%s scenes=%s fallback=%s",
            content.id,
            record.id,
            len(normalized["scenes"]),
            fallback_used,
        )
        return {
            "content_id": content.id,
            "storyboard_id": record.id,
            "video_title": record.title,
            "scene_count": len(normalized["scenes"]),
            "architecture_image_prompt_count": len(normalized["architecture_image_prompts"]),
            "progressive_script_length": len(record.progressive_script),
            "seedance_prompt_length": len(record.seedance_prompt),
            "model": raw_response_model or context.config.llm_model,
            "fallback_used": fallback_used,
            "skipped": False,
            "network_called": not fallback_used,
        }

    @staticmethod
    def _build_voiceover_text(storyboard: dict[str, Any]) -> str:
        """按分镜顺序汇总旁白，保证 TTS 与视频蓝图使用同一套语义。"""

        scenes = storyboard.get("scenes", [])
        narrations = [
            str(scene.get("narration", "")).strip()
            for scene in scenes
            if isinstance(scene, dict) and str(scene.get("narration", "")).strip()
        ]
        voiceover_text = "\n".join(narrations).strip()
        if voiceover_text:
            return voiceover_text
        return str(storyboard.get("progressive_script", "")).strip()

    def _generate_normalized_storyboard(
        self,
        provider: DeepSeekProvider,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> tuple[Any, dict[str, Any]]:
        """调用 DeepSeek 生成短口播，再由代码确定性组装短视频蓝图。"""

        attempts = [
            self._build_script_messages(content, video_instruction),
            self._build_script_retry_messages(content, video_instruction),
        ]
        last_error: Exception | None = None
        for attempt_index, messages in enumerate(attempts, start=1):
            response = provider.chat(
                messages,
                trace_metadata={
                    "attempt_index": attempt_index,
                    "phase": "initial" if attempt_index == 1 else "repair",
                    "reason_code": "initial_generation"
                    if attempt_index == 1
                    else "storyboard_schema_repair",
                },
            )
            try:
                parsed = parse_json_object_from_text(response.content)
                script_payload = self._normalize_script_payload(parsed=parsed, content=content)
                normalized = self._build_storyboard_from_script_payload(
                    content=content,
                    script_payload=script_payload,
                )
                if attempt_index > 1:
                    self.logger.info("DeepSeek 第 %s 次尝试输出了可解析短视频口播 JSON", attempt_index)
                return response, normalized
            except Exception as exc:
                last_error = exc
                if attempt_index < len(attempts):
                    self.logger.warning("DeepSeek 第 %s 次短视频口播 JSON 异常，将重试短版：%s", attempt_index, exc)
                    continue
                raise

        raise RuntimeError("DeepSeek 短视频蓝图生成失败") from last_error

    def _build_script_messages(
        self,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> list[DeepSeekMessage]:
        """构造只生成渐进式口播的小 JSON 请求。"""

        project_payload = [
            {
                "index": index,
                "repository_full_name": item["repository_full_name"],
                "summary_text": str(item.get("summary_text", "") or item.get("project_summary_text", "")).strip(),
                "project_evidence_card": self._project_evidence_card(
                    str(item.get("project_analysis_markdown", ""))
                ),
                "visual_brief": item.get("visual_brief", {}),
                "video_brief": item.get("video_brief", {}),
            }
            for index, item in enumerate(content.image_prompts, start=1)
        ]
        project_count = len(project_payload)
        per_project_duration = self._project_scene_durations(project_count)
        system_prompt = (
            "你是一名技术科普短视频口播编导。"
            "你只负责把 GitHub 周榜改写成 60 秒口播文案，不负责输出复杂分镜。"
            "只输出合法 JSON 对象。"
        )
        user_prompt = f"""
请基于以下内容生成短视频口播 JSON。

字段必须包含：
video_title: 36字以内
opening_line: 80字以内
project_summaries: {project_count}项数组，每项包含 repository_full_name、spoken_text、architecture_focus、scene_goal、motion_beat、transition_intent
closing_line: 90字以内
progressive_script: 900字以内，按“本周 GitHub 热门项目来了，第一是...第二是...”逐个讲，逻辑递进

要求：
- 只输出 JSON，不要 Markdown。
- project_summaries 顺序必须与输入项目一致。
- 第 i 个 spoken_text 要适合约 {per_project_duration[0] if per_project_duration else 10} 秒口播；总时长目标为 60 秒。
- architecture_focus 要说明这张图重点表现什么机制；scene_goal 要说明该段结束时观众理解了什么；motion_beat 要描述一个能执行的教学动画动作；transition_intent 要描述如何自然进入下一段。不要要求图片或视频里出现仓库名、长字幕、代码、数字或 logo。
- 不要把五段项目讲解写成同一套“输入—核心—输出”模板；必须尊重输入里的 visual_brief / video_brief，形成不同的结构、动作和阅读顺序。
- project_evidence_card 是对应项目的长文事实摘要。只从其中提炼一个适合 10 秒讲清的判断与机制，不要把整段长文机械念出来，也不要忽略后面项目的证据。
- 视频画面只服务教学关系，旁白和中文字幕由后续单独轨道处理；不要要求模型生成口型、旁白或内嵌长字幕。
- 不要编造项目能力；不确定时用“从仓库描述看”。
{self._build_runtime_instruction_section("管理员视频策略", video_instruction)}

标题：{content.title}
摘要：{content.digest}
本周主线摘录：{self._article_mainline_excerpt(content.article_markdown)}

项目：
{json.dumps(project_payload, ensure_ascii=False)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _build_script_retry_messages(
        self,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> list[DeepSeekMessage]:
        """构造更短的口播 JSON 重试请求。"""

        project_payload = [
            {
                "repository_full_name": item["repository_full_name"],
                "summary_text": str(item.get("summary_text", "") or item.get("project_summary_text", "")).strip(),
                "project_evidence_card": self._project_evidence_card(
                    str(item.get("project_analysis_markdown", ""))
                ),
                "visual_brief": item.get("visual_brief", {}),
                "video_brief": item.get("video_brief", {}),
            }
            for item in content.image_prompts
        ]
        system_prompt = "你只输出合法 JSON 对象。不要 Markdown，不要解释。"
        user_prompt = f"""
生成 60 秒 GitHub 热门项目技术科普口播 JSON。
字段：video_title、opening_line、project_summaries、closing_line、progressive_script。
project_summaries 正好 {len(project_payload)} 项，每项含 repository_full_name、spoken_text、architecture_focus、scene_goal、motion_beat、transition_intent。
项目与创作合同：{json.dumps(project_payload, ensure_ascii=False)}
主题：{content.title}
摘要：{content.digest}
{self._build_runtime_instruction_section("管理员视频策略", video_instruction)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _normalize_script_payload(
        self,
        parsed: dict[str, Any],
        content: GeneratedContentForStoryboard,
    ) -> dict[str, Any]:
        """规范化模型产出的短口播 JSON。"""

        project_names = [str(item["repository_full_name"]) for item in content.image_prompts]
        project_items_by_name = {
            str(item["repository_full_name"]): item
            for item in content.image_prompts
            if str(item.get("repository_full_name", "")).strip()
        }
        raw_project_summaries = parsed.get("project_summaries", [])
        if not isinstance(raw_project_summaries, list):
            raw_project_summaries = []

        repository_aliases: dict[str, str] = {}
        project_summaries: list[dict[str, Any]] = []
        for index, project_name in enumerate(project_names, start=1):
            raw_item = self._find_item_by_repository(raw_project_summaries, project_name)
            if not raw_item:
                raw_item = self._item_by_index(raw_project_summaries, index - 1)

            raw_repository_name = str(raw_item.get("repository_full_name", "")).strip()
            if raw_repository_name and raw_repository_name != project_name:
                repository_aliases[raw_repository_name] = project_name
                self.logger.warning(
                    "DeepSeek 短视频口播仓库名与 Summary 不一致，已按项目顺序纠偏：index=%s raw=%s expected=%s",
                    index,
                    raw_repository_name,
                    project_name,
                )

            spoken_text = str(raw_item.get("spoken_text", "")).strip()
            if raw_repository_name:
                spoken_text = spoken_text.replace(raw_repository_name, project_name)
            if not spoken_text:
                spoken_text = f"第 {index} 个项目是 {project_name}，它代表了本周技术趋势里的一个关键方向。"
            source_item = project_items_by_name.get(project_name, {})
            architecture_focus = str(raw_item.get("architecture_focus", "")).strip()
            if raw_repository_name:
                architecture_focus = architecture_focus.replace(raw_repository_name, project_name)
            if not architecture_focus:
                visual_brief = source_item.get("visual_brief", {})
                architecture_focus = str(
                    visual_brief.get("visual_thesis", "") if isinstance(visual_brief, dict) else ""
                ).strip() or "展示该项目最关键的工程关系与阅读路径。"
            scene_goal = str(raw_item.get("scene_goal", "")).strip()
            motion_beat = str(raw_item.get("motion_beat", "")).strip()
            transition_intent = str(raw_item.get("transition_intent", "")).strip()
            project_summaries.append(
                {
                    "project_index": index,
                    "repository_full_name": project_name,
                    "spoken_text": spoken_text[:220],
                    "architecture_focus": architecture_focus[:220],
                    "scene_goal": scene_goal[:160],
                    "motion_beat": motion_beat[:180],
                    "transition_intent": transition_intent[:160],
                    "project_summary_text": str(
                        source_item.get("summary_text", "") or source_item.get("project_summary_text", "")
                    ).strip()[:180],
                    "project_analysis_markdown": self._project_evidence_card(
                        str(source_item.get("project_analysis_markdown", ""))
                    ),
                    "visual_brief": source_item.get("visual_brief", {}),
                    "video_brief": source_item.get("video_brief", {}),
                }
            )

        video_title = str(parsed.get("video_title", content.title)).strip() or content.title
        opening_line = str(parsed.get("opening_line", "")).strip()
        closing_line = str(parsed.get("closing_line", "")).strip()
        progressive_script = str(parsed.get("progressive_script", "")).strip()
        repository_text_candidates = [video_title, opening_line, closing_line, progressive_script]
        for item in raw_project_summaries:
            if not isinstance(item, dict):
                continue
            repository_text_candidates.extend(
                [
                    str(item.get("repository_full_name", "")).strip(),
                    str(item.get("spoken_text", "")).strip(),
                    str(item.get("architecture_focus", "")).strip(),
                ]
            )
        repository_aliases.update(
            self._detect_repository_aliases_from_texts(
                texts=repository_text_candidates,
                expected_repository_names=project_names,
            )
        )

        project_summaries = [
            {
                **item,
                "spoken_text": self._replace_repository_aliases(str(item["spoken_text"]), repository_aliases),
                "architecture_focus": self._replace_repository_aliases(
                    str(item["architecture_focus"]),
                    repository_aliases,
                ),
                "scene_goal": self._replace_repository_aliases(str(item["scene_goal"]), repository_aliases),
                "motion_beat": self._replace_repository_aliases(str(item["motion_beat"]), repository_aliases),
                "transition_intent": self._replace_repository_aliases(
                    str(item["transition_intent"]), repository_aliases
                ),
            }
            for item in project_summaries
        ]

        video_title = self._replace_repository_aliases(video_title, repository_aliases)
        opening_line = self._replace_repository_aliases(opening_line, repository_aliases)
        project_count = len(project_summaries)
        if not opening_line:
            opening_line = f"本周 GitHub Top {project_count} 的变化并不只在 star 数，它们共同指向更具体的工程工作流。"
        closing_line = self._replace_repository_aliases(closing_line, repository_aliases)
        if not closing_line:
            closing_line = f"这 {project_count} 个项目放在一起看，说明 AI 工具正在从单点自动化走向更完整的工程工作流。"
        progressive_script = self._replace_repository_aliases(progressive_script, repository_aliases)
        if not progressive_script:
            progressive_script = opening_line + "".join(item["spoken_text"] for item in project_summaries) + closing_line

        return {
            "video_title": video_title,
            "opening_line": opening_line[:160],
            "project_summaries": project_summaries,
            "closing_line": closing_line[:180],
            "progressive_script": progressive_script[:900],
        }

    def _build_storyboard_from_script_payload(
        self,
        content: GeneratedContentForStoryboard,
        script_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """根据短口播确定性生成开场、项目段与结尾分镜及 Seedance 主 prompt。"""

        creative_brief_service = MediaCreativeBriefService()
        project_summaries: list[dict[str, Any]] = []
        for item in script_payload["project_summaries"]:
            repository_full_name = str(item["repository_full_name"])
            visual_brief = creative_brief_service.normalize_visual_brief(
                raw_brief=item.get("visual_brief"),
                repository_full_name=repository_full_name,
                fallback_text=str(item.get("architecture_focus", "") or item.get("spoken_text", "")),
                project_index=int(item["project_index"]),
            )
            video_brief = creative_brief_service.normalize_video_brief(
                raw_brief=item.get("video_brief"),
                visual_brief=visual_brief,
                project_summary_text=str(item.get("project_summary_text", "") or item.get("spoken_text", "")),
                repository_full_name=repository_full_name,
                project_index=int(item["project_index"]),
            )
            project_summaries.append({**item, "visual_brief": visual_brief, "video_brief": video_brief})

        architecture_prompts = [
            {
                "project_index": item["project_index"],
                "repository_full_name": item["repository_full_name"],
                "project_summary_text": item["project_summary_text"] or item["spoken_text"],
                "project_analysis_markdown": item.get("project_analysis_markdown", ""),
                "architecture_prompt": self._normalize_architecture_prompt(
                    repository_full_name=item["repository_full_name"],
                    prompt=str(item["architecture_focus"]),
                ),
                "visual_brief": item["visual_brief"],
                "video_brief": item["video_brief"],
                "prompt_stage": "storyboard_visual_intent_v2",
            }
            for item in project_summaries
        ]

        project_count = len(project_summaries)
        scene_durations = self._scene_durations(project_count)
        opening_contract = {
            "entry_state": "浅纸本教学画面从留白进入，五个趋势符号尚未展开",
            "beats": [
                "0-2 秒：中心出现本期趋势总览的抽象主轴",
                f"2-{max(3, scene_durations[0] - 1)} 秒：{project_count} 个项目方向依次以卡片和主线出现",
                f"{max(3, scene_durations[0] - 1)}-{scene_durations[0]} 秒：镜头沿第一条主流程线推入项目一",
            ],
            "exit_state": "第一条主流程线填满画面中心，保留可承接的右向运动",
            "camera": "稳定广角总览后缓慢推近，不使用旋转或突发闪切",
            "transition": "第一条主流程线延伸，擦拭进入项目一",
            "audio_directive": "不生成旁白、口型或内嵌字幕；保留轻微节拍空间",
            "negative_constraints": ["不要蓝绿霓虹满屏", "不要乱码或长英文", "不要图库人物或产品广告感"],
            "reference_image_role": "none",
        }
        scenes: list[dict[str, Any]] = [
            {
                "scene_index": 1,
                "time_range": self._time_range_for_scene(1, scene_durations),
                "duration_seconds": scene_durations[0],
                "purpose": "开场，本周趋势总览",
                "repository_full_name": None,
                "narration": script_payload["opening_line"],
                "subtitle": f"本周 GitHub Top {project_count} 技术趋势",
                "visual_design": f"{project_count} 个项目方向围绕一条本周工程趋势主线排布；保持浅纸本教学图质感，重点是可读的结构关系，不是产品海报。",
                "motion_design": "趋势节点按阅读顺序出现，第一条流程线放大并引导下一段。",
                "transition_to_next": opening_contract["transition"],
                "scene_contract": opening_contract,
                "seedance_scene_prompt": self._build_seedance_scene_prompt(
                    duration_seconds=scene_durations[0],
                    purpose="开场，本周趋势总览",
                    narration=script_payload["opening_line"],
                    visual_brief={
                        "diagram_type": "hub_spoke",
                        "teaching_goal": "用总览说明本周项目并非孤立热点，而是围绕工程工作流演进",
                        "visual_thesis": f"{project_count} 个项目共同指向可组合、可解释、可落地的工程能力",
                        "nodes": [],
                        "relationships": [],
                        "reading_order": [],
                        "chinese_labels": ["本周趋势", "工程能力", "项目方向"],
                        "palette_key": "paper_navy_orange",
                        "negative_constraints": opening_contract["negative_constraints"],
                    },
                    video_brief={
                        "motion_metaphor": "趋势节点围绕中心主线依次展开",
                        "camera": opening_contract["camera"],
                        "transition": opening_contract["transition"],
                        "audio_directive": opening_contract["audio_directive"],
                    },
                    scene_contract=opening_contract,
                ),
            }
        ]
        for item in project_summaries:
            scene_index = int(item["project_index"]) + 1
            motion_design = str(item.get("motion_beat", "")).strip() or str(item["video_brief"]["motion_metaphor"])
            transition_to_next = str(item.get("transition_intent", "")).strip() or str(item["video_brief"]["transition"])
            scene_contract = {
                "entry_state": "承接上一段离场的主流程线，从画面左侧或中心进入",
                "beats": self._project_scene_beats(
                    duration_seconds=scene_durations[scene_index - 1],
                    visual_brief=item["visual_brief"],
                    motion_design=motion_design,
                ),
                "exit_state": "当前关键关系收束为一条清晰主线，并留下进入下一段的方向",
                "camera": str(item["video_brief"]["camera"]),
                "transition": transition_to_next,
                "audio_directive": str(item["video_brief"]["audio_directive"]),
                "negative_constraints": list(item["visual_brief"]["negative_constraints"]),
                "reference_image_role": "none",
                "scene_goal": str(item.get("scene_goal", "")).strip() or str(item["video_brief"]["reader_gain"]),
            }
            scenes.append(
                {
                    "scene_index": scene_index,
                    "time_range": self._time_range_for_scene(scene_index, scene_durations),
                    "duration_seconds": scene_durations[scene_index - 1],
                    "purpose": self._project_scene_purpose(
                        project_index=int(item["project_index"]),
                        project_count=project_count,
                    ),
                    "repository_full_name": item["repository_full_name"],
                    "narration": item["spoken_text"],
                    "subtitle": item["repository_full_name"],
                    "visual_design": self._describe_visual_design(item["visual_brief"]),
                    "motion_design": motion_design,
                    "transition_to_next": transition_to_next,
                    "scene_contract": scene_contract,
                    "seedance_scene_prompt": self._build_seedance_scene_prompt(
                        duration_seconds=scene_durations[scene_index - 1],
                        purpose=self._project_scene_purpose(
                            project_index=int(item["project_index"]), project_count=project_count
                        ),
                        narration=item["spoken_text"],
                        visual_brief=item["visual_brief"],
                        video_brief=item["video_brief"],
                        scene_contract=scene_contract,
                    ),
                }
            )
        closing_contract = {
            "entry_state": "五个项目的主流程线从边缘回收，保留各自色彩线索",
            "beats": [
                "0-2 秒：五条主线汇入同一张趋势图",
                "2-4 秒：工程启发以三个中文短标签落位",
                "4-5 秒：画面干净留白并淡出",
            ],
            "exit_state": "趋势图完整静止在画面中央，留出片尾结束空间",
            "camera": "中景稳定收束，最后轻微拉远",
            "transition": "柔和淡出结束",
            "audio_directive": "不生成旁白、人物口型或内嵌字幕；只保留结尾提示音空间",
            "negative_constraints": ["不要强行出现关注按钮", "不要乱码或长英文", "不要抽象霓虹粒子"],
            "reference_image_role": "none",
        }
        scenes.append(
            {
                "scene_index": project_count + 2,
                "time_range": self._time_range_for_scene(project_count + 2, scene_durations),
                "duration_seconds": scene_durations[-1],
                "purpose": "结尾 CTA",
                "repository_full_name": None,
                "narration": script_payload["closing_line"],
                "subtitle": "本周工程启发",
                "visual_design": f"{project_count} 个项目的结构线索回收为一张总趋势图，突出可组合、可解释、可验证三类工程启发。",
                "motion_design": "多条主线向中心归并，形成一个干净的趋势图后留白结束。",
                "transition_to_next": closing_contract["transition"],
                "scene_contract": closing_contract,
                "seedance_scene_prompt": self._build_seedance_scene_prompt(
                    duration_seconds=scene_durations[-1],
                    purpose="结尾 CTA",
                    narration=script_payload["closing_line"],
                    visual_brief={
                        "diagram_type": "comparison",
                        "teaching_goal": "把五个项目回收为可带走的工程判断",
                        "visual_thesis": "热点并非孤立工具，而是工程流程中可组合的能力模块",
                        "nodes": [],
                        "relationships": [],
                        "reading_order": [],
                        "chinese_labels": ["可组合", "可解释", "可验证"],
                        "palette_key": "paper_violet_coral",
                        "negative_constraints": closing_contract["negative_constraints"],
                    },
                    video_brief={
                        "motion_metaphor": "多条流程线回收成一张完整趋势图",
                        "camera": closing_contract["camera"],
                        "transition": closing_contract["transition"],
                        "audio_directive": closing_contract["audio_directive"],
                    },
                    scene_contract=closing_contract,
                ),
            }
        )

        seedance_prompt = self._build_seedance_prompt_from_scenes(
            title=script_payload["video_title"],
            progressive_script=script_payload["progressive_script"],
            scenes=scenes,
        )
        return {
            "video_title": script_payload["video_title"],
            "total_duration_seconds": sum(scene_durations),
            "progressive_script": script_payload["progressive_script"],
            "architecture_image_prompts": architecture_prompts,
            "scenes": scenes,
            "seedance_master_prompt": seedance_prompt,
            "hyperframes_blueprint": creative_brief_service.build_hyperframes_blueprint(
                title=script_payload["video_title"],
                scenes=scenes,
                total_duration_seconds=sum(scene_durations),
            ),
            "quality_constraints": [
                "视频是一个连续教学讲解，不是多张图硬切。",
                "每段画面必须跟随对应项目的旁白逐步展开。",
                "每个项目必须遵守各自的视觉合同，不能全部套用同一色板或三栏结构。",
                "少用随机抽象画面，多用流程图、架构图、模块高亮和可解释的运动。",
                "不要生成乱码文字、错误 UI 或无意义代码。",
                "Seedance 只生成动态视觉片段；旁白、中文字幕、转场由 HyperFrames 蓝图在审核后确定性装配。",
            ],
            "source": {
                "content_id": content.id,
                "week_end": content.week_end,
                "summary_title": content.title,
            },
        }

    @staticmethod
    def _project_evidence_card(project_analysis_markdown: str) -> str:
        """把长文项目拆解压成分镜可用的事实卡，避免截断整篇文章前半段。"""

        normalized = re.sub(r"\s+", " ", project_analysis_markdown or "").strip()
        return normalized[:900]

    @staticmethod
    def _article_mainline_excerpt(article_markdown: str) -> str:
        """提取文章主线供开场口播使用，不让后续项目被全文前缀截断。"""

        match = re.search(r"###\s*本周主线\s*(.*?)(?=\n###\s+|\Z)", article_markdown or "", flags=re.DOTALL)
        text = match.group(1) if match else article_markdown
        return re.sub(r"\s+", " ", text or "").strip()[:650]

    def _build_fallback_storyboard(self, content: GeneratedContentForStoryboard) -> dict[str, Any]:
        """DeepSeek 不稳定时仍复用同一份结构化创作合同，避免回退旧蓝白模板。"""

        project_summaries: list[dict[str, Any]] = []
        for index, item in enumerate(content.image_prompts, start=1):
            repository_full_name = str(item["repository_full_name"])
            summary_text = str(item.get("summary_text", "") or item.get("project_summary_text", "")).strip()
            project_evidence_card = self._project_evidence_card(
                str(item.get("project_analysis_markdown", ""))
            )
            project_summaries.append(
                {
                    "project_index": index,
                    "repository_full_name": repository_full_name,
                    "spoken_text": summary_text
                    or f"第 {index} 个项目是 {repository_full_name}，重点不是堆叠功能，而是把一个具体工程环节组织得更清楚。",
                    "architecture_focus": project_evidence_card
                    or str(item.get("prompt", "")).strip()
                    or "解释项目如何把输入、关键处理和结果串成可理解的工程机制。",
                    "scene_goal": "让观众看懂该项目解决的是哪一段工程问题。",
                    "motion_beat": "关键节点按阅读顺序依次出现，主流程线只突出一条。",
                    "transition_intent": "当前主流程线向右延伸，带入下一项目的起点。",
                    "project_summary_text": summary_text,
                    "project_analysis_markdown": project_evidence_card,
                    "visual_brief": item.get("visual_brief", {}),
                    "video_brief": item.get("video_brief", {}),
                }
            )

        project_count = len(project_summaries)
        opening_line = (
            f"本周 GitHub Top {project_count} 的变化不只在 star 数，"
            "更值得看的，是开发者如何把 Agent、知识与工具拼进真实工作流。"
        )
        closing_line = (
            f"这 {project_count} 个项目放在一起，留下的工程启发是：先看信息如何流动，"
            "再决定工具应该放在哪一层。"
        )
        return self._build_storyboard_from_script_payload(
            content=content,
            script_payload={
                "video_title": content.title,
                "opening_line": opening_line,
                "project_summaries": project_summaries,
                "closing_line": closing_line,
                "progressive_script": opening_line
                + "".join(item["spoken_text"] for item in project_summaries)
                + closing_line,
            },
        )

    def _project_scene_beats(
        self,
        duration_seconds: int,
        visual_brief: dict[str, Any],
        motion_design: str,
    ) -> list[str]:
        """把项目视觉合同拆成连续的三拍教学动作。"""

        labels = [str(label) for label in visual_brief.get("chinese_labels", []) if str(label).strip()]
        first_label = labels[0] if labels else "关键输入"
        middle_label = labels[min(1, len(labels) - 1)] if labels else "核心机制"
        last_label = labels[-1] if labels else "工程结果"
        first_end = max(2, duration_seconds // 3)
        second_end = max(first_end + 2, duration_seconds - 2)
        return [
            f"0-{first_end} 秒：{first_label} 与主问题先落位，建立阅读起点",
            f"{first_end}-{second_end} 秒：{middle_label} 展开并沿主关系线推进；{motion_design}",
            f"{second_end}-{duration_seconds} 秒：{last_label} 收束为可带走的结果，保留下一段的主线方向",
        ]

    def _describe_visual_design(self, visual_brief: dict[str, Any]) -> str:
        """将结构化视觉合同压缩为审核台可读的画面说明。"""

        labels = "、".join(str(label) for label in visual_brief.get("chinese_labels", [])[:6]) or "关键节点"
        relationships = visual_brief.get("relationships", [])
        relationship_count = len(relationships) if isinstance(relationships, list) else 0
        return (
            f"采用 {visual_brief.get('diagram_type', 'structural_breakdown')} 教学图布局，"
            f"以“{visual_brief.get('visual_thesis', '工程机制')}”为核心，"
            f"展示 {labels} 等短标签与 {relationship_count} 条主要关系；"
            f"使用 {self._palette_description(str(visual_brief.get('palette_key', '')))} 的浅纸本信息图风格。"
        )

    def _build_seedance_scene_prompt(
        self,
        duration_seconds: int,
        purpose: str,
        narration: str,
        visual_brief: dict[str, Any],
        video_brief: dict[str, Any],
        scene_contract: dict[str, Any],
    ) -> str:
        """将单个结构化合同编译成真实提交给 Seedance 的动态片段 Prompt。

        该函数刻意不使用 @引用：当前配置关闭参考图输入。视频模型只负责连续动态
        教学画面，旁白与字幕由后续 HyperFrames 蓝图统一装配，避免生成乱码或口型错位。
        """

        nodes = visual_brief.get("nodes", [])
        node_text = "、".join(
            f"{node.get('label', '节点')}（{node.get('role', '关键节点')}）"
            for node in nodes[:6]
            if isinstance(node, dict)
        ) or "少量关键节点"
        relationships = visual_brief.get("relationships", [])
        relationship_text = "；".join(
            f"{relation.get('from', '起点')}→{relation.get('to', '终点')}（{relation.get('label', '流转')}）"
            for relation in relationships[:7]
            if isinstance(relation, dict)
        ) or "沿一条明确主流程线推进"
        labels = "、".join(str(label) for label in visual_brief.get("chinese_labels", [])[:8]) or "输入、核心、结果"
        beats = "；".join(str(beat) for beat in scene_contract.get("beats", [])[:3])
        negative_constraints = "；".join(
            str(item) for item in scene_contract.get("negative_constraints", [])[:6]
        )
        palette = self._palette_description(str(visual_brief.get("palette_key", "")))
        layout = self._diagram_layout_description(str(visual_brief.get("diagram_type", "")))

        prompt_parts = [
            f"生成 {duration_seconds} 秒横版 16:9 中文技术教学动态片段，这是连续科普视频的一个章节：{purpose}。",
            "目标不是广告海报或静态图片轮播，而是一段围绕一个工程机制逐步展开的 PPT 式信息图动画。",
            f"教学目标：{visual_brief.get('teaching_goal', '让观众看懂工程机制')}。",
            f"核心判断：{visual_brief.get('visual_thesis', '用画面解释关键关系')}。",
            f"版式：{layout}。色彩：{palette}。背景使用浅纸张、浅灰纤维或克制网格肌理，留白充足。",
            f"画面节点：{node_text}。主要关系：{relationship_text}。允许出现的中文短标签仅限：{labels}。",
            f"镜头与动作：{video_brief.get('camera', '稳定中景缓推')}；{video_brief.get('motion_metaphor', '关键节点依次高亮')}。",
            f"分时动作：{beats}。",
            f"进入状态：{scene_contract.get('entry_state', '从前一段主线进入')}；结尾衔接：{scene_contract.get('exit_state', '保留主线方向')}；转场：{scene_contract.get('transition', '平滑衔接')}。",
            f"口播语义仅用于确定节奏，不要把口播逐字写在画面中：{narration[:240]}。",
            "严格要求：不生成任何旁白、人物口型、字幕条或大段文字；不展示仓库名、网址、logo、代码、终端截图或产品界面；中文标签必须短而清晰。",
            f"负面约束：{negative_constraints or '不要乱码、伪文字、密集小字、抽象霓虹、杂乱网线、快节奏跳切或无意义粒子特效'}。",
            "运镜稳定、信息层级由低到高，元素只在讲到时出现；结尾保留与下一段相同的主线方向，实现无阻断衔接。",
        ]
        return "\n".join(part for part in prompt_parts if str(part).strip())[:2200]

    @staticmethod
    def _palette_description(palette_key: str) -> str:
        """将稳定色板键转换为图像模型能理解的可控配色。"""

        palettes = {
            "paper_cobalt_amber": "米白纸本底、钴蓝主结构、琥珀橙强调、石墨灰文字",
            "paper_violet_coral": "暖白纸本底、靛紫主结构、珊瑚橙强调、炭灰文字",
            "paper_teal_tangerine": "浅灰纸本底、深青蓝主结构、橘橙强调、墨灰文字",
            "paper_ink_lime": "象牙白纸本底、墨绿主结构、青柠强调、石墨灰文字",
            "paper_navy_orange": "雾白纸本底、海军蓝主结构、橙色强调、深灰文字",
        }
        return palettes.get(palette_key, palettes["paper_navy_orange"])

    @staticmethod
    def _diagram_layout_description(diagram_type: str) -> str:
        """选择可读性优先的镜头内信息图布局。"""

        layouts = {
            "structural_breakdown": "中心模块拆解成四至六个有序部件，主关系从中间向外展开",
            "linear_progression": "从左向右的单条主流程，步骤依次点亮且每步只出现一个短标签",
            "circular_flow": "环形闭环显示触发、处理、反馈和回流，避免过多分支",
            "hub_spoke": "一个中心能力连接三到五个外围能力点，先展开再收回主路径",
            "layered_system": "自下而上的三层能力结构，层间用少量垂直关系线连接",
            "comparison": "左右对照或前后对照，仅突出一个改造点和一个结果差异",
        }
        return layouts.get(diagram_type, layouts["structural_breakdown"])

    def _build_seedance_prompt_from_scenes(
        self,
        title: str,
        progressive_script: str,
        scenes: list[dict[str, Any]],
    ) -> str:
        """生成审核用的全片叙事总览，不替代各段真实 Seedance Prompt。"""

        scene_text = "\n".join(
            f"{scene['time_range']}：{scene['purpose']}。画面：{scene['visual_design']} 动作：{scene['motion_design']} 衔接：{scene['transition_to_next']}"
            for scene in scenes
        )
        return (
            "这是 60 秒中文技术教学视频的审核总览，实际生成应以每段 seedance_scene_prompt 为准。\n"
            f"标题：{title}。\n"
            "结构原则：七段由共享流程线连续衔接；每段依据自己的视觉合同，不套用单一蓝绿色模板；"
            "Seedance 只生成无旁白、无内嵌字幕的动态教学画面，HyperFrames 后续装配字幕、转场与旁白。\n"
            f"完整口播语义：{progressive_script}\n"
            f"分镜总览：\n{scene_text}"
        )[:2200]

    def _normalize_architecture_prompt(self, repository_full_name: str, prompt: str) -> str:
        """补齐架构图 prompt 的强约束。"""

        base_prompt = prompt or "生成技术架构概览图。"
        base_prompt = base_prompt.replace(repository_full_name, "这个项目")
        constraints = (
            "这只是视觉意图，不指定统一色板；最终美术由 visual_brief 决定。"
            "主体最多 6 个节点与 7 条关系；只允许少量中文短标签表达节点用途；"
            "不要仓库地址、长英文、代码、logo、水印、截图、复杂乱线、陈词滥调图标。"
        )
        return f"{base_prompt} {constraints}"[:720]

    def _find_item_by_repository(self, items: list[Any], repository_full_name: str) -> dict[str, Any]:
        """按 repository_full_name 在模型输出数组里找项目项。"""

        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("repository_full_name", "")).strip() == repository_full_name:
                return item
        return {}

    def _item_by_index(self, items: list[Any], index: int) -> dict[str, Any]:
        """模型没有严格返回仓库名时，按项目顺序取同位置口播项作为兜底。"""

        if index < 0 or index >= len(items):
            return {}
        item = items[index]
        if not isinstance(item, dict):
            return {}
        return item

    def _replace_repository_aliases(self, text: str, repository_aliases: dict[str, str]) -> str:
        """把口播 JSON 中的仓库名别名替换成 Summary 已校验过的真实 full_name。"""

        normalized_text = text
        for raw_repository_name, expected_repository_name in repository_aliases.items():
            if raw_repository_name == expected_repository_name:
                continue
            normalized_text = normalized_text.replace(raw_repository_name, expected_repository_name)
        return normalized_text

    def _detect_repository_aliases_from_texts(
        self,
        texts: list[str],
        expected_repository_names: list[str],
    ) -> dict[str, str]:
        """扫描口播文本里的 owner/repo 片段，把疑似拼写错误映射到真实项目名。"""

        aliases: dict[str, str] = {}
        expected_name_set = set(expected_repository_names)
        for text in texts:
            if not text:
                continue
            for match in self._repository_token_pattern.finditer(text):
                candidate = match.group(1).strip()
                if candidate in expected_name_set:
                    continue
                if self._looks_like_url_fragment(candidate):
                    continue
                expected_repository_name = self._best_repository_alias_match(
                    candidate=candidate,
                    expected_repository_names=expected_repository_names,
                )
                if expected_repository_name is None:
                    continue
                aliases[candidate] = expected_repository_name
                self.logger.warning(
                    "检测到短视频口播疑似仓库名拼写错误，已映射为真实项目名：raw=%s expected=%s",
                    candidate,
                    expected_repository_name,
                )
        return aliases

    def _best_repository_alias_match(
        self,
        candidate: str,
        expected_repository_names: list[str],
    ) -> str | None:
        """从当前候选项目里找出和 candidate 最相似的仓库名。"""

        candidate_owner, candidate_repo = self._split_repository_full_name(candidate)
        if not candidate_owner or not candidate_repo:
            return None

        best_match: str | None = None
        best_score = 0.0
        for expected_repository_name in expected_repository_names:
            expected_owner, expected_repo = self._split_repository_full_name(expected_repository_name)
            if not expected_owner or not expected_repo:
                continue

            full_score = SequenceMatcher(
                None,
                candidate.lower(),
                expected_repository_name.lower(),
            ).ratio()
            owner_score = SequenceMatcher(
                None,
                candidate_owner.lower(),
                expected_owner.lower(),
            ).ratio()
            repo_score = SequenceMatcher(
                None,
                candidate_repo.lower(),
                expected_repo.lower(),
            ).ratio()
            combined_score = max(full_score, owner_score * 0.45 + repo_score * 0.55)
            if combined_score > best_score:
                best_score = combined_score
                best_match = expected_repository_name

        if best_match is None:
            return None
        if best_score >= 0.88:
            return best_match
        return None

    def _split_repository_full_name(self, repository_full_name: str) -> tuple[str, str]:
        """把 owner/repo 拆成 owner 和 repo；格式不合法时返回空字符串。"""

        parts = repository_full_name.strip().split("/", 1)
        if len(parts) != 2:
            return "", ""
        return parts[0].strip(), parts[1].strip()

    def _looks_like_url_fragment(self, candidate: str) -> bool:
        """过滤 URL 中误扫出来的 github.com/owner 这类片段。"""

        owner, _ = self._split_repository_full_name(candidate)
        return owner.lower() in {"github.com", "www.github.com", "http", "https"}

    def _build_runtime_instruction_section(self, title: str, instruction: str) -> str:
        """把管理员配置以清晰边界追加到系统分镜约束中。"""

        normalized = instruction.strip()
        if not normalized:
            return ""
        return f"\n{title}（必须遵守，不能覆盖 JSON 输出格式和事实约束）：\n{normalized[:4000]}\n"

    def _scene_durations(self, project_count: int) -> list[int]:
        """在 60 秒总时长内为开场、项目和结尾确定性分配镜头时长。"""

        if project_count < 1:
            raise ValueError("project_count 必须大于 0")
        available = self.target_duration_seconds - self.opening_duration_seconds - self.closing_duration_seconds
        base_duration, remainder = divmod(available, project_count)
        project_durations = [base_duration + (1 if index < remainder else 0) for index in range(project_count)]
        return [self.opening_duration_seconds, *project_durations, self.closing_duration_seconds]

    def _project_scene_durations(self, project_count: int) -> list[int]:
        """返回项目讲解镜头时长，供口播 prompt 限制使用。"""

        return self._scene_durations(project_count)[1:-1]

    def _scene_repository_name(
        self,
        scene_index: int,
        content: GeneratedContentForStoryboard,
        project_count: int,
    ) -> str | None:
        """返回项目分镜对应的仓库名；开场和结尾为空。"""

        if scene_index in {1, project_count + 2}:
            return None
        project_index = scene_index - 2
        if project_index < 0 or project_index >= len(content.image_prompts):
            return None
        return str(content.image_prompts[project_index]["repository_full_name"])

    def _time_range_for_scene(self, scene_index: int, scene_durations: list[int]) -> str:
        """根据本轮动态时长返回分镜时间范围。"""

        start = sum(scene_durations[: scene_index - 1])
        end = start + scene_durations[scene_index - 1]
        return f"{start}-{end}s"

    def _project_scene_purpose(self, project_index: int, project_count: int) -> str:
        """返回项目分镜的渐进叙事目的，不再把节奏写死为五个项目。"""

        if project_index == 1:
            return "项目 1，架构图从中心展开"
        if project_index == project_count:
            return f"项目 {project_index}，总结工程启发"
        patterns = ["模块节点依次高亮", "流程线动画推进", "代码与工具链穿插", "关键能力逐层收束"]
        return f"项目 {project_index}，{patterns[(project_index - 2) % len(patterns)]}"

    def _project_motion_design(self, project_index: int, project_count: int) -> str:
        """返回与项目序号关联的教学式动态说明。"""

        if project_index == 1:
            return "架构图从中心节点向外展开，输入、核心模块、输出三层依次出现。"
        if project_index == project_count:
            return "项目卡片与工程启发标签并列出现，最后收束为一个趋势判断。"
        motion_designs = [
            "模块节点按旁白顺序依次高亮，关键路径用蓝色流程箭头连接。",
            "流程线从左到右推进，数据输入、处理、结果输出像电路一样点亮。",
            "代码卡片、命令行窗口和工具链节点穿插出现，但不展示可读乱码代码。",
            "镜头平滑推进，节点随旁白依次点亮并聚焦关键输入输出。",
        ]
        return motion_designs[(project_index - 2) % len(motion_designs)]
