"""组件化 Prompt 编排测试。"""

from pathlib import Path


def test_prompt_context_declares_all_required_component_slots() -> None:
    source = Path("src/career_assistant/prompt_context.py").read_text(encoding="utf-8")
    for key in (
        "system", "skills", "session_summary_critical",
        "session_summary_details", "resume", "job", "interview",
        "attachments", "current_input",
    ):
        assert f'"{key}"' in source
    assert 'f"recent_turn:{turn.turn_id}"' in source
    assert '"career_memory"' not in source
    assert '"career_memory_related"' not in source


def test_current_input_and_corrections_are_pinned() -> None:
    source = Path("src/career_assistant/prompt_context.py").read_text(encoding="utf-8")
    assert '"user_corrections": list(summary.user_corrections)' in source
    assert 'PromptComponent.pinned(\n                "current_input"' in source
    assert "limit=6" not in source
    assert "1_600" not in source


def test_prompt_context_recalculates_runtime_output_around_compaction() -> None:
    source = Path("src/career_assistant/prompt_context.py").read_text(encoding="utf-8")
    assert "system_max_output_tokens" in source
    assert source.count("runtime_policy(") >= 2
    assert "MINIMUM_VIABLE_OUTPUT_TOKENS" in source
    assert "desired_output_tokens" in source
