from __future__ import annotations

from pathlib import Path

import pytest

from src.career_assistant.online_assessment.contracts import (
    AssessmentTestCase,
    CapturedProblemInput,
    InterfaceKind,
    ProgrammingLanguage,
    TestCaseKind as AssessmentTestKind,
)
from src.career_assistant.online_assessment.problem_extractor import normalize_capture
from src.career_assistant.settings import load_online_assessment_settings


def test_capture_normalization_caps_text_and_blocks_low_confidence() -> None:
    problem = normalize_capture(CapturedProblemInput(visible_text="x" * 30_000))

    assert len(problem.statement) == 25_000
    assert problem.confidence < 0.65
    assert problem.needs_confirmation is True
    assert "无法判断题目输入输出接口" in problem.incomplete_reasons


def test_capture_prefers_problem_candidate_and_normalizes_public_test() -> None:
    problem = normalize_capture(
        CapturedProblemInput(
            source_url="https://leetcode.com/problems/two-sum/",
            source_title="Two Sum - LeetCode",
            source_platform="leetcode",
            visible_text="navigation noise",
            problem_candidates=[
                "Two Sum\nGiven an array of integers nums and an integer target, return indices.\n"
                "Constraints: 2 <= nums.length <= 10^4",
            ],
            starter_code="class Solution:\n    def twoSum(self, nums, target):",
            language_hint="python3",
            function_signature="twoSum(nums: list[int], target: int) -> list[int]",
            public_test_candidates=[
                {"input": "[2,7,11,15]\n9", "output": "[0,1]", "explanation": "基础样例"},
            ],
        ),
    )

    assert problem.title == "Two Sum"
    assert problem.language is ProgrammingLanguage.PYTHON
    assert problem.interface_kind is InterfaceKind.FUNCTION
    assert problem.needs_confirmation is False
    assert problem.examples[0].kind is AssessmentTestKind.PUBLIC
    assert problem.examples[0].expected_output == "[0,1]"


def test_leetcode_starter_code_supplies_missing_function_signature() -> None:
    problem = normalize_capture(
        CapturedProblemInput(
            source_url="https://leetcode.cn/problems/group-anagrams/",
            source_title="49. 字母异位词分组 - 力扣（LeetCode）",
            source_platform="leetcode",
            problem_candidates=[
                "给你一个字符串数组 strs，请你将字母异位词组合在一起。"
                "示例输入：strs = [eat, tea, tan]，示例输出：[[eat, tea], [tan]]。",
            ],
            starter_code=(
                "class Solution:\n"
                "    def groupAnagrams(self, strs: list[str]) -> list[list[str]]:\n"
                "        pass"
            ),
            language_hint="Python3",
        )
    )

    assert problem.source_platform == "leetcode"
    assert problem.title == "49. 字母异位词分组"
    assert problem.interface_kind is InterfaceKind.FUNCTION
    assert problem.function_signature == "def groupAnagrams(self, strs: list[str])"
    assert "无法判断题目输入输出接口" not in problem.incomplete_reasons
    assert "函数题缺少函数签名" not in problem.incomplete_reasons


def test_assessment_test_case_rejects_generated_case_without_explanation() -> None:
    with pytest.raises(ValueError, match="AI 测试必须说明"):
        AssessmentTestCase(
            kind=AssessmentTestKind.GENERATED,
            input_payload="[]",
            expected_output="0",
        )


def test_online_assessment_settings_are_strict(tmp_path: Path) -> None:
    config_path = tmp_path / "career.yaml"
    config_path.write_text(
        """
career_assistant:
  online_assessment:
    problem_extractor_profile_key: qwen-vision
    answer_profile_key: deepseek-code
    piston_base_url: http://127.0.0.1:2000
    request_timeout_seconds: 12
    max_test_cases: 20
    max_repair_rounds: 2
""".strip(),
        encoding="utf-8",
    )

    settings = load_online_assessment_settings(config_path)

    assert settings.problem_extractor_profile_key == "qwen-vision"
    assert settings.answer_profile_key == "deepseek-code"
    assert settings.piston_base_url == "http://127.0.0.1:2000"
    assert settings.max_test_cases == 20
    assert settings.max_repair_rounds == 2
