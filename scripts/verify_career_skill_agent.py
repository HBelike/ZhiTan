"""真实验证求职助手的 SKILL.md + DeepSeek Tool Calling Agent Loop。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.model_clients import ChatMessage, OpenAICompatibleChatClient
from src.career_assistant.model_gateway import (
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence import ModelCostTier, ModelProfileRecord
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.skill_runtime import CareerSkillRuntime
from src.career_assistant.skill_tools import SkillToolRegistry
from src.config.config_manager import ConfigManager
from src.services.skill_library_service import SkillLibraryService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        default="git@github.com:JimLiu/baoyu-skills.git",
    )
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()

    config = ConfigManager(PROJECT_ROOT).load()
    credential = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not credential:
        raise RuntimeError("DEEPSEEK_API_KEY 尚未配置")

    skill_library = SkillLibraryService(config)
    action = "分析并安装到当前项目 Agent" if args.install else "分析但不要安装"
    user_text = f"/find-skills {action}：{args.repository}"
    activation = CareerSkillRuntime(skill_library).resolve([], user_text)
    context = ModelTurnContext(
        redacted_user_text=activation.user_task_text,
        redacted_material_text="",
        redacted_job_text="",
        required_capabilities=frozenset({ModelCapability.TEXT}),
        contains_image_material=False,
        vision_images=(),
        received_attachment_kinds=(),
        pdf_without_extractable_text_count=0,
        activated_skills=activation.skills,
    )
    now = datetime.now(UTC)
    profile = ModelProfileRecord(
        id=uuid4(),
        organization_id=uuid4(),
        profile_key="verify-deepseek-skill-agent",
        display_name="DeepSeek Skill Agent 验证",
        provider_key="deepseek",
        model_id=config.llm_model,
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.FREE_QUOTA,
        priority=1,
        enabled=True,
        api_base_url="https://api.deepseek.com",
        created_at=now,
        updated_at=now,
    )
    resolution = ModelResolution(
        profile=profile,
        reason=ModelResolutionReason.USER_SELECTED,
        readiness=ModelReadiness.READY,
        credential_env_name="DEEPSEEK_API_KEY",
        credential=credential,
    )
    runner = CareerResponseRunner.__new__(CareerResponseRunner)
    runner._skill_tool_registry = SkillToolRegistry(skill_library)
    runner._chat_client = OpenAICompatibleChatClient()
    prompt = [
        ChatMessage(
            role="system",
            content=(
                "必须遵循以下已挂载的 find-skills SKILL.md，并使用平台提供的真实工具。\n\n"
                + activation.skills[0].instructions
            ),
        ),
        ChatMessage(role="user", content=activation.user_task_text),
    ]
    final_content, traces = runner._complete_skill_agent(resolution, prompt, context)
    print(
        json.dumps(
            {
                "model": profile.model_id,
                "repository": args.repository,
                "install_requested": args.install,
                "final": final_content,
                "traces": [
                    {
                        "tool": trace.tool_name,
                        "status": trace.status,
                        "result_count": trace.result_count,
                        "message": trace.message,
                    }
                    for trace in traces
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
