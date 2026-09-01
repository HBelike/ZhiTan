"""通过独立 Piston 服务运行模型代码，FastAPI 进程本身不执行任何题目代码。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from time import monotonic
from typing import Protocol
import re

import httpx

from src.career_assistant.online_assessment.contracts import (
    AssessmentExecutionReport,
    AssessmentProblem,
    AssessmentSolution,
    AssessmentTestCase,
    AssessmentTestResult,
    CompileStatus,
    ExecutionFinalStatus,
    InterfaceKind,
    ProgrammingLanguage,
    TestStatus,
)


MAX_OUTPUT_CHARACTERS = 65_536
_RUNTIME_NAMES = {
    ProgrammingLanguage.PYTHON: ("python", "main.py"),
    ProgrammingLanguage.JAVASCRIPT: ("javascript", "main.js"),
    ProgrammingLanguage.JAVA: ("java", "Main.java"),
    ProgrammingLanguage.CPP: ("c++", "main.cpp"),
}


class ExecutionUnavailableError(RuntimeError):
    """Piston 没有启动或无法响应时的安全业务错误。"""


class CodeExecutionProvider(Protocol):
    """执行服务的可替换边界。"""

    def execute(
        self,
        solution: AssessmentSolution,
        tests: Sequence[AssessmentTestCase],
        *,
        problem: AssessmentProblem | None = None,
    ) -> AssessmentExecutionReport: ...


@dataclass(frozen=True)
class _PistonCaseResult:
    compile_status: CompileStatus
    test_result: AssessmentTestResult


class PistonExecutionProvider:
    """使用 Piston v2 execute API 逐个执行公开或 AI 测试。"""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_seconds: float = 12.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(request_timeout_seconds),
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def execute(
        self,
        solution: AssessmentSolution,
        tests: Sequence[AssessmentTestCase],
        *,
        problem: AssessmentProblem | None = None,
    ) -> AssessmentExecutionReport:
        if not tests:
            raise ValueError("至少需要一个测试用例")
        if len(tests) > 20:
            raise ValueError("一次最多执行 20 个测试")

        started_at = monotonic()
        results: list[AssessmentTestResult] = []
        compile_status = CompileStatus.SKIPPED
        for test in tests:
            case = self._execute_case(solution, test, problem=problem)
            results.append(case.test_result)
            if case.compile_status is not CompileStatus.SKIPPED:
                compile_status = case.compile_status
            if case.compile_status in {CompileStatus.FAILED, CompileStatus.TIMEOUT}:
                break

        if len(results) < len(tests):
            error_summary = results[-1].error_summary if results else "编译失败"
            results.extend(
                AssessmentTestResult(
                    test_id=test.test_id,
                    kind=test.kind,
                    status=TestStatus.FAILED,
                    error_summary=error_summary,
                )
                for test in tests[len(results):]
            )

        passed_count = sum(result.passed for result in results)
        failed_count = len(results) - passed_count
        final_status = (
            ExecutionFinalStatus.PASSED
            if failed_count == 0
            else ExecutionFinalStatus.PARTIAL
            if passed_count > 0
            else ExecutionFinalStatus.FAILED
        )
        return AssessmentExecutionReport(
            compile_status=compile_status,
            tests=results,
            passed_count=passed_count,
            failed_count=failed_count,
            duration_ms=max(0, round((monotonic() - started_at) * 1_000)),
            final_status=final_status,
        )

    def _execute_case(
        self,
        solution: AssessmentSolution,
        test: AssessmentTestCase,
        *,
        problem: AssessmentProblem | None,
    ) -> _PistonCaseResult:
        runtime, filename = _RUNTIME_NAMES[solution.language]
        is_function = problem is not None and problem.interface_kind is InterfaceKind.FUNCTION
        source = _build_function_harness(solution, test, problem) if is_function else solution.code
        stdin = "" if is_function else (
            test.input_payload if isinstance(test.input_payload, str) else _json_input(test.input_payload)
        )
        payload = {
            "language": runtime,
            "version": "*",
            "files": [{"name": filename, "content": source}],
            "stdin": stdin,
            "compile_timeout": 10_000,
            "run_timeout": 3_000,
            "compile_memory_limit": 536_870_912,
            "run_memory_limit": 268_435_456,
        }
        started_at = monotonic()
        try:
            response = self._client.post(f"{self._base_url}/api/v2/execute", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExecutionUnavailableError("代码执行器未就绪，请启动本地 Piston 后重试") from exc

        compile_payload = body.get("compile") if isinstance(body, dict) else None
        compile_status = _compile_status(compile_payload)
        run_payload = body.get("run") if isinstance(body, dict) else None
        if not isinstance(run_payload, dict):
            run_payload = {}
        actual_output = _bounded(str(run_payload.get("stdout", "")))
        stderr = _safe_error(str(run_payload.get("stderr", "")))
        if compile_status in {CompileStatus.FAILED, CompileStatus.TIMEOUT}:
            compile_error = _safe_error(str((compile_payload or {}).get("stderr", "")))
            status = TestStatus.TIMEOUT if compile_status is CompileStatus.TIMEOUT else TestStatus.FAILED
            return _PistonCaseResult(
                compile_status=compile_status,
                test_result=AssessmentTestResult(
                    test_id=test.test_id,
                    kind=test.kind,
                    status=status,
                    actual_output=actual_output,
                    error_summary=compile_error or "代码编译失败",
                    duration_ms=max(0, round((monotonic() - started_at) * 1_000)),
                ),
            )

        timed_out = _is_timeout(run_payload)
        expected = _normalize_output(test.expected_output)
        actual = _normalize_output(actual_output)
        passed = not timed_out and int(run_payload.get("code", 0) or 0) == 0 and actual == expected
        status = TestStatus.TIMEOUT if timed_out else TestStatus.PASSED if passed else TestStatus.FAILED
        error_summary = stderr
        if status is TestStatus.FAILED and not error_summary and actual != expected:
            error_summary = "实际输出与期望输出不一致"
        return _PistonCaseResult(
            compile_status=compile_status,
            test_result=AssessmentTestResult(
                test_id=test.test_id,
                kind=test.kind,
                status=status,
                actual_output=actual_output,
                error_summary=error_summary,
                duration_ms=max(0, round((monotonic() - started_at) * 1_000)),
            ),
        )


def _compile_status(payload: object) -> CompileStatus:
    if not isinstance(payload, dict):
        return CompileStatus.SKIPPED
    if _is_timeout(payload):
        return CompileStatus.TIMEOUT
    return CompileStatus.PASSED if int(payload.get("code", 0) or 0) == 0 else CompileStatus.FAILED


def _is_timeout(payload: dict[str, object]) -> bool:
    signal = str(payload.get("signal", "")).lower()
    stderr = str(payload.get("stderr", "")).lower()
    return "timeout" in stderr or signal in {"sigkill", "sigxcpu"}


def _bounded(value: str) -> str:
    if len(value) <= MAX_OUTPUT_CHARACTERS:
        return value
    return f"{value[:MAX_OUTPUT_CHARACTERS]}\n[输出已截断]"


def _safe_error(value: str) -> str:
    bounded = _bounded(value).strip()
    bounded = re.sub(
        r"(?:[A-Za-z]:)?(?:[/\\][^/\\\s:]+)+[/\\]([^/\\\s:]+)",
        r"\1",
        bounded,
    )
    return bounded[:4_000]


def _normalize_output(value: str) -> str:
    normalized = "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").strip().split("\n"))
    try:
        return json.dumps(json.loads(normalized), ensure_ascii=False, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return normalized


def _json_input(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _build_function_harness(
    solution: AssessmentSolution,
    test: AssessmentTestCase,
    problem: AssessmentProblem,
) -> str:
    """为常见函数题生成仅用于本次测试的单文件入口。"""

    function_name = _function_name(problem.function_signature, solution.code)
    if not function_name:
        raise ValueError("函数题缺少可识别的函数名，请先确认函数签名")
    payload = _function_arguments(test.input_payload)
    builders = {
        ProgrammingLanguage.PYTHON: _python_harness,
        ProgrammingLanguage.JAVASCRIPT: _javascript_harness,
        ProgrammingLanguage.JAVA: _java_harness,
        ProgrammingLanguage.CPP: _cpp_harness,
    }
    return builders[solution.language](solution.code, function_name, payload)


def _function_name(signature: str, code: str) -> str:
    candidates = [signature, code]
    patterns = (
        r"\bdef\s+([A-Za-z_]\w*)\s*\(",
        r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(",
        r"\b([A-Za-z_]\w*)\s*\([^()]*\)\s*(?:const|throws|\{|:)",
    )
    ignored = {"if", "for", "while", "switch", "main", "constructor"}
    for value in candidates:
        for pattern in patterns:
            for match in re.finditer(pattern, value or ""):
                name = match.group(1)
                if name not in ignored:
                    return name
    return ""


def _function_arguments(value: object) -> list[object]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
    if isinstance(parsed, dict):
        return list(parsed.values())
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _python_harness(code: str, function_name: str, arguments: list[object]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    payload_literal = json.dumps(payload, ensure_ascii=False)
    name_literal = json.dumps(function_name)
    return f"""{code.rstrip()}

