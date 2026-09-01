"""线上笔试助手在扩展、Web、模型和执行器之间共享的稳定契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProgrammingLanguage(StrEnum):
    """首版允许生成和测试的语言。"""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    JAVA = "java"
    CPP = "cpp"


class InterfaceKind(StrEnum):
    """题目代码与测试输入的连接方式。"""

    FUNCTION = "function"
    STDIN_STDOUT = "stdin_stdout"
    UNKNOWN = "unknown"


class TestCaseKind(StrEnum):
    """区分平台公开样例和模型推导的边界测试。"""

    PUBLIC = "public"
    GENERATED = "generated"


class CompileStatus(StrEnum):
    SKIPPED = "skipped"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TestStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ExecutionFinalStatus(StrEnum):
    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"


class AssessmentTestCase(BaseModel):
    """单个公开样例或 AI 生成测试。"""

    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=80)
    kind: TestCaseKind = TestCaseKind.PUBLIC
    input_payload: str | list[Any] | dict[str, Any]
    expected_output: str = Field(max_length=64_000)
    explanation: str = Field(default="", max_length=1_000)

    @model_validator(mode="after")
    def require_generated_explanation(self) -> "AssessmentTestCase":
        if self.kind is TestCaseKind.GENERATED and not self.explanation.strip():
            raise ValueError("AI 测试必须说明覆盖的边界")
        return self


class CapturedProblemInput(BaseModel):
    """扩展只读采集后提交给后端的受限输入。"""

    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(default="", max_length=4_000)
    source_title: str = Field(default="", max_length=500)
    source_platform: str = Field(default="generic", max_length=80)
    visible_text: str = Field(default="", max_length=30_000)
    problem_candidates: list[str] = Field(default_factory=list, max_length=12)
    starter_code: str = Field(default="", max_length=80_000)
    language_hint: str = Field(default="", max_length=80)
    function_signature: str = Field(default="", max_length=2_000)
    public_test_candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    screenshot_data_url: str | None = Field(default=None, max_length=7_100_000)
    viewport: dict[str, float | int] = Field(default_factory=dict)
    capture_mode: str = Field(default="generic", max_length=80)

    @field_validator("problem_candidates")
    @classmethod
    def normalize_candidates(cls, value: list[str]) -> list[str]:
        return [str(item)[:25_000] for item in value if str(item).strip()]


class AssessmentProblem(BaseModel):
    """经确定性清洗或模型复核后的结构化题目。"""

    model_config = ConfigDict(extra="forbid")

    problem_id: UUID = Field(default_factory=uuid4)
    source_url: str = ""
    source_platform: str = "generic"
    title: str = "未识别题目"
    statement: str = Field(min_length=1, max_length=25_000)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    examples: list[AssessmentTestCase] = Field(default_factory=list, max_length=20)
    starter_code: str = Field(default="", max_length=80_000)
    language: ProgrammingLanguage = ProgrammingLanguage.PYTHON
    interface_kind: InterfaceKind = InterfaceKind.UNKNOWN
    function_signature: str = Field(default="", max_length=2_000)
    confidence: float = Field(ge=0.0, le=1.0)
    incomplete_reasons: list[str] = Field(default_factory=list, max_length=20)

    @property
    def needs_confirmation(self) -> bool:
        return self.confidence < 0.65 or bool(self.incomplete_reasons)


class AssessmentSolution(BaseModel):
    """可供用户编辑、复制并交给执行 Provider 的最终答案。"""

    model_config = ConfigDict(extra="forbid")

    approach_markdown: str = Field(min_length=1, max_length=40_000)
    code: str = Field(min_length=1, max_length=100_000)
    language: ProgrammingLanguage
    time_complexity: str = Field(min_length=1, max_length=500)
    space_complexity: str = Field(min_length=1, max_length=500)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class AssessmentTestResult(BaseModel):
    """执行器返回给浏览器的安全化单测结果。"""

    model_config = ConfigDict(extra="forbid")

    test_id: str
    kind: TestCaseKind
    status: TestStatus
    actual_output: str = Field(default="", max_length=65_536)
    error_summary: str = Field(default="", max_length=4_000)
    duration_ms: int = Field(default=0, ge=0)

    @property
    def passed(self) -> bool:
        return self.status is TestStatus.PASSED


class AssessmentExecutionReport(BaseModel):
    """一次编译和最多二十个测试的聚合结果。"""

    model_config = ConfigDict(extra="forbid")

    compile_status: CompileStatus
    tests: list[AssessmentTestResult] = Field(default_factory=list, max_length=20)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    final_status: ExecutionFinalStatus
    repair_rounds: int = Field(default=0, ge=0, le=2)
