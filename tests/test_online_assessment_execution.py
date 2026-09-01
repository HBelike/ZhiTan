from __future__ import annotations

import json

import httpx
import pytest

from src.career_assistant.online_assessment.contracts import (
    AssessmentProblem,
    AssessmentSolution,
    AssessmentTestCase,
    CompileStatus,
    ExecutionFinalStatus,
    InterfaceKind,
    ProgrammingLanguage,
    TestCaseKind as AssessmentTestKind,
    TestStatus as AssessmentTestStatus,
)
from src.career_assistant.online_assessment.execution import (
    ExecutionUnavailableError,
    PistonExecutionProvider,
)


def solution(language: ProgrammingLanguage = ProgrammingLanguage.PYTHON) -> AssessmentSolution:
    return AssessmentSolution(
        approach_markdown="读取两个整数并求和。",
        code="a, b = map(int, input().split())\nprint(a + b)",
        language=language,
        time_complexity="O(1)",
        space_complexity="O(1)",
    )


def test_piston_maps_python_and_marks_public_sample_passed() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"run": {"stdout": "3\n", "stderr": "", "code": 0, "signal": None}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = PistonExecutionProvider("http://piston.test", client=client)
    sample = AssessmentTestCase(
        test_id="public-1",
        kind=AssessmentTestKind.PUBLIC,
        input_payload="1 2\n",
        expected_output="3",
    )

    report = provider.execute(solution(), (sample,))

    assert requests[0]["language"] == "python"
    assert requests[0]["run_timeout"] == 3000
    assert report.tests[0].status is AssessmentTestStatus.PASSED
    assert report.tests[0].kind is AssessmentTestKind.PUBLIC
    assert report.compile_status is CompileStatus.SKIPPED
    assert report.final_status is ExecutionFinalStatus.PASSED


def test_piston_safely_reports_compile_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "compile": {"stdout": "", "stderr": "/tmp/piston/jobs/secret/Main.java: error", "code": 1},
                "run": {"stdout": "", "stderr": "", "code": 1},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = PistonExecutionProvider("http://piston.test", client=client)
    sample = AssessmentTestCase(input_payload="1 2", expected_output="3")

    report = provider.execute(solution(ProgrammingLanguage.JAVA), (sample,))

    assert report.compile_status is CompileStatus.FAILED
    assert report.final_status is ExecutionFinalStatus.FAILED
    assert "/tmp/" not in report.tests[0].error_summary
    assert "Main.java" in report.tests[0].error_summary


def test_piston_rejects_more_than_twenty_tests() -> None:
    provider = PistonExecutionProvider("http://piston.test")
    tests = tuple(AssessmentTestCase(input_payload=str(index), expected_output=str(index)) for index in range(21))

    with pytest.raises(ValueError, match="最多执行 20 个测试"):
        provider.execute(solution(), tests)


def test_piston_network_failure_is_readiness_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = PistonExecutionProvider("http://piston.test", client=client)

    with pytest.raises(ExecutionUnavailableError, match="代码执行器未就绪"):
        provider.execute(solution(), (AssessmentTestCase(input_payload="1 2", expected_output="3"),))


def test_piston_builds_python_function_harness_without_sending_test_as_stdin() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"run": {"stdout": "[0,1]\n", "stderr": "", "code": 0}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = PistonExecutionProvider("http://piston.test", client=client)
    problem = AssessmentProblem(
        title="两数之和",
        statement="返回两个数的下标。",
        language=ProgrammingLanguage.PYTHON,
        interface_kind=InterfaceKind.FUNCTION,
        function_signature="def twoSum(nums: list[int], target: int) -> list[int]",
        confidence=1,
    )
    function_solution = AssessmentSolution(
        approach_markdown="哈希表。",
        code=(
            "class Solution:\n"
            "    def twoSum(self, nums, target):\n"
            "        return [0, 1]\n"
        ),
        language=ProgrammingLanguage.PYTHON,
        time_complexity="O(n)",
        space_complexity="O(n)",
    )
    sample = AssessmentTestCase(input_payload=[[2, 7, 11, 15], 9], expected_output="[0, 1]")

    report = provider.execute(function_solution, (sample,), problem=problem)

    source = requests[0]["files"][0]["content"]
    assert "Solution()" in source
    assert "twoSum" in source
    assert requests[0]["stdin"] == ""
    assert report.final_status is ExecutionFinalStatus.PASSED


@pytest.mark.parametrize(
    ("language", "signature", "code", "marker"),
    [
        (
            ProgrammingLanguage.JAVASCRIPT,
            "twoSum(nums, target) {",
            "class Solution { twoSum(nums, target) { return [0, 1]; } }",
            "new Solution()",
        ),
        (
            ProgrammingLanguage.JAVA,
            "int[] twoSum(int[] nums, int target) {",
            "class Solution { int[] twoSum(int[] nums, int target) { return new int[]{0,1}; } }",
            "class Main",
        ),
        (
            ProgrammingLanguage.CPP,
            "vector<int> twoSum(vector<int>& nums, int target) {",
            "class Solution { public: vector<int> twoSum(vector<int>& nums, int target) { return {0,1}; } };",
            "int main()",
        ),
    ],
)
def test_piston_builds_supported_language_function_harnesses(
    language: ProgrammingLanguage,
    signature: str,
    code: str,
    marker: str,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"run": {"stdout": "[0,1]", "stderr": "", "code": 0}})

    provider = PistonExecutionProvider(
        "http://piston.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    problem = AssessmentProblem(
        title="两数之和",
        statement="返回两个数的下标。",
        language=language,
        interface_kind=InterfaceKind.FUNCTION,
        function_signature=signature,
        confidence=1,
    )
    candidate = AssessmentSolution(
        approach_markdown="哈希表。",
        code=code,
        language=language,
        time_complexity="O(n)",
        space_complexity="O(n)",
    )

    report = provider.execute(
        candidate,
        (AssessmentTestCase(input_payload=[[2, 7], 9], expected_output="[0, 1]"),),
        problem=problem,
    )

    source = requests[0]["files"][0]["content"]
    assert marker in source
    if language is ProgrammingLanguage.JAVA:
        assert source.index("class Main") < source.index("class Solution")
    if language is ProgrammingLanguage.CPP:
        assert "auto __oaArg0" in source
    assert report.final_status is ExecutionFinalStatus.PASSED