import json as __oa_json
__oa_args = __oa_json.loads({payload_literal})
__oa_name = {name_literal}
__oa_target = Solution() if "Solution" in globals() else None
__oa_fn = getattr(__oa_target, __oa_name) if __oa_target is not None else globals()[__oa_name]
__oa_result = __oa_fn(*__oa_args)
if isinstance(__oa_result, (dict, list, tuple, bool)) or __oa_result is None:
    print(__oa_json.dumps(__oa_result, ensure_ascii=False, separators=(",", ":")))
else:
    print(__oa_result)
"""


def _javascript_harness(code: str, function_name: str, arguments: list[object]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    payload_literal = json.dumps(payload, ensure_ascii=False)
    name_literal = json.dumps(function_name)
    return f"""{code.rstrip()}

const __oaArgs = JSON.parse({payload_literal});
const __oaName = {name_literal};
const __oaTarget = typeof Solution !== "undefined" ? new Solution() : null;
const __oaFn = __oaTarget && typeof __oaTarget[__oaName] === "function"
  ? __oaTarget[__oaName].bind(__oaTarget)
  : eval(__oaName);
const __oaResult = __oaFn(...__oaArgs);
console.log(typeof __oaResult === "string" ? __oaResult : JSON.stringify(__oaResult));
"""


def _java_harness(code: str, function_name: str, arguments: list[object]) -> str:
    call_arguments = ", ".join(_java_literal(value) for value in arguments)
    return f"""import java.util.*;
