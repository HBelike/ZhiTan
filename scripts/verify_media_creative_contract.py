"""离线验证内容、生图和视频创作合同。

此脚本不读取密钥、不访问 DeepSeek、Ark、Seedance、HyperFrames 或公众号。
它只验证每个项目的视觉合同能编译成不同的图像提示词，并验证 7 段
Seedance 分镜和 HyperFrames 后期蓝图不会在任务之间丢失关键信息。
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import ConfigManager
from src.repositories.generated_content_repository import GeneratedContentForStoryboard
from src.services.image_prompt_design_service import ImagePromptDesignService
from src.services.media_creative_brief_service import MediaCreativeBriefService
from src.tasks.image_task import ImageTask
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.video_clip_plan_task import VideoClipPlanTask

def build_project_prompt_items() -> list[dict[str, object]]:
    """构造五个结构不同的项目，用于验证批量生产不会回退成同一张蓝图。"""

    service = MediaCreativeBriefService()
    source_items = [
        ("demo/skills", "layered_system", "paper_cobalt_amber", "解释技能元数据、指令与资源如何分层加载"),
        ("demo/graph", "hub_spoke", "paper_violet_coral", "解释代码、文档与数据如何汇聚为可查询关系"),
        ("demo/learning", "linear_progression", "paper_teal_tangerine", "解释从拆解到验证的工程学习路径"),
        ("demo/agent", "circular_flow", "paper_ink_lime", "解释任务、计划、执行和反馈如何形成闭环"),
        ("demo/compare", "comparison", "paper_navy_orange", "解释旧流程和改造后流程之间的关键差异"),
    ]
    items: list[dict[str, object]] = []
    for index, (repository, diagram_type, palette_key, thesis) in enumerate(source_items, start=1):
        visual_brief = service.normalize_visual_brief(
            raw_brief={
                "diagram_type": diagram_type,
                "teaching_goal": "让中文技术读者一眼读懂该项目的工程机制",
                "visual_thesis": thesis,
                "nodes": [
                    {"id": "evidence", "label": "事实证据", "role": "事实来源"},
                    {"id": "orchestrator", "label": "编排规则", "role": "组织步骤"},
                    {"id": "gate", "label": "质量校验", "role": "确认边界"},
                    {"id": "artifact", "label": "交付结果", "role": "最终产物"},
                ],
                "relationships": [
                    {"from": "evidence", "to": "orchestrator", "label": "数据流"},
                    {"from": "orchestrator", "to": "gate", "label": "同步调用"},
                    {"from": "gate", "to": "artifact", "label": "事件推送"},
                ],
                "reading_order": ["evidence", "orchestrator", "gate", "artifact"],
                "chinese_labels": ["事实证据", "编排规则", "质量校验", "交付结果"],
                "palette_key": palette_key,
            },
            repository_full_name=repository,
            fallback_text=thesis,
            project_index=index,
        )
        video_brief = service.normalize_video_brief(
            raw_brief={
                "narrative_claim": thesis,
                "mechanism": "通过清晰的信息流与结构关系降低理解成本",
                "motion_metaphor": "主流程线推进，关键节点依次高亮",
                "camera": "稳定中景缓推",
                "transition": "主流程线延伸到下一段",
            },
            visual_brief=visual_brief,
            project_summary_text=thesis,
            repository_full_name=repository,
            project_index=index,
        )
        items.append(
            {
                "repository_full_name": repository,
                "prompt": thesis,
                "summary_text": thesis,
                "visual_brief": visual_brief,
                "video_brief": video_brief,
            }
        )
    return items


def main() -> None:
    """运行离线契约验证。"""

    config = ConfigManager(PROJECT_ROOT).load()
    image_service = ImagePromptDesignService(config=config)
    prompt_items = build_project_prompt_items()

    image_prompts = [
        image_service.build_project_architecture_prompt(
            repository_full_name=str(item["repository_full_name"]),
            focus_prompt=str(item["prompt"]),
            project_summary_text=str(item["summary_text"]),
            visual_brief=item["visual_brief"] if isinstance(item["visual_brief"], dict) else None,
            project_index=index,
        )
        for index, item in enumerate(prompt_items, start=1)
    ]
    assert all(120 <= len(prompt) <= config.image_prompt_max_length for prompt in image_prompts), "最终 Ark 图片 Prompt 长度异常"
    assert all("http://" not in prompt and "https://" not in prompt for prompt in image_prompts)
    assert all(
        "无标题的16:9工程架构信息图" in prompt
        and "每个模块只出现一次" in prompt
        and "箭头仅限" in prompt
        and "连线不写文字" in prompt
        for prompt in image_prompts
    )
    assert len(set(image_prompts)) == len(image_prompts), "五个项目不应编译为相同 Prompt"

    image_task = object.__new__(ImageTask)
    protected_contract = "基础视觉合同：白底矩形模块，黑色正交折线箭头，短标签清晰可读。" * 20
    merged_prompt = image_task._apply_runtime_image_instruction(
        generation_prompt=protected_contract,
        runtime_instruction="运行时策略" * 500,
        max_length=256,
    )
    assert merged_prompt.startswith("基础视觉合同"), "运行时策略不能覆盖基础视觉合同"
    assert "正交折线" in merged_prompt

    content = GeneratedContentForStoryboard(
        id=999,
        week_end="2026-08-14",
        title="本周工程能力如何从单点工具走向工作流",
        digest="五个项目围绕技能、图谱、学习、Agent 与流程改造给出了不同答案。",
        article_markdown="### 本周主线\n离线契约验证用文章。",
        video_script="仅用于验证。",
        voiceover_text="仅用于验证。",
        image_prompts=prompt_items,
    )
    task = object.__new__(ShortVideoPromptTask)
    storyboard = task._build_fallback_storyboard(content)
    scenes = storyboard["scenes"]
    assert len(scenes) == 7, "五个项目必须生成开场、五个项目段、结尾共七段"
    assert storyboard["total_duration_seconds"] == 60
    assert all("scene_contract" in scene for scene in scenes)
    assert all("分时动作" in str(scene["seedance_scene_prompt"]) for scene in scenes)
    assert all("@图片" not in str(scene["seedance_scene_prompt"]) for scene in scenes)
    blueprint = storyboard.get("hyperframes_blueprint", {})
    assert blueprint.get("render_mode") == "pending_user_approval"
    assert len(blueprint.get("segments", [])) == 7

    plan_task = object.__new__(VideoClipPlanTask)
    context = SimpleNamespace(config=config)
    first_scene = scenes[1]
    clip_prompt = plan_task._build_clip_seedance_prompt(
        context=context,
        storyboard=SimpleNamespace(title=str(storyboard["video_title"])),
        clip_index=2,
        total_clip_count=len(scenes),
        purpose=str(first_scene["purpose"]),
        narration=str(first_scene["narration"]),
        subtitle=str(first_scene["subtitle"]),
        visual_design=str(first_scene["visual_design"]),
        motion_design=str(first_scene["motion_design"]),
        transition_to_next=str(first_scene["transition_to_next"]),
        repository_full_name=str(first_scene["repository_full_name"]),
        clip_duration=int(first_scene["duration_seconds"]),
        seedance_scene_prompt=str(first_scene["seedance_scene_prompt"]),
        scene_contract=first_scene["scene_contract"] if isinstance(first_scene["scene_contract"], dict) else {},
    )
    assert "分时动作" in clip_prompt, "VideoClipPlan 必须保留详细 Seedance 分镜"
    assert "当前配置未启用参考图片" in clip_prompt
    assert "@图片" not in clip_prompt
    assert len(clip_prompt) <= config.video_clip_prompt_max_length

    long_clip_prompt = plan_task._build_clip_seedance_prompt(
        context=context,
        storyboard=SimpleNamespace(title=str(storyboard["video_title"])),
        clip_index=2,
        total_clip_count=len(scenes),
        purpose=str(first_scene["purpose"]),
        narration=str(first_scene["narration"]),
        subtitle=str(first_scene["subtitle"]),
        visual_design=str(first_scene["visual_design"]),
        motion_design=str(first_scene["motion_design"]),
        transition_to_next=str(first_scene["transition_to_next"]),
        repository_full_name=str(first_scene["repository_full_name"]),
        clip_duration=int(first_scene["duration_seconds"]),
        seedance_scene_prompt="基础教学画面细节。" * 600,
        scene_contract=first_scene["scene_contract"] if isinstance(first_scene["scene_contract"], dict) else {},
    )
    assert "本段退出状态" in long_clip_prompt, "长分镜不能截掉离场状态"
    assert "本段转场" in long_clip_prompt, "长分镜不能截掉转场"
    assert len(long_clip_prompt) <= config.video_clip_prompt_max_length

    print(
        "媒体创作合同验证通过："
        f"image_prompts={len(image_prompts)} scenes={len(scenes)} "
        f"clip_prompt_length={len(clip_prompt)}"
    )


if __name__ == "__main__":
    main()