import java.lang.reflect.Array;

class Main {{
    private static String __oaFormat(Object value) {{
        if (value == null) return "null";
        if (value instanceof Boolean) return ((Boolean) value) ? "true" : "false";
        if (value.getClass().isArray()) {{
            StringJoiner joiner = new StringJoiner(",", "[", "]");
            for (int i = 0; i < Array.getLength(value); i++) joiner.add(__oaFormat(Array.get(value, i)));
            return joiner.toString();
        }}
        if (value instanceof Collection<?>) {{
            StringJoiner joiner = new StringJoiner(",", "[", "]");
            for (Object item : (Collection<?>) value) joiner.add(__oaFormat(item));
            return joiner.toString();
        }}
        return String.valueOf(value);
    }}

    public static void main(String[] args) {{
        Object result = new Solution().{function_name}({call_arguments});
        System.out.print(__oaFormat(result));
    }}
}}

{code.rstrip()}
"""


def _java_literal(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "new int[]{}"
        if all(isinstance(item, list) for item in value):
            return "new int[][]{" + ",".join(_java_literal(item) for item in value) + "}"
        if all(isinstance(item, str) for item in value):
            return "new String[]{" + ",".join(_java_literal(item) for item in value) + "}"
        return "new int[]{" + ",".join(_java_literal(item) for item in value) + "}"
    raise ValueError("Java 函数测试暂不支持对象参数")


def _cpp_harness(code: str, function_name: str, arguments: list[object]) -> str:
    declarations = "\n    ".join(
        f"auto __oaArg{index} = {_cpp_literal(value)};"
        for index, value in enumerate(arguments)
    )
    call_arguments = ", ".join(f"__oaArg{index}" for index in range(len(arguments)))
    return f"""#include <bits/stdc++.h>
using namespace std;

{code.rstrip()}

template <typename T> void __oaPrint(const T& value) {{ cout << value; }}
void __oaPrint(const bool& value) {{ cout << (value ? "true" : "false"); }}
template <typename T> void __oaPrint(const vector<T>& values) {{
    cout << "[";
    for (size_t i = 0; i < values.size(); ++i) {{ if (i) cout << ","; __oaPrint(values[i]); }}
    cout << "]";
}}

int main() {{
    Solution solution;
    {declarations}
    auto result = solution.{function_name}({call_arguments});
    __oaPrint(result);
    return 0;
}}
"""


def _cpp_literal(value: object) -> str:
    if value is None:
        return "nullptr"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        if not value:
            return "vector<int>{}"
        if all(isinstance(item, list) for item in value):
            return "vector<vector<int>>{" + ",".join(_cpp_literal(item) for item in value) + "}"
        if all(isinstance(item, str) for item in value):
            return "vector<string>{" + ",".join(_cpp_literal(item) for item in value) + "}"
        return "vector<int>{" + ",".join(_cpp_literal(item) for item in value) + "}"
    raise ValueError("C++ 函数测试暂不支持对象参数")
